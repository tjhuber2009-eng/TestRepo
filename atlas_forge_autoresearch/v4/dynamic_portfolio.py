"""Causal dynamic allocator across already-qualified strategy return streams.

This layer does not discover new alpha. It changes only how capital is distributed
among strategies that have already passed their own evidence gates. Every decision
uses returns available strictly before the allocation row.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd


@dataclass
class DynamicPortfolioResult:
    returns: pd.Series
    weights: pd.DataFrame
    turnover: pd.Series
    average_active_strategies: float
    average_gross: float
    average_turnover: float
    last_weights: dict[str, float]
    policy: dict

    def summary(self) -> dict:
        return {
            "average_active_strategies": self.average_active_strategies,
            "average_gross": self.average_gross,
            "average_turnover": self.average_turnover,
            "last_weights": self.last_weights,
            "policy": self.policy,
        }


def _capped_weights(preference: np.ndarray, max_weight: float) -> np.ndarray:
    """Long-only capped simplex projection; unused capacity remains cash."""
    pref = np.maximum(np.asarray(preference, dtype=float), 0.0)
    n = pref.size
    out = np.zeros(n, dtype=float)
    active = np.flatnonzero(pref > 0.0)
    if active.size == 0:
        return out

    # If too few strategies have positive evidence to fully invest without
    # breaking concentration, deliberately retain cash rather than dilute into
    # negative-score strategies.
    target_total = min(1.0, float(active.size) * float(max_weight))
    remaining = target_total
    free = active.copy()
    while free.size and remaining > 1e-12:
        base = pref[free]
        if base.sum() <= 0.0:
            base = np.ones(free.size, dtype=float)
        proposal = remaining * base / base.sum()
        over = proposal > float(max_weight) + 1e-12
        if not over.any():
            out[free] = proposal
            remaining = 0.0
            break
        capped = free[over]
        out[capped] = float(max_weight)
        remaining -= float(max_weight) * len(capped)
        free = free[~over]
    return out


def _portfolio_drawdown(realized: np.ndarray) -> float:
    if realized.size == 0:
        return 0.0
    eq = np.cumprod(1.0 + realized)
    peaks = np.maximum.accumulate(eq)
    return float(np.min(eq / np.where(peaks == 0.0, np.nan, peaks) - 1.0))


def causal_dynamic_allocation(
    returns: pd.DataFrame,
    *,
    periods_per_year: float = 252.0,
    min_history: int = 252,
    growth_lookback: int = 126,
    risk_lookback: int = 63,
    rebalance_every: int = 21,
    max_weight: float = 0.55,
    correlation_penalty: float = 1.0,
    downside_penalty: float = 0.75,
    soft_drawdown: float = -0.12,
    hard_drawdown: float = -0.22,
    soft_scale: float = 0.75,
    hard_scale: float = 0.50,
) -> DynamicPortfolioResult:
    """Walk-forward strategy rotation with correlation and drawdown controls.

    Allocation at row t is estimated from rows strictly before t. Positive
    trailing geometric growth earns capital; volatility, downside volatility,
    and correlation to the rest of the qualified strategy set reduce it.
    """
    x = returns.apply(pd.to_numeric, errors="coerce").dropna(how="any")
    if len(x) < max(int(min_history) + 2, 50):
        raise ValueError("dynamic portfolio requires sufficient aligned history")
    if x.shape[1] < 2:
        raise ValueError("dynamic portfolio requires at least two strategies")
    if not (0.0 < max_weight <= 1.0):
        raise ValueError("max_weight must be in (0,1]")
    if not (0.0 <= hard_scale <= soft_scale <= 1.0):
        raise ValueError("require 0 <= hard_scale <= soft_scale <= 1")
    if not (hard_drawdown < soft_drawdown < 0.0):
        raise ValueError("require hard_drawdown < soft_drawdown < 0")

    names = list(x.columns)
    arr = x.to_numpy(dtype=float)
    weights = np.zeros_like(arr, dtype=float)
    realized = np.zeros(len(x), dtype=float)
    turnover = np.zeros(len(x), dtype=float)
    current = np.zeros(len(names), dtype=float)

    for i in range(len(x)):
        rebalance = (
            i >= int(min_history)
            and (i == int(min_history) or (i - int(min_history)) % int(rebalance_every) == 0)
        )
        if rebalance:
            g0 = max(0, i - int(growth_lookback))
            r0 = max(0, i - int(risk_lookback))
            growth_hist = arr[g0:i]
            risk_hist = arr[r0:i]

            safe = np.clip(growth_hist, -0.999999, None)
            ann_log_growth = (
                np.mean(np.log1p(safe), axis=0) * float(periods_per_year)
            )
            ann_vol = (
                np.std(risk_hist, axis=0, ddof=1)
                * np.sqrt(float(periods_per_year))
            )
            downside = np.minimum(risk_hist, 0.0)
            downside_vol = (
                np.sqrt(np.mean(np.square(downside), axis=0))
                * np.sqrt(float(periods_per_year))
            )

            corr = np.corrcoef(risk_hist, rowvar=False)
            corr = np.nan_to_num(corr, nan=0.0, posinf=0.0, neginf=0.0)
            avg_abs_corr = (
                np.sum(np.abs(corr), axis=1) - 1.0
            ) / max(len(names) - 1, 1)

            denominator = (
                np.maximum(ann_vol, 1e-9)
                + float(downside_penalty) * np.maximum(downside_vol, 0.0)
            ) * (1.0 + float(correlation_penalty) * np.maximum(avg_abs_corr, 0.0))
            preference = np.divide(
                np.maximum(ann_log_growth, 0.0),
                denominator,
                out=np.zeros(len(names), dtype=float),
                where=np.isfinite(denominator) & (denominator > 0.0),
            )
            proposed = _capped_weights(preference, float(max_weight))

            # Drawdown throttle is based only on returns realized before row i.
            dd = _portfolio_drawdown(realized[:i])
            if dd <= float(hard_drawdown):
                proposed *= float(hard_scale)
            elif dd <= float(soft_drawdown):
                proposed *= float(soft_scale)

            turnover[i] = float(np.sum(np.abs(proposed - current)))
            current = proposed

        weights[i] = current
        realized[i] = float(np.dot(current, arr[i]))

    w = pd.DataFrame(weights, index=x.index, columns=names)
    r = pd.Series(realized, index=x.index, name="dynamic_strategy_allocator")
    t = pd.Series(turnover, index=x.index, name="turnover")
    active = (w.abs() > 1e-12).sum(axis=1)
    gross = w.abs().sum(axis=1)

    return DynamicPortfolioResult(
        returns=r,
        weights=w,
        turnover=t,
        average_active_strategies=float(active.mean()),
        average_gross=float(gross.mean()),
        average_turnover=float(t.mean()),
        last_weights={
            name: float(value)
            for name, value in w.iloc[-1].items()
            if abs(float(value)) > 1e-12
        },
        policy={
            "name": "causal_strategy_rotation_v1",
            "periods_per_year": float(periods_per_year),
            "min_history": int(min_history),
            "growth_lookback": int(growth_lookback),
            "risk_lookback": int(risk_lookback),
            "rebalance_every": int(rebalance_every),
            "max_weight": float(max_weight),
            "correlation_penalty": float(correlation_penalty),
            "downside_penalty": float(downside_penalty),
            "soft_drawdown": float(soft_drawdown),
            "hard_drawdown": float(hard_drawdown),
            "soft_scale": float(soft_scale),
            "hard_scale": float(hard_scale),
            "causality": "row_t_weights_use_rows_strictly_before_t",
            "eligibility": "input_strategies_must_already_pass_individual_evidence_gates",
        },
    )
