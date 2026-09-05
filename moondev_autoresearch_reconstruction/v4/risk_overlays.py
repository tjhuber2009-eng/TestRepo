"""Causal portfolio risk overlays for AUTORESEARCH v4."""
from __future__ import annotations

from typing import Callable, Mapping
from math import sqrt

import numpy as np
import pandas as pd

from .multi_asset_engine import StrategyFn


def volatility_target_overlay(
    base_strategy: StrategyFn,
    *,
    target_vol: float,
    periods_per_year: float = 252.0,
    lookback: int = 20,
    max_gross: float = 1.0,
    min_scale: float = 0.0,
    max_scale: float = 2.0,
) -> StrategyFn:
    """Scale target weights using trailing realized strategy volatility.

    The scale at close[t] uses only returns realized through close[t] under
    weights chosen no later than close[t-1], so the overlay is causal. The
    resulting target still executes no earlier than open[t+1].
    """
    if target_vol <= 0:
        raise ValueError("target_vol must be positive")
    if lookback < 5:
        raise ValueError("lookback must be >=5")
    if max_gross <= 0:
        raise ValueError("max_gross must be positive")

    def strategy(
        data: Mapping[str, pd.DataFrame],
        features: Mapping[str, pd.DataFrame] | None = None,
    ) -> pd.DataFrame:
        base = base_strategy(data, features)
        symbols = sorted(data)
        index = next(iter(data.values())).index
        base = base.reindex(index=index, columns=symbols).fillna(0.0).astype(float)

        close_ret = pd.DataFrame(
            {s: pd.to_numeric(data[s]["Close"], errors="coerce").pct_change() for s in symbols},
            index=index,
        ).fillna(0.0)
        # Pseudo realized return through t uses the prior close's target and
        # close[t]/close[t-1], all known by close[t].
        realized = (base.shift(1).fillna(0.0) * close_ret).sum(axis=1)
        rv = (
            realized.rolling(lookback, min_periods=lookback).std(ddof=1)
            * sqrt(float(periods_per_year))
        )
        scale = (float(target_vol) / rv.replace(0.0, np.nan)).clip(
            lower=float(min_scale), upper=float(max_scale)
        )
        scale = scale.replace([np.inf, -np.inf], np.nan).fillna(0.0)
        out = base.mul(scale, axis=0)

        gross = out.abs().sum(axis=1)
        too_big = gross > float(max_gross)
        if too_big.any():
            out.loc[too_big] = out.loc[too_big].div(gross.loc[too_big], axis=0) * float(max_gross)
        return out

    return strategy


def drawdown_brake_overlay(
    base_strategy: StrategyFn,
    *,
    soft_drawdown: float = 0.08,
    hard_drawdown: float = 0.16,
    soft_scale: float = 0.65,
    hard_scale: float = 0.35,
) -> StrategyFn:
    """Causal exposure brake based on lagged pseudo-strategy drawdown."""
    if not (0 < soft_drawdown < hard_drawdown < 1):
        raise ValueError("drawdown thresholds must satisfy 0<soft<hard<1")

    def strategy(
        data: Mapping[str, pd.DataFrame],
        features: Mapping[str, pd.DataFrame] | None = None,
    ) -> pd.DataFrame:
        base = base_strategy(data, features)
        symbols = sorted(data)
        index = next(iter(data.values())).index
        base = base.reindex(index=index, columns=symbols).fillna(0.0).astype(float)
        close_ret = pd.DataFrame(
            {s: pd.to_numeric(data[s]["Close"], errors="coerce").pct_change() for s in symbols},
            index=index,
        ).fillna(0.0)
        realized = (base.shift(1).fillna(0.0) * close_ret).sum(axis=1)
        equity = (1.0 + realized).cumprod()
        dd = equity / equity.cummax().replace(0.0, np.nan) - 1.0
        scale = pd.Series(1.0, index=index)
        scale.loc[dd <= -abs(soft_drawdown)] = float(soft_scale)
        scale.loc[dd <= -abs(hard_drawdown)] = float(hard_scale)
        return base.mul(scale, axis=0)

    return strategy


def compose_overlays(base_strategy: StrategyFn, *overlays: Callable[[StrategyFn], StrategyFn]) -> StrategyFn:
    out = base_strategy
    for overlay in overlays:
        out = overlay(out)
    return out


def vix_stress_overlay(
    base_strategy: StrategyFn,
    vix_close: pd.Series,
    *,
    stress_quantile: float = 0.85,
    severe_quantile: float = 0.97,
    stress_scale: float = 0.50,
    severe_scale: float = 0.0,
    min_history: int = 252,
) -> StrategyFn:
    """Causal exposure scaling from the market's volatility regime.

    VIX close[t] is compared with percentile thresholds estimated from VIX
    history ending at t-1. The resulting target weight is formed after close[t]
    and still executes only at open[t+1] in the portfolio engine.
    """
    if not (0.5 <= stress_quantile < severe_quantile < 1.0):
        raise ValueError("require 0.5 <= stress_quantile < severe_quantile < 1")
    if not (0.0 <= severe_scale <= stress_scale <= 1.0):
        raise ValueError("require 0 <= severe_scale <= stress_scale <= 1")
    vix = pd.to_numeric(vix_close, errors="coerce").sort_index()
    stress_threshold = (
        vix.expanding(min_periods=int(min_history))
        .quantile(float(stress_quantile))
        .shift(1)
    )
    severe_threshold = (
        vix.expanding(min_periods=int(min_history))
        .quantile(float(severe_quantile))
        .shift(1)
    )

    def strategy(
        data: Mapping[str, pd.DataFrame],
        features: Mapping[str, pd.DataFrame] | None = None,
    ) -> pd.DataFrame:
        base = base_strategy(data, features).copy()
        idx = base.index
        vv = vix.reindex(idx).ffill()
        st = stress_threshold.reindex(idx).ffill()
        sv = severe_threshold.reindex(idx).ffill()
        scale = pd.Series(1.0, index=idx)
        scale.loc[(vv >= st) & st.notna()] = float(stress_scale)
        scale.loc[(vv >= sv) & sv.notna()] = float(severe_scale)
        return base.mul(scale, axis=0)

    return strategy


def probability_filter_overlay(
    base_strategy: StrategyFn,
    probabilities: pd.Series,
    *,
    threshold: float = 0.55,
    below_scale: float = 0.0,
) -> StrategyFn:
    """Scale an existing strategy using precomputed causal walk-forward odds."""
    if not (0.0 <= threshold <= 1.0):
        raise ValueError("threshold must be in [0,1]")
    if not (0.0 <= below_scale <= 1.0):
        raise ValueError("below_scale must be in [0,1]")
    probs = pd.to_numeric(probabilities, errors="coerce").sort_index()

    def strategy(
        data: Mapping[str, pd.DataFrame],
        features: Mapping[str, pd.DataFrame] | None = None,
    ) -> pd.DataFrame:
        base = base_strategy(data, features).copy()
        p = probs.reindex(base.index)
        # Before enough walk-forward training history exists, leave the
        # underlying strategy untouched rather than creating an artificial
        # warm-up cash regime.
        scale = pd.Series(1.0, index=base.index)
        known = p.notna()
        scale.loc[known & (p < float(threshold))] = float(below_scale)
        return base.mul(scale, axis=0)

    return strategy
