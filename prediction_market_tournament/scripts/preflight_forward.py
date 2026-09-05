#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from tournament.adapters.polymarket import (
    get_server_time,
    list_events,
)
from tournament.adapters.rtds import (
    TOPIC_RAW,
    TOPIC_TWAP60,
    stream_ticks,
)
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


def _clock_probe() -> dict[str, float]:
    before = time.time()
    server = get_server_time()
    after = time.time()
    midpoint = (before + after) / 2.0
    rtt = after - before
    offset = midpoint - server

    # /time is second-resolution. Allow quantization plus network delay, but
    # fail well inside the frozen 3-second crypto checkpoint allowance.
    if rtt > 2.0:
        raise RuntimeError(f"CLOB server-time RTT too high: {rtt:.3f}s")
    if abs(offset) > 1.5:
        raise RuntimeError(
            f"host clock differs from CLOB server by {offset:.3f}s"
        )
    return {
        "clob_time_rtt_seconds": rtt,
        "host_minus_clob_seconds": offset,
    }


async def _rtds_probe(
    timeout_seconds: float = 20.0,
    max_source_lag_seconds: float = 5.0,
) -> dict:
    accepted: dict[str, dict] = {}

    async def collect() -> None:
        async for tick in stream_ticks(
            symbol="btc/usd",
            stall_timeout_s=10.0,
        ):
            lag_seconds = (
                tick.receive_timestamp_ms
                - tick.source_timestamp_ms
            ) / 1000.0
            if abs(lag_seconds) > max_source_lag_seconds:
                continue

            current = accepted.get(tick.topic)
            if (
                current is None
                or abs(lag_seconds)
                < abs(float(current["source_to_host_lag_seconds"]))
            ):
                accepted[tick.topic] = {
                    "source_to_host_lag_seconds": lag_seconds,
                    "source_timestamp_ms": tick.source_timestamp_ms,
                    "receive_timestamp_ms": tick.receive_timestamp_ms,
                }

            if {TOPIC_RAW, TOPIC_TWAP60}.issubset(accepted):
                return

    await asyncio.wait_for(collect(), timeout=timeout_seconds)
    return accepted


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

    clock = _clock_probe()

    events = list_events(active=True, closed=False, limit=1, offset=0)
    if not isinstance(events, list) or not events:
        raise SystemExit("Polymarket Gamma probe returned no active events")

    rtds = asyncio.run(_rtds_probe())
    expected = {TOPIC_RAW, TOPIC_TWAP60}
    if not expected.issubset(rtds):
        raise SystemExit(
            f"RTDS fresh-data probe incomplete: expected={sorted(expected)} "
            f"got={sorted(rtds)}"
        )

    result = {
        "ok": True,
        "project": spec["project"],
        "version": spec["version"],
        "spec_sha256": spec_sha,
        "implementation_sha256": implementation_hash(root),
        "runtime_sha256": runtime_hash(),
        "runtime": runtime_fingerprint(),
        "clock": clock,
        "gamma_active_event_probe": True,
        "rtds": rtds,
        "rtds_freshness_limit_seconds": 5.0,
        "pre_start_data_clean": True,
        "checked_at_epoch": time.time(),
    }
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
