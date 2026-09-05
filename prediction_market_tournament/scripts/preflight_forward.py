#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import subprocess
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

FROZEN_BRANCH = "prediction-market-tournament"


def _git(
    repo_root: Path,
    *args: str,
    check: bool = True,
) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", str(repo_root), *args],
        check=check,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )


def _repository_probe(root: Path) -> dict:
    repo_root = root.parent

    branch = _git(repo_root, "branch", "--show-current").stdout.strip()
    if branch != FROZEN_BRANCH:
        raise RuntimeError(
            f"expected branch {FROZEN_BRANCH!r}, got {branch!r}"
        )

    status = _git(
        repo_root,
        "status",
        "--porcelain=v1",
        "--untracked-files=all",
    ).stdout.splitlines()
    if status:
        raise RuntimeError(
            "working tree must be clean before V1 start: "
            + " | ".join(status[:20])
        )

    fetch = _git(
        repo_root,
        "fetch",
        "origin",
        FROZEN_BRANCH,
        check=False,
    )
    if fetch.returncode != 0:
        raise RuntimeError(
            "could not fetch frozen remote branch: "
            + fetch.stderr[-1000:]
        )

    head = _git(repo_root, "rev-parse", "HEAD").stdout.strip()
    remote = _git(
        repo_root,
        "rev-parse",
        f"origin/{FROZEN_BRANCH}",
    ).stdout.strip()
    if not head or head != remote:
        raise RuntimeError(
            "local HEAD must exactly equal the remote frozen branch "
            f"before start: local={head!r} remote={remote!r}"
        )

    return {
        "branch": branch,
        "head_sha": head,
        "remote_sha": remote,
        "working_tree_clean": True,
    }


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


def _clock_probe(
    *,
    max_rtt_seconds: float,
    max_abs_offset_seconds: float,
) -> dict[str, float]:
    before = time.time()
    server = get_server_time()
    after = time.time()
    midpoint = (before + after) / 2.0
    rtt = after - before
    offset = midpoint - server

    # /time is second-resolution. Allow quantization plus network delay, but
    # fail comfortably inside the frozen 3-second crypto checkpoint allowance.
    if rtt > max_rtt_seconds:
        raise RuntimeError(
            f"CLOB server-time RTT too high: {rtt:.3f}s "
            f"> {max_rtt_seconds:.3f}s"
        )
    if abs(offset) > max_abs_offset_seconds:
        raise RuntimeError(
            f"host clock differs from CLOB server by {offset:.3f}s; "
            f"limit={max_abs_offset_seconds:.3f}s"
        )
    return {
        "clob_time_rtt_seconds": rtt,
        "host_minus_clob_seconds": offset,
    }


async def _rtds_probe(
    *,
    timeout_seconds: float,
    max_source_lag_seconds: float,
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
    service_cfg = spec["service"]

    repository = _repository_probe(root)

    clock = _clock_probe(
        max_rtt_seconds=float(
            service_cfg["preflight_clob_time_max_rtt_seconds"]
        ),
        max_abs_offset_seconds=float(
            service_cfg["preflight_clock_max_abs_offset_seconds"]
        ),
    )

    events = list_events(active=True, closed=False, limit=1, offset=0)
    if not isinstance(events, list) or not events:
        raise SystemExit("Polymarket Gamma probe returned no active events")

    max_rtds_lag = float(
        service_cfg["preflight_rtds_max_source_to_host_lag_seconds"]
    )
    rtds = asyncio.run(
        _rtds_probe(
            timeout_seconds=20.0,
            max_source_lag_seconds=max_rtds_lag,
        )
    )
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
        "repository": repository,
        "clock": clock,
        "gamma_active_event_probe": True,
        "rtds": rtds,
        "rtds_freshness_limit_seconds": max_rtds_lag,
        "pre_start_data_clean": True,
        "checked_at_epoch": time.time(),
    }
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
