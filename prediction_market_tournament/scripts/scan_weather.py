#!/usr/bin/env python3
from __future__ import annotations

import argparse
from datetime import date

from tournament.adapters.polymarket import _get_json, GAMMA
from tournament.freeze import load_frozen_spec
from tournament.ledger import record_signal
from tournament.weather_market import weather_signal_from_market


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--event-slug", required=True)
    ap.add_argument("--date", required=True, help="YYYY-MM-DD")
    ap.add_argument("--ledger", default="data/signals.jsonl")
    ap.add_argument("--spec", default="config/frozen_v1.json")
    args = ap.parse_args()

    events = _get_json(f"{GAMMA}/events?slug={args.event_slug}")
    if not events:
        raise SystemExit(f"event not found: {args.event_slug}")
    event = events[0]
    target = date.fromisoformat(args.date)
    spec, sha = load_frozen_spec(args.spec)
    lane_cfg = spec["lanes"]["weather_ensemble_taker"]

    count = 0
    for market in event.get("markets") or []:
        try:
            signal = weather_signal_from_market(
                market,
                event=event,
                target_date=target,
                min_edge=float(lane_cfg["min_edge"]),
            )
        except (ValueError, LookupError) as e:
            print(f"SKIP {market.get('question')}: {e}")
            continue
        if signal is None:
            continue
        record_signal(args.ledger, signal, spec_sha256=sha)
        print(signal.as_json())
        count += 1

    print(f"recorded {count} weather signals")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
