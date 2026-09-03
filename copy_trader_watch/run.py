#!/usr/bin/env python3
"""Production entrypoint for Copy Trader Watch.

Candidate source:
1. public per-user endpoint exposed by weirdapps/etoro_census;
2. throttled top-1,500 census fallback when useful.

Benchmark source:
1. Yahoo Finance public chart endpoint;
2. Stooq fallback.

No broker login or trade execution is performed.
"""
from __future__ import annotations

import json
from datetime import date, datetime, timezone
from typing import Any
from urllib.parse import quote
from zoneinfo import ZoneInfo

import requests

import watch

PUBLIC_USER_ENDPOINT = "https://etoro-census.vercel.app/api/public/{username}"
YAHOO_CHART_ENDPOINT = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
USER_AGENT = "Mozilla/5.0 (compatible; copy-trader-watch/2.0; +https://github.com/)"
PACIFIC = ZoneInfo("America/Los_Angeles")


def _number(value: Any) -> float | None:
    try:
        number = float(value) if value is not None else None
        if number is None or number != number:
            return None
        return number
    except (TypeError, ValueError):
        return None


def _short_error(exc: Exception, limit: int = 180) -> str:
    text = " ".join(str(exc).split())
    return text[:limit] if text else exc.__class__.__name__


def previous_observation(username: str, run_date: str) -> tuple[str, float] | None:
    history = watch.load_json(watch.HISTORY_PATH, [])
    target = username.casefold()
    for row in reversed(history):
        row_date = str(row.get("date", ""))
        if row_date == run_date:
            continue
        for key, candidate in row.get("candidates", {}).items():
            if key.casefold() != target or not candidate.get("present"):
                continue
            ytd = _number(candidate.get("gain_ytd_pct"))
            if ytd is not None:
                return row_date, ytd
    return None


def previous_ytd(username: str, run_date: str) -> float | None:
    """Backward-compatible helper retained for tests/callers."""
    previous = previous_observation(username, run_date)
    return previous[1] if previous else None


def observation_return(
    previous: tuple[str, float] | None,
    current_date: str,
    current_ytd: float,
) -> float | None:
    if previous is None:
        return None
    previous_date, previous_ytd_value = previous
    try:
        same_year = date.fromisoformat(previous_date).year == date.fromisoformat(current_date).year
    except ValueError:
        same_year = True
    if same_year:
        return watch.forward_return_from_ytd(previous_ytd_value, current_ytd)
    return current_ytd


def build_investor_record(username: str, payload: dict[str, Any], run_date: str) -> dict[str, Any]:
    if not payload.get("success"):
        raise ValueError(payload.get("error") or f"Public lookup failed for {username}")

    data = payload.get("data", {}) or {}
    portfolio = data.get("portfolio") or {}
    ytd = _number(portfolio.get("ytdReturn"))
    if ytd is None:
        raise ValueError(f"Public lookup returned no YTD return for {username}")

    derived_return = observation_return(previous_observation(username, run_date), run_date, ytd)

    positions = []
    for position in portfolio.get("topPositions") or []:
        market_value = _number(position.get("marketValue"))
        if market_value is None or market_value < 0:
            continue
        positions.append(
            {
                "instrumentId": position.get("instrumentId"),
                "investmentPct": market_value,
                "symbol": position.get("symbol"),
            }
        )

    return {
        "userName": portfolio.get("username") or username,
        "fullName": portfolio.get("fullName") or username,
        "gain": ytd,
        "dailyGain": derived_return,
        "riskScore": portfolio.get("riskScore"),
        "copiers": portfolio.get("copiers"),
        "trades": portfolio.get("trades"),
        "winRatio": portfolio.get("winRatio"),
        "country": portfolio.get("country"),
        "portfolio": {
            "positions": positions,
            "totalValue": portfolio.get("totalValue"),
            "profitLossPercentage": None,
            "positionsCount": portfolio.get("positionCount"),
            "cashPercent": portfolio.get("cashPercent"),
        },
        "source": "etoro-census-public-user-api",
        "sourceTimestamp": data.get("timestamp") or payload.get("timestamp"),
    }


def fetch_public_user(username: str, run_date: str) -> dict[str, Any]:
    url = PUBLIC_USER_ENDPOINT.format(username=quote(username, safe=""))
    response = requests.get(
        url,
        timeout=60,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    response.raise_for_status()
    return build_investor_record(username, response.json(), run_date)


def last_yahoo_quote(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return the most recent non-null chart close and its session date."""
    chart = payload.get("chart") or {}
    results = chart.get("result") or []
    if not results:
        return None
    result = results[0]
    quotes = result.get("indicators", {}).get("quote") or []
    if not quotes:
        return None
    closes = quotes[0].get("close") or []
    timestamps = result.get("timestamp") or []
    for index in range(len(closes) - 1, -1, -1):
        close = _number(closes[index])
        if close is None or close <= 0:
            continue
        as_of = None
        if index < len(timestamps):
            try:
                as_of = datetime.fromtimestamp(int(timestamps[index]), timezone.utc).date().isoformat()
            except (TypeError, ValueError, OSError):
                pass
        return {"close": close, "as_of": as_of, "source": "yahoo"}
    return None


def last_yahoo_close(payload: dict[str, Any]) -> float | None:
    quote_data = last_yahoo_quote(payload)
    return _number(quote_data.get("close")) if quote_data else None


def fetch_benchmark_quote(symbol: str, stooq_fallback) -> dict[str, Any]:
    """Fetch an ETF close from Yahoo; fall back to the Stooq quote reader."""
    yahoo_symbol = symbol.split(".", 1)[0].upper()
    try:
        response = requests.get(
            YAHOO_CHART_ENDPOINT.format(symbol=quote(yahoo_symbol, safe="")),
            params={"range": "5d", "interval": "1d", "includePrePost": "false"},
            timeout=20,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        response.raise_for_status()
        quote_data = last_yahoo_quote(response.json())
        if quote_data and quote_data.get("close") is not None:
            return quote_data
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        print(f"WARNING: Yahoo benchmark lookup failed for {yahoo_symbol}: {_short_error(exc)}")

    fallback = stooq_fallback(symbol)
    if isinstance(fallback, dict):
        return fallback
    return {"close": _number(fallback), "as_of": None, "source": "stooq"}


def fetch_benchmark_close(symbol: str, stooq_fallback) -> float | None:
    return _number(fetch_benchmark_quote(symbol, stooq_fallback).get("close"))


def _has_prior_success(username: str) -> bool:
    target = username.casefold()
    history = watch.load_json(watch.HISTORY_PATH, [])
    for row in history:
        for key, candidate in row.get("candidates", {}).items():
            if key.casefold() == target and candidate.get("present"):
                return True
    return False


def census_fallback_due(
    failures: list[str],
    run_date: str,
    refresh_days: int,
    state: dict[str, Any],
) -> bool:
    if any(_has_prior_success(username) for username in failures):
        return True
    previous = state.get("last_census_fallback_attempt_date")
    if not previous:
        return True
    try:
        elapsed = (date.fromisoformat(run_date) - date.fromisoformat(str(previous))).days
    except ValueError:
        return True
    return elapsed >= max(1, refresh_days)


def build_runtime_census(
    candidates: list[str],
    census_fallback_url: str,
    fallback_refresh_days: int = 7,
) -> dict[str, Any]:
    now_utc = datetime.now(timezone.utc)
    local_now = now_utc.astimezone(PACIFIC)
    run_date = local_now.date().isoformat()
    investors: list[dict[str, Any]] = []
    failures: list[str] = []
    unresolved: dict[str, str] = {}

    for username in candidates:
        try:
            investors.append(fetch_public_user(username, run_date))
        except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
            error = _short_error(exc)
            print(f"WARNING: per-user lookup failed for {username}: {error}")
            failures.append(username)
            unresolved[username] = f"per-user lookup failed: {error}"

    if failures:
        state = watch.load_json(watch.STATE_PATH, {})
        if census_fallback_due(failures, run_date, fallback_refresh_days, state):
            state["last_census_fallback_attempt_date"] = run_date
            watch.save_json(watch.STATE_PATH, state)
            try:
                fallback = watch.fetch_json(census_fallback_url)
            except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
                error = _short_error(exc)
                print(f"WARNING: census fallback failed: {error}")
                fallback = {"investors": [], "metadata": {}}
                for username in failures:
                    unresolved[username] += f"; census fallback failed: {error}"
            else:
                fallback_timestamp = (fallback.get("metadata", {}) or {}).get("collectedAt")
                for username in failures:
                    candidate = watch.find_investor(fallback, username)
                    if candidate is not None:
                        candidate = dict(candidate)
                        candidate["source"] = "etoro-census-top1500-fallback"
                        candidate["sourceTimestamp"] = fallback_timestamp
                        investors.append(candidate)
                        unresolved.pop(username, None)
                    else:
                        unresolved[username] += "; census fallback did not contain candidate"
        else:
            for username in failures:
                unresolved[username] += f"; census fallback throttled to every {max(1, fallback_refresh_days)} days"

    return {
        "metadata": {
            "collectedAt": now_utc.isoformat().replace("+00:00", "Z"),
            "collectedAtUTC": now_utc.strftime("%Y.%m.%d at %H:%M UTC"),
            "observationDate": run_date,
            "observationTimezone": "America/Los_Angeles",
            "totalInvestors": len(investors),
            "period": "CurrYear",
            "dataSource": "per-user public API with throttled census fallback",
            "unresolved": unresolved,
        },
        "investors": investors,
    }


def main() -> int:
    config = watch.load_json(watch.CONFIG_PATH, {})
    candidates = config.get("candidates", [])
    if not candidates:
        raise SystemExit("No candidates configured")

    fallback_refresh_days = int(
        config.get("data_quality", {}).get("census_fallback_refresh_days", 7)
    )
    runtime_census = build_runtime_census(
        candidates,
        config["census_url"],
        fallback_refresh_days,
    )
    original_fetch_json = watch.fetch_json
    original_benchmark = watch.fetch_benchmark_quote

    def runtime_fetch(_url: str) -> dict[str, Any]:
        return runtime_census

    def runtime_benchmark(symbol: str) -> dict[str, Any]:
        return fetch_benchmark_quote(symbol, watch.fetch_stooq_quote)

    watch.fetch_json = runtime_fetch
    watch.fetch_benchmark_quote = runtime_benchmark
    try:
        return watch.main()
    finally:
        watch.fetch_json = original_fetch_json
        watch.fetch_benchmark_quote = original_benchmark


if __name__ == "__main__":
    raise SystemExit(main())
