#!/usr/bin/env python3
"""Production entrypoint for Copy Trader Watch.

Primary source: the public per-user endpoint exposed by weirdapps/etoro_census.
That route can analyze an eToro username even when the investor is not in the
census top-1,500 list. If a per-user lookup fails, this runner falls back to the
large public census configured in config.json and lets watch.py surface a real
missing-data alert if neither source can resolve the candidate.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from urllib.parse import quote

import requests

import watch

PUBLIC_USER_ENDPOINT = "https://etoro-census.vercel.app/api/public/{username}"
USER_AGENT = "copy-trader-watch/1.0"


def _number(value: Any) -> float | None:
    try:
        return float(value) if value is not None else None
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
    prev = previous_ytd(username, run_date)
    derived_daily = watch.forward_return_from_ytd(prev, ytd) if prev is not None else None

    positions = []
    for position in portfolio.get("topPositions") or []:
        market_value = _number(position.get("marketValue"))
        if market_value is None:
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
                # Preserve an explicit placeholder so watch.py can produce a
                # genuine missing-data alert rather than silently dropping it.
                investors.append({"userName": username, "_lookup_failed": True})

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

    def runtime_fetch(_url: str) -> dict[str, Any]:
        return runtime_census

    watch.fetch_json = runtime_fetch
    try:
        return watch.main()
    finally:
        watch.fetch_json = original_fetch_json


if __name__ == "__main__":
    raise SystemExit(main())
