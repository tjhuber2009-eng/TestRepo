import importlib.util
import sys
from pathlib import Path

MODULE_PATH = Path(__file__).resolve().parents[1] / "watch.py"
spec = importlib.util.spec_from_file_location("watch", MODULE_PATH)
watch = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["watch"] = watch
spec.loader.exec_module(watch)


def investor(**overrides):
    base = {
        "userName": "alice",
        "fullName": "Alice",
        "gain": 20.0,
        "dailyGain": 1.0,
        "riskScore": 4,
        "copiers": 100,
        "trades": 50,
        "winRatio": 60.0,
        "country": "US",
        "source": "test",
        "sourceTimestamp": "2026-09-04T00:00:00Z",
        "portfolio": {
            "positions": [
                {"instrumentId": 1, "investmentPct": 20.0},
                {"instrumentId": 1, "investmentPct": 15.0},
                {"instrumentId": 2, "investmentPct": 25.0},
                {"instrumentId": 3, "investmentPct": 10.0},
            ],
            "totalValue": 95.0,
            "profitLossPercentage": 3.0,
            "positionsCount": 4,
        },
    }
    base.update(overrides)
    return base


def row(day, inv=None, benchmark=None, observed="2026-09-04T01:00:00Z"):
    return {
        "date": day,
        "source_collected_at": observed,
        "candidates": {"alice": watch.snapshot_investor(inv) if inv else {"present": False}},
        "benchmarks": {"SPY": benchmark} if benchmark is not None else {},
    }


def test_concentration_aggregates_multiple_lots():
    top1, top2, count = watch.concentration(investor())
    assert top1 == 35.0
    assert top2 == 60.0
    assert count == 3


def test_forward_return_from_ytd_compounds_correctly():
    assert round(watch.forward_return_from_ytd(10.0, 21.0), 8) == 10.0


def test_cross_year_curve_handles_ytd_reset():
    a = investor(gain=20.0)
    b = investor(gain=10.0)
    history = [
        row("2026-12-31", a),
        row("2027-01-02", b, observed="2027-01-02T20:00:00Z"),
    ]
    curve = watch.candidate_series(history, "alice")
    assert [round(x, 8) for x in curve] == [0.0, 10.0]


def test_multi_period_curve_chains_returns():
    history = [
        row("2026-09-03", investor(gain=10.0)),
        row("2026-09-04", investor(gain=21.0)),
        row("2026-09-05", investor(gain=33.1)),
    ]
    curve = watch.candidate_series(history, "alice")
    assert round(curve[-1], 8) == 21.0


def test_forward_drawdown_uses_peak_equity():
    dd = watch.max_drawdown_from_returns([0.0, 10.0, 5.0, 20.0, 8.0])
    assert round(dd, 8) == -10.0


def test_missing_candidate_requires_consecutive_runs():
    one = [{"date": "2026-09-03", "candidates": {"alice": {"present": False}}}]
    assert not any(
        a["type"] == "missing"
        for a in watch.make_alerts(one, ["alice"], watch.Thresholds())
    )

    two = one + [{"date": "2026-09-04", "candidates": {"alice": {"present": False}}}]
    assert any(
        a["type"] == "missing"
        for a in watch.make_alerts(two, ["alice"], watch.Thresholds())
    )


def test_missing_threshold_can_be_one():
    history = [{"date": "2026-09-03", "candidates": {"alice": {"present": False}}}]
    thresholds = watch.Thresholds(missing_consecutive_runs=1)
    assert any(a["type"] == "missing" for a in watch.make_alerts(history, ["alice"], thresholds))


def test_high_concentration_and_daily_loss_alerts():
    current = investor(
        dailyGain=-6.0,
        portfolio={
            "positions": [
                {"instrumentId": 1, "investmentPct": 60.0},
                {"instrumentId": 2, "investmentPct": 20.0},
            ]
        },
    )
    history = [row("2026-09-03", current)]
    kinds = {a["type"] for a in watch.make_alerts(history, ["alice"], watch.Thresholds())}
    assert "daily_loss" in kinds
    assert "top1_concentration" in kinds
    assert "top2_concentration" in kinds


def test_risk_jump_alert_after_previous_observation():
    history = [
        row("2026-09-03", investor(riskScore=3)),
        row("2026-09-04", investor(riskScore=6)),
    ]
    kinds = {a["type"] for a in watch.make_alerts(history, ["alice"], watch.Thresholds())}
    assert "risk_jump" in kinds


def test_stale_candidate_source_alert():
    current = investor(sourceTimestamp="2026-09-01T00:00:00Z")
    history = [row("2026-09-03", current, observed="2026-09-03T00:00:00Z")]
    kinds = {a["type"] for a in watch.make_alerts(history, ["alice"], watch.Thresholds())}
    assert "stale_source" in kinds


def test_score_waits_for_minimum_observations():
    history = [
        row("2026-09-03", investor(gain=10.0)),
        row("2026-09-04", investor(gain=11.0)),
    ]
    metrics = watch.current_metrics(history, ["alice"], min_score_observations=5)
    assert metrics[0]["research_score"] is None
    assert metrics[0]["observation_count"] == 2


def test_score_activates_at_minimum_observations():
    history = []
    for i, gain in enumerate([10, 11, 12, 13, 14], start=3):
        history.append(row(f"2026-09-{i:02d}", investor(gain=gain)))
    metrics = watch.current_metrics(history, ["alice"], min_score_observations=5)
    assert metrics[0]["research_score"] is not None


def test_benchmark_metrics_preserve_metadata():
    history = [
        row("2026-09-03", investor(), {"close": 100.0, "as_of": "2026-09-03", "source": "yahoo"}),
        row("2026-09-04", investor(), {"close": 105.0, "as_of": "2026-09-04", "source": "yahoo"}),
    ]
    item = watch.benchmark_metrics(history)["SPY"]
    assert round(item["forward_return_pct"], 8) == 5.0
    assert item["as_of"] == "2026-09-04"
    assert item["source"] == "yahoo"


def test_benchmark_metrics_accept_legacy_numeric_history():
    history = [
        row("2026-09-03", investor(), 100.0),
        row("2026-09-04", investor(), 105.0),
    ]
    assert round(watch.benchmark_metrics(history)["SPY"]["forward_return_pct"], 8) == 5.0


def test_collected_date_prefers_explicit_observation_date():
    census = {
        "metadata": {
            "observationDate": "2026-09-03",
            "collectedAt": "2026-09-04T02:30:00Z",
        }
    }
    assert watch.collected_date(census) == "2026-09-03"


def test_snapshot_retains_source_provenance():
    snapshot = watch.snapshot_investor(investor())
    assert snapshot["source"] == "test"
    assert snapshot["source_timestamp"] == "2026-09-04T00:00:00Z"
