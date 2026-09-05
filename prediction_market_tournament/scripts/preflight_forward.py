#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from tournament.adapters.polymarket import list_events
from tournament.adapters.rtds import TOPIC_RAW, TOPIC_TWAP60, stream_ticks
from tournament.freeze import (
    FORWARD_MARKER,
    implementation_hash,
    load_frozen_spec,
    runtime_fingerprint,
    runtime_hash,
)


def _data_artifacts(root: Path) -> list[str]:
    data = root / "data"
    if not data.exists():
        return []
    return sorted(
        path.name
        for path in data.iterdir()
        if path.is_file()
        and path.name != FORWARD_MARKER.name
        and path.stat().st_size > 0
    )


async def _rtds_probe(timeout_seconds: float = 20.0) -> list[str]:
    seen: set[str] = set()

    async def collect() -> None:
        async for tick in stream_ticks(symbol="btc/usd", stall_timeout_s=10.0):
            seen.add(tick.topic)
            if {TOPIC_RAW, TOPIC_TWAP60}.issubset(seen):
                return

    await asyncio.wait_for(collect(), timeout=timeout_seconds)
    return sorted(seen)


def main() -> int:
    root = Path(__file__).resolve().parents[1]
    marker = root / FORWARD_MARKER
    if marker.exists():
        raise SystemExit(
            "preflight is pre-start only; forward_start_v1.json already exists"
        )

    artifacts = _data_artifacts(root)
    if artifacts:
        raise SystemExit(
            "pre-start data directory is not clean: " + ", ".join(artifacts)
        )

    spec, spec_sha = load_frozen_spec(root / "config" / "frozen_v1.json")
    events = list_events(active=True, closed=False, limit=1, offset=0)
    if not isinstance(events, list) or not events:
        raise SystemExit("Polymarket Gamma probe returned no active events")

    topics = asyncio.run(_rtds_probe())
    expected = {TOPIC_RAW, TOPIC_TWAP60}
    if not expected.issubset(set(topics)):
        raise SystemExit(
            f"RTDS probe incomplete: expected={sorted(expected)} got={topics}"
        )

    result = {
        "ok": True,
        "project": spec["project"],
        "version": spec["version"],
        "spec_sha256": spec_sha,
        "implementation_sha256": implementation_hash(root),
        "runtime_sha256": runtime_hash(),
        "runtime": runtime_fingerprint(),
        "gamma_active_event_probe": True,
        "rtds_topics_seen": topics,
        "pre_start_data_clean": True,
        "checked_at_epoch": time.time(),
    }
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
