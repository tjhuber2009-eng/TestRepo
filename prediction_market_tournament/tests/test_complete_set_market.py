from datetime import datetime, timezone

import tournament.complete_set_market as csm


def _market(q: str, token: str, neg=True):
    return {
        "question": q,
        "outcomes": '["Yes","No"]',
        "clobTokenIds": f'["{token}","no-{token}"]',
        "negRisk": neg,
        "active": True,
        "closed": False,
        "acceptingOrders": True,
    }


def test_requires_multiple_neg_risk_markets():
    assert csm.is_neg_risk_multioutcome_event({"markets": [_market("A", "1")]}) is False
    assert csm.is_neg_risk_multioutcome_event({"markets": [_market("A", "1"), _market("B", "2")]}) is True
    assert csm.is_neg_risk_multioutcome_event({"markets": [_market("A", "1"), _market("B", "2", False)]}) is False


def test_snapshot_detects_complete_set_edge(monkeypatch):
    books = {"1": {"asks": [{"price": "0.40"}]}, "2": {"asks": [{"price": "0.55"}]}}
    monkeypatch.setattr(csm, "get_book", lambda token: books[token])
    event = {"id": "event-1", "markets": [_market("A", "1"), _market("B", "2")]}
    snap = csm.snapshot_complete_set(
        event,
        observed_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
        min_edge=0.01,
    )
    assert snap is not None
    assert snap.opportunity.trade is True
    assert round(snap.opportunity.gross_edge, 6) == 0.05


def test_missing_leg_book_rejects_snapshot(monkeypatch):
    books = {"1": {"asks": [{"price": "0.40"}]}, "2": {"asks": []}}
    monkeypatch.setattr(csm, "get_book", lambda token: books[token])
    event = {"id": "event-1", "markets": [_market("A", "1"), _market("B", "2")]}
    assert csm.snapshot_complete_set(event) is None
