from models import TraderSnapshot, score_evidence, score_snapshot


def rec(**overrides):
    base = dict(
        platform="x",
        trader_id="1",
        name="one",
        observed_at="2026-09-03T00:00:00Z",
        source="test",
        source_quality=90.0,
        free=True,
        us_access="yes",
        live_evidence="real",
        return_pct=50.0,
        return_window="year",
        max_drawdown_pct=-10.0,
        profit_factor=1.5,
        trades=500,
        win_rate_pct=55.0,
        age_days=730,
        leverage=2.0,
        copyability_score=85.0,
    )
    base.update(overrides)
    return TraderSnapshot(**base)


def test_score_prefers_better_return_drawdown():
    assert score_snapshot(rec(return_pct=60, max_drawdown_pct=-10)) > score_snapshot(rec(return_pct=30, max_drawdown_pct=-20))


def test_score_penalizes_unknown_drawdown():
    assert score_snapshot(rec(max_drawdown_pct=None)) < score_snapshot(rec(max_drawdown_pct=-10))


def test_score_penalizes_extreme_leverage():
    assert score_snapshot(rec(leverage=50)) < score_snapshot(rec(leverage=2))


def test_seed_score_does_not_penalize_small_sample():
    tiny = rec(trades=3, age_days=2)
    huge = rec(trades=5000, age_days=1095)
    assert score_snapshot(tiny) == score_snapshot(huge)


def test_evidence_score_reports_sample_depth_separately():
    tiny = rec(trades=3, age_days=2)
    huge = rec(trades=5000, age_days=1095)
    assert score_evidence(tiny) < score_evidence(huge)
