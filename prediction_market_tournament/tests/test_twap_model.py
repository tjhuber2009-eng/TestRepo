import json
from decimal import Decimal

from tournament.adapters.rtds import (
    TOPIC_TWAP60,
    parse_frame,
    server_error_message,
    subscribe_frame,
)
from tournament.twap_model import (
    final_twap_distribution,
    time_weighted_mean,
)


def test_future_full_window_variance():
    distribution = final_twap_distribution(
        strike=100,
        current_spot=101,
        sigma_per_sqrt_second=1,
        seconds_remaining=120,
        window_seconds=60,
    )
    assert round(distribution.effective_variance_seconds, 6) == 80.0
    assert distribution.probability_above_strike > 0.5


def test_partial_window_variance_collapses_near_close():
    distribution = final_twap_distribution(
        strike=100,
        current_spot=101,
        sigma_per_sqrt_second=1,
        seconds_remaining=30,
        window_seconds=60,
        known_window_mean=100,
    )
    assert round(distribution.effective_variance_seconds, 6) == 2.5
    assert distribution.mean == 100.5


def test_time_weighted_mean_carry_forward():
    points = [(0, 100), (1000, 102), (3000, 104)]
    assert (
        time_weighted_mean(points, start_ms=0, end_ms=4000)
        == 102.0
    )


def test_time_weighted_mean_never_backfills_from_future_tick():
    points = [(1000, 102), (3000, 104)]
    assert time_weighted_mean(points, start_ms=0, end_ms=4000) is None


def test_parse_exact_e18_twap():
    raw = (
        '{"topic":"crypto_prices_twap_sixty","type":"update",'
        '"payload":{"symbol":"btc/usd","timestamp":1234,'
        '"value":"81234.5",'
        '"full_accuracy_value":"81234500000000000000000",'
        '"window_s":60}}'
    )
    tick = parse_frame(raw, receive_timestamp_ms=2000)
    assert tick is not None
    assert tick.topic == TOPIC_TWAP60
    assert tick.value == Decimal("81234.5")
    assert tick.window_seconds == 60


def test_subscribe_uses_current_topics_and_compact_string_filters():
    subscription = json.loads(subscribe_frame("btc/usd"))
    rows = subscription["subscriptions"]
    assert {row["topic"] for row in rows} == {
        "crypto_prices_chainlink",
        "crypto_prices_twap_sixty",
    }
    assert all(
        row["filters"] == '{"symbol":"btc/usd"}'
        and isinstance(row["filters"], str)
        for row in rows
    )


def test_rtds_server_error_is_not_silently_dropped():
    raw = '{"message":"Invalid request body","connectionId":"x"}'
    assert server_error_message(raw) == "Invalid request body"
