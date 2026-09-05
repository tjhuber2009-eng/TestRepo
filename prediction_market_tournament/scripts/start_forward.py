#!/usr/bin/env python3
from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path

from tournament.freeze import create_forward_marker


def _parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Deliberately start PMT-FROZEN-V1 exactly once."
    )
    parser.add_argument(
        "--started-at",
        help="Optional explicit ISO timestamp; defaults to current UTC time.",
    )
    args = parser.parse_args()

    root = Path(__file__).resolve().parents[1]
    marker = create_forward_marker(
        root,
        started_at=_parse_time(args.started_at),
    )
    print(json.dumps(marker, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
