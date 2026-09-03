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


def test_concentration_aggregates_multiple_lots():
    top1, top2, count = watch.concentration(investor())
    assert top1 == 35.0
    assert top2 == 60.0
    assert count == 3


def test_forward_return_from_ytd_compounds_correctly():
    # 10% YTD to 21% YTD is exactly another 10%: 1.21 / 1.10 = 1.10.
    assert round(watch.forward_return_from_ytd(10.0, 21.0), 8) == 10.0


def test_forward_drawdown_uses_peak_equity():
    dd = watch.max_drawdown_from_returns([0.0, 10.0, 5.0, 20.0, 8.0])
    # Peak 1.20 falling to 1.08 = -10%.
    assert round(dd, 8) == -10.0


def test_missing_candidate_alerts():
    history = [{"date": "2026-09-03", "candidates": {"alice": {"present": False}}}]
    alerts = watch.make_alerts(history, ["alice"], watch.Thresholds())
    assert any(a["type"] == "missing" for a in alerts)


def test_high_concentration_and_daily_loss_alerts():
    current = watch.snapshot_investor(
        investor(
            dailyGain=-6.0,
            portfolio={
                "positions": [
                    {"instrumentId": 1, "investmentPct": 60.0},
                    {"instrumentId": 2, "investmentPct": 20.0},
                ]
            },
        )
    )
    history = [{"date": "2026-09-03", "candidates": {"alice": current}}]
    kinds = {a["type"] for a in watch.make_alerts(history, ["alice"], watch.Thresholds())}
    assert "daily_loss" in kinds
    assert "top1_concentration" in kinds
    assert "top2_concentration" in kinds


def test_risk_jump_alert_after_previous_observation():
    a = watch.snapshot_investor(investor(riskScore=3))
    b = watch.snapshot_investor(investor(riskScore=6))
    history = [
        {"date": "2026-09-03", "candidates": {"alice": a}},
        {"date": "2026-09-04", "candidates": {"alice": b}},
    ]
    kinds = {a["type"] for a in watch.make_alerts(history, ["alice"], watch.Thresholds())}
    assert "risk_jump" in kinds


def test_idempotent_metrics_baseline():
    a = watch.snapshot_investor(investor(gain=10.0))
    b = watch.snapshot_investor(investor(gain=21.0))
    history = [
        {"date": "2026-09-03", "candidates": {"alice": a}, "benchmarks": {"SPY": 100}},
        {"date": "2026-09-04", "candidates": {"alice": b}, "benchmarks": {"SPY": 105}},
    ]
    metrics = watch.current_metrics(history, ["alice"])
    assert round(metrics[0]["forward_return_pct"], 8) == 10.0
    assert round(watch.benchmark_metrics(history)["SPY"], 8) == 5.0
