#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from tournament.adapters.polymarket import list_events
from tournament.freeze import load_frozen_spec, require_forward_started
from tournament.ledger import append_jsonl, record_signal
from tournament.weather_market import weather_signal_from_market

MONTHS = {
    name.lower(): index
    for index, name in enumerate(
        [
            "", "January", "February", "March", "April", "May", "June",
            "July", "August", "September", "October", "November", "December",
        ]
    )
    if name
}


def target_date_from_event(event: dict) -> date:
    title = str(event.get("title") or "")
    match = re.search(
        r"\bon\s+"
        r"(January|February|March|April|May|June|July|August|"
        r"September|October|November|December)"
        r"\s+(\d{1,2})(?:,?\s+(\d{4}))?",
        title,
        re.I,
    )
    end = str(event.get("endDate") or "")
    fallback_year = (
        int(end[:4])
        if len(end) >= 4 and end[:4].isdigit()
        else datetime.now(timezone.utc).year
    )
    if not match:
        if len(end) >= 10:
            return date.fromisoformat(end[:10])
        raise ValueError(f"cannot parse target date: {title}")
    return date(
        int(match.group(3) or fallback_year),
        MONTHS[match.group(1).lower()],
        int(match.group(2)),
    )


def is_temperature_event(event: dict) -> bool:
    title = str(event.get("title") or "").lower()
    return (
        "highest temperature in " in title
        or "lowest temperature in " in title
    )


def existing_weather_markets(ledger_path: Path) -> set[str]:
    seen: set[str] = set()
    if not ledger_path.exists():
        return seen
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if (
            row.get("kind") == "signal"
            and row.get("lane") == "weather_ensemble_taker"
            and row.get("market_id") is not None
        ):
            seen.add(str(row["market_id"]))
    return seen


def discover_active_events(max_pages: int = 20) -> list[dict]:
    out: list[dict] = []
    limit = 100
    for page in range(max_pages):
        rows = list_events(
            active=True,
            closed=False,
            limit=limit,
            offset=page * limit,
        )
        if not rows:
            break
        out.extend(rows)
        if len(rows) < limit:
            break
    return out


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    marker = require_forward_started(root)
    spec, sha = load_frozen_spec(root / "config" / "frozen_v1.json")
    implementation_sha = str(marker["implementation_sha256"])

    ledger_path = root / "data" / "signals.jsonl"
    scan_log = root / "data" / "weather_scan_log.jsonl"
    lane_cfg = spec["lanes"]["weather_ensemble_taker"]
    paper_stake_usd = (
        float(spec["risk"]["paper_account_usd"])
        * float(spec["risk"]["risk_fraction_per_trade"])
    )
    existing = existing_weather_markets(ledger_path)
    now = datetime.now(timezone.utc)

    events = discover_active_events()
    temp_events = [event for event in events if is_temperature_event(event)]

    markets_examined = 0
    recorded = 0
    errors: list[str] = []
    for event in temp_events:
        try:
            target = target_date_from_event(event)
        except Exception as exc:
            errors.append(f"date:{event.get('id')}:{exc}")
            continue

        if target < now.date() or (target - now.date()).days > 7:
            continue

        for market in event.get("markets") or []:
            market_id = str(
                market.get("id") or market.get("conditionId") or ""
            )
            if not market_id or market_id in existing:
                continue
            markets_examined += 1
            try:
                signal = weather_signal_from_market(
                    market,
                    event=event,
                    target_date=target,
                    observed_at=now,
                    min_edge=float(lane_cfg["min_edge"]),
                    cash_budget_usd=paper_stake_usd,
                )
            except Exception as exc:
                errors.append(
                    f"market:{market_id}:{type(exc).__name__}:{exc}"
                )
                continue
            if signal is None:
                continue
            record_signal(
                ledger_path,
                signal,
                spec_sha256=sha,
                implementation_sha256=implementation_sha,
            )
            existing.add(signal.market_id)
            recorded += 1

    append_jsonl(
        scan_log,
        {
            "kind": "weather_scan",
            "observed_at": now.isoformat(),
            "spec_sha256": sha,
            "implementation_sha256": implementation_sha,
            "active_events": len(events),
            "temperature_events": len(temp_events),
            "markets_examined": markets_examined,
            "signals_recorded": recorded,
            "paper_stake_usd": paper_stake_usd,
            "errors": errors[:50],
            "error_count": len(errors),
        },
    )
    print(
        json.dumps(
            {
                "active_events": len(events),
                "temperature_events": len(temp_events),
                "markets_examined": markets_examined,
                "signals_recorded": recorded,
                "error_count": len(errors),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
