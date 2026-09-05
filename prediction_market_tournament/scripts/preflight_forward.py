#!/usr/bin/env python3
from __future__ import annotations

import asyncio
import json
import subprocess
import time
from datetime import datetime, timezone
from pathlib import Path

from tournament.adapters.aviation import station_coordinates
from tournament.adapters.open_meteo import (
    fetch_temperature_ensemble,
    member_daily_extremes,
)
from tournament.adapters.polymarket import (
    get_books,
    get_server_time,
    list_events,
    market_execution_rules,
    validate_book_identity,
)
from tournament.adapters.rtds import (
    TOPIC_RAW,
    TOPIC_TWAP60,
    stream_ticks,
)
from tournament.crypto_market import (
    discover_btc_5m_market,
    outcome_token_map,
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


def _weather_probe(
    *,
    models: list[str],
    min_members_per_model: int,
) -> dict:
    # KLAX is a representative live-resolution station and exercises the same
    # AviationWeather -> Open-Meteo path as the active weather scanner.
    lat, lon = station_coordinates("KLAX")
    target = datetime.now(timezone.utc).date()
    counts: dict[str, int] = {}

    for model in models:
        payload = fetch_temperature_ensemble(
            lat,
            lon,
            target,
            model=model,
            unit="fahrenheit",
            timezone="auto",
        )
        values = member_daily_extremes(payload, kind="max")
        count = len(values)
        if count < min_members_per_model:
            raise RuntimeError(
                f"weather preflight model {model!r} returned only "
                f"{count} members; minimum={min_members_per_model}"
            )
        counts[model] = count

    return {
        "station": "KLAX",
        "latitude": lat,
        "longitude": lon,
        "target_date": target.isoformat(),
        "model_member_counts": counts,
    }


def _btc_market_probe(*, timeout_seconds: float) -> dict:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        start_s = int(time.time() // 300) * 300
        try:
            event, market = discover_btc_5m_market(start_s)
            condition_id = str(market.get("conditionId") or "")
            rules = market_execution_rules(condition_id)
            if rules.taker_order_delay_enabled:
                raise RuntimeError(
                    "current BTC market has taker-order delay enabled"
                )

            tokens = outcome_token_map(market)
            requested = [tokens["UP"], tokens["DOWN"]]
            books = get_books(requested)
            if len(books) != 2:
                raise RuntimeError(
                    f"expected 2 BTC books, got {len(books)}"
                )

            by_asset = {
                str(book.get("asset_id") or ""): book
                for book in books
                if isinstance(book, dict)
            }
            if set(by_asset) != set(requested):
                raise RuntimeError(
                    "BTC batch books do not exactly match UP/DOWN tokens"
                )
            for side in ("UP", "DOWN"):
                validate_book_identity(
                    by_asset[tokens[side]],
                    token_id=tokens[side],
                    condition_id=condition_id,
                )

            return {
                "window_start_epoch_seconds": start_s,
                "event_slug": str(event.get("slug") or ""),
                "market_id": str(market.get("id") or ""),
                "condition_id": condition_id,
                "fee_rate": rules.fee_rate,
                "fee_exponent": rules.fee_exponent,
                "minimum_order_shares": rules.min_order_shares,
                "tick_size": rules.tick_size,
                "taker_order_delay_enabled":
                    rules.taker_order_delay_enabled,
                "book_assets": sorted(by_asset),
                "book_timestamps": {
                    side: str(
                        by_asset[tokens[side]].get("timestamp") or ""
                    )
                    for side in ("UP", "DOWN")
                },
            }
        except Exception as exc:
            last_error = exc
            time.sleep(1.0)

    raise RuntimeError(
        "could not verify a live executable BTC 5-minute market "
        f"within {timeout_seconds:.1f}s: "
        f"{type(last_error).__name__ if last_error else 'unknown'}:"
        f"{last_error}"
    )


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
    weather_cfg = spec["lanes"]["weather_ensemble_taker"]

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

    weather = _weather_probe(
        models=list(weather_cfg["weather_models"]),
        min_members_per_model=int(
            service_cfg["preflight_weather_min_members_per_model"]
        ),
    )

    max_rtds_lag = float(
        service_cfg["preflight_rtds_max_source_to_host_lag_seconds"]
    )
    rtds = asyncio.run(
        _rtds_probe(
            timeout_seconds=float(
                service_cfg["preflight_rtds_timeout_seconds"]
            ),
            max_source_lag_seconds=max_rtds_lag,
        )
    )
    expected = {TOPIC_RAW, TOPIC_TWAP60}
    if not expected.issubset(rtds):
        raise SystemExit(
            f"RTDS fresh-data probe incomplete: expected={sorted(expected)} "
            f"got={sorted(rtds)}"
        )

    btc_market = _btc_market_probe(
        timeout_seconds=float(
            service_cfg["preflight_btc_market_timeout_seconds"]
        )
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
        "weather": weather,
        "rtds": rtds,
        "rtds_freshness_limit_seconds": max_rtds_lag,
        "btc_market": btc_market,
        "pre_start_data_clean": True,
        "checked_at_epoch": time.time(),
    }
    print(json.dumps(result, sort_keys=True, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
