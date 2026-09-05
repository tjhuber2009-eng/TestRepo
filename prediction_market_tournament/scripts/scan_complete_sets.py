#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tournament.adapters.polymarket import list_events
from tournament.complete_set_market import (
    is_neg_risk_multioutcome_event,
    snapshot_complete_set,
)
from tournament.freeze import load_frozen_spec, require_forward_started
from tournament.ledger import append_jsonl


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
    impl_sha = str(marker["implementation_sha256"])
    cfg = spec["lanes"]["complete_set"]
    ledger = root / "data" / "complete_set_snapshots.jsonl"
    scan_log = root / "data" / "complete_set_scan_log.jsonl"
    now = datetime.now(timezone.utc)

    events = discover_active_events()
    candidates = [
        event
        for event in events
        if is_neg_risk_multioutcome_event(event)
    ]
    opportunities = 0
    complete = 0
    errors: list[str] = []
    for event in candidates:
        event_id = str(event.get("id") or event.get("slug") or "unknown")
        try:
            snap = snapshot_complete_set(
                event,
                observed_at=now,
                min_edge=float(cfg["min_gross_edge"]),
            )
        except Exception as exc:
            errors.append(f"{event_id}:{type(exc).__name__}:{exc}")
            continue
        if snap is None:
            continue
        complete += 1
        row = {
            "spec_sha256": sha,
            "implementation_sha256": impl_sha,
            **snap.as_json(),
        }
        append_jsonl(ledger, row)
        if snap.opportunity.trade:
            opportunities += 1
            print(json.dumps(row, sort_keys=True))

    append_jsonl(
        scan_log,
        {
            "kind": "complete_set_scan",
            "observed_at": now.isoformat(),
            "spec_sha256": sha,
            "implementation_sha256": impl_sha,
            "active_events": len(events),
            "neg_risk_multioutcome_events": len(candidates),
            "complete_snapshots": complete,
            "gross_opportunities": opportunities,
            "error_count": len(errors),
            "errors": errors[:50],
            "note": (
                "shadow lane: gross edge only; no promotion until "
                "per-leg fees/fill simultaneity are modeled"
            ),
        },
    )
    print(
        json.dumps(
            {
                "active_events": len(events),
                "neg_risk_multioutcome_events": len(candidates),
                "complete_snapshots": complete,
                "gross_opportunities": opportunities,
                "error_count": len(errors),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
