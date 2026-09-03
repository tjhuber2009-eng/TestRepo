import importlib.util
import sys
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


def test_build_investor_record_maps_public_api(monkeypatch):
    monkeypatch.setattr(run, "previous_ytd", lambda username, date: 10.0)
    payload = {
        "success": True,
        "data": {
            "portfolio": {
                "username": "alice",
                "fullName": "Alice Example",
                "country": 219,
                "ytdReturn": 21.0,
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
            }
        },
    }
    record = run.build_investor_record("alice", payload, "2026-09-04")
    assert record["userName"] == "alice"
    assert round(record["dailyGain"], 8) == 10.0
    assert record["riskScore"] == 4
    assert record["portfolio"]["positions"][0]["investmentPct"] == 30.0
    top1, top2, count = watch.concentration(record)
    assert (top1, top2, count) == (30.0, 50.0, 2)


def test_build_investor_record_requires_ytd(monkeypatch):
    monkeypatch.setattr(run, "previous_ytd", lambda username, date: None)
    payload = {"success": True, "data": {"portfolio": {"username": "alice"}}}
    try:
        run.build_investor_record("alice", payload, "2026-09-04")
    except ValueError as exc:
        assert "no YTD return" in str(exc)
    else:
        raise AssertionError("Expected missing YTD return to fail")


def test_last_yahoo_close_uses_latest_non_null():
    payload = {
        "chart": {
            "result": [
                {
                    "indicators": {
                        "quote": [{"close": [100.0, 101.5, None, 103.25, None]}]
                    }
                }
            ]
        }
    }
    assert run.last_yahoo_close(payload) == 103.25


def test_fetch_benchmark_prefers_yahoo(monkeypatch):
    class Response:
        def raise_for_status(self):
            return None

        def json(self):
            return {
                "chart": {
                    "result": [
                        {"indicators": {"quote": [{"close": [500.0, 505.5]}]}}
                    ]
                }
            }

    monkeypatch.setattr(run.requests, "get", lambda *args, **kwargs: Response())
    assert run.fetch_benchmark_close("spy.us", lambda symbol: 400.0) == 505.5


def test_fetch_benchmark_falls_back_to_stooq(monkeypatch):
    def fail(*args, **kwargs):
        raise requests.RequestException("network")

    monkeypatch.setattr(run.requests, "get", fail)
    assert run.fetch_benchmark_close("qqq.us", lambda symbol: 444.0) == 444.0


def test_unresolved_candidate_stays_absent_for_real_missing_alert(monkeypatch):
    def fail_user(username, run_date):
        raise requests.RequestException("unavailable")

    monkeypatch.setattr(run, "fetch_public_user", fail_user)
    monkeypatch.setattr(watch, "fetch_json", lambda url: {"investors": []})
    census = run.build_runtime_census(["alice"], "fallback")
    assert census["investors"] == []
    assert watch.find_investor(census, "alice") is None
