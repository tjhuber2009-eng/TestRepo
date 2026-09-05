#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
import math
from datetime import datetime, timezone
from pathlib import Path

from tournament.freeze import load_frozen_spec
from tournament.leaderboard import (
    build_equal_window_leaderboard,
)
from tournament.settlement import (
    read_jsonl,
    resolved_trade_from_json,
    signal_from_json,
)


def _dt(text: str) -> datetime:
    out = datetime.fromisoformat(
        text.replace("Z", "+00:00")
    )
    if out.tzinfo is None:
        out = out.replace(
            tzinfo=timezone.utc
        )
    return out


def _window_start(
    root: Path, explicit: str | None
) -> datetime:
    if explicit:
        return _dt(explicit)

    start_path = (
        root
        / "data"
        / "forward_start_v1.json"
    )
    if not start_path.exists():
        raise SystemExit(
            "PMT-FROZEN-V1 forward clock has "
            "not been deliberately started. "
            "Create data/forward_start_v1.json "
            "only when the start decision is "
            "final, or pass --window-start for "
            "an audit-only replay."
        )
    payload = json.loads(
        start_path.read_text(
            encoding="utf-8"
        )
    )
    return _dt(str(payload["started_at"]))


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--window-start",
        help="ISO timestamp; audit-only override",
    )
    parser.add_argument(
        "--as-of",
        help="ISO timestamp; defaults to now",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    spec, sha = load_frozen_spec(
        root / "config" / "frozen_v1.json"
    )
    start = _window_start(
        root, args.window_start
    )
    as_of = (
        _dt(args.as_of)
        if args.as_of
        else datetime.now(timezone.utc)
    )

    signal_rows = [
        row
        for row in read_jsonl(
            root / "data" / "signals.jsonl"
        )
        if row.get("kind") == "signal"
    ]
    trade_rows = [
        row
        for row in read_jsonl(
            root
            / "data"
            / "resolved_trades.jsonl"
        )
        if row.get("kind")
        == "resolved_trade"
    ]
    signals = [
        signal_from_json(row)
        for row in signal_rows
        if row.get("spec_sha256") == sha
    ]
    trades = [
        resolved_trade_from_json(row)
        for row in trade_rows
        if row.get("spec_sha256") == sha
    ]

    rows = build_equal_window_leaderboard(
        signals,
        trades,
        window_start=start,
        as_of=as_of,
        window_days=int(
            spec["ranking"][
                "primary_window_days"
            ]
        ),
        risk_fraction=float(
            spec["risk"][
                "risk_fraction_per_trade"
            ]
        ),
        max_concurrent_positions=int(
            spec["risk"][
                "max_concurrent_positions"
            ]
        ),
    )

    def safe_row(row):
        value = row.as_dict()
        pf = value.get("profit_factor")
        if (
            isinstance(pf, float)
            and not math.isfinite(pf)
        ):
            value["profit_factor"] = "inf"
        return value

    payload = {
        "project": spec["project"],
        "version": spec["version"],
        "spec_sha256": sha,
        "window_start": start.astimezone(
            timezone.utc
        ).isoformat(),
        "as_of": as_of.astimezone(
            timezone.utc
        ).isoformat(),
        "window_days": int(
            spec["ranking"][
                "primary_window_days"
            ]
        ),
        "lanes": [
            safe_row(row) for row in rows
        ],
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
