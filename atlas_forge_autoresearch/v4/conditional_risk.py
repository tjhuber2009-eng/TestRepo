"""Causal downside-semivolatility risk scaling for a qualified portfolio stream.

The overlay only cuts risk in an extreme high-downside-volatility state. It
does not interpret upside/rally volatility as risk. Unit exposure never rises
above 1x, so the outer robust optimizer's existing gross cap remains
authoritative. Every decision for return[t] uses returns <= t-1.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
import math

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class ConditionalRiskSummary:
    lookback: int
    min_history: int
    high_vol_quantile: float
    min_risk_scale: float
    high_state_observations: int
    scaled_observations: int
    average_risk_scale: float
    minimum_realized_risk_scale: float
    policy: str

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class ConditionalRiskResult:
    returns: pd.Series
    gross_profile: pd.Series
    risk_scale: pd.Series
    summary: ConditionalRiskSummary


def conditional_high_downside_volatility_overlay(
    returns: pd.Series,
    gross_profile: pd.Series,
    *,
    lookback: int = 20,
    min_history: int = 252,
    high_vol_quantile: float = 0.80,
    min_risk_scale: float = 0.50,
    periods_per_year: float = 252.0,
) -> ConditionalRiskResult:
    """Cut exposure only in a causal extreme downside-semivolatility state.

    Downside semivolatility is the annualized RMS of negative returns, with
    positive returns contributing zero. The estimate applied to return[t] is
    formed from returns strictly before t. Its high-state threshold and
    reference median are formed from still earlier estimates. This avoids
    cutting exposure merely because an upside rally is volatile.
    """
    r=pd.to_numeric(returns,errors="coerce").astype(float)
    g=pd.to_numeric(gross_profile,errors="coerce").reindex(r.index).astype(float)
    if r.isna().any() or g.isna().any():
        raise ValueError("conditional risk inputs must be complete")
    if (g < 0.0).any():
        raise ValueError("gross_profile must be nonnegative")
    if lookback < 5:
        raise ValueError("lookback must be >=5")
    if min_history < max(lookback * 2, 50):
        raise ValueError("min_history too short")
    if not (0.50 < high_vol_quantile < 1.0):
        raise ValueError("high_vol_quantile must be in (0.5,1)")
    if not (0.0 < min_risk_scale <= 1.0):
        raise ValueError("min_risk_scale must be in (0,1]")

    # shift(1) is the central causality guard: the scale applied to r[t] never
    # observes r[t]. Positive returns contribute zero rather than inflating the
    # risk signal, which is important for rally-prone crypto return streams.
    downside_squared=pd.Series(
        np.square(np.minimum(r.to_numpy(dtype=float),0.0)),
        index=r.index,
        dtype=float,
    )
    realized_vol=(
        downside_squared.rolling(
            lookback,min_periods=lookback
        ).mean().pow(0.5).shift(1)
        * math.sqrt(float(periods_per_year))
    )
    prior_vol=realized_vol.shift(1)
    high_threshold=prior_vol.expanding(
        min_periods=min_history
    ).quantile(high_vol_quantile)
    reference_vol=prior_vol.expanding(
        min_periods=min_history
    ).median()

    high_state=(
        realized_vol.notna()
        & high_threshold.notna()
        & reference_vol.notna()
        & (realized_vol > high_threshold)
        & (reference_vol > 0.0)
    )
    scale=pd.Series(1.0,index=r.index,dtype=float,name="conditional_risk_scale")
    ratio=(reference_vol/realized_vol).clip(
        lower=float(min_risk_scale),upper=1.0
    )
    scale.loc[high_state]=ratio.loc[high_state]
    scale=scale.fillna(1.0).clip(lower=float(min_risk_scale),upper=1.0)

    adjusted_returns=(r*scale).rename(
        returns.name or "conditional_high_volatility"
    )
    adjusted_gross=(g*scale).rename(
        gross_profile.name or "conditional_high_volatility_gross"
    )
    summary=ConditionalRiskSummary(
        lookback=int(lookback),
        min_history=int(min_history),
        high_vol_quantile=float(high_vol_quantile),
        min_risk_scale=float(min_risk_scale),
        high_state_observations=int(high_state.sum()),
        scaled_observations=int((scale < 1.0-1e-12).sum()),
        average_risk_scale=float(scale.mean()),
        minimum_realized_risk_scale=float(scale.min()),
        policy=(
            "causal top-quintile downside-semivolatility de-risking only; "
            "positive/upside volatility does not increase the risk signal; "
            "unit exposure never exceeds 1x; no low-risk leverage boost"
        ),
    )
    return ConditionalRiskResult(
        returns=adjusted_returns,
        gross_profile=adjusted_gross,
        risk_scale=scale,
        summary=summary,
    )
