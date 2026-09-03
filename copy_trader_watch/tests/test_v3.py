import run_v3
from models import AdapterResult, TraderSnapshot


def snap(platform="x", actionable=False, free=True, us="yes", score=10):
    s = TraderSnapshot(
        platform=platform,
        trader_id="id",
        name="Name",
        observed_at="2026-09-03T00:00:00Z",
        source="test",
        free=free,
        us_access=us,
        live_evidence="real",
        return_pct=10,
        return_window="month",
        max_drawdown_pct=-5,
        actionable=actionable,
        actionable_reason="reason",
    )
    s.research_score = score
    s.evidence_score = 50
    s.rank_score = score
    return s


def test_all_records_sorts_score_before_forward_result():
    rows = run_v3.all_records([AdapterResult("a", "x", [snap(score=5), snap(score=20)])])
    assert rows[0].research_score == 20


def test_report_separates_practical_and_research():
    a = snap(platform="etoro", actionable=True, free=True, us="yes", score=20)
    b = snap(platform="hyperliquid", actionable=False, free=True, us="no", score=30)
    report = run_v3.build_report(
        [AdapterResult("etoro", "x", [a]), AdapterResult("hyperliquid", "x", [b])],
        {"report": {"top_n": 10}},
        "2026-09-03",
        {"tracked_candidates": 2},
    )
    assert "Free U.S.-practical candidates" in report
    assert "Historical discovery leaderboard" in report
    assert "etoro" in report
    assert "hyperliquid" in report


def test_etoro_v2_missing_history_returns_unavailable(monkeypatch):
    monkeypatch.setattr(run_v3, "load_json", lambda path, default: [])
    assert run_v3._v2_etoro_records().status == "unavailable"
