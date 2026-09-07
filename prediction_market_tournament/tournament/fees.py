from __future__ import annotations

FEE_RATES = {
    "crypto": 0.07,
    "sports": 0.03,
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


def polymarket_taker_fee_usd(
    shares: float,
    price: float,
    fee_rate: float,
    fee_exponent: float = 1.0,
) -> float:
    """Platform fee from Polymarket's market fee schedule."""
    if shares < 0:
        raise ValueError("shares must be >= 0")
    if not 0 <= price <= 1:
        raise ValueError("price must be in [0,1]")
    if fee_rate < 0 or fee_exponent < 0:
        raise ValueError("fee rate/exponent must be >= 0")
    effective_rate = fee_rate * ((price * (1.0 - price)) ** fee_exponent)
    return round(shares * effective_rate, 5)


def shares_for_stake(stake_usd: float, price: float) -> float:
    if stake_usd <= 0:
        raise ValueError("stake_usd must be > 0")
    if not 0 < price <= 1:
        raise ValueError("price must be in (0,1]")
    return stake_usd / price


def effective_taker_cost_per_share(
    price: float,
    fee_rate: float,
    fee_exponent: float = 1.0,
) -> float:
    if not 0 < price <= 1:
        raise ValueError("price must be in (0,1]")
    return price + fee_rate * ((price * (1.0 - price)) ** fee_exponent)


def expected_value_per_share(
    fair_probability: float,
    price: float,
    fee_rate: float,
    fee_exponent: float = 1.0,
    *,
    maker: bool = False,
) -> float:
    if not 0 <= fair_probability <= 1:
        raise ValueError("fair_probability must be in [0,1]")
    effective = (
        price
        if maker
        else effective_taker_cost_per_share(price, fee_rate, fee_exponent)
    )
    return fair_probability - effective


def exact_execution_edge_per_share(
    fair_probability: float,
    *,
    shares: float,
    spent_usd: float,
    fee_usd: float,
) -> float:
    """EV edge per outcome share from the exact executable fill economics."""
    if not 0 <= fair_probability <= 1:
        raise ValueError("fair_probability must be in [0,1]")
    if shares <= 0 or spent_usd <= 0 or fee_usd < 0:
        raise ValueError("invalid execution economics")
    all_in_cost_per_share = (spent_usd + fee_usd) / shares
    return fair_probability - all_in_cost_per_share
