from __future__ import annotations

import json
from pathlib import Path

from .models import ResolvedTrade, Signal


def append_jsonl(path: str | Path, row: dict) -> None:
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    with file_path.open("a", encoding="utf-8") as handle:
        handle.write(
            json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
        )


def record_signal(
    path: str | Path,
    signal: Signal,
    *,
    spec_sha256: str,
    implementation_sha256: str | None = None,
) -> None:
    row = {
        "kind": "signal",
        "spec_sha256": spec_sha256,
        **signal.as_json(),
    }
    if implementation_sha256 is not None:
        row["implementation_sha256"] = implementation_sha256
    append_jsonl(path, row)


def record_trade(
    path: str | Path,
    trade: ResolvedTrade,
    *,
    spec_sha256: str,
    implementation_sha256: str | None = None,
) -> None:
    row = {
        "kind": "resolved_trade",
        "spec_sha256": spec_sha256,
        **trade.as_json(),
    }
    if implementation_sha256 is not None:
        row["implementation_sha256"] = implementation_sha256
    append_jsonl(path, row)
