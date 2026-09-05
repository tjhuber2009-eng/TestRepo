from __future__ import annotations

from dataclasses import dataclass
from math import erf, log, sqrt

from .fees import expected_value_per_share


@dataclass(frozen=True)
class LaneDecision:
    trade: bool
    edge: float
    fair_probability: float
    reason: str


def weather_ensemble_decision(
    fair_probability: float,
    ask: float,
    *,
    fee_rate: float = 0.05,
    fee_exponent: float = 1.0,
    min_edge: float = 0.05,
) -> LaneDecision:
    edge = expected_value_per_share(
        fair_probability, ask, fee_rate, fee_exponent
    )
    return LaneDecision(
        trade=edge >= min_edge,
        edge=edge,
        fair_probability=fair_probability,
        reason=f"weather ensemble edge={edge:.4f}",
    )


def _normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def crypto_up_probability(
    *,
    start_twap: float,
    current_price: float,
    annualized_vol: float,
    seconds_remaining: float,
) -> float:
    if min(start_twap, current_price) <= 0:
        raise ValueError("prices must be > 0")
    if annualized_vol <= 0:
        return 1.0 if current_price >= start_twap else 0.0
    if seconds_remaining <= 0:
        return 1.0 if current_price >= start_twap else 0.0
    year_seconds = 365.0 * 24 * 60 * 60
    sigma = annualized_vol * sqrt(seconds_remaining / year_seconds)
    z = log(current_price / start_twap) / sigma
    return _normal_cdf(z)


def crypto_twap_decision(
    fair_probability: float,
    ask: float,
    *,
    fee_rate: float = 0.07,
    fee_exponent: float = 1.0,
    min_edge: float = 0.04,
) -> LaneDecision:
    edge = expected_value_per_share(
        fair_probability, ask, fee_rate, fee_exponent
    )
    return LaneDecision(
        trade=edge >= min_edge,
        edge=edge,
        fair_probability=fair_probability,
        reason=f"crypto twap edge={edge:.4f}",
    )


def late_resolution_decision(
    fair_probability: float,
    ask: float,
    *,
    seconds_remaining: float,
    fee_rate: float = 0.07,
    fee_exponent: float = 1.0,
    min_fair_probability: float = 0.92,
    min_edge: float = 0.025,
    max_seconds_remaining: float = 60.0,
) -> LaneDecision:
    edge = expected_value_per_share(
        fair_probability, ask, fee_rate, fee_exponent
    )
    ok = (
        seconds_remaining <= max_seconds_remaining
        and fair_probability >= min_fair_probability
        and edge >= min_edge
    )
    return LaneDecision(
        trade=ok,
        edge=edge,
        fair_probability=fair_probability,
        reason=(
            f"late-resolution p={fair_probability:.4f} edge={edge:.4f} "
            f"t={seconds_remaining:.1f}s"
        ),
    )


def favorite_longshot_shadow(
    price: float, historical_hit_rate: float
) -> LaneDecision:
    edge = historical_hit_rate - price
    return LaneDecision(
        trade=False,
        edge=edge,
        fair_probability=historical_hit_rate,
        reason="shadow-only until point-in-time calibration is available",
    )
