from __future__ import annotations

import hashlib
from datetime import datetime, timezone

from .adapters.polymarket import (
    get_book,
    get_event_by_slug,
    market_buy_quote,
    market_execution_rules,
    parse_jsonish_list,
)
from .fees import exact_execution_edge_per_share
from .models import Signal
from .twap_model import (
    estimate_sigma_per_sqrt_second,
    final_twap_distribution,
    time_weighted_mean,
)

WINDOW_SECONDS = 300
TWAP_STREAM_MARKER = "btc-usd-twap-60s"


def btc_5m_slug(start_epoch_seconds: int) -> str:
    start = int(start_epoch_seconds)
    if start % WINDOW_SECONDS != 0:
        raise ValueError("BTC 5m start epoch must be aligned to 300 seconds")
    return f"btc-updown-5m-{start}"


def outcome_token_map(market: dict) -> dict[str, str]:
    outcomes = parse_jsonish_list(market.get("outcomes"))
    tokens = parse_jsonish_list(market.get("clobTokenIds"))
    if len(outcomes) != len(tokens):
        raise ValueError("outcomes/clobTokenIds mismatch")
    mapping = {
        str(outcome).strip().upper(): str(token)
        for outcome, token in zip(outcomes, tokens)
    }
    if "UP" not in mapping or "DOWN" not in mapping:
        raise ValueError("UP/DOWN outcome tokens not found")
    return mapping


def _rules_blob(event: dict, market: dict) -> str:
    fields = (
        event.get("title"),
        event.get("description"),
        event.get("resolutionSource"),
        market.get("question"),
        market.get("description"),
        market.get("resolutionSource"),
    )
    return " ".join(str(value or "") for value in fields).lower()


def validate_btc_5m_market(
    event: dict,
    market: dict,
    *,
    expected_start_epoch_seconds: int,
) -> None:
    expected_slug = btc_5m_slug(expected_start_epoch_seconds)
    if str(event.get("slug") or "") != expected_slug:
        raise ValueError("event slug does not match expected BTC 5m window")
    blob = _rules_blob(event, market)
    if "up or down" not in blob:
        raise ValueError("market is not an Up/Down market")
    if "chainlink" not in blob or TWAP_STREAM_MARKER not in blob:
        raise ValueError("market does not specify Chainlink BTC/USD 60s TWAP")
    outcome_token_map(market)


def discover_btc_5m_market(
    start_epoch_seconds: int,
) -> tuple[dict, dict]:
    event = get_event_by_slug(btc_5m_slug(start_epoch_seconds))
    markets = event.get("markets") or []
    if not markets:
        raise LookupError("BTC 5m event has no markets")
    matches: list[dict] = []
    for market in markets:
        try:
            validate_btc_5m_market(
                event,
                market,
                expected_start_epoch_seconds=start_epoch_seconds,
            )
        except ValueError:
            continue
        if market.get("closed") is True or market.get("active") is False:
            continue
        if market.get("acceptingOrders") is False:
            continue
        matches.append(market)
    if len(matches) != 1:
        raise LookupError(
            f"expected exactly one active BTC 5m market, found {len(matches)}"
        )
    return event, matches[0]


def _latest_raw_point(
    raw_points: list[tuple[float, float]],
    observed_ms: float,
) -> tuple[float, float] | None:
    eligible = [
        (timestamp, price)
        for timestamp, price in raw_points
        if timestamp <= observed_ms and price > 0
    ]
    return max(eligible, default=None)


def crypto_signal_from_market(
    market: dict,
    *,
    event: dict,
    lane: str,
    strike: float,
    raw_points: list[tuple[float, float]],
    window_start_ms: float,
    observed_at: datetime,
    lane_cfg: dict,
    size_usd: float,
) -> Signal | None:
    """Evaluate one frozen BTC 5m checkpoint using only causal raw data."""
    if observed_at.tzinfo is None:
        raise ValueError("observed_at must be timezone-aware")
    if strike <= 0:
        raise ValueError("strike must be > 0")
    if lane not in {"crypto_twap_taker", "crypto_late_resolution"}:
        raise ValueError("unsupported crypto lane")

    start_seconds = int(window_start_ms // 1000)
    validate_btc_5m_market(
        event,
        market,
        expected_start_epoch_seconds=start_seconds,
    )
    condition_id = str(market.get("conditionId") or "").strip()
    market_id = str(market.get("id") or "").strip()
    if not condition_id or not market_id:
        raise ValueError("market id/conditionId missing")

    tokens = outcome_token_map(market)
    execution = market_execution_rules(condition_id)
    books = {
        side: get_book(tokens[side])
        for side in ("UP", "DOWN")
    }

    observed_ms = observed_at.timestamp() * 1000.0
    end_ms = window_start_ms + WINDOW_SECONDS * 1000.0
    seconds_remaining = (end_ms - observed_ms) / 1000.0
    target_remaining = float(lane_cfg["entry_seconds_remaining"])
    checkpoint_ms = end_ms - target_remaining * 1000.0
    lag_seconds = (observed_ms - checkpoint_ms) / 1000.0
    max_lag = float(lane_cfg["checkpoint_max_lag_seconds"])
    if lag_seconds < 0 or lag_seconds > max_lag:
        return None

    latest = _latest_raw_point(raw_points, observed_ms)
    if latest is None:
        return None
    latest_timestamp, current_spot = latest
    max_spot_age = float(lane_cfg.get("raw_spot_max_age_seconds", 3.0))
    if (observed_ms - latest_timestamp) / 1000.0 > max_spot_age:
        return None

    lookback_seconds = float(lane_cfg["volatility_lookback_seconds"])
    lookback_start = observed_ms - lookback_seconds * 1000.0
    causal_points = sorted(
        (timestamp, price)
        for timestamp, price in raw_points
        if lookback_start <= timestamp <= observed_ms and price > 0
    )
    minimum_points = int(lane_cfg.get("minimum_raw_price_points", 30))
    if len(causal_points) < minimum_points:
        return None

    sigma = estimate_sigma_per_sqrt_second(causal_points)
    if sigma <= 0:
        return None

    window_seconds = float(lane_cfg["twap_window_seconds"])
    known_window_mean = None
    if seconds_remaining < window_seconds:
        known_window_mean = time_weighted_mean(
            causal_points,
            start_ms=end_ms - window_seconds * 1000.0,
            end_ms=observed_ms,
        )
        if known_window_mean is None:
            return None

    distribution = final_twap_distribution(
        strike=strike,
        current_spot=current_spot,
        sigma_per_sqrt_second=sigma,
        seconds_remaining=seconds_remaining,
        window_seconds=window_seconds,
        known_window_mean=known_window_mean,
    )
    probability_up = distribution.probability_above_strike

    candidates: list[
        tuple[float, str, float, object]
    ] = []
    for side, fair_probability in (
        ("UP", probability_up),
        ("DOWN", 1.0 - probability_up),
    ):
        quote = market_buy_quote(
            books[side],
            size_usd,
            fee_rate=execution.fee_rate,
            fee_exponent=execution.fee_exponent,
            min_order_shares=execution.min_order_shares,
        )
        if quote is None:
            continue
        edge = exact_execution_edge_per_share(
            fair_probability,
            shares=quote.shares,
            spent_usd=quote.spent_usd,
            fee_usd=quote.fee_usd,
        )
        candidates.append((edge, side, fair_probability, quote))

    if not candidates:
        return None
    edge, side, fair_probability, quote = max(
        candidates,
        key=lambda row: (row[0], row[1] == "UP"),
    )

    if lane == "crypto_twap_taker":
        if edge < float(lane_cfg["min_edge"]):
            return None
    else:
        if fair_probability < float(lane_cfg["min_fair_probability"]):
            return None
        if edge < float(lane_cfg["min_edge"]):
            return None

    signal_raw = (
        f"{lane}|{market_id}|{side}|"
        f"{int(checkpoint_ms)}|{observed_at.astimezone(timezone.utc).isoformat()}"
    )
    signal_id = hashlib.sha256(signal_raw.encode()).hexdigest()[:24]
    return Signal(
        signal_id=signal_id,
        lane=lane,
        market_id=market_id,
        observed_at=observed_at,
        side=side,
        market_price=quote.average_price,
        fair_probability=fair_probability,
        order_mode="taker",
        size_usd=quote.spent_usd,
        fee_rate=execution.fee_rate,
        fee_exponent=execution.fee_exponent,
        executed_shares=quote.shares,
        entry_fee_usd=quote.fee_usd,
        notes=(
            f"strike={strike:.8f}; p_up={probability_up:.6f}; "
            f"sigma_sqrt_s={sigma:.8f}; t={seconds_remaining:.3f}s; "
            f"edge={edge:.6f}"
        ),
        metadata={
            "event_slug": str(event.get("slug") or ""),
            "condition_id": condition_id,
            "token_id": tokens[side],
            "window_start_ms": window_start_ms,
            "window_end_ms": end_ms,
            "checkpoint_ms": checkpoint_ms,
            "checkpoint_lag_seconds": lag_seconds,
            "strike": strike,
            "current_spot": current_spot,
            "latest_raw_source_timestamp_ms": latest_timestamp,
            "raw_points_used": len(causal_points),
            "sigma_per_sqrt_second": sigma,
            "twap_distribution_mean": distribution.mean,
            "twap_distribution_std": distribution.std,
            "probability_up": probability_up,
            "known_window_mean": known_window_mean,
            "fee_source": "clob-market-info.fd",
            "ask_source": "full-size-book-level-fill",
            "all_in_cost_per_share": quote.all_in_cost_per_share,
            "exact_edge_per_share": edge,
        },
    )
