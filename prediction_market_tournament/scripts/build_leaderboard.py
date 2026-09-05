#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from tournament.freeze import (
    implementation_hash,
    load_frozen_spec,
    require_forward_started,
)
from tournament.leaderboard import build_equal_window_leaderboard
from tournament.settlement import (
    read_jsonl,
    resolved_trade_from_json,
    signal_from_json,
)


def _dt(text: str) -> datetime:
    parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--window-start",
        help=(
            "ISO timestamp audit-only override. Without this override the "
            "verified PMT-FROZEN-V1 start marker is mandatory."
        ),
    )
    parser.add_argument(
        "--as-of",
        help="ISO timestamp; defaults to now",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    spec, spec_sha = load_frozen_spec(root / "config" / "frozen_v1.json")
    current_impl_sha = implementation_hash(root)

    if args.window_start:
        start = _dt(args.window_start)
        marker = None
    else:
        marker = require_forward_started(root)
        start = _dt(str(marker["started_at"]))
        current_impl_sha = str(marker["implementation_sha256"])

    as_of = _dt(args.as_of) if args.as_of else datetime.now(timezone.utc)

    signal_rows = [
        row
        for row in read_jsonl(root / "data" / "signals.jsonl")
        if (
            row.get("kind") == "signal"
            and row.get("spec_sha256") == spec_sha
            and row.get("implementation_sha256") == current_impl_sha
        )
    ]
    trade_rows = [
        row
        for row in read_jsonl(root / "data" / "resolved_trades.jsonl")
        if (
            row.get("kind") == "resolved_trade"
            and row.get("spec_sha256") == spec_sha
            and row.get("implementation_sha256") == current_impl_sha
        )
    ]
    signals = [signal_from_json(row) for row in signal_rows]
    trades = [resolved_trade_from_json(row) for row in trade_rows]

    rows = build_equal_window_leaderboard(
        signals,
        trades,
        window_start=start,
        as_of=as_of,
        window_days=int(spec["ranking"]["primary_window_days"]),
        risk_fraction=float(spec["risk"]["risk_fraction_per_trade"]),
        max_concurrent_positions=int(spec["risk"]["max_concurrent_positions"]),
    )

    def safe_row(row):
        value = row.as_dict()
        profit_factor = value.get("profit_factor")
        if isinstance(profit_factor, float) and not math.isfinite(profit_factor):
            value["profit_factor"] = "inf"
        return value

    payload = {
        "project": spec["project"],
        "version": spec["version"],
        "spec_sha256": spec_sha,
        "implementation_sha256": current_impl_sha,
        "window_start": start.astimezone(timezone.utc).isoformat(),
        "as_of": as_of.astimezone(timezone.utc).isoformat(),
        "window_days": int(spec["ranking"]["primary_window_days"]),
        "paper_account_usd": float(spec["risk"]["paper_account_usd"]),
        "position_sizing": "exact_observed_fixed_cash_no_hidden_compounding",
        "audit_override": marker is None,
        "lanes": [safe_row(row) for row in rows],
    }
    print(
        json.dumps(
            payload,
            sort_keys=True,
            indent=2,
            allow_nan=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
