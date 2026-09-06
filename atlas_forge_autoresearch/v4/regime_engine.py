"""Causal market-regime labeling for AUTORESEARCH v4."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class RegimeConfig:
    trend_window: int = 200
    fast_trend_window: int = 50
    vol_window: int = 20
    liquidity_window: int = 20
    min_history: int = 252
    low_quantile: float = 0.33
    high_quantile: float = 0.67


def expanding_quantile_state(
    series: pd.Series,
    *,
    min_history: int,
    low_q: float,
    high_q: float,
    labels=("low", "normal", "high"),
) -> pd.Series:
    """Classify a series relative to its past-only expanding distribution."""
    values = pd.to_numeric(series, errors="coerce")
    out = pd.Series(index=values.index, dtype="object")
    for i in range(len(values)):
        if i < min_history or not np.isfinite(values.iloc[i]):
            out.iloc[i] = "unknown"
            continue
        hist = values.iloc[:i].dropna().to_numpy(dtype=float)
        if hist.size < min_history:
            out.iloc[i] = "unknown"
            continue
        lo, hi = np.quantile(hist, [low_q, high_q])
        cur = float(values.iloc[i])
        if cur <= lo:
            out.iloc[i] = labels[0]
        elif cur >= hi:
            out.iloc[i] = labels[2]
        else:
            out.iloc[i] = labels[1]
    return out


class RegimeEngine:
    def __init__(self, config: RegimeConfig | None = None):
        self.config = config or RegimeConfig()

    def label_asset(self, market: pd.DataFrame, features: pd.DataFrame | None = None) -> pd.DataFrame:
        cfg = self.config
        close = pd.to_numeric(market["Close"], errors="coerce")
        out = pd.DataFrame(index=market.index)
        slow = close.rolling(cfg.trend_window, min_periods=cfg.trend_window).mean()
        fast = close.rolling(cfg.fast_trend_window, min_periods=cfg.fast_trend_window).mean()
        out["trend_regime"] = np.where(
            (close > slow) & (fast > slow),
            "bull",
            np.where((close < slow) & (fast < slow), "bear", "transition"),
        )
        out.loc[slow.isna(), "trend_regime"] = "unknown"

        ret = close.pct_change()
        rv = ret.rolling(cfg.vol_window, min_periods=cfg.vol_window).std(ddof=1)
        out["vol_regime"] = expanding_quantile_state(
            rv,
            min_history=cfg.min_history,
            low_q=cfg.low_quantile,
            high_q=cfg.high_quantile,
        )

        if features is not None and "dollar_volume" in features:
            liq = np.log1p(pd.to_numeric(features["dollar_volume"], errors="coerce"))
        elif "Volume" in market:
            liq = np.log1p(pd.to_numeric(market["Volume"], errors="coerce") * close)
        else:
            liq = pd.Series(np.nan, index=market.index)
        out["liquidity_regime"] = expanding_quantile_state(
            liq.rolling(cfg.liquidity_window, min_periods=cfg.liquidity_window).mean(),
            min_history=cfg.min_history,
            low_q=cfg.low_quantile,
            high_q=cfg.high_quantile,
            labels=("thin", "normal", "deep"),
        )
        out["regime"] = (
            out["trend_regime"].astype(str)
            + "|"
            + out["vol_regime"].astype(str)
            + "|"
            + out["liquidity_regime"].astype(str)
        )
        return out

    def build(
        self,
        market_data: Mapping[str, pd.DataFrame],
        feature_data: Mapping[str, pd.DataFrame] | None = None,
    ) -> dict[str, pd.DataFrame]:
        return {
            symbol: self.label_asset(frame, (feature_data or {}).get(symbol))
            for symbol, frame in market_data.items()
        }
