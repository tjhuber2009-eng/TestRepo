#!/usr/bin/env python3
"""Production entrypoint for Copy Trader Watch.

Primary candidate source: the public per-user endpoint exposed by
weirdapps/etoro_census. That route can analyze an eToro username even when the
investor is not in the census top-1,500 list. If a per-user lookup fails, this
runner lazily falls back to the large public census configured in config.json.

Primary benchmark source: Yahoo Finance's public chart endpoint, with the Stooq
reader in watch.py retained as a fallback.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any
from urllib.parse import quote

import requests

import watch

PUBLIC_USER_ENDPOINT = "https://etoro-census.vercel.app/api/public/{username}"
YAHOO_CHART_ENDPOINT = "https://query1.finance.yahoo.com/v8/finance/chart/{symbol}"
USER_AGENT = "Mozilla/5.0 (compatible; copy-trader-watch/1.0; +https://github.com/)"


def _number(value: Any) -> float | None:
    try:
        number = float(value) if value is not None else None
        if number is None or number != number:  # NaN guard
            return None
        return number
    except (TypeError, ValueError):
        return None


def previous_ytd(username: str, run_date: str) -> float | None:
    history = watch.load_json(watch.HISTORY_PATH, [])
    target = username.casefold()
    for row in reversed(history):
        if row.get("date") == run_date:
            continue
        for key, candidate in row.get("candidates", {}).items():
            if key.casefold() == target and candidate.get("present"):
                return _number(candidate.get("gain_ytd_pct"))
    return None


def build_investor_record(username: str, payload: dict[str, Any], run_date: str) -> dict[str, Any]:
    if not payload.get("success"):
        raise ValueError(payload.get("error") or f"Public lookup failed for {username}")

    portfolio = payload.get("data", {}).get("portfolio") or {}
    ytd = _number(portfolio.get("ytdReturn"))
    if ytd is None:
        raise ValueError(f"Public lookup returned no YTD return for {username}")

    prev = previous_ytd(username, run_date)
    derived_daily = watch.forward_return_from_ytd(prev, ytd) if prev is not None else None

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
        "dailyGain": derived_daily,
        "riskScore": portfolio.get("riskScore"),
        "copiers": None,
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


def last_yahoo_close(payload: dict[str, Any]) -> float | None:
    """Return the most recent non-null chart close from a Yahoo response."""
    chart = payload.get("chart") or {}
    results = chart.get("result") or []
    if not results:
        return None
    quotes = results[0].get("indicators", {}).get("quote") or []
    if not quotes:
        return None
    closes = quotes[0].get("close") or []
    for value in reversed(closes):
        close = _number(value)
        if close is not None and close > 0:
            return close
    return None


def fetch_benchmark_close(symbol: str, stooq_fallback) -> float | None:
    """Fetch an ETF close from Yahoo; fall back to the original Stooq reader."""
    yahoo_symbol = symbol.split(".", 1)[0].upper()
    try:
        response = requests.get(
            YAHOO_CHART_ENDPOINT.format(symbol=quote(yahoo_symbol, safe="")),
            params={"range": "5d", "interval": "1d", "includePrePost": "false"},
            timeout=20,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        response.raise_for_status()
        close = last_yahoo_close(response.json())
        if close is not None:
            return close
    except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
        print(f"WARNING: Yahoo benchmark lookup failed for {yahoo_symbol}: {exc}")

    return stooq_fallback(symbol)


def build_runtime_census(candidates: list[str], census_fallback_url: str) -> dict[str, Any]:
    now = datetime.now(timezone.utc)
    run_date = now.date().isoformat()
    investors: list[dict[str, Any]] = []
    failures: list[str] = []

    for username in candidates:
        try:
            investors.append(fetch_public_user(username, run_date))
        except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
            print(f"WARNING: per-user lookup failed for {username}: {exc}")
            failures.append(username)

    # The fallback is intentionally lazy because the current census is ~89 MB.
    if failures:
        try:
            fallback = watch.fetch_json(census_fallback_url)
        except (requests.RequestException, ValueError, json.JSONDecodeError) as exc:
            print(f"WARNING: census fallback failed: {exc}")
            fallback = {"investors": []}

        for username in failures:
            candidate = watch.find_investor(fallback, username)
            if candidate is not None:
                candidate = dict(candidate)
                candidate["source"] = "etoro-census-top1500-fallback"
                investors.append(candidate)
            else:
                print(f"WARNING: no public-data source resolved {username}")

    return {
        "metadata": {
            "collectedAt": now.isoformat().replace("+00:00", "Z"),
            "collectedAtUTC": now.strftime("%Y.%m.%d at %H:%M UTC"),
            "totalInvestors": len(investors),
            "period": "CurrYear",
            "dataSource": "per-user public API with census fallback",
        },
        "investors": investors,
    }


def main() -> int:
    config = watch.load_json(watch.CONFIG_PATH, {})
    candidates = config.get("candidates", [])
    if not candidates:
        raise SystemExit("No candidates configured")

    runtime_census = build_runtime_census(candidates, config["census_url"])
    original_fetch_json = watch.fetch_json
    original_stooq = watch.fetch_stooq_close

    def runtime_fetch(_url: str) -> dict[str, Any]:
        return runtime_census

    def runtime_benchmark(symbol: str) -> float | None:
        return fetch_benchmark_close(symbol, original_stooq)

    watch.fetch_json = runtime_fetch
    watch.fetch_stooq_close = runtime_benchmark
    try:
        return watch.main()
    finally:
        watch.fetch_json = original_fetch_json
        watch.fetch_stooq_close = original_stooq


if __name__ == "__main__":
    raise SystemExit(main())
