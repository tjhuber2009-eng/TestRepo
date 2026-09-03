from forward import update_tracker
from models import AdapterResult, TraderSnapshot


def rec(day: str, value: float, *, trades: int = 1, period: str = "all", kind: str = "cumulative_pct", base=None):
    return TraderSnapshot(
        platform="test",
        trader_id="alpha",
        name="Alpha",
        observed_at=f"{day}T12:00:00Z",
        source="test",
        source_quality=90,
        free=True,
        us_access="yes",
        live_evidence="real",
        return_pct=value if kind == "cumulative_pct" else 25.0,
        return_window="all",
        max_drawdown_pct=-10,
        profit_factor=1.5,
        trades=trades,
        age_days=1 if trades == 1 else 1000,
        copyability_score=80,
        metadata={
            "forward_metric_kind": kind,
            "forward_metric_value": value,
            "forward_metric_base": base,
            "forward_period_key": period,
        },
    )


def result(record):
    return [AdapterResult(platform=record.platform, observed_at=record.observed_at, records=[record])]


def test_two_observations_create_forward_rank():
    state = update_tracker(result(rec("2026-09-03", 10)), {}, "2026-09-03")
    second = rec("2026-09-04", 21)
    state = update_tracker(result(second), state, "2026-09-04")
    assert second.forward_observations == 2
    assert round(second.forward_return_pct, 8) == 10.0
    assert second.forward_max_drawdown_pct == 0.0
    assert second.forward_score is not None


def test_forward_score_is_not_discounted_for_small_historical_sample():
    small_state = update_tracker(result(rec("2026-09-03", 10, trades=1)), {}, "2026-09-03")
    small = rec("2026-09-04", 21, trades=1)
    update_tracker(result(small), small_state, "2026-09-04")

    large_state = update_tracker(result(rec("2026-09-03", 10, trades=5000)), {}, "2026-09-03")
    large = rec("2026-09-04", 21, trades=5000)
    update_tracker(result(large), large_state, "2026-09-04")

    assert small.forward_score == large.forward_score
    assert small.evidence_score < large.evidence_score


def test_period_reset_chains_new_period_return_instead_of_comparing_reset_values():
    first = rec("2026-12-31", 50, period="2026")
    state = update_tracker(result(first), {}, "2026-12-31")
    second = rec("2027-01-02", 10, period="2027")
    update_tracker(result(second), state, "2027-01-02")
    assert round(second.forward_return_pct, 8) == 10.0


def test_pnl_index_uses_prior_observed_capital_base():
    first = rec("2026-09-03", 100, kind="pnl_index", base=1000)
    state = update_tracker(result(first), {}, "2026-09-03")
    second = rec("2026-09-04", 110, kind="pnl_index", base=1100)
    update_tracker(result(second), state, "2026-09-04")
    assert round(second.forward_return_pct, 8) == 1.0


def test_same_date_rerun_replaces_observation_not_sample_count():
    first = rec("2026-09-03", 10)
    state = update_tracker(result(first), {}, "2026-09-03")
    rerun = rec("2026-09-03", 12)
    state = update_tracker(result(rerun), state, "2026-09-03")
    track = state["tracks"]["test:alpha"]
    assert len(track["observations"]) == 1
    assert track["observations"][0]["value"] == 12
