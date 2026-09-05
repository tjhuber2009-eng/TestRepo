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
    get_books,
    market_buy_quote,
    market_execution_rules,
    parse_jsonish_list,
    validate_book_identity,
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
    """Extract the market's named weather-station identifier.

    Current Polymarket daily-temperature rules commonly use Wunderground
    history URLs ending in an ICAO station code (for example .../KLAX).
    Older/source variants may expose the same code in a ?site= query.
    """
    blob = " ".join(text or "" for text in texts)

    query_match = re.search(
        r"[?&]site=([a-z0-9]{3,6})",
        blob,
        flags=re.I,
    )
    if query_match:
        return query_match.group(1).upper()

    wunderground_match = re.search(
        r"wunderground\.com/history/daily/"
        r"[^\s\"'<>?#]+/"
        r"([a-z0-9]{3,6})"
        r"(?:[/?#\s\"'<>]|$)",
        blob,
        flags=re.I,
    )
    if wunderground_match:
        return wunderground_match.group(1).upper()

    raise ValueError("could not find resolution station code")


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


def binary_outcome_tokens(market: dict) -> dict[str, str]:
    outcomes = parse_jsonish_list(market.get("outcomes"))
    tokens = parse_jsonish_list(market.get("clobTokenIds"))
    if len(outcomes) != len(tokens):
        raise ValueError("outcomes and clobTokenIds length mismatch")
    mapping = {
        str(outcome).strip().upper(): str(token)
        for outcome, token in zip(outcomes, tokens)
    }
    if set(mapping) != {"YES", "NO"}:
        raise ValueError("weather market must have exactly YES/NO outcomes")
    return mapping


def weather_signal_from_market(
    market: dict,
    *,
    event: dict,
    target_date: date,
    observed_at: datetime | None = None,
    models: tuple[str, ...] = (
        "ecmwf_aifs025_ensemble",
        "ecmwf_ifs025_ensemble",
        "ncep_gefs025",
    ),
    min_edge: float = 0.05,
    cash_budget_usd: float = 5.0,
) -> Signal | None:
    explicit_observed_at = observed_at
    if explicit_observed_at is not None and explicit_observed_at.tzinfo is None:
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
    if not models:
        raise ValueError("at least one weather ensemble model is required")

    model_probabilities: dict[str, float] = {}
    model_members: dict[str, int] = {}
    for model in models:
        payload = fetch_temperature_ensemble(
            lat,
            lon,
            target_date,
            model=model,
            unit=unit,
            timezone="auto",
        )
        values = member_daily_extremes(payload, kind=bracket.kind)
        model_members[model] = len(values)
        model_probabilities[model] = bracket_probability(
            values,
            lower=bracket.lower,
            upper=bracket.upper,
        )

    # Equal weight per forecast MODEL, not per ensemble member. This prevents
    # 51-member ECMWF systems from dominating 31-member GEFS merely because
    # they expose more perturbations.
    fair = sum(model_probabilities.values()) / len(model_probabilities)

    tokens = binary_outcome_tokens(market)
    condition_id = str(market.get("conditionId") or "").strip()
    if not condition_id:
        raise ValueError(
            "conditionId missing; cannot obtain authoritative market execution rules"
        )

    execution = market_execution_rules(condition_id)
    if execution.taker_order_delay_enabled:
        raise ValueError(
            "taker order delay enabled; immediate executable fill is invalid"
        )
    requested_tokens = [tokens["YES"], tokens["NO"]]
    batch = get_books(requested_tokens)
    by_asset = {
        str(book.get("asset_id") or ""): book
        for book in batch
        if isinstance(book, dict)
    }
    if set(by_asset) != set(requested_tokens):
        raise LookupError(
            "batch weather books did not return exactly the requested YES/NO assets"
        )

    # Live observation time is captured only after both forecast inputs and
    # executable CLOB books/rules have been retrieved. Tests may inject an
    # explicit timestamp for deterministic assertions.
    observed_at = explicit_observed_at or datetime.now(timezone.utc)

    candidates: list[tuple[float, str, float, object, dict]] = []
    for side, side_fair in (
        ("YES", fair),
        ("NO", 1.0 - fair),
    ):
        token = tokens[side]
        book = by_asset[token]
        validate_book_identity(
            book,
            token_id=token,
            condition_id=condition_id,
        )
        quote = market_buy_quote(
            book,
            cash_budget_usd,
            fee_rate=execution.fee_rate,
            fee_exponent=execution.fee_exponent,
            min_order_shares=execution.min_order_shares,
        )
        if quote is None:
            continue
        edge = exact_execution_edge_per_share(
            side_fair,
            shares=quote.shares,
            spent_usd=quote.spent_usd,
            fee_usd=quote.fee_usd,
        )
        candidates.append((edge, side, side_fair, quote, book))

    if not candidates:
        return None

    edge, side, side_fair, quote, book = max(
        candidates,
        key=lambda row: (row[0], row[1] == "YES"),
    )
    if edge < min_edge:
        return None

    market_id = str(market.get("id") or condition_id)
    signal_id = hashlib.sha256(
        f"weather_ensemble_taker|{market_id}".encode()
    ).hexdigest()[:24]
    return Signal(
        signal_id=signal_id,
        lane="weather_ensemble_taker",
        market_id=market_id,
        observed_at=observed_at,
        side=side,
        market_price=quote.average_price,
        fair_probability=side_fair,
        order_mode="taker",
        size_usd=quote.spent_usd,
        fee_rate=execution.fee_rate,
        fee_exponent=execution.fee_exponent,
        executed_shares=quote.shares,
        entry_fee_usd=quote.fee_usd,
        notes=(
            f"station={station}; models={','.join(models)}; {bracket.kind} "
            f"{bracket.lower}..{bracket.upper}{bracket.unit}; "
            f"p_yes={fair:.6f}; side={side}; exact_edge={edge:.6f}"
        ),
        metadata={
            "condition_id": condition_id,
            "yes_token_id": tokens["YES"],
            "no_token_id": tokens["NO"],
            "chosen_token_id": tokens[side],
            "station": station,
            "fee_source": "clob-market-info.fd",
            "ask_source": "batch-full-size-book-level-fill",
            "min_order_shares": execution.min_order_shares,
            "fair_yes_probability": fair,
            "weather_models": list(models),
            "model_probabilities": model_probabilities,
            "model_member_counts": model_members,
            "model_blend": "equal_weight_probability",
            "all_in_cost_per_share": quote.all_in_cost_per_share,
            "exact_edge_per_share": edge,
            "chosen_book_timestamp": str(book.get("timestamp") or ""),
            "chosen_book_hash": str(book.get("hash") or ""),
        },
    )
