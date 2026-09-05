from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class CompleteSetOpportunity:
    event_id: str
    outcome_count: int
    total_ask: float
    gross_edge: float
    trade: bool
    reason: str


def scan_complete_set(
    event_id: str,
    yes_asks: list[float],
    *,
    min_edge: float = 0.01,
    require_exhaustive: bool = True,
) -> CompleteSetOpportunity:
    if not yes_asks:
        return CompleteSetOpportunity(event_id, 0, 0, 0, False, "no outcomes")
    if any(not 0 < p <= 1 for p in yes_asks):
        raise ValueError("all asks must be in (0,1]")
    total = sum(yes_asks)
    edge = 1.0 - total
    trade = require_exhaustive and edge >= min_edge
    return CompleteSetOpportunity(
        event_id=event_id,
        outcome_count=len(yes_asks),
        total_ask=total,
        gross_edge=edge,
        trade=trade,
        reason=(
            f"complete-set total={total:.4f}, gross_edge={edge:.4f}, "
            f"exhaustive={require_exhaustive}"
        ),
    )
