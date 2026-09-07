from datetime import datetime, timedelta, timezone

from tournament.adapters.open_meteo import (
    bracket_probability,
)
from tournament.arbitrage import scan_complete_set
from tournament.fees import (
    expected_value_per_share,
    polymarket_taker_fee_usd,
)
from tournament.freeze import spec_hash
from tournament.lanes import (
    crypto_up_probability,
    late_resolution_decision,
    weather_ensemble_decision,
)
from tournament.models import Signal
from tournament.scoring import (
    settle_binary_signal,
    summarize,
)


def test_fee_formula_midpoint():
    assert (
        polymarket_taker_fee_usd(
            100, 0.50, 0.07
        )
        == 1.75
    )


def test_maker_has_more_ev_than_taker_same_price():
    assert expected_value_per_share(
        0.60,
        0.55,
        0.07,
        maker=True,
    ) > expected_value_per_share(
        0.60,
        0.55,
        0.07,
        maker=False,
    )


def test_weather_decision_accounts_for_fee():
    assert weather_ensemble_decision(
        0.70,
        0.60,
        fee_rate=0.05,
        min_edge=0.05,
    ).trade


def test_crypto_probability_moves_with_price():
    up = crypto_up_probability(
        start_twap=100,
        current_price=101,
        annualized_vol=0.50,
        seconds_remaining=60,
    )
    down = crypto_up_probability(
        start_twap=100,
        current_price=99,
        annualized_vol=0.50,
        seconds_remaining=60,
    )
    assert up > 0.5 > down


def test_late_resolution_gate():
    assert late_resolution_decision(
        0.98,
        0.90,
        seconds_remaining=30,
    ).trade
    assert not late_resolution_decision(
        0.98,
        0.90,
        seconds_remaining=90,
    ).trade


def test_small_sample_is_scored_not_rejected():
    signal = Signal(
        signal_id="x",
        lane="weather",
        market_id="m",
        observed_at=datetime.now(timezone.utc),
        side="YES",
        market_price=0.40,
        fair_probability=0.60,
        order_mode="taker",
        size_usd=10,
        fee_rate=0.05,
    )
    metrics = summarize(
        [settle_binary_signal(signal, True)]
    )
    assert (
        metrics.trades == 1
        and metrics.net_pnl_usd > 0
    )


def test_complete_set():
    opportunity = scan_complete_set(
        "e",
        [0.20, 0.30, 0.45],
        min_edge=0.01,
    )
    assert (
        opportunity.trade
        and round(
            opportunity.gross_edge, 6
        )
        == 0.05
    )


def test_ensemble_bracket_smoothing():
    probability = bracket_probability(
        [68, 69, 70, 71],
        lower=68,
        upper=69,
    )
    assert 0 < probability < 1


def test_temperature_probability_respects_whole_degree_bins():
    probability = bracket_probability(
        [75.49, 75.50, 77.49, 77.50],
        lower=76,
        upper=77,
    )
    # 75.50 and 77.49 map into whole-degree outcomes 76/77.
    assert probability == 0.5


def test_spec_hash_stable():
    assert spec_hash(
        {"b": 2, "a": 1}
    ) == spec_hash(
        {"a": 1, "b": 2}
    )


def test_capital_efficiency_counts_overlapping_notional():
    base = datetime(
        2026,
        9,
        5,
        tzinfo=timezone.utc,
    )

    def signal(i, minute):
        return Signal(
            signal_id=str(i),
            lane="x",
            market_id=f"m{i}",
            observed_at=(
                base
                + timedelta(minutes=minute)
            ),
            side="YES",
            market_price=0.50,
            fair_probability=0.75,
            order_mode="taker",
            size_usd=10,
            fee_rate=0.0,
        )

    first = settle_binary_signal(
        signal(1, 0),
        True,
        resolved_at=(
            base + timedelta(minutes=10)
        ),
    )
    second = settle_binary_signal(
        signal(2, 1),
        True,
        resolved_at=(
            base + timedelta(minutes=11)
        ),
    )
    metrics = summarize([first, second])
    # Net $20 / peak overlapping
    # committed capital $20 = 1.0.
    assert metrics.capital_efficiency == 1.0
