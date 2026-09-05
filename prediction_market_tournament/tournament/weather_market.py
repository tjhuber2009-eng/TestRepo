from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

from .adapters.aviation import station_coordinates
from .adapters.open_meteo import (
    bracket_probability,
    fetch_temperature_ensemble,
    member_daily_extremes,
)
from .adapters.polymarket import (
    get_book,
    market_buy_quote,
    market_execution_rules,
    parse_jsonish_list,
)
from .fees import exact_execution_edge_per_share
from .models import Signal


@dataclass(frozen=True)
class TemperatureBracket:
    kind: str
    unit: str
    lower: float | None
    upper: float | None


def extract_station_code(*texts: str | None) -> str:
    blob = " ".join(text or "" for text in texts)
    match = re.search(
        r"[?&]site=([a-z0-9]{3,6})",
        blob,
        flags=re.I,
    )
    if not match:
        raise ValueError("could not find resolution station code")
    return match.group(1).upper()


def parse_temperature_bracket(question: str) -> TemperatureBracket:
    normalized = question.replace("–", "-").replace("—", "-")
    kind = (
        "max"
        if re.search(r"\b(highest|maximum|max)\b", normalized, re.I)
        else (
            "min"
            if re.search(r"\b(lowest|minimum|min)\b", normalized, re.I)
            else ""
        )
    )
    if not kind:
        raise ValueError(f"could not infer max/min from: {question}")

    match = re.search(
        r"(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*°?\s*([FC])\b",
        normalized,
        re.I,
    )
    if match:
        return TemperatureBracket(
            kind,
            match.group(3).upper(),
            float(match.group(1)),
            float(match.group(2)),
        )

    match = re.search(
        r"(-?\d+(?:\.\d+)?)\s*°?\s*([FC])\s+or\s+(below|lower|less)",
        normalized,
        re.I,
    )
    if match:
        return TemperatureBracket(
            kind,
            match.group(2).upper(),
            None,
            float(match.group(1)),
        )

    match = re.search(
        r"(-?\d+(?:\.\d+)?)\s*°?\s*([FC])\s+or\s+(above|higher|more)",
        normalized,
        re.I,
    )
    if match:
        return TemperatureBracket(
            kind,
            match.group(2).upper(),
            float(match.group(1)),
            None,
        )

    match = re.search(
        r"\b(?:be|is)\s+(-?\d+(?:\.\d+)?)\s*°?\s*([FC])\b",
        normalized,
        re.I,
    )
    if match:
        value = float(match.group(1))
        return TemperatureBracket(
            kind,
            match.group(2).upper(),
            value,
            value,
        )

    raise ValueError(f"could not parse temperature bracket: {question}")


def yes_token_id(market: dict) -> str:
    outcomes = parse_jsonish_list(market.get("outcomes"))
    tokens = parse_jsonish_list(market.get("clobTokenIds"))
    if len(outcomes) != len(tokens):
        raise ValueError("outcomes and clobTokenIds length mismatch")
    for outcome, token in zip(outcomes, tokens):
        if str(outcome).strip().upper() == "YES":
            return str(token)
    raise ValueError("YES token not found")


def weather_signal_from_market(
    market: dict,
    *,
    event: dict,
    target_date: date,
    observed_at: datetime | None = None,
    model: str = "ncep_gefs025",
    min_edge: float = 0.05,
    size_usd: float = 5.0,
) -> Signal | None:
    observed_at = observed_at or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")

    bracket = parse_temperature_bracket(str(market.get("question") or ""))
    station = extract_station_code(
        str(event.get("description") or ""),
        str(event.get("resolutionSource") or ""),
        str(market.get("description") or ""),
        str(market.get("resolutionSource") or ""),
    )
    lat, lon = station_coordinates(station)
    unit = "fahrenheit" if bracket.unit == "F" else "celsius"
    payload = fetch_temperature_ensemble(
        lat,
        lon,
        target_date,
        model=model,
        unit=unit,
        timezone="auto",
    )
    values = member_daily_extremes(payload, kind=bracket.kind)
    fair = bracket_probability(
        values,
        lower=bracket.lower,
        upper=bracket.upper,
    )

    token = yes_token_id(market)
    condition_id = str(market.get("conditionId") or "").strip()
    if not condition_id:
        raise ValueError(
            "conditionId missing; cannot obtain authoritative market execution rules"
        )

    execution = market_execution_rules(condition_id)
    quote = market_buy_quote(
        get_book(token),
        size_usd,
        fee_rate=execution.fee_rate,
        fee_exponent=execution.fee_exponent,
        min_order_shares=execution.min_order_shares,
    )
    if quote is None:
        return None

    edge = exact_execution_edge_per_share(
        fair,
        shares=quote.shares,
        spent_usd=quote.spent_usd,
        fee_usd=quote.fee_usd,
    )
    if edge < min_edge:
        return None

    market_id = str(market.get("id") or condition_id or token)
    raw = (
        f"weather|{market_id}|"
        f"{observed_at.astimezone(timezone.utc).isoformat()}"
    )
    signal_id = hashlib.sha256(raw.encode()).hexdigest()[:24]
    return Signal(
        signal_id=signal_id,
        lane="weather_ensemble_taker",
        market_id=market_id,
        observed_at=observed_at,
        side="YES",
        market_price=quote.average_price,
        fair_probability=fair,
        order_mode="taker",
        size_usd=quote.spent_usd,
        fee_rate=execution.fee_rate,
        fee_exponent=execution.fee_exponent,
        executed_shares=quote.shares,
        entry_fee_usd=quote.fee_usd,
        notes=(
            f"station={station}; model={model}; {bracket.kind} "
            f"{bracket.lower}..{bracket.upper}{bracket.unit}; "
            f"n={len(values)}; exact_edge={edge:.6f}"
        ),
        metadata={
            "condition_id": condition_id,
            "yes_token_id": token,
            "station": station,
            "fee_source": "clob-market-info.fd",
            "ask_source": "full-size-book-level-fill",
            "min_order_shares": execution.min_order_shares,
            "all_in_cost_per_share": quote.all_in_cost_per_share,
            "exact_edge_per_share": edge,
        },
    )
