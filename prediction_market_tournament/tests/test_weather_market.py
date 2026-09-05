from datetime import date, datetime, timezone

import pytest

import tournament.weather_market as weather
from tournament.adapters.polymarket import MarketExecutionRules
from tournament.weather_market import (
    extract_station_code,
    parse_temperature_bracket,
)


def test_station_code():
    assert (
        extract_station_code(
            "https://www.weather.gov/wrh/timeseries?site=klax"
        )
        == "KLAX"
    )


def test_station_code_from_current_wunderground_rule_url():
    assert (
        extract_station_code(
            "https://www.wunderground.com/history/daily/us/ca/los-angeles/KLAX"
        )
        == "KLAX"
    )


def test_range_f():
    bracket = parse_temperature_bracket(
        "Will the highest temperature in Los Angeles be between 70-71°F on September 4?"
    )
    assert (
        bracket.kind,
        bracket.unit,
        bracket.lower,
        bracket.upper,
    ) == ("max", "F", 70, 71)


def test_low_tail():
    bracket = parse_temperature_bracket(
        "Will the lowest temperature in Denver be 55°F or below on September 4?"
    )
    assert (
        bracket.kind == "min"
        and bracket.lower is None
        and bracket.upper == 55
    )


def test_high_tail_c():
    bracket = parse_temperature_bracket(
        "Will the highest temperature in Madrid be 38°C or higher on September 4?"
    )
    assert (
        bracket.kind == "max"
        and bracket.lower == 38
        and bracket.upper is None
    )


def _weather_market_fixture():
    event = {
        "description": (
            "Resolution station: "
            "https://www.weather.gov/wrh/timeseries?site=KLAX"
        )
    }
    market = {
        "id": "weather-1",
        "conditionId": "0xweather",
        "question": (
            "Will the highest temperature in Los Angeles be "
            "between 70-71°F on September 5?"
        ),
        "outcomes": '["Yes","No"]',
        "clobTokenIds": '["yes-token","no-token"]',
    }
    return event, market


def _patch_weather_dependencies(monkeypatch, values):
    monkeypatch.setattr(
        weather,
        "station_location",
        lambda _: (33.94, -118.40, 38.0),
    )
    monkeypatch.setattr(
        weather,
        "fetch_temperature_ensemble",
        lambda *args, **kwargs: {"payload": True},
    )
    monkeypatch.setattr(
        weather,
        "member_daily_extremes",
        lambda payload, kind: list(values),
    )
    monkeypatch.setattr(
        weather,
        "market_execution_rules",
        lambda _: MarketExecutionRules(
            fee_rate=0.0,
            fee_exponent=1.0,
            min_order_shares=1.0,
        ),
    )
    books = {
        "yes-token": {
            "market": "0xweather",
            "asset_id": "yes-token",
            "timestamp": "1",
            "hash": "yes-hash",
            "min_order_size": "1",
            "asks": [{"price": "0.50", "size": "100"}],
        },
        "no-token": {
            "market": "0xweather",
            "asset_id": "no-token",
            "timestamp": "1",
            "hash": "no-hash",
            "min_order_size": "1",
            "asks": [{"price": "0.50", "size": "100"}],
        },
    }
    monkeypatch.setattr(
        weather,
        "get_books",
        lambda tokens: [books[token] for token in tokens],
    )


def test_weather_chooses_yes_when_bracket_probability_is_high(monkeypatch):
    event, market = _weather_market_fixture()
    _patch_weather_dependencies(monkeypatch, [70, 70, 71, 71])
    signal = weather.weather_signal_from_market(
        market,
        event=event,
        target_date=date(2026, 9, 5),
        observed_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
        min_edge=0.05,
        cash_budget_usd=5.0,
    )
    assert signal is not None
    assert signal.side == "YES"
    assert signal.fair_probability == 0.9
    assert signal.metadata["fair_yes_probability"] == 0.9
    assert 4.99 < signal.size_usd + signal.entry_fee_usd <= 5.0


def test_weather_chooses_no_when_bracket_probability_is_low(monkeypatch):
    event, market = _weather_market_fixture()
    _patch_weather_dependencies(monkeypatch, [60, 61, 62, 63])
    signal = weather.weather_signal_from_market(
        market,
        event=event,
        target_date=date(2026, 9, 5),
        observed_at=datetime(2026, 9, 5, tzinfo=timezone.utc),
        min_edge=0.05,
        cash_budget_usd=5.0,
    )
    assert signal is not None
    assert signal.side == "NO"
    assert signal.fair_probability == 0.9
    assert signal.metadata["fair_yes_probability"] == pytest.approx(0.1)
