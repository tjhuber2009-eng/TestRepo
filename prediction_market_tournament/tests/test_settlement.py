from datetime import datetime, timezone

import pytest

from tournament.models import Signal
from tournament.scoring import settle_binary_signal
from tournament.settlement import (
    read_jsonl,
    resolve_signal,
    signal_from_json,
    terminal_outcome,
)


def _signal(side="YES"):
    return Signal(
        "s1",
        "lane",
        "m1",
        datetime(2026, 9, 5, tzinfo=timezone.utc),
        side,
        0.4,
        0.6,
        "taker",
        1.0,
        0.05,
    )


def test_terminal_outcome_requires_closed_one_hot():
    market = {
        "closed": True,
        "outcomes": '["Yes","No"]',
        "outcomePrices": '["1","0"]',
    }
    assert terminal_outcome(market) == "Yes"
    assert terminal_outcome({**market, "closed": False}) is None
    assert (
        terminal_outcome(
            {**market, "outcomePrices": '["0.5","0.5"]'}
        )
        is None
    )


def test_proposed_or_disputed_market_is_not_final():
    market = {
        "closed": True,
        "umaResolutionStatus": "proposed",
        "outcomes": '["Yes","No"]',
        "outcomePrices": '["1","0"]',
    }
    assert terminal_outcome(market) is None
    assert (
        terminal_outcome(
            {**market, "umaResolutionStatus": "disputed"}
        )
        is None
    )
    assert (
        terminal_outcome(
            {**market, "umaResolutionStatus": "resolved"}
        )
        == "Yes"
    )


def test_resolve_signal_matches_side_case_insensitive():
    market = {
        "closed": True,
        "outcomes": '["Up","Down"]',
        "outcomePrices": '["0","1"]',
        "closedTime": "2026-09-05T00:05:00Z",
        "updatedAt": "2026-09-05T00:07:00Z",
        "umaResolutionStatus": "resolved",
    }
    trade = resolve_signal(_signal("DOWN"), market)
    assert trade is not None and trade.won and trade.resolved_at is not None
    assert trade.resolved_at.minute == 7


def test_signal_from_json_round_trip():
    signal = Signal(
        "exact",
        "lane",
        "m1",
        datetime(2026, 9, 5, tzinfo=timezone.utc),
        "YES",
        0.4,
        0.6,
        "taker",
        4.0,
        0.07,
        1.0,
        12.5,
        0.14,
    )
    row = {"kind": "signal", **signal.as_json()}
    parsed = signal_from_json(row)
    assert parsed.signal_id == signal.signal_id
    assert parsed.observed_at == signal.observed_at
    assert parsed.executed_shares == 12.5
    assert parsed.entry_fee_usd == 0.14


def test_exact_recorded_fee_and_shares_survive_settlement():
    signal = Signal(
        "exact",
        "lane",
        "m1",
        datetime(2026, 9, 5, tzinfo=timezone.utc),
        "YES",
        0.32,
        0.6,
        "taker",
        4.0,
        0.07,
        1.0,
        12.5,
        0.14,
    )
    trade = settle_binary_signal(signal, True)
    assert trade.fee_usd == 0.14
    assert trade.payout_usd == 12.5
    assert trade.pnl_usd == pytest.approx(8.36)
    assert trade.return_on_stake == pytest.approx(8.36 / 4.14)


def test_signal_from_json_rejects_missing_observation_time():
    with pytest.raises(ValueError):
        signal_from_json(
            {
                "signal_id": "x",
                "lane": "x",
                "market_id": "m",
                "side": "YES",
                "market_price": 0.5,
                "fair_probability": 0.6,
                "order_mode": "taker",
            }
        )


def test_read_jsonl_fails_closed_on_malformed_row(tmp_path):
    path = tmp_path / "signals.jsonl"
    path.write_text(
        '{"kind":"signal"}\n{"broken":\n',
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="malformed JSONL"):
        read_jsonl(path)


def test_read_jsonl_rejects_non_object_row(tmp_path):
    path = tmp_path / "signals.jsonl"
    path.write_text('["not","an","object"]\n', encoding="utf-8")
    with pytest.raises(ValueError, match="non-object JSONL"):
        read_jsonl(path)
