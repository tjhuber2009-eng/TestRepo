"""Reference v4 strategy families demonstrating richer alpha sources."""
from __future__ import annotations

from typing import Mapping, Sequence

import numpy as np
import pandas as pd


def cross_sectional_momentum_rotation(
    *,
    lookback: int = 126,
    trend_window: int = 200,
    top_k: int = 2,
    gross_weight: float = 1.0,
    eligible_symbols: Sequence[str] | None = None,
):
    """Rank assets by causal momentum and hold top assets that are above trend."""
    def strategy(data: Mapping[str, pd.DataFrame], features=None) -> pd.DataFrame:
        symbols = sorted(set(eligible_symbols or data.keys()).intersection(data))
        index = next(iter(data.values())).index
        out = pd.DataFrame(0.0, index=index, columns=sorted(data))
        mom = pd.DataFrame({s: data[s]["Close"] / data[s]["Close"].shift(lookback) - 1.0 for s in symbols})
        trend = pd.DataFrame({
            s: data[s]["Close"] > data[s]["Close"].rolling(trend_window, min_periods=trend_window).mean()
            for s in symbols
        })
        for ts in index:
            vals = mom.loc[ts].where(trend.loc[ts]).dropna().sort_values(ascending=False)
            vals = vals[vals > 0.0].head(top_k)
            if len(vals):
                w = gross_weight / len(vals)
                out.loc[ts, vals.index] = w
        return out
    return strategy


def independent_trend_basket(
    *,
    symbols: Sequence[str],
    momentum_window: int = 252,
    trend_window: int = 200,
    gross_weight: float = 1.0,
):
    """Diversified long/cash time-series trend basket with no cross-sectional ranking.

    Each asset independently earns an equal share of gross exposure only when
    its close is above its own trend average and its trailing return is
    positive. Signals use close[t] only; the engine executes at open[t+1].
    """
    symbols = tuple(symbols)

    def strategy(data: Mapping[str, pd.DataFrame], features=None) -> pd.DataFrame:
        missing = set(symbols).difference(data)
        if missing:
            raise KeyError(f"missing trend-basket assets: {sorted(missing)}")
        index = data[symbols[0]].index
        out = pd.DataFrame(0.0, index=index, columns=sorted(data))
        healthy = pd.DataFrame(False, index=index, columns=list(symbols))
        for symbol in symbols:
            close = data[symbol]["Close"]
            momentum = close / close.shift(momentum_window) - 1.0
            trend = close.rolling(
                trend_window, min_periods=trend_window
            ).mean()
            healthy[symbol] = (close > trend) & (momentum > 0.0)
        for ts in index:
            active = list(healthy.columns[healthy.loc[ts]])
            if active:
                weight = float(gross_weight) / float(len(active))
                out.loc[ts, active] = weight
        return out

    return strategy


def pead_event_weights(
    data: Mapping[str, pd.DataFrame],
    earnings: pd.DataFrame,
    *,
    surprise_threshold: float = 1.0,
    hold_bars: int = 20,
    top_k: int = 5,
    gross_weight: float = 1.0,
) -> pd.DataFrame:
    """Post-earnings drift reference strategy using timestamped surprises."""
    if not isinstance(earnings.index, pd.DatetimeIndex):
        raise ValueError("earnings table requires DatetimeIndex")
    if not {"symbol", "surprise_z"}.issubset(earnings.columns):
        raise ValueError("earnings table requires symbol and surprise_z")
    index = next(iter(data.values())).index
    symbols = sorted(data)
    out = pd.DataFrame(0.0, index=index, columns=symbols)
    active: dict[str, int] = {}
    events_by_pos: dict[int, list[tuple[str, float]]] = {}
    for ts, row in earnings.sort_index().iterrows():
        sym = str(row["symbol"])
        if sym not in data or float(row["surprise_z"]) < surprise_threshold:
            continue
        pos = int(index.searchsorted(ts, side="left"))
        if pos < len(index):
            events_by_pos.setdefault(pos, []).append((sym, float(row["surprise_z"])))

    for i, _ts in enumerate(index):
        for sym, score in events_by_pos.get(i, []):
            active[sym] = hold_bars
        ranked = sorted(
            [(sym, remaining) for sym, remaining in active.items() if remaining > 0],
            key=lambda x: x[1],
            reverse=True,
        )[:top_k]
        if ranked:
            w = gross_weight / len(ranked)
            for sym, _ in ranked:
                out.iloc[i, out.columns.get_loc(sym)] = w
        active = {sym: remaining - 1 for sym, remaining in active.items() if remaining - 1 > 0}
    return out


def regime_conditioned_mean_reversion(
    *,
    symbol: str,
    rsi_threshold: float = 10.0,
    exit_rsi: float = 65.0,
    trend_window: int = 200,
    allowed_vol_regimes: tuple[str, ...] = ("low", "normal"),
):
    """Mean-reversion entry conditioned on trend and causal volatility regime."""
    def strategy(data: Mapping[str, pd.DataFrame], features: Mapping[str, pd.DataFrame] | None = None) -> pd.DataFrame:
        if features is None or symbol not in features:
            raise ValueError("features with rsi_2 and vol_regime required")
        index = data[symbol].index
        out = pd.DataFrame(0.0, index=index, columns=sorted(data))
        close = data[symbol]["Close"]
        feat = features[symbol]
        rsi = feat["rsi_2"]
        vol_regime = feat["vol_regime"]
        trend = close > close.rolling(trend_window, min_periods=trend_window).mean()
        active = False
        for i in range(len(index)):
            if not active and trend.iloc[i] and rsi.iloc[i] < rsi_threshold and vol_regime.iloc[i] in allowed_vol_regimes:
                active = True
            elif active and rsi.iloc[i] > exit_rsi:
                active = False
            out.iloc[i, out.columns.get_loc(symbol)] = 1.0 if active else 0.0
        return out
    return strategy


def leveraged_defensive_rotation(
    *,
    signal_symbol: str,
    risk_symbol: str,
    defensive_symbols: Sequence[str],
    risk_sma_window: int = 175,
    risk_momentum_window: int = 126,
    defensive_momentum_window: int = 126,
    defensive_trend_window: int = 200,
    risk_weight: float = 1.0,
    defensive_weight: float = 1.0,
):
    """Risk-on leveraged asset; otherwise rotate to strongest healthy defense.

    If no defensive asset has positive momentum and is above its own trend
    filter, the portfolio remains in cash. All decisions use close[t] and are
    executed by the multi-asset engine at open[t+1].
    """
    defensive_symbols = tuple(defensive_symbols)

    def strategy(data: Mapping[str, pd.DataFrame], features=None) -> pd.DataFrame:
        required = {signal_symbol, risk_symbol, *defensive_symbols}
        missing = required.difference(data)
        if missing:
            raise KeyError(f"missing defensive-rotation assets: {sorted(missing)}")
        index = data[signal_symbol].index
        out = pd.DataFrame(0.0, index=index, columns=sorted(data))

        signal_close = data[signal_symbol]["Close"]
        signal_sma = signal_close.rolling(
            risk_sma_window, min_periods=risk_sma_window
        ).mean()
        signal_mom = (
            signal_close / signal_close.shift(risk_momentum_window) - 1.0
        )
        risk_on = (signal_close > signal_sma) & (signal_mom > 0.0)

        def_scores = pd.DataFrame(index=index)
        def_ok = pd.DataFrame(False, index=index, columns=list(defensive_symbols))
        for symbol in defensive_symbols:
            close = data[symbol]["Close"]
            mom = close / close.shift(defensive_momentum_window) - 1.0
            trend = close.rolling(
                defensive_trend_window, min_periods=defensive_trend_window
            ).mean()
            def_scores[symbol] = mom
            def_ok[symbol] = (close > trend) & (mom > 0.0)

        for i, ts in enumerate(index):
            if bool(risk_on.iloc[i]):
                out.loc[ts, risk_symbol] = float(risk_weight)
                continue
            healthy = def_scores.loc[ts].where(def_ok.loc[ts]).dropna()
            if not healthy.empty:
                winner = str(healthy.idxmax())
                out.loc[ts, winner] = float(defensive_weight)
        return out

    return strategy


def rolling_pair_reversion(
    *,
    left_symbol: str,
    right_symbol: str,
    formation_window: int = 252,
    z_window: int = 60,
    entry_z: float = 2.0,
    exit_z: float = 0.5,
    stop_z: float = 4.0,
    gross_weight: float = 1.0,
):
    """Causal rolling-hedge-ratio pair mean reversion.

    Rules are deliberately fixed and transparent: estimate beta from trailing
    log closes, z-score the resulting spread, enter outside +/-entry_z, exit
    inside +/-exit_z or beyond stop_z, and execute through the v4 engine on the
    next bar open. Pair gross exposure is normalized to gross_weight.
    """
    if formation_window < 20 or z_window < 10:
        raise ValueError("formation_window>=20 and z_window>=10 required")
    if not (0.0 <= exit_z < entry_z < stop_z):
        raise ValueError("require 0 <= exit_z < entry_z < stop_z")
    if gross_weight <= 0.0:
        raise ValueError("gross_weight must be positive")

    def strategy(data: Mapping[str, pd.DataFrame], features=None) -> pd.DataFrame:
        if left_symbol not in data or right_symbol not in data:
            raise KeyError("pair symbols missing from market data")
        index = data[left_symbol].index
        out = pd.DataFrame(0.0, index=index, columns=sorted(data))
        a = np.log(data[left_symbol]["Close"].astype(float))
        b = np.log(data[right_symbol]["Close"].astype(float))
        cov = a.rolling(formation_window, min_periods=formation_window).cov(b)
        var = b.rolling(formation_window, min_periods=formation_window).var()
        beta = (cov / var.replace(0.0, np.nan)).replace([np.inf, -np.inf], np.nan)
        spread = a - beta * b
        mean = spread.rolling(z_window, min_periods=z_window).mean()
        std = spread.rolling(z_window, min_periods=z_window).std(ddof=0)
        z = ((spread - mean) / std.replace(0.0, np.nan)).replace(
            [np.inf, -np.inf], np.nan
        )

        state = 0
        for ts in index:
            zi = z.loc[ts]
            bi = beta.loc[ts]
            if not np.isfinite(zi) or not np.isfinite(bi):
                state = 0
                continue
            if state == 0:
                if zi >= entry_z:
                    state = -1
                elif zi <= -entry_z:
                    state = 1
            elif abs(zi) <= exit_z or abs(zi) >= stop_z:
                state = 0
            if state:
                left_raw = float(state)
                right_raw = float(-state * bi)
                gross = abs(left_raw) + abs(right_raw)
                if gross > 0.0:
                    scale = float(gross_weight) / gross
                    out.loc[ts, left_symbol] = left_raw * scale
                    out.loc[ts, right_symbol] = right_raw * scale
        return out

    return strategy


def overnight_gap_reversal_diagnostic(
    frame: pd.DataFrame,
    *,
    gap_threshold: float = 0.01,
    one_way_cost_bps: float = 3.0,
    cost_stress_multiplier: float = 3.0,
    periods_per_year: float = 252.0,
) -> dict:
    """Causal open-to-close reversal using only the opening gap known at entry.

    A positive gap above threshold is shorted from that day's open to close; a
    negative gap below -threshold is bought. The signal uses open[t] and
    close[t-1] only. This is a daily-OHLC session engine, not a next-open proxy.
    """
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("DatetimeIndex required")
    if not {"Open", "Close"}.issubset(frame.columns):
        raise ValueError("Open and Close required")
    if gap_threshold < 0.0:
        raise ValueError("gap_threshold must be non-negative")

    open_ = pd.to_numeric(frame["Open"], errors="coerce")
    close = pd.to_numeric(frame["Close"], errors="coerce")
    prev_close = close.shift(1)
    gap = open_ / prev_close - 1.0
    weight = pd.Series(0.0, index=frame.index)
    weight.loc[gap >= gap_threshold] = -1.0
    weight.loc[gap <= -gap_threshold] = 1.0
    intraday = close / open_ - 1.0
    base_cost = weight.abs() * 2.0 * float(one_way_cost_bps) / 10_000.0
    ret = (weight * intraday - base_cost).fillna(0.0)
    stress_ret = (
        weight * intraday
        - base_cost * float(cost_stress_multiplier)
    ).fillna(0.0)

    def summarize(series: pd.Series) -> tuple[float, float, float]:
        eq = (1.0 + series).cumprod()
        if len(eq) < 2:
            return 0.0, 0.0, 0.0
        elapsed = max(
            (eq.index[-1] - eq.index[0]).total_seconds() / 86400.0,
            1.0,
        )
        years = elapsed / 365.2425
        cagr = (
            (float(eq.iloc[-1]) ** (1.0 / years) - 1.0) * 100.0
            if float(eq.iloc[-1]) > 0.0 else -100.0
        )
        peak = eq.cummax()
        dd = float((eq / peak - 1.0).min() * 100.0)
        sd = float(series.std(ddof=0))
        sharpe = (
            float(series.mean()) / sd * np.sqrt(float(periods_per_year))
            if sd > 0.0 else 0.0
        )
        return float(cagr), dd, sharpe

    cagr, dd, sharpe = summarize(ret)
    stress_cagr, _, _ = summarize(stress_ret)
    return {
        "policy": "open_gap_known_at_entry_open_to_same_day_close_v1",
        "gap_threshold": float(gap_threshold),
        "one_way_cost_bps": float(one_way_cost_bps),
        "cost_stress_multiplier": float(cost_stress_multiplier),
        "cagr_pct": cagr,
        "max_dd_pct": dd,
        "sharpe": sharpe,
        "cost_stress_cagr_pct": stress_cagr,
        "trades": int((weight != 0.0).sum()),
        "active_fraction": float((weight != 0.0).mean()),
        "causality": "signal uses open[t] and close[t-1]; exit is close[t]",
    }
