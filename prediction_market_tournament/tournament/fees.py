from __future__ import annotations

FEE_RATES = {
    "crypto": 0.07,
    "sports": 0.05,
    "finance": 0.04,
    "politics": 0.04,
    "economics": 0.05,
    "culture": 0.05,
    "weather": 0.05,
    "other": 0.05,
    "mentions": 0.04,
    "tech": 0.04,
    "geopolitics": 0.0,
}


def polymarket_taker_fee_usd(shares: float, price: float, fee_rate: float) -> float:
    """Polymarket fee formula: shares * fee_rate * p * (1-p)."""
    if shares < 0:
        raise ValueError("shares must be >= 0")
    if not 0 <= price <= 1:
        raise ValueError("price must be in [0,1]")
    if fee_rate < 0:
        raise ValueError("fee_rate must be >= 0")
    return round(shares * fee_rate * price * (1.0 - price), 5)


def shares_for_stake(stake_usd: float, price: float) -> float:
    if stake_usd <= 0:
        raise ValueError("stake_usd must be > 0")
    if not 0 < price <= 1:
        raise ValueError("price must be in (0,1]")
    return stake_usd / price


def effective_taker_cost_per_share(price: float, fee_rate: float) -> float:
    if not 0 < price <= 1:
        raise ValueError("price must be in (0,1]")
    return price + fee_rate * price * (1.0 - price)


def expected_value_per_share(
    fair_probability: float,
    price: float,
    fee_rate: float,
    *,
    maker: bool = False,
) -> float:
    if not 0 <= fair_probability <= 1:
        raise ValueError("fair_probability must be in [0,1]")
    effective = price if maker else effective_taker_cost_per_share(price, fee_rate)
    return fair_probability - effective
