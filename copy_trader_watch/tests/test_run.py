import importlib.util
import sys
from datetime import datetime, timezone
from pathlib import Path

import requests

PROJECT_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_DIR))

import watch  # noqa: E402

spec = importlib.util.spec_from_file_location("run_module", PROJECT_DIR / "run.py")
run = importlib.util.module_from_spec(spec)
assert spec and spec.loader
sys.modules["run_module"] = run
spec.loader.exec_module(run)


def payload(ytd=21.0, timestamp="2026-09-04T02:00:00Z"):
    return {
        "success": True,
        "data": {
            "timestamp": timestamp,
            "portfolio": {
                "username": "alice",
                "fullName": "Alice Example",
                "country": 219,
                "ytdReturn": ytd,
                "riskScore": 4,
                "trades": 123,
                "winRatio": 62.5,
                "totalValue": 100.0,
                "positionCount": 3,
                "cashPercent": 12.0,
                "topPositions": [
                    {"instrumentId": 1, "symbol": "AAA", "marketValue": 30.0},
                    {"instrumentId": 2, "symbol": "BBB", "marketValue": 20.0},
                ],
            },
        },
    }


def test_build_investor_record_maps_public_api(monkeypatch):
    monkeypatch.setattr(run, "previous_observation", lambda username, date: ("2026-09-03", 10.0))
    record = run.build_investor_record("alice", payload(), "2026-09-04")
    assert record["userName"] == "alice"
    assert round(record["dailyGain"], 8) == 10.0
    assert record["riskScore"] == 4
    assert record["sourceTimestamp"] == "2026-09-04T02:00:00Z"
    assert record["portfolio"]["positions"][0]["investmentPct"] == 30.0
    top1, top2, count = watch.concentration(record)
    assert (top1, top2, count) == (30.0, 50.0, 2)


def test_build_investor_record_year_boundary_uses_new_ytd(monkeypatch):
    monkeypatch.setattr(run, "previous_observation", lambda username, date: ("2026-12-31", 35.0))
    record = run.build_investor_record("alice", payload(ytd=4.0), "2027-01-02")
    assert record["dailyGain"] == 4.0


def test_build_investor_record_requires_ytd(monkeypatch):
    monkeypatch.setattr(run, "previous_observation", lambda username, date: None)
    bad = {"success": True, "data": {"portfolio": {"username": "alice"}}}
    try:
        run.build_investor_record("alice", bad, "2026-09-04")
    except ValueError as exc:
        assert "no YTD return" in str(exc)
    else:
        raise AssertionError("Expected missing YTD return to fail")


def test_last_yahoo_quote_uses_latest_non_null_and_date():
    ts1 = int(datetime(2026, 9, 3, 20, tzinfo=timezone.utc).timestamp())
    ts2 = int(datetime(2026, 9, 4, 20, tzinfo=timezone.utc).timestamp())
    payload_data = {
        "chart": {
            "result": [
                {
                    "timestamp": [ts1, ts2],
                    "indicators": {"quote": [{"close": [500.0, 505.5]}]},
                }
            ]
        }
    }
    quote = run.last_yahoo_quote(payload_data)
    assert quote == {"close": 505.5, "as_of": "2026-09-04", "source": "yahoo"}


def test_fetch_benchmark_prefers_yahoo(monkeypatch):
    ts = int(datetime(2026, 9, 4, 20, tzinfo=timezone.utc).timestamp())

    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "chart": {
                    "result": [
                        {
                            "timestamp": [ts],
                            "indicators": {"quote": [{"close": [505.5]}]},
                        }
                    ]
                }
            }

    monkeypatch.setattr(run.requests, "get", lambda *args, **kwargs: Response())
    quote = run.fetch_benchmark_quote(
        "spy.us",
        lambda symbol: {"close": 400.0, "as_of": "2026-09-03", "source": "stooq"},
    )
    assert quote["close"] == 505.5
    assert quote["source"] == "yahoo"


def test_fetch_benchmark_falls_back_to_stooq(monkeypatch):
    def fail(*args, **kwargs):
        raise requests.RequestException("network")

    monkeypatch.setattr(run.requests, "get", fail)
    fallback = {"close": 444.0, "as_of": "2026-09-03", "source": "stooq"}
    assert run.fetch_benchmark_quote("qqq.us", lambda symbol: fallback) == fallback


def test_unresolved_candidate_records_reason(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "fetch_public_user", lambda *args, **kwargs: (_ for _ in ()).throw(requests.RequestException("unavailable")))
    monkeypatch.setattr(run, "census_fallback_due", lambda *args, **kwargs: False)
    monkeypatch.setattr(watch, "STATE_PATH", tmp_path / "state.json")
    census = run.build_runtime_census(["alice"], "fallback", 7)
    assert census["investors"] == []
    assert "alice" in census["metadata"]["unresolved"]
    assert "per-user lookup failed" in census["metadata"]["unresolved"]["alice"]


def test_census_fallback_is_throttled_for_never_resolved(monkeypatch, tmp_path):
    monkeypatch.setattr(watch, "HISTORY_PATH", tmp_path / "history.json")
    watch.save_json(watch.HISTORY_PATH, [])
    state = {"last_census_fallback_attempt_date": "2026-09-01"}
    assert not run.census_fallback_due(["alice"], "2026-09-03", 7, state)
    assert run.census_fallback_due(["alice"], "2026-09-08", 7, state)


def test_census_fallback_runs_immediately_for_previously_resolved(monkeypatch, tmp_path):
    monkeypatch.setattr(watch, "HISTORY_PATH", tmp_path / "history.json")
    watch.save_json(
        watch.HISTORY_PATH,
        [{"date": "2026-09-01", "candidates": {"alice": {"present": True, "gain_ytd_pct": 10.0}}}],
    )
    state = {"last_census_fallback_attempt_date": "2026-09-03"}
    assert run.census_fallback_due(["alice"], "2026-09-03", 7, state)


def test_fallback_candidate_gets_source_timestamp(monkeypatch, tmp_path):
    monkeypatch.setattr(run, "fetch_public_user", lambda *args, **kwargs: (_ for _ in ()).throw(requests.RequestException("unavailable")))
    monkeypatch.setattr(run, "census_fallback_due", lambda *args, **kwargs: True)
    monkeypatch.setattr(watch, "STATE_PATH", tmp_path / "state.json")
    monkeypatch.setattr(
        watch,
        "fetch_json",
        lambda url: {
            "metadata": {"collectedAt": "2026-09-03T03:34:59Z"},
            "investors": [{"userName": "alice", "gain": 10, "portfolio": {"positions": []}}],
        },
    )
    census = run.build_runtime_census(["alice"], "fallback", 7)
    candidate = census["investors"][0]
    assert candidate["source"] == "etoro-census-top1500-fallback"
    assert candidate["sourceTimestamp"] == "2026-09-03T03:34:59Z"
    assert "alice" not in census["metadata"]["unresolved"]


def test_runtime_census_uses_pacific_observation_date(monkeypatch):
    monkeypatch.setattr(run, "fetch_public_user", lambda username, run_date: {
        "userName": username,
        "gain": 1.0,
        "portfolio": {"positions": []},
    })
    census = run.build_runtime_census(["alice"], "fallback", 7)
    observation_date = census["metadata"]["observationDate"]
    assert len(observation_date) == 10
    assert census["metadata"]["observationTimezone"] == "America/Los_Angeles"
