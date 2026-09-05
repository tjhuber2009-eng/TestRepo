from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone

from .adapters.open_meteo import (
    bracket_probability,
    fetch_temperature_ensemble,
    member_daily_extremes,
)
from .adapters.aviation import station_coordinates
from .adapters.polymarket import (
    best_ask,
    get_book,
    market_fee_curve,
    parse_jsonish_list,
)
from .lanes import weather_ensemble_decision
from .models import Signal


@dataclass(frozen=True)
class TemperatureBracket:
    kind: str
    unit: str
    lower: float | None
    upper: float | None


def extract_station_code(*texts: str | None) -> str:
    blob = " ".join(x or "" for x in texts)
    m = re.search(r"[?&]site=([a-z0-9]{3,6})", blob, flags=re.I)
    if not m:
        raise ValueError("could not find resolution station code")
    return m.group(1).upper()


def parse_temperature_bracket(question: str) -> TemperatureBracket:
    q = question.replace("–", "-").replace("—", "-")
    kind = "max" if re.search(r"\b(highest|maximum|max)\b", q, re.I) else (
        "min" if re.search(r"\b(lowest|minimum|min)\b", q, re.I) else ""
    )
    if not kind:
        raise ValueError(f"could not infer max/min from: {question}")

    m = re.search(
        r"(-?\d+(?:\.\d+)?)\s*-\s*(-?\d+(?:\.\d+)?)\s*°?\s*([FC])\b",
        q,
        re.I,
    )
    if m:
        return TemperatureBracket(
            kind,
            m.group(3).upper(),
            float(m.group(1)),
            float(m.group(2)),
        )

    m = re.search(
        r"(-?\d+(?:\.\d+)?)\s*°?\s*([FC])\s+or\s+(below|lower|less)",
        q,
        re.I,
    )
    if m:
        return TemperatureBracket(
            kind, m.group(2).upper(), None, float(m.group(1))
        )
    m = re.search(
        r"(-?\d+(?:\.\d+)?)\s*°?\s*([FC])\s+or\s+(above|higher|more)",
        q,
        re.I,
    )
    if m:
        return TemperatureBracket(
            kind, m.group(2).upper(), float(m.group(1)), None
        )

    m = re.search(
        r"\b(?:be|is)\s+(-?\d+(?:\.\d+)?)\s*°?\s*([FC])\b", q, re.I
    )
    if m:
        x = float(m.group(1))
        return TemperatureBracket(kind, m.group(2).upper(), x, x)

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
) -> Signal | None:
    observed_at = observed_at or datetime.now(timezone.utc)
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")

    bracket = parse_temperature_bracket(
        str(market.get("question") or "")
    )
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
        values, lower=bracket.lower, upper=bracket.upper
    )

    token = yes_token_id(market)
    ask = best_ask(get_book(token))
    if ask is None:
        return None

    condition_id = str(market.get("conditionId") or "").strip()
    if not condition_id:
        raise ValueError(
            "conditionId missing; cannot obtain authoritative market fee curve"
        )
    fee_rate, fee_exponent = market_fee_curve(condition_id)
    decision = weather_ensemble_decision(
        fair,
        ask,
        fee_rate=fee_rate,
        fee_exponent=fee_exponent,
        min_edge=min_edge,
    )
    if not decision.trade:
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
        market_price=ask,
        fair_probability=fair,
        order_mode="taker",
        size_usd=1.0,
        fee_rate=fee_rate,
        fee_exponent=fee_exponent,
        notes=(
            f"station={station}; model={model}; {bracket.kind} "
            f"{bracket.lower}..{bracket.upper}{bracket.unit}; n={len(values)}"
        ),
        metadata={
            "condition_id": condition_id,
            "yes_token_id": token,
            "station": station,
            "fee_source": "clob-market-info.fd",
        },
    )
