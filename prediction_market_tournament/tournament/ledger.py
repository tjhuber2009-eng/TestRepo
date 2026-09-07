from __future__ import annotations

import json
import os
from pathlib import Path

from .models import ResolvedTrade, Signal


def append_jsonl(path: str | Path, row: dict) -> None:
    """Append exactly one compact JSON line with one O_APPEND system write."""
    file_path = Path(path)
    file_path.parent.mkdir(parents=True, exist_ok=True)
    payload = (
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n"
    ).encode("utf-8")
    fd = os.open(
        file_path,
        os.O_APPEND | os.O_CREAT | os.O_WRONLY,
        0o644,
    )
    try:
        written = os.write(fd, payload)
        if written != len(payload):
            raise OSError(
                f"short JSONL write: {written}/{len(payload)} bytes"
            )
        os.fsync(fd)
    finally:
        os.close(fd)


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
