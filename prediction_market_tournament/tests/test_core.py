from datetime import datetime, timezone

from tournament.fees import expected_value_per_share, polymarket_taker_fee_usd
from tournament.freeze import spec_hash
from tournament.lanes import crypto_up_probability, late_resolution_decision, weather_ensemble_decision
from tournament.models import Signal
from tournament.scoring import settle_binary_signal, summarize
from tournament.arbitrage import scan_complete_set
from tournament.adapters.open_meteo import bracket_probability


def test_fee_formula_midpoint():
    assert polymarket_taker_fee_usd(100, 0.50, 0.07) == 1.75


def test_maker_has_more_ev_than_taker_same_price():
    assert expected_value_per_share(0.60, 0.55, 0.07, maker=True) > expected_value_per_share(0.60, 0.55, 0.07, maker=False)


def test_weather_decision_accounts_for_fee():
    assert weather_ensemble_decision(0.70, 0.60, fee_rate=0.05, min_edge=0.05).trade


def test_crypto_probability_moves_with_price():
    up = crypto_up_probability(start_twap=100, current_price=101, annualized_vol=0.50, seconds_remaining=60)
    down = crypto_up_probability(start_twap=100, current_price=99, annualized_vol=0.50, seconds_remaining=60)
    assert up > 0.5 > down


def test_late_resolution_gate():
    assert late_resolution_decision(0.98, 0.90, seconds_remaining=30).trade
    assert not late_resolution_decision(0.98, 0.90, seconds_remaining=90).trade


def test_small_sample_is_scored_not_rejected():
    s = Signal(
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
    m = summarize([settle_binary_signal(s, True)])
    assert m.trades == 1 and m.net_pnl_usd > 0


def test_complete_set():
    op = scan_complete_set("e", [0.20, 0.30, 0.45], min_edge=0.01)
    assert op.trade and round(op.gross_edge, 6) == 0.05


def test_ensemble_bracket_smoothing():
    p = bracket_probability([68, 69, 70, 71], lower=68, upper=69)
    assert 0 < p < 1


def test_spec_hash_stable():
    assert spec_hash({"b": 2, "a": 1}) == spec_hash({"a": 1, "b": 2})
