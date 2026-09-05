#!/usr/bin/env python3
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

from tournament.adapters.polymarket import get_market_by_id
from tournament.freeze import require_forward_started
from tournament.ledger import append_jsonl, record_trade
from tournament.settlement import (
    read_jsonl,
    resolve_signal,
    signal_from_json,
)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    marker = require_forward_started(root)
    current_spec_sha = str(marker["spec_sha256"])
    current_impl_sha = str(marker["implementation_sha256"])

    signals_path = root / "data" / "signals.jsonl"
    trades_path = root / "data" / "resolved_trades.jsonl"
    scan_log = root / "data" / "settlement_scan_log.jsonl"

    signal_rows = [
        row
        for row in read_jsonl(signals_path)
        if row.get("kind") == "signal"
    ]
    trade_rows = [
        row
        for row in read_jsonl(trades_path)
        if row.get("kind") == "resolved_trade"
    ]
    resolved_ids = {
        str((row.get("signal") or {}).get("signal_id"))
        for row in trade_rows
        if isinstance(row.get("signal"), dict)
    }

    checked = 0
    settled = 0
    unresolved = 0
    errors: list[str] = []
    for row in signal_rows:
        signal_id = str(row.get("signal_id") or "")
        if not signal_id or signal_id in resolved_ids:
            continue

        row_spec_sha = str(row.get("spec_sha256") or "")
        row_impl_sha = str(row.get("implementation_sha256") or "")
        if row_spec_sha != current_spec_sha:
            errors.append(f"{signal_id}:spec_sha_mismatch")
            continue
        if row_impl_sha != current_impl_sha:
            errors.append(f"{signal_id}:implementation_sha_mismatch")
            continue

        try:
            signal = signal_from_json(row)
            market = get_market_by_id(signal.market_id)
            checked += 1
            trade = resolve_signal(signal, market)
        except Exception as exc:
            errors.append(f"{signal_id}:{type(exc).__name__}:{exc}")
            continue

        if trade is None:
            unresolved += 1
            continue

        record_trade(
            trades_path,
            trade,
            spec_sha256=row_spec_sha,
            implementation_sha256=row_impl_sha,
        )
        resolved_ids.add(signal_id)
        settled += 1

    append_jsonl(
        scan_log,
        {
            "kind": "settlement_scan",
            "observed_at": datetime.now(timezone.utc).isoformat(),
            "spec_sha256": current_spec_sha,
            "implementation_sha256": current_impl_sha,
            "signals_seen": len(signal_rows),
            "markets_checked": checked,
            "newly_settled": settled,
            "still_unresolved": unresolved,
            "error_count": len(errors),
            "errors": errors[:50],
        },
    )
    print(
        json.dumps(
            {
                "signals_seen": len(signal_rows),
                "markets_checked": checked,
                "newly_settled": settled,
                "still_unresolved": unresolved,
                "error_count": len(errors),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
