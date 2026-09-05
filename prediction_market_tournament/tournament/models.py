from __future__ import annotations

from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from typing import Literal, Optional

OrderMode = Literal["taker", "maker", "shadow"]
Side = Literal["YES", "NO"]


@dataclass(frozen=True)
class Signal:
    signal_id: str
    lane: str
    market_id: str
    observed_at: datetime
    side: Side
    market_price: float
    fair_probability: float
    order_mode: OrderMode
    size_usd: float = 1.0
    fee_rate: float = 0.0
    notes: str = ""

    def __post_init__(self) -> None:
        if self.observed_at.tzinfo is None:
            raise ValueError("observed_at must be timezone-aware")
        for name, value in (
            ("market_price", self.market_price),
            ("fair_probability", self.fair_probability),
        ):
            if not 0.0 <= value <= 1.0:
                raise ValueError(f"{name} must be in [0,1]")
        if self.size_usd <= 0:
            raise ValueError("size_usd must be > 0")
        if self.fee_rate < 0:
            raise ValueError("fee_rate must be >= 0")

    def as_json(self) -> dict:
        d = asdict(self)
        d["observed_at"] = self.observed_at.astimezone(timezone.utc).isoformat()
        return d


@dataclass(frozen=True)
class ResolvedTrade:
    signal: Signal
    won: bool
    fill_price: float
    fee_usd: float
    payout_usd: float
    pnl_usd: float
    return_on_stake: float
    resolved_at: Optional[datetime] = None

    def as_json(self) -> dict:
        d = asdict(self)
        d["signal"] = self.signal.as_json()
        if self.resolved_at is not None:
            d["resolved_at"] = self.resolved_at.astimezone(timezone.utc).isoformat()
        return d
