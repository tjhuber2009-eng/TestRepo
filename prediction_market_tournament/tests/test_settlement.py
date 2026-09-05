from datetime import datetime, timezone

import pytest

from tournament.models import Signal
from tournament.settlement import (
    resolve_signal,
    signal_from_json,
    terminal_outcome,
)


def _signal(side="YES"):
    return Signal(
        "s1",
        "lane",
        "m1",
        datetime(
            2026,
            9,
            5,
            tzinfo=timezone.utc,
        ),
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
    assert (
        terminal_outcome(
            {**market, "closed": False}
        )
        is None
    )
    assert (
        terminal_outcome(
            {
                **market,
                "outcomePrices": (
                    '["0.5","0.5"]'
                ),
            }
        )
        is None
    )


def test_resolve_signal_matches_side_case_insensitive():
    market = {
        "closed": True,
        "outcomes": '["Up","Down"]',
        "outcomePrices": '["0","1"]',
        "closedTime": (
            "2026-09-05T00:05:00Z"
        ),
    }
    trade = resolve_signal(
        _signal("DOWN"), market
    )
    assert (
        trade is not None
        and trade.won
        and trade.resolved_at is not None
    )


def test_signal_from_json_round_trip():
    signal = _signal()
    row = {
        "kind": "signal",
        **signal.as_json(),
    }
    parsed = signal_from_json(row)
    assert parsed.signal_id == signal.signal_id
    assert (
        parsed.observed_at
        == signal.observed_at
    )


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
