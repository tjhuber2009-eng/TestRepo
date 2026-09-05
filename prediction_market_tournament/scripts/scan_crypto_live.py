#!/usr/bin/env python3
from __future__ import annotations

import argparse
import asyncio
import json
import time
from collections import deque
from datetime import datetime, timezone
from pathlib import Path

from tournament.adapters.rtds import (
    TOPIC_RAW,
    TOPIC_TWAP60,
    stream_ticks,
)
from tournament.crypto_market import (
    WINDOW_SECONDS,
    crypto_signal_from_market,
    discover_btc_5m_market,
)
from tournament.freeze import (
    load_frozen_spec,
    require_forward_started,
)
from tournament.ledger import append_jsonl, record_signal
from tournament.settlement import read_jsonl

WINDOW_MS = WINDOW_SECONDS * 1000.0


def existing_crypto_lane_markets(path: Path) -> set[tuple[str, str]]:
    out: set[tuple[str, str]] = set()
    for row in read_jsonl(path):
        if row.get("kind") != "signal":
            continue
        lane = str(row.get("lane") or "")
        market_id = str(row.get("market_id") or "")
        if lane.startswith("crypto_") and market_id:
            out.add((lane, market_id))
    return out


def _window_start_ms(timestamp_ms: float) -> float:
    return float(int(timestamp_ms // WINDOW_MS) * int(WINDOW_MS))


def _utc_from_ms(timestamp_ms: float) -> str:
    return datetime.fromtimestamp(
        timestamp_ms / 1000.0,
        tz=timezone.utc,
    ).isoformat()


async def run(duration_seconds: float) -> int:
    root = Path(__file__).resolve().parents[1]
    marker = require_forward_started(root)
    spec, spec_sha = load_frozen_spec(root / "config" / "frozen_v1.json")
    impl_sha = str(marker["implementation_sha256"])

    signals_path = root / "data" / "signals.jsonl"
    scan_log = root / "data" / "crypto_scan_log.jsonl"
    existing = existing_crypto_lane_markets(signals_path)

    paper_stake_usd = (
        float(spec["risk"]["paper_account_usd"])
        * float(spec["risk"]["risk_fraction_per_trade"])
    )
    lane_configs = {
        lane: spec["lanes"][lane]
        for lane in ("crypto_twap_taker", "crypto_late_resolution")
    }

    raw_points: deque[tuple[float, float]] = deque()
    states: dict[float, dict] = {}
    missed_strike_windows: set[float] = set()
    started_monotonic = time.monotonic()

    async for tick in stream_ticks(
        symbol=str(lane_configs["crypto_twap_taker"]["rtds_symbol"]),
        stall_timeout_s=30.0,
    ):
        if duration_seconds > 0 and (
            time.monotonic() - started_monotonic >= duration_seconds
        ):
            break

        receive_ms = float(tick.receive_timestamp_ms)

        if tick.topic == TOPIC_RAW:
            raw_points.append(
                (float(tick.source_timestamp_ms), float(tick.value))
            )
            cutoff = receive_ms - 10 * 60 * 1000.0
            while raw_points and raw_points[0][0] < cutoff:
                raw_points.popleft()

        if tick.topic == TOPIC_TWAP60:
            start_ms = _window_start_ms(float(tick.source_timestamp_ms))
            strike_lag_ms = float(tick.source_timestamp_ms) - start_ms
            max_strike_lag_ms = (
                float(
                    lane_configs["crypto_twap_taker"][
                        "strike_capture_max_lag_seconds"
                    ]
                )
                * 1000.0
            )
            if (
                0.0 <= strike_lag_ms <= max_strike_lag_ms
                and start_ms not in states
            ):
                states[start_ms] = {
                    "strike": float(tick.value),
                    "strike_source_timestamp_ms": float(
                        tick.source_timestamp_ms
                    ),
                    "event": None,
                    "market": None,
                    "last_discovery_attempt_ms": 0.0,
                    "done": set(),
                }
                append_jsonl(
                    scan_log,
                    {
                        "kind": "crypto_strike_capture",
                        "observed_at": _utc_from_ms(receive_ms),
                        "spec_sha256": spec_sha,
                        "implementation_sha256": impl_sha,
                        "window_start_ms": start_ms,
                        "strike": float(tick.value),
                        "strike_source_timestamp_ms": float(
                            tick.source_timestamp_ms
                        ),
                        "strike_source_lag_seconds": strike_lag_ms / 1000.0,
                    },
                )
            elif (
                start_ms not in states
                and start_ms not in missed_strike_windows
                and strike_lag_ms > max_strike_lag_ms
                and strike_lag_ms <= 10_000.0
            ):
                missed_strike_windows.add(start_ms)
                append_jsonl(
                    scan_log,
                    {
                        "kind": "crypto_strike_missed",
                        "observed_at": _utc_from_ms(receive_ms),
                        "spec_sha256": spec_sha,
                        "implementation_sha256": impl_sha,
                        "window_start_ms": start_ms,
                        "first_seen_twap_lag_seconds": strike_lag_ms / 1000.0,
                    },
                )

        for start_ms, state in list(states.items()):
            end_ms = start_ms + WINDOW_MS
            if receive_ms > end_ms + 60_000:
                states.pop(start_ms, None)
                continue

            if (
                state["market"] is None
                and receive_ms - state["last_discovery_attempt_ms"] >= 5_000
            ):
                state["last_discovery_attempt_ms"] = receive_ms
                try:
                    event, market = await asyncio.to_thread(
                        discover_btc_5m_market,
                        int(start_ms // 1000),
                    )
                    state["event"] = event
                    state["market"] = market
                    append_jsonl(
                        scan_log,
                        {
                            "kind": "crypto_market_discovered",
                            "observed_at": _utc_from_ms(receive_ms),
                            "spec_sha256": spec_sha,
                            "implementation_sha256": impl_sha,
                            "window_start_ms": start_ms,
                            "event_slug": str(event.get("slug") or ""),
                            "market_id": str(market.get("id") or ""),
                        },
                    )
                except Exception as exc:
                    append_jsonl(
                        scan_log,
                        {
                            "kind": "crypto_market_discovery_error",
                            "observed_at": _utc_from_ms(receive_ms),
                            "spec_sha256": spec_sha,
                            "implementation_sha256": impl_sha,
                            "window_start_ms": start_ms,
                            "error": f"{type(exc).__name__}:{exc}",
                        },
                    )

            if tick.topic != TOPIC_RAW:
                continue

            for lane, cfg in lane_configs.items():
                if lane in state["done"]:
                    continue

                target_remaining = float(cfg["entry_seconds_remaining"])
                checkpoint_ms = end_ms - target_remaining * 1000.0
                max_lag_ms = float(cfg["checkpoint_max_lag_seconds"]) * 1000.0

                if receive_ms > checkpoint_ms + max_lag_ms:
                    state["done"].add(lane)
                    append_jsonl(
                        scan_log,
                        {
                            "kind": "crypto_checkpoint_missed",
                            "observed_at": _utc_from_ms(receive_ms),
                            "spec_sha256": spec_sha,
                            "implementation_sha256": impl_sha,
                            "lane": lane,
                            "window_start_ms": start_ms,
                            "checkpoint_ms": checkpoint_ms,
                            "reason": (
                                "no eligible raw tick/market evaluation "
                                "inside frozen checkpoint lag"
                            ),
                        },
                    )
                    continue

                if receive_ms < checkpoint_ms:
                    continue

                state["done"].add(lane)
                if state["market"] is None or state["event"] is None:
                    append_jsonl(
                        scan_log,
                        {
                            "kind": "crypto_checkpoint_missed",
                            "observed_at": _utc_from_ms(receive_ms),
                            "spec_sha256": spec_sha,
                            "implementation_sha256": impl_sha,
                            "lane": lane,
                            "window_start_ms": start_ms,
                            "checkpoint_ms": checkpoint_ms,
                            "reason": "market_not_discovered_by_checkpoint",
                        },
                    )
                    continue

                market_id = str(state["market"].get("id") or "")
                if (lane, market_id) in existing:
                    append_jsonl(
                        scan_log,
                        {
                            "kind": "crypto_checkpoint_duplicate_guard",
                            "observed_at": _utc_from_ms(receive_ms),
                            "spec_sha256": spec_sha,
                            "implementation_sha256": impl_sha,
                            "lane": lane,
                            "market_id": market_id,
                            "window_start_ms": start_ms,
                        },
                    )
                    continue

                try:
                    signal = await asyncio.to_thread(
                        crypto_signal_from_market,
                        state["market"],
                        event=state["event"],
                        lane=lane,
                        strike=float(state["strike"]),
                        raw_points=list(raw_points),
                        window_start_ms=start_ms,
                        lane_cfg=cfg,
                        size_usd=paper_stake_usd,
                    )
                except Exception as exc:
                    append_jsonl(
                        scan_log,
                        {
                            "kind": "crypto_checkpoint_error",
                            "observed_at": datetime.now(timezone.utc).isoformat(),
                            "spec_sha256": spec_sha,
                            "implementation_sha256": impl_sha,
                            "lane": lane,
                            "market_id": market_id,
                            "window_start_ms": start_ms,
                            "error": f"{type(exc).__name__}:{exc}",
                        },
                    )
                    continue

                if signal is None:
                    append_jsonl(
                        scan_log,
                        {
                            "kind": "crypto_checkpoint_no_signal",
                            "observed_at": datetime.now(timezone.utc).isoformat(),
                            "spec_sha256": spec_sha,
                            "implementation_sha256": impl_sha,
                            "lane": lane,
                            "market_id": market_id,
                            "window_start_ms": start_ms,
                        },
                    )
                    continue

                record_signal(
                    signals_path,
                    signal,
                    spec_sha256=spec_sha,
                    implementation_sha256=impl_sha,
                )
                existing.add((lane, signal.market_id))
                append_jsonl(
                    scan_log,
                    {
                        "kind": "crypto_signal_recorded",
                        "observed_at": signal.observed_at.astimezone(
                            timezone.utc
                        ).isoformat(),
                        "spec_sha256": spec_sha,
                        "implementation_sha256": impl_sha,
                        "lane": lane,
                        "market_id": signal.market_id,
                        "signal_id": signal.signal_id,
                        "window_start_ms": start_ms,
                        "side": signal.side,
                        "fair_probability": signal.fair_probability,
                        "market_price": signal.market_price,
                        "entry_fee_usd": signal.entry_fee_usd,
                    },
                )

    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--duration-seconds",
        type=float,
        default=0.0,
        help="0 means run continuously; workflow may set a bounded duration.",
    )
    args = parser.parse_args()
    return asyncio.run(run(args.duration_seconds))


if __name__ == "__main__":
    raise SystemExit(main())
