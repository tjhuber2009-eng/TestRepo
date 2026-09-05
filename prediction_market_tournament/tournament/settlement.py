from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from .adapters.polymarket import parse_jsonish_list
from .models import ResolvedTrade, Signal
from .scoring import settle_binary_signal


def _dt(value: str | None) -> datetime | None:
    if not value:
        return None
    text = str(value).replace("Z", "+00:00")
    out = datetime.fromisoformat(text)
    if out.tzinfo is None:
        out = out.replace(tzinfo=timezone.utc)
    return out


def signal_from_json(row: dict) -> Signal:
    payload = (
        row.get("signal")
        if isinstance(row.get("signal"), dict)
        else row
    )
    observed_at = _dt(payload.get("observed_at"))
    if observed_at is None:
        raise ValueError("signal observed_at missing")
    return Signal(
        signal_id=str(payload["signal_id"]),
        lane=str(payload["lane"]),
        market_id=str(payload["market_id"]),
        observed_at=observed_at,
        side=str(payload["side"]).upper(),
        market_price=float(payload["market_price"]),
        fair_probability=float(payload["fair_probability"]),
        order_mode=str(payload["order_mode"]),
        size_usd=float(payload.get("size_usd", 1.0)),
        fee_rate=float(payload.get("fee_rate", 0.0)),
        fee_exponent=float(payload.get("fee_exponent", 1.0)),
        executed_shares=(
            None
            if payload.get("executed_shares") is None
            else float(payload["executed_shares"])
        ),
        entry_fee_usd=(
            None
            if payload.get("entry_fee_usd") is None
            else float(payload["entry_fee_usd"])
        ),
        notes=str(payload.get("notes", "")),
        metadata=dict(payload.get("metadata") or {}),
    )


def resolved_trade_from_json(row: dict) -> ResolvedTrade:
    signal = signal_from_json(row)
    return ResolvedTrade(
        signal=signal,
        won=bool(row["won"]),
        fill_price=float(row["fill_price"]),
        fee_usd=float(row["fee_usd"]),
        payout_usd=float(row["payout_usd"]),
        pnl_usd=float(row["pnl_usd"]),
        return_on_stake=float(row["return_on_stake"]),
        resolved_at=_dt(row.get("resolved_at")),
    )


def terminal_outcome(
    market: dict, *, tolerance: float = 1e-6
) -> str | None:
    """Return the unique terminal outcome for one-hot 1/0 settlements only.

    Split, ambiguous, or cancelled resolutions are intentionally not coerced
    into a win or loss.
    """
    if market.get("closed") is not True:
        return None

    resolution_status = str(
        market.get("umaResolutionStatus") or ""
    ).strip().lower()
    if resolution_status and resolution_status not in {
        "resolved",
        "settled",
    }:
        return None

    outcomes = [
        str(x)
        for x in parse_jsonish_list(market.get("outcomes"))
    ]
    prices_raw = parse_jsonish_list(market.get("outcomePrices"))
    if len(outcomes) < 2 or len(outcomes) != len(prices_raw):
        return None

    try:
        prices = [float(x) for x in prices_raw]
    except (TypeError, ValueError):
        return None

    winners = [
        i
        for i, price in enumerate(prices)
        if price >= 1.0 - tolerance
    ]
    if len(winners) != 1:
        return None

    winner = winners[0]
    if any(
        i != winner and price > tolerance
        for i, price in enumerate(prices)
    ):
        return None
    return outcomes[winner]


def outcome_matches_side(side: str, outcome: str) -> bool:
    return side.strip().casefold() == outcome.strip().casefold()


def resolve_signal(signal: Signal, market: dict) -> ResolvedTrade | None:
    outcome = terminal_outcome(market)
    if outcome is None:
        return None
    # closedTime can precede oracle finality. Prefer the later Gamma update
    # that delivered the final resolution state; fall back only when absent.
    resolved_at = _dt(
        market.get("updatedAt") or market.get("closedTime")
    )
    return settle_binary_signal(
        signal,
        outcome_matches_side(signal.side, outcome),
        resolved_at=resolved_at,
    )


def read_jsonl(path: str | Path) -> list[dict]:
    file_path = Path(path)
    if not file_path.exists():
        return []
    out: list[dict] = []
    for line in file_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            out.append(row)
    return out
