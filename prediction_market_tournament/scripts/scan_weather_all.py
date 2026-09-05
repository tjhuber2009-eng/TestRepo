#!/usr/bin/env python3
from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path

from tournament.adapters.polymarket import list_events
from tournament.freeze import load_frozen_spec
from tournament.ledger import append_jsonl, record_signal
from tournament.weather_market import weather_signal_from_market

MONTHS = {
    name.lower(): i for i, name in enumerate(
        ["", "January", "February", "March", "April", "May", "June",
         "July", "August", "September", "October", "November", "December"]
    ) if name
}


def target_date_from_event(event: dict) -> date:
    title = str(event.get("title") or "")
    m = re.search(
        r"\bon\s+(January|February|March|April|May|June|July|August|September|October|November|December)"
        r"\s+(\d{1,2})(?:,?\s+(\d{4}))?",
        title,
        re.I,
    )
    end = str(event.get("endDate") or "")
    fallback_year = int(end[:4]) if len(end) >= 4 and end[:4].isdigit() else datetime.now(timezone.utc).year
    if not m:
        if len(end) >= 10:
            return date.fromisoformat(end[:10])
        raise ValueError(f"cannot parse target date: {title}")
    return date(
        int(m.group(3) or fallback_year),
        MONTHS[m.group(1).lower()],
        int(m.group(2)),
    )


def is_temperature_event(event: dict) -> bool:
    title = str(event.get("title") or "").lower()
    return ("highest temperature in " in title or "lowest temperature in " in title)


def existing_weather_markets(ledger_path: Path) -> set[str]:
    seen: set[str] = set()
    if not ledger_path.exists():
        return seen
    for line in ledger_path.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if row.get("kind") == "signal" and row.get("lane") == "weather_ensemble_taker":
            if row.get("market_id") is not None:
                seen.add(str(row["market_id"]))
    return seen


def discover_active_events(max_pages: int = 20) -> list[dict]:
    out = []
    limit = 100
    for page in range(max_pages):
        rows = list_events(active=True, closed=False, limit=limit, offset=page * limit)
        if not rows:
            break
        out.extend(rows)
        if len(rows) < limit:
            break
    return out


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    spec_path = root / "config" / "frozen_v1.json"
    ledger_path = root / "data" / "signals.jsonl"
    scan_log = root / "data" / "weather_scan_log.jsonl"

    spec, sha = load_frozen_spec(spec_path)
    lane_cfg = spec["lanes"]["weather_ensemble_taker"]
    existing = existing_weather_markets(ledger_path)
    now = datetime.now(timezone.utc)

    events = discover_active_events()
    temp_events = [e for e in events if is_temperature_event(e)]

    markets_examined = 0
    recorded = 0
    errors = []
    for event in temp_events:
        try:
            target = target_date_from_event(event)
        except Exception as e:
            errors.append(f"date:{event.get('id')}:{e}")
            continue
        if target < now.date() or (target - now.date()).days > 7:
            continue

        for market in event.get("markets") or []:
            market_id = str(market.get("id") or market.get("conditionId") or "")
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
                )
            except Exception as e:
                errors.append(f"market:{market_id}:{type(e).__name__}:{e}")
                continue
            if signal is None:
                continue
            record_signal(ledger_path, signal, spec_sha256=sha)
            existing.add(signal.market_id)
            recorded += 1

    append_jsonl(scan_log, {
        "kind": "weather_scan",
        "observed_at": now.isoformat(),
        "spec_sha256": sha,
        "active_events": len(events),
        "temperature_events": len(temp_events),
        "markets_examined": markets_examined,
        "signals_recorded": recorded,
        "errors": errors[:50],
        "error_count": len(errors),
    })
    print(json.dumps({
        "active_events": len(events),
        "temperature_events": len(temp_events),
        "markets_examined": markets_examined,
        "signals_recorded": recorded,
        "error_count": len(errors),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
