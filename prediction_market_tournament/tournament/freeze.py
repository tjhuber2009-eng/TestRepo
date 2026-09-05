from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any


def canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(obj, sort_keys=True, separators=(",", ":")).encode("utf-8")


def spec_hash(spec: dict) -> str:
    return hashlib.sha256(canonical_json_bytes(spec)).hexdigest()


def load_frozen_spec(path: str | Path) -> tuple[dict, str]:
    p = Path(path)
    spec = json.loads(p.read_text(encoding="utf-8"))
    return spec, spec_hash(spec)
