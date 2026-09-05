from __future__ import annotations

import json
from pathlib import Path

from .models import Signal, ResolvedTrade


def append_jsonl(path: str | Path, row: dict) -> None:
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")


def record_signal(path: str | Path, signal: Signal, *, spec_sha256: str) -> None:
    row = {"kind": "signal", "spec_sha256": spec_sha256, **signal.as_json()}
    append_jsonl(path, row)


def record_trade(path: str | Path, trade: ResolvedTrade, *, spec_sha256: str) -> None:
    row = {"kind": "resolved_trade", "spec_sha256": spec_sha256, **trade.as_json()}
    append_jsonl(path, row)
