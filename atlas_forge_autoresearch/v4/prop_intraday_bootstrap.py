"""Intraday prop-firm development optimizer aligned to Prague reset days.

Uses checksum-verified Binance 1h BTC/ETH spot history as a price proxy for
FTMO BTCUSD/ETHUSD CFDs.  This is materially more faithful than the daily
screen because it reconstructs intraday equity paths and resets FTMO daily
limits at midnight Europe/Prague.
"""
from __future__ import annotations

from pathlib import Path
from dataclasses import replace
import argparse
import json
import subprocess

import numpy as np
import pandas as pd

from .account_profiles import FTMO_1STEP, FTMO_2STEP, PropFirmProgram
from .live_bootstrap import json_safe
from .multi_asset_engine import AssetCost, MultiAssetBacktester, PortfolioLimits
from .prop_firm_engine import active_day_proxy, optimize_prop_exposure
from .risk_overlays import volatility_target_overlay
from .continuous_bridge import prop_transfer_candidates
from .phase2_bridge import prop_transfer_candidates as phase2_prop_transfer_candidates


PRAGUE = "Europe/Prague"
PROP_SCALES = tuple(np.round(np.arange(0.05, 1.01, 0.05), 2))
FTMO_CRYPTO_COMMISSION_BPS = 3.25
RESEARCH_SLIPPAGE_BPS = 2.0
PROP_COST_STRESS_MULTIPLIER = 3.0


def research_commit_sha() -> str | None:
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def read_hourly(path: Path) -> pd.DataFrame:
    x = pd.read_csv(path)
    if "Date" not in x:
        raise ValueError(f"{path}: Date required")
    idx = pd.to_datetime(x.pop("Date"), utc=True, format="mixed")
    x.index = pd.DatetimeIndex(idx)
    x.index.name = "Date"
    if x.index.has_duplicates or not x.index.is_monotonic_increasing:
        raise ValueError(f"{path}: invalid timestamp ordering")
    for c in ("Open", "High", "Low", "Close", "Volume"):
        if c in x:
            x[c] = pd.to_numeric(x[c], errors="coerce")
    if x[["Open", "High", "Low", "Close"]].isna().any().any():
        raise ValueError(f"{path}: invalid OHLC")
    if len(x) and x.index.max().tz_convert("UTC").tz_localize(None) >= pd.Timestamp("2021-01-01"):
        raise RuntimeError("intraday prop development data crosses sealed boundary")
    return x


def load_data(root: Path) -> dict[str, pd.DataFrame]:
    mapping = {
        "BTCUSDT": "btc_1h.csv",
        "ETHUSDT": "eth_1h.csv",
        "BNBUSDT": "bnb_1h.csv",
        "LTCUSDT": "ltc_1h.csv",
    }
    required = {"BTCUSDT", "ETHUSDT"}
    out = {}
    for symbol, name in mapping.items():
        p = root / name
        if p.exists():
            out[symbol] = read_hourly(p)
        elif symbol in required:
            raise FileNotFoundError(p)
    common = None
    for frame in out.values():
        common = frame.index if common is None else common.intersection(frame.index)
    if common is None or len(common) < 24 * 300:
        raise RuntimeError("insufficient common hourly history")
    return {s: x.loc[common].copy() for s, x in out.items()}


def hourly_rotation_strategy(params, symbols):
    lookback = int(params["lookback"])
    trend = int(params["trend"])
    top_k = int(params["top_k"])
    rebalance_hours = int(params.get("rebalance_hours", 1))
    execution_session = str(params.get("execution_session", "all"))
    if rebalance_hours not in (1, 2, 4, 8, 12, 24):
        raise ValueError("unsupported rebalance_hours")
    if execution_session not in (
        "all",
        "avoid_funding_hours",
        "europe_us",
    ):
        raise ValueError("unsupported execution_session")

    def strategy(data, features=None):
        index = next(iter(data.values())).index
        columns = sorted(data)
        momentum = pd.DataFrame({
            s: data[s]["Close"] / data[s]["Close"].shift(lookback) - 1.0
            for s in symbols
        }, index=index).reindex(columns=columns)
        healthy = pd.DataFrame({
            s: data[s]["Close"] > data[s]["Close"].rolling(
                trend, min_periods=trend
            ).mean()
            for s in symbols
        }, index=index).reindex(columns=columns).fillna(False)

        eligible = momentum.where(healthy & momentum.gt(0.0))
        ranks = eligible.rank(axis=1, method="first", ascending=False)
        selected = ranks.le(float(top_k)) & eligible.notna()
        count = selected.sum(axis=1).replace(0, np.nan)
        desired = selected.astype(float).div(count, axis=0).fillna(0.0)

        # Target[t] executes no earlier than open[t+1]. Session and rebalance
        # decisions therefore use the known clock of that next execution bar.
        utc_hours = pd.Series(
            index.tz_convert("UTC").hour,
            index=index,
            dtype=int,
        )
        next_hours = utc_hours.shift(-1)
        if execution_session == "all":
            allowed = pd.Series(True, index=index)
        elif execution_session == "avoid_funding_hours":
            allowed = ~next_hours.isin([0, 8, 16])
        else:
            allowed = (next_hours >= 7) & (next_hours < 22)
        allowed = allowed.fillna(False)

        rebalance = (
            next_hours.notna()
            & (next_hours.astype("Int64") % rebalance_hours == 0)
        )

        # Prague midnight is an explicit state reset, not merely one flat bar:
        # after flattening, exposure stays at zero until the next eligible
        # scheduled rebalance.
        local_dates = pd.Series(index.tz_convert(PRAGUE).date, index=index)
        next_dates = local_dates.shift(-1)
        reset = next_dates.notna() & (next_dates != local_dates)

        update = (~allowed) | rebalance | reset
        out = pd.DataFrame(np.nan, index=index, columns=columns)
        zero_update = update & (~allowed | reset | ~rebalance)
        signal_update = update & allowed & rebalance & ~reset
        out.loc[zero_update] = 0.0
        out.loc[signal_update] = desired.loc[signal_update]
        out = out.ffill().fillna(0.0)
        if len(out):
            out.iloc[-1] = 0.0
        return out

    return strategy


def hourly_tsmom_strategy(params, symbols):
    """Causal equal-risk-sign crypto time-series momentum hypothesis.

    Each asset is long when its own trailing return is positive and short when
    negative. Raw gross exposure is normalized to one before the portfolio
    volatility target is applied. Session/rebalance timing is based only on
    the known next execution-bar clock.
    """
    lookback = int(params["lookback"])
    rebalance_hours = int(params.get("rebalance_hours", 4))
    execution_session = str(params.get("execution_session", "all"))
    if rebalance_hours not in (1, 2, 4, 8, 12, 24):
        raise ValueError("unsupported rebalance_hours")
    if execution_session not in (
        "all",
        "avoid_funding_hours",
        "europe_us",
    ):
        raise ValueError("unsupported execution_session")

    def strategy(data, features=None):
        index = next(iter(data.values())).index
        columns = sorted(data)
        momentum = pd.DataFrame(
            {
                s: data[s]["Close"] / data[s]["Close"].shift(lookback) - 1.0
                for s in symbols
            },
            index=index,
        ).reindex(columns=columns)
        signs = np.sign(momentum).replace([np.inf, -np.inf], np.nan).fillna(0.0)
        count = signs.ne(0.0).sum(axis=1).replace(0, np.nan)
        desired = signs.div(count, axis=0).fillna(0.0)

        utc_hours = pd.Series(
            index.tz_convert("UTC").hour,
            index=index,
            dtype=int,
        )
        next_hours = utc_hours.shift(-1)
        if execution_session == "all":
            allowed = pd.Series(True, index=index)
        elif execution_session == "avoid_funding_hours":
            allowed = ~next_hours.isin([0, 8, 16])
        else:
            allowed = (next_hours >= 7) & (next_hours < 22)
        allowed = allowed.fillna(False)
        rebalance = (
            next_hours.notna()
            & (next_hours.astype("Int64") % rebalance_hours == 0)
        )

        local_dates = pd.Series(index.tz_convert(PRAGUE).date, index=index)
        next_dates = local_dates.shift(-1)
        reset = next_dates.notna() & (next_dates != local_dates)

        update = (~allowed) | rebalance | reset
        out = pd.DataFrame(np.nan, index=index, columns=columns)
        zero_update = update & (~allowed | reset | ~rebalance)
        signal_update = update & allowed & rebalance & ~reset
        out.loc[zero_update] = 0.0
        out.loc[signal_update] = desired.loc[signal_update]
        out = out.ffill().fillna(0.0)
        if len(out):
            out.iloc[-1] = 0.0
        return out

    return strategy


def _utc_daily_ohlc(frame: pd.DataFrame) -> pd.DataFrame:
    """Aggregate completed UTC crypto days for exact daily-signal transfer."""
    daily = frame.resample("1D").agg(
        {
            "Open": "first",
            "High": "max",
            "Low": "min",
            "Close": "last",
            "Volume": "sum",
        }
    )
    return daily.dropna(subset=["Open", "High", "Low", "Close"])


def _rsi_now_daily(close: pd.Series, n: int) -> pd.Series:
    d = close.astype(float).diff()
    up = d.clip(lower=0.0).ewm(alpha=1.0 / float(n), adjust=False).mean()
    dn = (-d.clip(upper=0.0)).ewm(alpha=1.0 / float(n), adjust=False).mean()
    rs = up / dn.replace(0.0, np.nan)
    return 100.0 - 100.0 / (1.0 + rs)


def _atr_shifted_daily(frame: pd.DataFrame, n: int) -> pd.Series:
    h = frame["High"].astype(float)
    l = frame["Low"].astype(float)
    cl = frame["Close"].astype(float)
    pc = cl.shift(1)
    tr = pd.concat(
        [(h - l), (h - pc).abs(), (l - pc).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(int(n)).mean().shift(1)


def _adx_now_daily(frame: pd.DataFrame, n: int) -> pd.Series:
    h = frame["High"].astype(float)
    l = frame["Low"].astype(float)
    c = frame["Close"].astype(float)
    up = h.diff()
    dn = -l.diff()
    plus_dm = pd.Series(
        np.where((up > dn) & (up > 0.0), up, 0.0),
        index=frame.index,
    )
    minus_dm = pd.Series(
        np.where((dn > up) & (dn > 0.0), dn, 0.0),
        index=frame.index,
    )
    pc = c.shift(1)
    tr = pd.concat(
        [(h - l).abs(), (h - pc).abs(), (l - pc).abs()],
        axis=1,
    ).max(axis=1)
    atr = tr.ewm(alpha=1.0 / float(n), adjust=False).mean()
    plus = (
        100.0
        * plus_dm.ewm(alpha=1.0 / float(n), adjust=False).mean()
        / atr.replace(0.0, np.nan)
    )
    minus = (
        100.0
        * minus_dm.ewm(alpha=1.0 / float(n), adjust=False).mean()
        / atr.replace(0.0, np.nan)
    )
    dx = 100.0 * (plus - minus).abs() / (plus + minus).replace(0.0, np.nan)
    return dx.ewm(alpha=1.0 / float(n), adjust=False).mean()


def _continuous_daily_state(frame: pd.DataFrame, params: dict) -> pd.Series:
    """Replay a supported continuous daily entry/exit state without sizing."""
    family = str(params["source_family"])
    daily = _utc_daily_ohlc(frame)
    close = daily["Close"].astype(float)

    if family == "btc_rsi_adx":
        sma = close.rolling(int(params.get("sma_window", 50))).mean()
        ema = close.ewm(
            span=int(params.get("ema_window", 7)),
            adjust=False,
        ).mean()
        rsi = _rsi_now_daily(close, int(params.get("rsi_window", 2)))
        adx = _adx_now_daily(daily, int(params.get("adx_window", 2)))
        entry = (close > sma) & (close > ema) & (rsi > adx)
        exit_ = rsi < adx
    elif family in {"sentinel63", "sentinel65"}:
        window = int(
            params.get(
                "signal_window",
                63 if family == "sentinel63" else 65,
            )
        )
        ema = close.ewm(span=window, adjust=False).mean()
        sd = close.rolling(window).std(ddof=0)
        z = (close - ema) / sd.replace(0.0, np.nan)
        entry = z > float(params.get("entry_z", 0.5))
        exit_ = z < float(params.get("exit_z", -0.5))
    elif family in {"donchian_20_10", "donchian_sma50"}:
        entry_lb = int(params.get("entry_lookback", 20))
        exit_lb = int(params.get("exit_lookback", 10))
        hh = daily["High"].rolling(entry_lb).max().shift(1)
        ll = daily["Low"].rolling(exit_lb).min().shift(1)
        entry = close > hh
        exit_ = close < ll
        sma_window = params.get("sma_window")
        if sma_window is not None:
            sma = close.rolling(int(sma_window)).mean().shift(1)
            entry = entry & (close > sma)
            exit_ = exit_ | (close < sma)
    elif family == "swing_terminal_pullback_proxy":
        fast = int(params.get("ema_fast", 20))
        slow = int(params.get("ema_slow", 50))
        atr_n = int(params.get("atr_window", 20))
        adx_n = int(params.get("adx_window", 14))
        efast = close.ewm(span=fast, adjust=False).mean().shift(1)
        eslow = close.ewm(span=slow, adjust=False).mean().shift(1)
        atr = _atr_shifted_daily(daily, atr_n)
        adx = _adx_now_daily(daily, adx_n).shift(1)
        trend = (efast > eslow) & (close > eslow)
        pullback = (
            (close - efast).abs()
            <= float(params.get("pullback_atr_mult", 0.40)) * atr
        )
        entry = (
            trend
            & pullback
            & (adx > float(params.get("adx_min", 20.0)))
        )
        exit_ = close < eslow
    else:
        raise ValueError(
            f"unsupported continuous daily signal family: {family}"
        )

    # Preserve the source strategy's decision warm-up and realized-volatility
    # validity gate. Source sizing is replaced by V4, but the source strategy
    # does not evaluate entries or exits until both conditions are satisfied.
    has_source_gate = (
        "source_min_bars" in params or "source_vol_lookback" in params
    )
    if has_source_gate:
        if "source_min_bars" not in params or "source_vol_lookback" not in params:
            raise ValueError(
                "authoritative continuous transfer requires both source_min_bars "
                "and source_vol_lookback"
            )
        source_min_bars = int(params["source_min_bars"])
        source_vol_lookback = int(params["source_vol_lookback"])
        log_ret = np.log(close / close.shift(1))
        source_rv = (
            log_ret.rolling(source_vol_lookback)
            .std(ddof=0)
            .shift(1)
            * np.sqrt(365.0)
        )
        bar_count = pd.Series(
            np.arange(1, len(daily) + 1, dtype=int),
            index=daily.index,
        )
        source_decision_ok = (
            bar_count.ge(source_min_bars)
            & source_rv.notna()
            & source_rv.gt(0.0)
        )
        entry = entry & source_decision_ok
        exit_ = exit_ & source_decision_ok

    state = []
    long = False
    for ent, ex in zip(entry.fillna(False), exit_.fillna(False)):
        if not long and bool(ent):
            long = True
        elif long and bool(ex):
            long = False
        state.append(1.0 if long else 0.0)
    return pd.Series(state, index=daily.index, dtype=float)


def _phase2_daily_state(frame: pd.DataFrame, params: dict) -> pd.Series:
    """Replay the exact signed Phase-2 daily signal state without source sizing."""
    family = str(params["source_family"])
    daily = _utc_daily_ohlc(frame)
    close = daily["Close"].astype(float)

    if family != "bollinger_breakout_20_2":
        raise ValueError(f"unsupported Phase-2 daily signal family: {family}")

    mid = close.rolling(20, min_periods=20).mean()
    # Phase-2 uses pandas rolling.std() default ddof=1.
    sd = close.rolling(20, min_periods=20).std()
    upper = mid + 2.0 * sd
    lower = mid - 2.0 * sd
    long_entry = (close > upper) & (close.shift(1) <= upper.shift(1))
    long_exit = close <= mid
    short_entry = (close < lower) & (close.shift(1) >= lower.shift(1))
    short_exit = close >= mid

    raw = []
    cur = 0.0
    for le, lx, se, sx in zip(
        long_entry.fillna(False),
        long_exit.fillna(False),
        short_entry.fillna(False),
        short_exit.fillna(False),
    ):
        if cur == 1.0 and bool(lx):
            cur = 0.0
        elif cur == -1.0 and bool(sx):
            cur = 0.0
        if cur == 0.0:
            if bool(le) and not bool(se):
                cur = 1.0
            elif bool(se) and not bool(le):
                cur = -1.0
        raw.append(cur)
    source_state = pd.Series(raw, index=daily.index, dtype=float)

    source_min_bars = int(params.get("source_min_bars", 40))
    source_vol_lookback = int(params.get("source_vol_lookback", 30))
    log_ret = np.log(close / close.shift(1))
    source_rv = (
        log_ret.rolling(source_vol_lookback)
        .std(ddof=0)
        .shift(1)
        * np.sqrt(365.0)
    )
    bar_count = pd.Series(
        np.arange(1, len(daily) + 1, dtype=int),
        index=daily.index,
    )
    decision_ok = (
        bar_count.ge(source_min_bars)
        & source_rv.notna()
        & source_rv.gt(0.0)
    )

    # The source Strategy returns immediately when the decision gate is invalid,
    # preserving any already-open position rather than forcing it flat.
    actual = []
    current = 0.0
    for desired, ok in zip(source_state, decision_ok):
        if bool(ok):
            current = float(desired)
        actual.append(current)
    return pd.Series(actual, index=daily.index, dtype=float)


def hourly_phase2_daily_signal_strategy(params, all_symbols):
    """Map a signed Phase-2 completed-day signal into causal hourly prop execution."""
    target = str(params["source_target"])
    if target not in all_symbols:
        raise ValueError(f"Phase-2 prop target unavailable: {target}")

    def strategy(data, features=None):
        index = next(iter(data.values())).index
        columns = sorted(data)
        daily_state = _phase2_daily_state(data[target], params)

        utc_dates = pd.Series(index.floor("1D"), index=index)
        next_utc_dates = utc_dates.shift(-1)
        utc_day_complete = (
            next_utc_dates.notna()
            & (next_utc_dates != utc_dates)
        )
        state_map = daily_state.to_dict()
        desired_at_close = utc_dates.map(state_map).fillna(0.0)

        local_dates = pd.Series(index.tz_convert(PRAGUE).date, index=index)
        next_local_dates = local_dates.shift(-1)
        reset = next_local_dates.notna() & (next_local_dates != local_dates)

        out = pd.DataFrame(np.nan, index=index, columns=columns)
        out.loc[reset] = 0.0
        update = utc_day_complete & ~reset
        out.loc[update] = 0.0
        out.loc[update, target] = desired_at_close.loc[update].astype(float)
        out = out.ffill().fillna(0.0)
        if len(out):
            out.iloc[-1] = 0.0
        return out

    return strategy


def _donchian_daily_decisions(frame: pd.DataFrame, params: dict):
    """Return completed-day Donchian decisions and the source ATR stop basis."""
    family = str(params["source_family"])
    if family not in {"donchian_20_10", "donchian_sma50"}:
        raise ValueError(f"stop-aware Donchian adapter cannot handle {family}")

    daily = _utc_daily_ohlc(frame)
    close = daily["Close"].astype(float)
    entry_lb = int(params["entry_lookback"])
    exit_lb = int(params["exit_lookback"])
    atr_window = int(params["atr_window"])

    hh = daily["High"].rolling(entry_lb).max().shift(1)
    ll = daily["Low"].rolling(exit_lb).min().shift(1)
    atr = _atr_shifted_daily(daily, atr_window)
    entry = close > hh
    exit_ = close < ll

    sma_window = params.get("sma_window")
    if sma_window is not None:
        sma = close.rolling(int(sma_window)).mean().shift(1)
        entry = entry & (close > sma)
        exit_ = exit_ | (close < sma)

    if "source_min_bars" not in params or "source_vol_lookback" not in params:
        raise ValueError(
            "stop-aware Donchian transfer requires source warmup and vol gate"
        )
    source_min_bars = int(params["source_min_bars"])
    source_vol_lookback = int(params["source_vol_lookback"])
    log_ret = np.log(close / close.shift(1))
    source_rv = (
        log_ret.rolling(source_vol_lookback)
        .std(ddof=0)
        .shift(1)
        * np.sqrt(365.0)
    )
    bar_count = pd.Series(
        np.arange(1, len(daily) + 1, dtype=int),
        index=daily.index,
    )
    decision_ok = (
        bar_count.ge(source_min_bars)
        & source_rv.notna()
        & source_rv.gt(0.0)
        & atr.notna()
        & atr.gt(0.0)
    )
    return (
        daily,
        (entry & decision_ok).fillna(False),
        (exit_ & decision_ok).fillna(False),
        atr,
    )


def _atr_channel_daily_decisions(frame: pd.DataFrame, params: dict):
    """Return completed-day ATR-channel decisions and source stop basis."""
    daily = _utc_daily_ohlc(frame)
    close = daily["Close"].astype(float)
    ema_window = int(params["ema_window"])
    atr_window = int(params["atr_window"])
    entry_atr_mult = float(params["entry_atr_mult"])

    ema = close.ewm(span=ema_window, adjust=False).mean().shift(1)
    atr = _atr_shifted_daily(daily, atr_window)
    entry = close > (ema + entry_atr_mult * atr)
    exit_ = close < ema

    if "source_min_bars" not in params or "source_vol_lookback" not in params:
        raise ValueError(
            "stop-aware ATR-channel transfer requires source warmup and vol gate"
        )
    source_min_bars = int(params["source_min_bars"])
    source_vol_lookback = int(params["source_vol_lookback"])
    log_ret = np.log(close / close.shift(1))
    source_rv = (
        log_ret.rolling(source_vol_lookback)
        .std(ddof=0)
        .shift(1)
        * np.sqrt(365.0)
    )
    bar_count = pd.Series(
        np.arange(1, len(daily) + 1, dtype=int),
        index=daily.index,
    )
    decision_ok = (
        bar_count.ge(source_min_bars)
        & source_rv.notna()
        & source_rv.gt(0.0)
        & ema.notna()
        & atr.notna()
        & atr.gt(0.0)
    )
    return (
        daily,
        (entry & decision_ok).fillna(False),
        (exit_ & decision_ok).fillna(False),
        atr,
    )


def _hourly_source_stop_state(
    frame: pd.DataFrame,
    params: dict,
) -> tuple[pd.Series, pd.Series]:
    """Replay supported source entry/exit plus standing ATR stop on hourly bars.

    The source decision is formed from the completed UTC daily bar and can
    execute no earlier than the following hourly open. A stop already placed
    on an open position may trigger intrabar from the hourly low. Prague reset
    still forces the V4 prop account flat.
    """
    family = str(params["source_family"])
    if family in {"donchian_20_10", "donchian_sma50"}:
        daily, entry, exit_, atr = _donchian_daily_decisions(frame, params)
    elif family == "atr_channel_trend":
        daily, entry, exit_, atr = _atr_channel_daily_decisions(frame, params)
    else:
        raise ValueError(f"unsupported stop-aware source family: {family}")
    index = frame.index
    utc_dates = pd.Series(index.floor("1D"), index=index)
    next_utc_dates = utc_dates.shift(-1)
    utc_day_complete = next_utc_dates.notna() & (next_utc_dates != utc_dates)

    local_dates = pd.Series(index.tz_convert(PRAGUE).date, index=index)
    next_local_dates = local_dates.shift(-1)
    reset = next_local_dates.notna() & (next_local_dates != local_dates)

    entry_map = entry.to_dict()
    exit_map = exit_.to_dict()
    close_map = daily["Close"].astype(float).to_dict()
    atr_map = atr.astype(float).to_dict()
    stop_mult = float(params["stop_mult"])

    lows = frame["Low"].astype(float)
    state_values = []
    stop_values = []
    long = False
    stop = float("nan")

    for i, ts in enumerate(index):
        # The standing stop was known before this bar opened, so using this
        # bar's low to determine whether the stop order filled is causal.
        if long and np.isfinite(stop) and float(lows.iloc[i]) <= stop:
            long = False
            stop = float("nan")

        if bool(reset.iloc[i]):
            long = False
            stop = float("nan")
        elif bool(utc_day_complete.iloc[i]):
            day = utc_dates.iloc[i]
            if long and bool(exit_map.get(day, False)):
                long = False
                stop = float("nan")
            if (not long) and bool(entry_map.get(day, False)):
                px = float(close_map.get(day, float("nan")))
                av = float(atr_map.get(day, float("nan")))
                if np.isfinite(px) and np.isfinite(av) and av > 0.0:
                    long = True
                    stop = px - stop_mult * av

        state_values.append(1.0 if long else 0.0)
        stop_values.append(stop if long and np.isfinite(stop) else np.nan)

    state = pd.Series(state_values, index=index, dtype=float)
    stops = pd.Series(stop_values, index=index, dtype=float)
    if len(state):
        state.iloc[-1] = 0.0
        stops.iloc[-1] = np.nan
    return state, stops


def hourly_continuous_daily_signal_strategy(params, all_symbols):
    """Transfer a daily continuous signal into the hourly prop execution model.

    Signal calculations use completed UTC daily bars, matching Binance daily
    research convention. A target update is emitted only after the UTC day's
    final hourly bar, so MultiAssetBacktester executes it no earlier than the
    following bar open. Prague-midnight resets override the target to zero and
    it remains flat until the next completed UTC-day signal update.

    Source position sizing is intentionally not copied: V4's volatility target
    and prop exposure optimizer own sizing, daily-loss rails, and payout risk.
    Supported transfers preserve source signal formulas, warm-up, realized-vol
    decision validity, and exits; unsupported stop/bracket/short mechanics fail
    closed before they reach this adapter.
    """
    target = str(params["source_target"])
    if target not in all_symbols:
        raise ValueError(f"continuous prop target unavailable: {target}")

    def strategy(data, features=None):
        index = next(iter(data.values())).index
        columns = sorted(data)

        if bool(params.get("source_stop_required")):
            state, _ = _hourly_source_stop_state(data[target], params)
            out = pd.DataFrame(0.0, index=index, columns=columns)
            out.loc[:, target] = state.reindex(index).fillna(0.0)
            return out

        daily_state = _continuous_daily_state(data[target], params)
        utc_dates = pd.Series(index.floor("1D"), index=index)
        next_utc_dates = utc_dates.shift(-1)
        utc_day_complete = (
            next_utc_dates.notna()
            & (next_utc_dates != utc_dates)
        )
        state_map = daily_state.to_dict()
        desired_at_close = utc_dates.map(state_map).fillna(0.0)

        local_dates = pd.Series(index.tz_convert(PRAGUE).date, index=index)
        next_local_dates = local_dates.shift(-1)
        reset = next_local_dates.notna() & (next_local_dates != local_dates)

        out = pd.DataFrame(np.nan, index=index, columns=columns)
        # Reset first. If a UTC day also completes at the same bar, reset wins;
        # the candidate waits until the next completed daily signal update.
        out.loc[reset] = 0.0
        update = utc_day_complete & ~reset
        out.loc[update] = 0.0
        out.loc[update, target] = desired_at_close.loc[update].astype(float)
        out = out.ffill().fillna(0.0)
        if len(out):
            out.iloc[-1] = 0.0
        return out

    return strategy


def intraday_bar_adverse(
    data: dict[str, pd.DataFrame],
    weights: pd.DataFrame,
    costs: pd.Series,
    long_stop_prices: pd.DataFrame | None = None,
) -> pd.Series:
    idx = weights.index
    out = pd.Series(0.0, index=idx)
    for symbol in weights.columns:
        frame = data[symbol].reindex(idx)
        w = weights[symbol].astype(float)
        open_ = frame["Open"]
        low = frame["Low"]
        high = frame["High"]
        long_bad = low / open_ - 1.0
        if long_stop_prices is not None and symbol in long_stop_prices:
            stop = pd.to_numeric(
                long_stop_prices[symbol].reindex(idx),
                errors="coerce",
            )
            active_stop = w.gt(0.0) & stop.notna() & stop.gt(0.0)
            hit = active_stop & low.le(stop)
            if hit.any():
                fill = pd.Series(
                    np.where(open_.le(stop), open_, stop),
                    index=idx,
                    dtype=float,
                )
                stop_bad = fill / open_ - 1.0
                long_bad = long_bad.where(~hit, stop_bad)
        short_bad = high / open_ - 1.0
        out += pd.Series(
            np.where(w >= 0.0, w * long_bad, w * short_bad),
            index=idx,
        ).fillna(0.0)
    out -= costs.reindex(idx).fillna(0.0)
    return out


def aggregate_prague_days(
    bar_returns: pd.Series,
    bar_adverse: pd.Series,
    weights: pd.DataFrame,
) -> tuple[pd.Series, pd.Series, pd.Series]:
    idx = bar_returns.index
    local_date = pd.Index(idx.tz_convert(PRAGUE).date, name="PragueDate")
    groups = pd.Series(np.arange(len(idx)), index=idx).groupby(local_date)

    daily_return = {}
    daily_adverse = {}
    opened_day = {}

    opened_bar = active_day_proxy(weights).reindex(idx).fillna(False)

    for day, positions in groups:
        pos = positions.to_numpy(dtype=int)
        eq = 1.0
        worst = 1.0
        for i in pos:
            # Worst intrabar equity occurs before the bar's marked close.
            worst = min(worst, eq * (1.0 + float(bar_adverse.iloc[i])))
            eq *= 1.0 + float(bar_returns.iloc[i])
        daily_return[day] = eq - 1.0
        daily_adverse[day] = worst - 1.0
        opened_day[day] = bool(opened_bar.iloc[pos].any())

    index = pd.to_datetime(list(daily_return.keys()))
    return (
        pd.Series(list(daily_return.values()), index=index, name="return"),
        pd.Series(list(daily_adverse.values()), index=index, name="adverse"),
        pd.Series(list(opened_day.values()), index=index, name="opened").astype(bool),
    )



def aggregate_prague_days_scaled(
    bar_returns: pd.Series,
    bar_adverse: pd.Series,
    weights: pd.DataFrame,
    scales=PROP_SCALES,
    *,
    day_profit_cap: float | None = None,
    day_loss_cap: float | None = None,
) -> tuple[
    dict[float, pd.Series],
    dict[float, pd.Series],
    pd.Series,
]:
    """Apply prop scale before compounding, with optional causal daily brake.

    When a scaled account reaches a configured Prague-day profit or loss
    threshold at an hourly close, exposure is flattened for subsequent hours
    of that Prague day. The triggering hour and its adverse excursion remain
    fully counted. An additional stressed one-way close cost is charged at the
    trigger. This is development-only hourly execution realism, not tick-level
    proof of FTMO rule compliance.
    """
    idx = bar_returns.index
    local_date = pd.Index(idx.tz_convert(PRAGUE).date, name="PragueDate")
    groups = pd.Series(np.arange(len(idx)), index=idx).groupby(local_date)
    opened_bar = active_day_proxy(weights).reindex(idx).fillna(False)
    scale_arr = np.asarray(tuple(float(x) for x in scales), dtype=float)
    raw_r = bar_returns.to_numpy(dtype=float)
    raw_a = bar_adverse.to_numpy(dtype=float)
    aligned_weights = weights.reindex(idx).fillna(0.0)
    weight_matrix = aligned_weights.to_numpy(dtype=float)
    gross = aligned_weights.abs().sum(axis=1).to_numpy(dtype=float)
    raw_turnover = np.zeros(len(idx), dtype=float)
    if len(idx):
        raw_turnover[0] = float(np.abs(weight_matrix[0]).sum())
    if len(idx) > 1:
        raw_turnover[1:] = np.abs(
            weight_matrix[1:] - weight_matrix[:-1]
        ).sum(axis=1)

    profit_cap = None if day_profit_cap is None else float(day_profit_cap)
    loss_cap = None if day_loss_cap is None else float(day_loss_cap)
    if profit_cap is not None and profit_cap <= 0.0:
        raise ValueError("day_profit_cap must be positive")
    if loss_cap is not None and loss_cap <= 0.0:
        raise ValueError("day_loss_cap must be positive")
    brake_enabled = profit_cap is not None or loss_cap is not None

    # Current stressed research transaction-cost assumption for an emergency
    # flatten. Base bar returns already include ordinary strategy transaction
    # costs; future base costs are zeroed once the day is stopped.
    emergency_close_fraction = (
        (FTMO_CRYPTO_COMMISSION_BPS + RESEARCH_SLIPPAGE_BPS)
        / 10_000.0
        * PROP_COST_STRESS_MULTIPLIER
    )

    days = []
    return_rows = []
    adverse_rows = []
    opened_values = []
    # If a brake flattened a scaled account on the prior Prague day, the raw
    # backtest may still assume that position was carried. Track that mismatch
    # so the next real re-entry pays any one-way turnover missing from the raw
    # strategy's transaction-cost series.
    needs_reopen = np.zeros(len(scale_arr), dtype=bool)
    for day, positions in groups:
        pos = positions.to_numpy(dtype=int)
        if not brake_enabled:
            rr = raw_r[pos]
            aa = raw_a[pos]
            factors = 1.0 + scale_arr[:, None] * rr[None, :]
            eq_path = np.cumprod(factors, axis=1)
            before = np.concatenate(
                [np.ones((len(scale_arr), 1)), eq_path[:, :-1]],
                axis=1,
            )
            adverse_path = before * (
                1.0 + scale_arr[:, None] * aa[None, :]
            )
            ending = (
                eq_path[:, -1] if len(pos) else np.ones(len(scale_arr))
            )
            worst = np.minimum(1.0, adverse_path.min(axis=1))
        else:
            ending = np.ones(len(scale_arr), dtype=float)
            worst = np.ones(len(scale_arr), dtype=float)
            stopped = np.zeros(len(scale_arr), dtype=bool)
            for i in pos:
                active = ~stopped
                if not np.any(active):
                    break

                # Reconcile an emergency-flat account with the raw strategy at
                # the first point where the raw path carries exposure again.
                # Raw returns already include raw strategy turnover. We charge
                # only the additional one-way turnover required because the
                # brake forced the real scaled path to zero previously.
                reopen_mask = active & needs_reopen
                if np.any(reopen_mask):
                    if float(gross[i]) <= 1e-15:
                        needs_reopen[reopen_mask] = False
                    else:
                        missing_turnover = max(
                            float(gross[i]) - float(raw_turnover[i]),
                            0.0,
                        )
                        if missing_turnover > 0.0:
                            reopen_frac = (
                                scale_arr[reopen_mask]
                                * missing_turnover
                                * emergency_close_fraction
                            )
                            ending[reopen_mask] *= 1.0 - reopen_frac
                            worst[reopen_mask] = np.minimum(
                                worst[reopen_mask],
                                ending[reopen_mask],
                            )
                        needs_reopen[reopen_mask] = False

                adverse_equity = ending * (
                    1.0 + scale_arr * float(raw_a[i])
                )
                worst[active] = np.minimum(
                    worst[active], adverse_equity[active]
                )
                next_equity = ending * (
                    1.0 + scale_arr * float(raw_r[i])
                )
                ending[active] = next_equity[active]
                pnl = ending - 1.0
                hit = active & (
                    (
                        False
                        if profit_cap is None
                        else pnl >= profit_cap
                    )
                    | (
                        False
                        if loss_cap is None
                        else pnl <= -loss_cap
                    )
                )
                if np.any(hit):
                    close_frac = (
                        scale_arr[hit]
                        * float(gross[i])
                        * emergency_close_fraction
                    )
                    ending[hit] *= 1.0 - close_frac
                    worst[hit] = np.minimum(worst[hit], ending[hit])
                    stopped[hit] = True
            needs_reopen = stopped.copy()
            worst = np.minimum(1.0, worst)

        days.append(day)
        return_rows.append(ending - 1.0)
        adverse_rows.append(worst - 1.0)
        opened_values.append(bool(opened_bar.iloc[pos].any()))

    day_index = pd.to_datetime(days)
    ret_matrix = np.vstack(return_rows).T
    adv_matrix = np.vstack(adverse_rows).T
    returns = {
        float(scale): pd.Series(
            ret_matrix[i],
            index=day_index,
            name="return",
        )
        for i, scale in enumerate(scale_arr)
    }
    adverse = {
        float(scale): pd.Series(
            adv_matrix[i],
            index=day_index,
            name="adverse",
        )
        for i, scale in enumerate(scale_arr)
    }
    opened = pd.Series(
        opened_values,
        index=day_index,
        name="opened",
    ).astype(bool)
    return returns, adverse, opened


def _resolve_prop_symbols(data, universe: str) -> tuple[str, ...]:
    """Resolve a compact robustness universe without changing data alignment."""
    label = str(universe or "all_available")
    available = set(data)
    if label == "all_available":
        wanted = available
    elif label == "no_bnb":
        wanted = {"BTCUSDT", "ETHUSDT", "LTCUSDT"}
    elif label == "btc_eth":
        wanted = {"BTCUSDT", "ETHUSDT"}
    else:
        raise ValueError(f"unsupported prop universe: {label}")
    symbols = tuple(sorted(available.intersection(wanted)))
    if not {"BTCUSDT", "ETHUSDT"}.issubset(symbols):
        raise RuntimeError(
            f"prop universe {label} must retain BTCUSDT and ETHUSDT"
        )
    return symbols

def evaluate_strategy(data, params):
    """Build one causal hourly strategy path, reusable across prop programs."""
    all_symbols = tuple(sorted(data))
    family = str(params.get("family", "cross_sectional_long"))
    if family in {"continuous_daily_signal", "phase2_daily_signal"}:
        symbols = (str(params["source_target"]),)
        if symbols[0] not in data:
            raise RuntimeError(
                f"{family} transfer target unavailable: {symbols[0]}"
            )
    else:
        symbols = _resolve_prop_symbols(
            data,
            str(params.get("universe", "all_available")),
        )
    costs = {
        s: AssetCost(
            commission_bps=FTMO_CRYPTO_COMMISSION_BPS,
            slippage_bps=RESEARCH_SLIPPAGE_BPS,
        )
        for s in all_symbols
    }
    if family == "cross_sectional_long":
        net_min, net_max = 0.0, 1.0
        base_strategy = hourly_rotation_strategy(params, symbols)
    elif family == "tsmom_long_short":
        net_min, net_max = -1.0, 1.0
        base_strategy = hourly_tsmom_strategy(params, symbols)
    elif family == "continuous_daily_signal":
        net_min, net_max = 0.0, 1.0
        base_strategy = hourly_continuous_daily_signal_strategy(
            params,
            all_symbols,
        )
    elif family == "phase2_daily_signal":
        net_min, net_max = -1.0, 1.0
        base_strategy = hourly_phase2_daily_signal_strategy(
            params,
            all_symbols,
        )
    else:
        raise ValueError(f"unknown intraday prop family: {family}")

    engine = MultiAssetBacktester(
        data,
        costs=costs,
        limits=PortfolioLimits(
            gross_leverage=1.0,
            net_min=net_min,
            net_max=net_max,
            per_asset_abs_weight=1.0,
        ),
        periods_per_year=365.0 * 24.0,
    )
    strategy = volatility_target_overlay(
        base_strategy,
        target_vol=float(params["vol_target"]),
        periods_per_year=365.0 * 24.0,
        lookback=int(params["vol_lookback"]),
        max_gross=1.0,
        max_scale=1.0,
    )
    long_stop_prices = None
    if (
        family == "continuous_daily_signal"
        and bool(params.get("source_stop_required"))
    ):
        _, stop_series = _hourly_source_stop_state(
            data[str(params["source_target"])],
            params,
        )
        long_stop_prices = pd.DataFrame(
            np.nan,
            index=stop_series.index,
            columns=all_symbols,
        )
        long_stop_prices.loc[:, str(params["source_target"])] = stop_series

    result = engine.run(
        strategy,
        cost_multiplier=PROP_COST_STRESS_MULTIPLIER,
        long_stop_prices=long_stop_prices,
    )
    execution_stop_prices = (
        None
        if long_stop_prices is None
        else long_stop_prices.shift(1).reindex(result.returns.index)
    )
    bar_adverse = intraday_bar_adverse(
        data,
        result.execution_weights,
        result.costs,
        long_stop_prices=execution_stop_prices,
    ).reindex(result.returns.index)
    aligned_weights = result.execution_weights.reindex(result.returns.index)
    daily_ret, daily_adv, opened = aggregate_prague_days(
        result.returns,
        bar_adverse,
        aligned_weights,
    )
    scaled_ret, scaled_adv, scaled_opened = aggregate_prague_days_scaled(
        result.returns,
        bar_adverse,
        aligned_weights,
        PROP_SCALES,
        day_profit_cap=params.get("day_profit_cap"),
        day_loss_cap=params.get("day_loss_cap"),
    )
    if not opened.equals(scaled_opened):
        raise RuntimeError("scaled intraday aggregation changed trading-day flags")
    if params.get("day_profit_cap") is not None or params.get("day_loss_cap") is not None:
        daily_ret = scaled_ret[1.0]
        daily_adv = scaled_adv[1.0]
    return (
        result,
        daily_ret,
        daily_adv,
        opened,
        scaled_ret,
        scaled_adv,
    )


def evaluate_family(data, params, program, *, paths, seed):
    (
        result,
        daily_ret,
        daily_adv,
        opened,
        scaled_ret,
        scaled_adv,
    ) = evaluate_strategy(data, params)
    prop = optimize_prop_exposure(
        daily_ret.to_numpy(dtype=float),
        daily_adv.to_numpy(dtype=float),
        opened.to_numpy(dtype=bool),
        program,
        exposure_scales=PROP_SCALES,
        paths=paths,
        block=10,
        seed=seed,
        input_precision=(
            "hourly_intraday_equity_proxy_binance_spot_to_ftmo_crypto_cfd_"
            "prague_midnight_reset_exact_scale_compounding_daily_flat_policy"
        ),
        prescaled_returns_by_scale=scaled_ret,
        prescaled_adverse_by_scale=scaled_adv,
    )
    return result, daily_ret, daily_adv, prop


def _frontier_rank(view_name, candidate):
    if candidate is None:
        return (-1e99,)
    if view_name == "max_repeat_payout_efficiency":
        return (
            float(candidate.repeat_payout_efficiency_score),
            float(candidate.funded.survival_probability),
            float(candidate.combined_evaluation_pass_probability),
            float(candidate.payout_efficiency_score),
        )
    if view_name == "max_repeat_expected_reward":
        return (
            float(candidate.repeat_expected_reward_pct),
            float(candidate.funded.survival_probability),
            float(candidate.combined_evaluation_pass_probability),
            float(candidate.repeat_payout_efficiency_score),
        )
    if view_name == "max_evaluation_pass":
        days = (
            1e99
            if candidate.expected_evaluation_days_if_passed is None
            else float(candidate.expected_evaluation_days_if_passed)
        )
        return (
            float(candidate.combined_evaluation_pass_probability),
            -days,
            float(candidate.payout_efficiency_score),
            float(candidate.funded.survival_probability),
        )
    if view_name == "safest_funded":
        return (
            float(candidate.funded.survival_probability),
            -float(candidate.funded.daily_loss_breach_probability),
            -float(candidate.funded.max_loss_breach_probability),
            float(candidate.funded.expected_reward_pct),
        )
    return (
        float(candidate.payout_efficiency_score),
        float(candidate.combined_evaluation_pass_probability),
        float(candidate.funded.survival_probability),
    )



def _frontier_structural_mutations(
    seed_params: list[dict],
) -> list[dict]:
    """Small evidence-led mutation set around coarse frontier leaders."""
    seen = set()
    out = []
    structures = (
        ("all", 4),
        ("all", 8),
        ("avoid_funding_hours", 1),
        ("avoid_funding_hours", 4),
        ("europe_us", 1),
        ("europe_us", 4),
        ("europe_us", 8),
    )
    for base in seed_params:
        base_key = {
            key: value
            for key, value in base.items()
            if key not in {"execution_session", "rebalance_hours"}
        }
        for session, rebalance in structures:
            candidate = dict(base_key)
            candidate["execution_session"] = session
            candidate["rebalance_hours"] = int(rebalance)
            key = tuple(sorted(candidate.items()))
            if key in seen:
                continue
            seen.add(key)
            out.append(candidate)
    return out


def _frontier_universe_mutations(
    leaders_by_program: dict,
    view_names: tuple[str, ...],
) -> list[dict]:
    """Test whether frontier quality depends on the BNB proxy or broad basket."""
    seen = set()
    out = []
    for program_leaders in leaders_by_program.values():
        for view_name in view_names:
            leader = program_leaders.get(view_name)
            if leader is None:
                continue
            base = dict(leader["params"])
            if str(base.get("family", "cross_sectional_long")) != "cross_sectional_long":
                continue
            for universe in ("no_bnb", "btc_eth"):
                candidate = dict(base)
                candidate["universe"] = universe
                key = tuple(sorted(candidate.items()))
                if key in seen:
                    continue
                seen.add(key)
                out.append(candidate)
    return out


def _frontier_day_brake_mutations(
    leaders_by_program: dict,
    view_names: Sequence[str] = (
        "max_payout_efficiency",
        "max_repeat_payout_efficiency",
        "max_repeat_expected_reward",
        "max_evaluation_pass",
        "safest_funded",
        "balanced",
        "conservative",
    ),
) -> list[dict]:
    """Compact risk/payout smoothing mutations around strict frontier leaders."""
    seen = set()
    out = []
    for program_leaders in leaders_by_program.values():
        for view_name in view_names:
            leader = program_leaders.get(view_name)
            if leader is None:
                continue
            base = dict(leader["params"])
            if str(base.get("family", "cross_sectional_long")) != "cross_sectional_long":
                continue
            for profit_cap in (0.010, 0.015):
                for loss_cap in (0.010, 0.015):
                    candidate = dict(base)
                    candidate["day_profit_cap"] = float(profit_cap)
                    candidate["day_loss_cap"] = float(loss_cap)
                    key = tuple(sorted(candidate.items()))
                    if key in seen:
                        continue
                    seen.add(key)
                    out.append(candidate)
    return out

def program_with_analysis_horizon(
    program: PropFirmProgram,
    days: int,
) -> PropFirmProgram:
    """Clone evaluation stages with a research horizon, not a firm deadline."""
    horizon = int(days)
    if horizon < 30:
        raise ValueError("analysis horizon must be >=30 days")
    return replace(
        program,
        challenge=replace(
            program.challenge,
            analysis_horizon_days=horizon,
        ),
        verification=(
            None
            if program.verification is None
            else replace(
                program.verification,
                analysis_horizon_days=horizon,
            )
        ),
    )


def run(data_dir: str | Path, output: str | Path) -> dict:
    data = load_data(Path(data_dir))

    params_grid = [
        {
            "family": "cross_sectional_long",
            "lookback": lb,
            "trend": tr,
            "top_k": k,
            "vol_target": vt,
            "vol_lookback": vl,
        }
        for lb in (72, 168, 336)
        for tr in (168, 336)
        for k in (1, 2)
        for vt in (0.20, 0.30, 0.40, 0.60, 0.80)
        for vl in (72, 168)
    ]

    programs = [FTMO_2STEP, FTMO_1STEP]
    view_names = (
        "max_payout_efficiency",
        "max_repeat_payout_efficiency",
        "max_evaluation_pass",
        "safest_funded",
        "balanced",
        "conservative",
    )
    rows_by_program = {program.id: [] for program in programs}
    leaders = {
        program.id: {name: None for name in view_names}
        for program in programs
    }

    # Strategy construction is independent of prop-program rules. Build it
    # once per parameter set, then evaluate 1-Step and 2-Step on the same
    # daily return/adverse path. The same bootstrap seed is also reused across
    # parameter sets within a program to reduce Monte Carlo ranking noise.
    for i, params in enumerate(params_grid):
        (
            base,
            dret,
            dadv,
            opened,
            scaled_ret,
            scaled_adv,
        ) = evaluate_strategy(data, params)
        for pidx, program in enumerate(programs):
            prop = optimize_prop_exposure(
                dret.to_numpy(dtype=float),
                dadv.to_numpy(dtype=float),
                opened.to_numpy(dtype=bool),
                program,
                exposure_scales=PROP_SCALES,
                paths=400,
                block=10,
                seed=20261000 + pidx * 100000,
                input_precision=(
                    "hourly_intraday_equity_proxy_binance_spot_to_ftmo_crypto_cfd_"
                    "prague_midnight_reset_exact_scale_compounding_daily_flat_policy"
                ),
                prescaled_returns_by_scale=scaled_ret,
                prescaled_adverse_by_scale=scaled_adv,
            )
            sel = prop.selected
            rows_by_program[program.id].append({
                "params": params,
                "search_phase": "broad_base",
                "hourly_base_cagr_pct": base.metrics.cagr_pct,
                "hourly_base_max_dd_pct": base.metrics.max_dd_pct,
                "daily_worst_adverse_pct": float(dadv.min() * 100.0),
                "selected": None if sel is None else sel.to_dict(),
            })

            for view_name in view_names:
                candidate = prop.views.get(view_name)
                if candidate is None:
                    continue
                current = leaders[program.id][view_name]
                if (
                    current is None
                    or _frontier_rank(view_name, candidate)
                    > _frontier_rank(view_name, current["candidate"])
                ):
                    leaders[program.id][view_name] = {
                        "params": dict(params),
                        "candidate": candidate,
                    }

    # Mutate only coarse frontier leaders, not the full parameter grid.
    # This adds session/rebalance structure where the broad search already
    # found promise while avoiding a 9x Cartesian expansion of every trial.
    seed_params = []
    seed_seen = set()
    for program in programs:
        for view_name in view_names:
            leader = leaders[program.id][view_name]
            if leader is None:
                continue
            params = dict(leader["params"])
            key = tuple(sorted(params.items()))
            if key not in seed_seen:
                seed_seen.add(key)
                seed_params.append(params)

    structural_params = _frontier_structural_mutations(seed_params)
    for params in structural_params:
        (
            base,
            dret,
            dadv,
            opened,
            scaled_ret,
            scaled_adv,
        ) = evaluate_strategy(data, params)
        for pidx, program in enumerate(programs):
            prop = optimize_prop_exposure(
                dret.to_numpy(dtype=float),
                dadv.to_numpy(dtype=float),
                opened.to_numpy(dtype=bool),
                program,
                exposure_scales=PROP_SCALES,
                paths=400,
                block=10,
                seed=20261000 + pidx * 100000,
                input_precision=(
                    "hourly_intraday_equity_proxy_binance_spot_to_ftmo_crypto_cfd_"
                    "prague_midnight_reset_exact_scale_compounding_daily_flat_policy"
                ),
                prescaled_returns_by_scale=scaled_ret,
                prescaled_adverse_by_scale=scaled_adv,
            )
            sel = prop.selected
            rows_by_program[program.id].append({
                "params": params,
                "search_phase": "frontier_structural_mutation",
                "hourly_base_cagr_pct": base.metrics.cagr_pct,
                "hourly_base_max_dd_pct": base.metrics.max_dd_pct,
                "daily_worst_adverse_pct": float(dadv.min() * 100.0),
                "selected": None if sel is None else sel.to_dict(),
            })
            for view_name in view_names:
                candidate = prop.views.get(view_name)
                if candidate is None:
                    continue
                current = leaders[program.id][view_name]
                if (
                    current is None
                    or _frontier_rank(view_name, candidate)
                    > _frontier_rank(view_name, current["candidate"])
                ):
                    leaders[program.id][view_name] = {
                        "params": dict(params),
                        "candidate": candidate,
                    }



    # Robustness mutation: re-evaluate current long-only frontier leaders
    # without BNB and on BTC/ETH only. This directly tests whether the edge
    # depends on the historically non-FTMO BNB proxy.
    universe_params = _frontier_universe_mutations(leaders, view_names)
    for params in universe_params:
        (
            base,
            dret,
            dadv,
            opened,
            scaled_ret,
            scaled_adv,
        ) = evaluate_strategy(data, params)
        for pidx, program in enumerate(programs):
            prop = optimize_prop_exposure(
                dret.to_numpy(dtype=float),
                dadv.to_numpy(dtype=float),
                opened.to_numpy(dtype=bool),
                program,
                exposure_scales=PROP_SCALES,
                paths=400,
                block=10,
                seed=20261000 + pidx * 100000,
                input_precision=(
                    "hourly_intraday_equity_proxy_binance_spot_to_ftmo_crypto_cfd_"
                    "prague_midnight_reset_exact_scale_compounding_daily_flat_policy"
                ),
                prescaled_returns_by_scale=scaled_ret,
                prescaled_adverse_by_scale=scaled_adv,
            )
            sel = prop.selected
            rows_by_program[program.id].append({
                "params": params,
                "search_phase": "frontier_universe_mutation",
                "hourly_base_cagr_pct": base.metrics.cagr_pct,
                "hourly_base_max_dd_pct": base.metrics.max_dd_pct,
                "daily_worst_adverse_pct": float(dadv.min() * 100.0),
                "selected": None if sel is None else sel.to_dict(),
            })
            for view_name in view_names:
                candidate = prop.views.get(view_name)
                if candidate is None:
                    continue
                current = leaders[program.id][view_name]
                if (
                    current is None
                    or _frontier_rank(view_name, candidate)
                    > _frontier_rank(view_name, current["candidate"])
                ):
                    leaders[program.id][view_name] = {
                        "params": dict(params),
                        "candidate": candidate,
                    }


    # Causal Prague-day risk/profit smoothing around the strict frontier.
    # This directly targets daily-loss risk and the 1-Step Best Day rule.
    brake_params = _frontier_day_brake_mutations(leaders, view_names)
    for params in brake_params:
        (
            base,
            dret,
            dadv,
            opened,
            scaled_ret,
            scaled_adv,
        ) = evaluate_strategy(data, params)
        for pidx, program in enumerate(programs):
            prop = optimize_prop_exposure(
                dret.to_numpy(dtype=float),
                dadv.to_numpy(dtype=float),
                opened.to_numpy(dtype=bool),
                program,
                exposure_scales=PROP_SCALES,
                paths=400,
                block=10,
                seed=20261000 + pidx * 100000,
                input_precision=(
                    "hourly_intraday_equity_proxy_binance_spot_to_ftmo_crypto_cfd_"
                    "prague_midnight_reset_exact_scale_compounding_daily_flat_"
                    "hourly_close_day_brake_policy"
                ),
                prescaled_returns_by_scale=scaled_ret,
                prescaled_adverse_by_scale=scaled_adv,
            )
            sel = prop.selected
            rows_by_program[program.id].append({
                "params": params,
                "search_phase": "frontier_prague_day_brake",
                "hourly_base_cagr_pct": base.metrics.cagr_pct,
                "hourly_base_max_dd_pct": base.metrics.max_dd_pct,
                "daily_worst_adverse_pct": float(dadv.min() * 100.0),
                "selected": None if sel is None else sel.to_dict(),
            })
            for view_name in view_names:
                candidate = prop.views.get(view_name)
                if candidate is None:
                    continue
                current = leaders[program.id][view_name]
                if (
                    current is None
                    or _frontier_rank(view_name, candidate)
                    > _frontier_rank(view_name, current["candidate"])
                ):
                    leaders[program.id][view_name] = {
                        "params": dict(params),
                        "candidate": candidate,
                    }

    # Independent compact family: volatility-normalized long/short
    # time-series momentum. This is not a mutation of the long-only rotation.
    tsmom_params = [
        {
            "family": "tsmom_long_short",
            "lookback": lb,
            "vol_target": vt,
            "vol_lookback": 72,
            "execution_session": session,
            "rebalance_hours": rebalance,
        }
        for lb in (24, 72, 168)
        for vt in (0.20, 0.30, 0.40)
        for session in ("all", "europe_us")
        for rebalance in (4, 8)
    ]
    for params in tsmom_params:
        (
            base,
            dret,
            dadv,
            opened,
            scaled_ret,
            scaled_adv,
        ) = evaluate_strategy(data, params)
        for pidx, program in enumerate(programs):
            prop = optimize_prop_exposure(
                dret.to_numpy(dtype=float),
                dadv.to_numpy(dtype=float),
                opened.to_numpy(dtype=bool),
                program,
                exposure_scales=PROP_SCALES,
                paths=400,
                block=10,
                seed=20261000 + pidx * 100000,
                input_precision=(
                    "hourly_intraday_equity_proxy_binance_spot_to_ftmo_crypto_cfd_"
                    "prague_midnight_reset_exact_scale_compounding_daily_flat_policy"
                ),
                prescaled_returns_by_scale=scaled_ret,
                prescaled_adverse_by_scale=scaled_adv,
            )
            sel = prop.selected
            rows_by_program[program.id].append({
                "params": params,
                "search_phase": "independent_tsmom_family",
                "hourly_base_cagr_pct": base.metrics.cagr_pct,
                "hourly_base_max_dd_pct": base.metrics.max_dd_pct,
                "daily_worst_adverse_pct": float(dadv.min() * 100.0),
                "selected": None if sel is None else sel.to_dict(),
            })
            for view_name in view_names:
                candidate = prop.views.get(view_name)
                if candidate is None:
                    continue
                current = leaders[program.id][view_name]
                if (
                    current is None
                    or _frontier_rank(view_name, candidate)
                    > _frontier_rank(view_name, current["candidate"])
                ):
                    leaders[program.id][view_name] = {
                        "params": dict(params),
                        "candidate": candidate,
                    }

    # Automatically transfer compatible champions from continuous breadth/depth
    # research into the stricter FTMO simulator. The source signal is preserved;
    # V4 re-searches only prop-specific risk sizing.
    transfer_seeds, continuous_prop_transfer = prop_transfer_candidates(
        data.keys()
    )
    transfer_params = []
    transfer_seen = set()
    for source in transfer_seeds:
        for vt in (0.20, 0.40, 0.60):
            for vl in (72, 168):
                params = dict(source)
                params["vol_target"] = float(vt)
                params["vol_lookback"] = int(vl)
                key = tuple(sorted(params.items()))
                if key in transfer_seen:
                    continue
                transfer_seen.add(key)
                transfer_params.append(params)

    for params in transfer_params:
        (
            base,
            dret,
            dadv,
            opened,
            scaled_ret,
            scaled_adv,
        ) = evaluate_strategy(data, params)
        for pidx, program in enumerate(programs):
            prop = optimize_prop_exposure(
                dret.to_numpy(dtype=float),
                dadv.to_numpy(dtype=float),
                opened.to_numpy(dtype=bool),
                program,
                exposure_scales=PROP_SCALES,
                paths=400,
                block=10,
                seed=20261000 + pidx * 100000,
                input_precision=(
                    "continuous_daily_signal_to_hourly_ftmo_proxy_"
                    "prague_midnight_reset_v4_risk_sizing"
                ),
                prescaled_returns_by_scale=scaled_ret,
                prescaled_adverse_by_scale=scaled_adv,
            )
            sel = prop.selected
            rows_by_program[program.id].append({
                "params": params,
                "search_phase": "continuous_prop_transfer",
                "hourly_base_cagr_pct": base.metrics.cagr_pct,
                "hourly_base_max_dd_pct": base.metrics.max_dd_pct,
                "daily_worst_adverse_pct": float(dadv.min() * 100.0),
                "selected": None if sel is None else sel.to_dict(),
            })
            for view_name in view_names:
                candidate = prop.views.get(view_name)
                if candidate is None:
                    continue
                current = leaders[program.id][view_name]
                if (
                    current is None
                    or _frontier_rank(view_name, candidate)
                    > _frontier_rank(view_name, current["candidate"])
                ):
                    leaders[program.id][view_name] = {
                        "params": dict(params),
                        "candidate": candidate,
                    }

    # Transfer finite Phase-2 prior-work survivors after the broad/continuous
    # families. Only exact persisted promotion artifacts with explicit signed
    # adapters reach this point; V4 re-searches prop-specific risk sizing only.
    phase2_transfer_seeds, phase2_prop_transfer = (
        phase2_prop_transfer_candidates(data.keys())
    )
    phase2_transfer_params = []
    phase2_seen = set()
    for source in phase2_transfer_seeds:
        for vt in (0.20, 0.40, 0.60):
            for vl in (72, 168):
                params = dict(source)
                params["vol_target"] = float(vt)
                params["vol_lookback"] = int(vl)
                key = tuple(sorted(params.items()))
                if key in phase2_seen:
                    continue
                phase2_seen.add(key)
                phase2_transfer_params.append(params)

    for params in phase2_transfer_params:
        (
            base,
            dret,
            dadv,
            opened,
            scaled_ret,
            scaled_adv,
        ) = evaluate_strategy(data, params)
        for pidx, program in enumerate(programs):
            prop = optimize_prop_exposure(
                dret.to_numpy(dtype=float),
                dadv.to_numpy(dtype=float),
                opened.to_numpy(dtype=bool),
                program,
                exposure_scales=PROP_SCALES,
                paths=400,
                block=10,
                seed=20261000 + pidx * 100000,
                input_precision=(
                    "phase2_exact_signed_daily_signal_to_hourly_ftmo_proxy_"
                    "prague_midnight_reset_v4_risk_sizing"
                ),
                prescaled_returns_by_scale=scaled_ret,
                prescaled_adverse_by_scale=scaled_adv,
            )
            sel = prop.selected
            rows_by_program[program.id].append({
                "params": params,
                "search_phase": "phase2_prop_transfer",
                "hourly_base_cagr_pct": base.metrics.cagr_pct,
                "hourly_base_max_dd_pct": base.metrics.max_dd_pct,
                "daily_worst_adverse_pct": float(dadv.min() * 100.0),
                "selected": None if sel is None else sel.to_dict(),
            })
            for view_name in view_names:
                candidate = prop.views.get(view_name)
                if candidate is None:
                    continue
                current = leaders[program.id][view_name]
                if (
                    current is None
                    or _frontier_rank(view_name, candidate)
                    > _frontier_rank(view_name, current["candidate"])
                ):
                    leaders[program.id][view_name] = {
                        "params": dict(params),
                        "candidate": candidate,
                    }

    program_results = {}
    for pidx, program in enumerate(programs):
        rows = rows_by_program[program.id]
        rows.sort(
            key=lambda x: (
                -1e99
                if x["selected"] is None
                else x["selected"]["payout_efficiency_score"],
                -1e99
                if x["selected"] is None
                else x["selected"]["combined_evaluation_pass_probability"],
            ),
            reverse=True,
        )

        refined_cache = {}
        refined_frontiers = {}
        for view_name in view_names:
            leader = leaders[program.id][view_name]
            if leader is None:
                refined_frontiers[view_name] = None
                continue
            params = leader["params"]
            key = tuple(sorted(params.items()))
            if key not in refined_cache:
                (
                    base,
                    d_ret,
                    d_adv,
                    opened,
                    scaled_ret,
                    scaled_adv,
                ) = evaluate_strategy(data, params)
                final_prop = optimize_prop_exposure(
                    d_ret.to_numpy(dtype=float),
                    d_adv.to_numpy(dtype=float),
                    opened.to_numpy(dtype=bool),
                    program,
                    exposure_scales=PROP_SCALES,
                    paths=4000,
                    block=10,
                    seed=20269900 + pidx * 100000,
                    input_precision=(
                        "hourly_intraday_equity_proxy_binance_spot_to_ftmo_crypto_cfd_"
                        "prague_midnight_reset_exact_scale_compounding_daily_flat_policy"
                    ),
                    prescaled_returns_by_scale=scaled_ret,
                    prescaled_adverse_by_scale=scaled_adv,
                )
                refined_cache[key] = {
                    "base": base,
                    "daily_ret": d_ret,
                    "daily_adv": d_adv,
                    "opened": opened,
                    "scaled_ret": scaled_ret,
                    "scaled_adv": scaled_adv,
                    "optimization": final_prop,
                }

            ref = refined_cache[key]
            candidate = ref["optimization"].views.get(view_name)
            refined_frontiers[view_name] = {
                "params": params,
                "days": int(len(ref["daily_ret"])),
                "worst_prague_day_adverse_pct": float(
                    ref["daily_adv"].min() * 100.0
                ),
                "view": (
                    None if candidate is None else candidate.to_dict()
                ),
            }

        # FTMO has no maximum evaluation duration. The 252-day broad
        # search horizon is therefore a computational screen, not a firm
        # timeout. Re-evaluate every refined frontier at longer research
        # horizons so the persisted state is complete and comparable.
        horizon_sensitivity = {}
        for view_name in view_names:
            leader = leaders[program.id][view_name]
            if leader is None:
                horizon_sensitivity[view_name] = None
                continue
            params = leader["params"]
            key = tuple(sorted(params.items()))
            ref = refined_cache[key]
            sensitivity_rows = {}
            for horizon in (252, 365, 504):
                if horizon == 252:
                    sensitivity_prop = ref["optimization"]
                else:
                    sensitivity_program = program_with_analysis_horizon(
                        program,
                        horizon,
                    )
                    sensitivity_prop = optimize_prop_exposure(
                        ref["daily_ret"].to_numpy(dtype=float),
                        ref["daily_adv"].to_numpy(dtype=float),
                        ref["opened"].to_numpy(dtype=bool),
                        sensitivity_program,
                        exposure_scales=PROP_SCALES,
                        paths=2000,
                        block=10,
                        seed=20279900 + pidx * 100000 + horizon,
                        input_precision=(
                            "hourly_intraday_equity_proxy_binance_spot_to_ftmo_crypto_cfd_"
                            "prague_midnight_reset_exact_scale_compounding_daily_flat_policy"
                        ),
                        prescaled_returns_by_scale=ref["scaled_ret"],
                        prescaled_adverse_by_scale=ref["scaled_adv"],
                    )
                candidate = sensitivity_prop.views.get(view_name)
                sensitivity_rows[str(horizon)] = {
                    "analysis_horizon_days": horizon,
                    "firm_time_limit_days": None,
                    "view": (
                        None if candidate is None else candidate.to_dict()
                    ),
                }
            horizon_sensitivity[view_name] = {
                "params": params,
                "horizons": sensitivity_rows,
            }

        max_payout = refined_frontiers["max_payout_efficiency"]
        refined_winner = None
        if max_payout is not None:
            key = tuple(sorted(max_payout["params"].items()))
            ref = refined_cache[key]
            refined_winner = {
                "params": max_payout["params"],
                "days": max_payout["days"],
                "worst_prague_day_adverse_pct": (
                    max_payout["worst_prague_day_adverse_pct"]
                ),
                "optimization": ref["optimization"].to_dict(),
            }

        program_results[program.id] = {
            "program": program.to_dict(),
            "parameter_candidates": len(rows),
            "development_leaderboard": rows,
            "coarse_frontier_leaders": {
                name: (
                    None
                    if leaders[program.id][name] is None
                    else {
                        "params": leaders[program.id][name]["params"],
                        "view": leaders[program.id][name][
                            "candidate"
                        ].to_dict(),
                    }
                )
                for name in view_names
            },
            "refined_frontiers": refined_frontiers,
            "evaluation_horizon_policy": {
                "firm_time_limit_days": None,
                "broad_search_horizon_days": 252,
                "sensitivity_horizons_days": [252, 365, 504],
                "interpretation": (
                    "research horizons only; FTMO evaluation has no maximum "
                    "time limit, so horizon timeouts are not firm-rule failures"
                ),
            },
            "horizon_sensitivity": horizon_sensitivity,
            "refined_winner": refined_winner,
        }

    payload = {
        "protocol": "alpha_generation_v4",
        "track": "prop_firm_intraday",
        "stage": "development_only",
        "research_commit_sha": research_commit_sha(),
        "exposure_scaling_method": (
            "stage exposure applied to each hourly portfolio return/adverse "
            "before Prague-day compounding"
        ),
        "prague_day_brake_execution": {
            "trigger_resolution": "hourly_bar_close_proxy",
            "trigger_bar_adverse_excursion_preserved": True,
            "subsequent_same_prague_day_exposure_flattened": True,
            "emergency_close_cost_stress_multiplier": (
                PROP_COST_STRESS_MULTIPLIER
            ),
            "missing_next_day_reentry_cost_charged": True,
            "limitation": (
                "hourly reconstruction cannot prove tick-level floating-equity "
                "rail enforcement; validate on MT5/FTMO forward execution before "
                "any funded deployment"
            ),
        },
        "repeat_payout_projection": {
            "cycles": 12,
            "reward_cycle_days": int(FTMO_2STEP.first_reward_eligible_days),
            "method": (
                "sum identical first-reward expected value across up to 12 "
                "cycles, discounting cycle k by funded survival_probability^k"
            ),
            "profit_rollover_or_compounding": False,
            "challenge_retries_included": False,
            "challenge_fees_included": False,
            "scaling_plan_upgrades_included": False,
            "interpretation": (
                "conservative repeat-payout research proxy; not a literal "
                "withdrawal-path simulation"
            ),
        },
        "continuous_prop_transfer": continuous_prop_transfer,
        "phase2_prop_transfer": phase2_prop_transfer,
        "search_policy": {
            "broad_base_candidates": len(params_grid),
            "frontier_seed_parameter_sets": len(seed_params),
            "frontier_structural_mutations": len(structural_params),
            "frontier_universe_mutations": len(universe_params),
            "frontier_prague_day_brake_mutations": len(brake_params),
            "independent_tsmom_candidates": len(tsmom_params),
            "continuous_transfer_source_candidates": len(transfer_seeds),
            "continuous_transfer_risk_candidates": len(transfer_params),
            "phase2_transfer_source_candidates": len(phase2_transfer_seeds),
            "phase2_transfer_risk_candidates": len(phase2_transfer_params),
            "candidate_families": [
                "cross_sectional_long",
                "tsmom_long_short",
                "continuous_daily_signal",
                "phase2_daily_signal",
            ],
            "structural_dimensions": {
                "execution_session": [
                    "all",
                    "avoid_funding_hours",
                    "europe_us",
                ],
                "rebalance_hours": [1, 4, 8],
                "universe": [
                    "all_available",
                    "no_bnb",
                    "btc_eth",
                ],
                "day_profit_cap": [0.010, 0.015],
                "day_loss_cap": [0.010, 0.015],
            },
            "policy": (
                "broad long-only alpha/risk search first; mutate only coarse "
                "frontier leaders for session/rebalance structure, asset-"
                "universe robustness, and causal Prague-day risk/profit "
                "smoothing; evaluate time-series momentum as an "
                "independent compact family; replay frozen Phase-2 survivors "
                "only through exact signed adapters and V4 prop risk sizing"
            ),
        },
        "data_end": max(
            frame.index.max().tz_convert("UTC").strftime("%Y-%m-%d")
            for frame in data.values()
        ),
        "hidden_validation_opened": False,
        "final_oos_opened": False,
        "reset_timezone": PRAGUE,
        "policy": "force flat for execution at each Prague midnight reset",
        "venue_hours_assumption": {
            "research_proxy": "Binance spot 1h is continuous except exchange data gaps",
            "ftmo_historical_maintenance_reconstructed": False,
            "policy": (
                "do not invent a fixed historical FTMO crypto maintenance window; "
                "FTMO states weekend crypto hours can vary by platform and maintenance"
            ),
            "deployment_requirement": (
                "verify symbol trading hours, maintenance gaps, spread, and fills on "
                "the intended FTMO platform during forward testing"
            ),
        },
        "market_mapping": {
            symbol: {
                "research_source": (
                    "Binance spot 1h, monthly archive checksums verified"
                ),
                "intended_prop_symbol": {
                    "BTCUSDT": "BTCUSD",
                    "ETHUSDT": "ETHUSD",
                    "BNBUSDT": "BNBUSD",
                    "LTCUSDT": "LTCUSD",
                }[symbol],
                "venue_execution_verified": False,
                "current_ftmo_listing_effective": (
                    "2025-07-28" if symbol == "BNBUSDT" else None
                ),
            }
            for symbol in sorted(data)
        },
        "ftmo_crypto_execution_assumptions": {
            "current_fee_regime_effective": "2025-07-28",
            "commission_per_side_pct": FTMO_CRYPTO_COMMISSION_BPS / 100.0,
            "research_slippage_bps_per_side": RESEARCH_SLIPPAGE_BPS,
            "development_cost_stress_multiplier": PROP_COST_STRESS_MULTIPLIER,
            "weekend_hours_platform_dependent": True,
        },
        "deployment_blockers": [
            "FTMO CFD tick/spread/slippage history still differs from Binance spot",
            "current FTMO crypto fee/spread regime began after the sealed development sample",
            "BNBUSD was not an FTMO instrument during the sealed development sample",
            "weekend crypto trading hours can vary by FTMO platform/maintenance window",
            "FTMO-specific swap history not reconstructed",
            "exact platform execution must be forward-tested before funded deployment",
        ],
        "programs": program_results,
    }
    safe = json_safe(payload)
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(safe, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return safe


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="v4_prop_intraday_data")
    ap.add_argument("--output", default="v4_state/prop-intraday-bootstrap.json")
    args = ap.parse_args()
    x = run(args.data_dir, args.output)
    summary = {}
    for key, row in x["programs"].items():
        ref = row["refined_winner"]
        sel = None if ref is None else ref["optimization"]["selected"]
        summary[key] = None if sel is None else {
            "params": ref["params"],
            "challenge_scale": sel["challenge_exposure_scale"],
            "verification_scale": sel["verification_exposure_scale"],
            "funded_scale": sel["funded_exposure_scale"],
            "combined_pass_probability": sel["combined_evaluation_pass_probability"],
            "expected_reward_pct": sel["funded"]["expected_reward_pct"],
            "payout_efficiency_score": sel["payout_efficiency_score"],
        }
    print(json.dumps({
        "track": x["track"],
        "data_end": x["data_end"],
        "hidden_validation_opened": x["hidden_validation_opened"],
        "final_oos_opened": x["final_oos_opened"],
        "winners": summary,
    }, indent=2, allow_nan=False))
