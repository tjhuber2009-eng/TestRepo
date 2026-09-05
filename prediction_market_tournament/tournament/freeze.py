from __future__ import annotations

import hashlib
import importlib.metadata
import json
import platform
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


def runtime_fingerprint() -> dict[str, str]:
    """Audit runtime details, including the exact patch version."""
    version = platform.python_version()
    pieces = version.split(".")
    major_minor = ".".join(pieces[:2])
    return {
        "python_implementation": platform.python_implementation(),
        "python_major_minor": major_minor,
        "python_version_full": version,
        "system": platform.system(),
        "machine": platform.machine(),
        "websockets_version": importlib.metadata.version("websockets"),
    }


def runtime_semantics_fingerprint() -> dict[str, str]:
    """Runtime properties enforced across V1 and replacement hosts.

    Python patch releases remain recorded for audit but are not treated as a
    strategy mutation; pyproject already freezes the interpreter family to
    CPython 3.12.x. Transport-library version and architecture remain exact.
    """
    audit = runtime_fingerprint()
    return {
        "python_implementation": audit["python_implementation"],
        "python_major_minor": audit["python_major_minor"],
        "system": audit["system"],
        "machine": audit["machine"],
        "websockets_version": audit["websockets_version"],
    }


def runtime_hash() -> str:
    return hashlib.sha256(
        canonical_json_bytes(runtime_semantics_fingerprint())
    ).hexdigest()


def implementation_hash(root: str | Path) -> str:
    """Hash PMT executable/runtime/deployment files, independent of Git."""
    base = Path(root)
    labeled_files: list[tuple[str, Path]] = []

    for relative in ("tournament", "scripts", "deploy"):
        directory = base / relative
        if not directory.exists():
            continue
        for path in directory.rglob("*"):
            if (
                path.is_file()
                and "__pycache__" not in path.parts
                and not path.name.endswith((".pyc", ".pyo"))
            ):
                labeled_files.append(
                    (path.relative_to(base).as_posix(), path)
                )

    for root_file in ("pyproject.toml", ".gitignore"):
        path = base / root_file
        if path.exists():
            labeled_files.append((root_file, path))

    digest = hashlib.sha256()
    for label, path in sorted(labeled_files):
        label_bytes = label.encode("utf-8")
        payload = path.read_bytes()
        digest.update(len(label_bytes).to_bytes(4, "big"))
        digest.update(label_bytes)
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

    Refuses to start if any forward-data artifact already exists.
    """
    base = Path(root)
    marker_path = base / FORWARD_MARKER
    if marker_path.exists():
        raise FileExistsError("forward start marker already exists")

    data_dir = base / "data"
    if data_dir.exists():
        contaminated = [
            path
            for path in data_dir.iterdir()
            if path.is_file()
            and path.name != FORWARD_MARKER.name
            and _nonempty(path)
        ]
        if contaminated:
            names = ", ".join(sorted(path.name for path in contaminated))
            raise RuntimeError(
                "refusing to start with pre-existing forward data: "
                f"{names}"
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
        "runtime_sha256": runtime_hash(),
        "runtime": runtime_fingerprint(),
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
    """Verify the immutable start marker against current spec/code/runtime."""
    base = Path(root)
    marker = load_forward_marker(base)
    spec, spec_sha = load_frozen_spec(base / "config" / "frozen_v1.json")

    expected = {
        "project": spec["project"],
        "version": spec["version"],
        "spec_sha256": spec_sha,
        "implementation_sha256": implementation_hash(base),
        "runtime_sha256": runtime_hash(),
    }
    for key, value in expected.items():
        if marker.get(key) != value:
            raise RuntimeError(
                f"forward freeze mismatch for {key}: "
                f"marker={marker.get(key)!r} current={value!r}"
            )
    return marker
