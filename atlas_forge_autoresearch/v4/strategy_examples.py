"""Reference v4 strategy families demonstrating richer alpha sources."""
from __future__ import annotations

from typing import Mapping, Sequence

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
