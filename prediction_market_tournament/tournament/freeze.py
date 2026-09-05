from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

FORWARD_MARKER = Path("data") / "forward_start_v1.json"


def canonical_json_bytes(obj: Any) -> bytes:
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")


def spec_hash(spec: dict) -> str:
    return hashlib.sha256(canonical_json_bytes(spec)).hexdigest()


def load_frozen_spec(path: str | Path) -> tuple[dict, str]:
    file_path = Path(path)
    spec = json.loads(file_path.read_text(encoding="utf-8"))
    return spec, spec_hash(spec)


def implementation_hash(root: str | Path) -> str:
    """Hash all executable PMT Python code, independent of Git metadata."""
    base = Path(root)
    files: list[Path] = []
    for relative in ("tournament", "scripts"):
        directory = base / relative
        if directory.exists():
            files.extend(
                path
                for path in directory.rglob("*.py")
                if "__pycache__" not in path.parts
            )

    digest = hashlib.sha256()
    for path in sorted(files, key=lambda item: item.relative_to(base).as_posix()):
        relative = path.relative_to(base).as_posix().encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(relative).to_bytes(4, "big"))
        digest.update(relative)
        digest.update(len(payload).to_bytes(8, "big"))
        digest.update(payload)
    return digest.hexdigest()


def _nonempty(path: Path) -> bool:
    return path.exists() and bool(path.read_text(encoding="utf-8").strip())


def create_forward_marker(
    root: str | Path,
    *,
    started_at: datetime | None = None,
) -> dict:
    """Deliberately start PMT-FROZEN-V1 exactly once.

    Refuses to start if signal/trade ledgers already contain observations.
    """
    base = Path(root)
    marker_path = base / FORWARD_MARKER
    if marker_path.exists():
        raise FileExistsError("forward start marker already exists")

    for relative in (
        Path("data") / "signals.jsonl",
        Path("data") / "resolved_trades.jsonl",
    ):
        path = base / relative
        if _nonempty(path):
            raise RuntimeError(
                f"refusing to start with pre-existing forward ledger: {relative}"
            )

    spec, spec_sha = load_frozen_spec(base / "config" / "frozen_v1.json")
    when = started_at or datetime.now(timezone.utc)
    if when.tzinfo is None:
        raise ValueError("started_at must be timezone-aware")

    marker = {
        "project": spec["project"],
        "version": spec["version"],
        "started_at": when.astimezone(timezone.utc).isoformat(),
        "spec_sha256": spec_sha,
        "implementation_sha256": implementation_hash(base),
    }
    marker_path.parent.mkdir(parents=True, exist_ok=True)
    with marker_path.open("x", encoding="utf-8") as handle:
        handle.write(
            json.dumps(marker, sort_keys=True, separators=(",", ":")) + "\n"
        )
    return marker


def load_forward_marker(root: str | Path) -> dict:
    base = Path(root)
    marker_path = base / FORWARD_MARKER
    if not marker_path.exists():
        raise RuntimeError(
            "PMT-FROZEN-V1 forward clock has not been deliberately started"
        )
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if not isinstance(marker, dict):
        raise RuntimeError("invalid forward start marker")
    return marker


def require_forward_started(root: str | Path) -> dict:
    """Verify the immutable start marker against current spec and code."""
    base = Path(root)
    marker = load_forward_marker(base)
    spec, spec_sha = load_frozen_spec(base / "config" / "frozen_v1.json")
    impl_sha = implementation_hash(base)

    expected = {
        "project": spec["project"],
        "version": spec["version"],
        "spec_sha256": spec_sha,
        "implementation_sha256": impl_sha,
    }
    for key, value in expected.items():
        if marker.get(key) != value:
            raise RuntimeError(
                f"forward freeze mismatch for {key}: "
                f"marker={marker.get(key)!r} current={value!r}"
            )
    return marker
