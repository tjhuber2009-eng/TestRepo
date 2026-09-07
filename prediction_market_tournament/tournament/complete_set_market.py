from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime, timezone

from .adapters.polymarket import best_ask, get_book, parse_jsonish_list
from .arbitrage import CompleteSetOpportunity, scan_complete_set


@dataclass(frozen=True)
class CompleteSetSnapshot:
    event_id: str
    observed_at: datetime
    questions: tuple[str, ...]
    yes_token_ids: tuple[str, ...]
    yes_asks: tuple[float, ...]
    opportunity: CompleteSetOpportunity
    snapshot_id: str

    def as_json(self) -> dict:
        return {
            "kind": "complete_set_snapshot",
            "snapshot_id": self.snapshot_id,
            "event_id": self.event_id,
            "observed_at": self.observed_at.astimezone(timezone.utc).isoformat(),
            "questions": list(self.questions),
            "yes_token_ids": list(self.yes_token_ids),
            "yes_asks": list(self.yes_asks),
            "outcome_count": self.opportunity.outcome_count,
            "total_ask": self.opportunity.total_ask,
            "gross_edge": self.opportunity.gross_edge,
            "trade": self.opportunity.trade,
            "reason": self.opportunity.reason,
        }


def _yes_token_id(market: dict) -> str:
    outcomes = parse_jsonish_list(market.get("outcomes"))
    token_ids = parse_jsonish_list(market.get("clobTokenIds"))
    if len(outcomes) != len(token_ids):
        raise ValueError("outcomes/clobTokenIds mismatch")
    for outcome, token_id in zip(outcomes, token_ids):
        if str(outcome).strip().upper() == "YES":
            return str(token_id)
    raise ValueError("YES token not found")


def is_neg_risk_multioutcome_event(event: dict) -> bool:
    markets = [m for m in (event.get("markets") or []) if not m.get("closed")]
    if len(markets) < 2:
        return False
    return all(bool(m.get("negRisk")) for m in markets)


def snapshot_complete_set(
    event: dict,
    *,
    observed_at: datetime | None = None,
    min_edge: float = 0.01,
) -> CompleteSetSnapshot | None:
    if not is_neg_risk_multioutcome_event(event):
        return None
    observed_at = observed_at or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")

    questions: list[str] = []
    token_ids: list[str] = []
    asks: list[float] = []
    markets = [m for m in (event.get("markets") or []) if not m.get("closed")]
    for market in markets:
        if market.get("active") is False or market.get("acceptingOrders") is False:
            return None
        token = _yes_token_id(market)
        ask = best_ask(get_book(token))
        if ask is None:
            return None
        questions.append(str(market.get("question") or ""))
        token_ids.append(token)
        asks.append(float(ask))

    event_id = str(event.get("id") or event.get("slug") or "")
    if not event_id:
        raise ValueError("event id/slug missing")
    op = scan_complete_set(event_id, asks, min_edge=min_edge, require_exhaustive=True)
    raw = f"complete-set|{event_id}|{observed_at.astimezone(timezone.utc).isoformat()}|" + ",".join(
        f"{x:.8f}" for x in asks
    )
    snapshot_id = hashlib.sha256(raw.encode()).hexdigest()[:24]
    return CompleteSetSnapshot(
        event_id=event_id,
        observed_at=observed_at,
        questions=tuple(questions),
        yes_token_ids=tuple(token_ids),
        yes_asks=tuple(asks),
        opportunity=op,
        snapshot_id=snapshot_id,
    )
