"""Instrument/timeframe routing policy for Atlas Forge strategy evidence.

The research engine currently evaluates the Phase-1/Stock-FX lanes on one
explicit bar resolution per target (today: 1D). This module prevents that
implementation fact from being confused with source-faithful reproduction.

Evidence stages:
- reproduction: source-native market/instrument and native timeframe match,
  and the family has a verified source route.
- transfer: source route is verified, but Atlas intentionally evaluates a
  different supported market/instrument or timeframe.
- atlas_variant: the family/proxy is research-inspired or its source-native
  route is not sufficiently verified to claim reproduction.
- blocked: the requested bar resolution is unsupported by the adapter.
"""
from __future__ import annotations

import re
from typing import Any


def canonical_timeframe(value: Any) -> str:
    raw = str(value or "").strip().lower()
    compact = re.sub(r"[\s_\-]+", "", raw)
    aliases = {
        "daily": "1D", "day": "1D", "1d": "1D", "d1": "1D",
        "weekly": "1W", "week": "1W", "1w": "1W", "w1": "1W",
        "monthly": "1MO", "month": "1MO", "1mo": "1MO", "mn1": "1MO",
        "4hour": "4H", "4hours": "4H", "4h": "4H", "h4": "4H",
        "1hour": "1H", "1hours": "1H", "1h": "1H", "h1": "1H",
        "30minute": "30M", "30minutes": "30M", "30min": "30M", "30m": "30M",
        "15minute": "15M", "15minutes": "15M", "15min": "15M", "15m": "15M",
        "5minute": "5M", "5minutes": "5M", "5min": "5M", "5m": "5M",
        "1minute": "1M", "1minutes": "1M", "1min": "1M", "1m": "1M",
    }
    return aliases.get(compact, str(value or "").strip().upper())


def _norm_symbol(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


def routing_spec(family: dict[str, Any]) -> dict[str, Any]:
    raw = family.get("routing") or {}
    return {
        "native_markets": tuple(str(x) for x in raw.get("native_markets", [])),
        "native_instruments": tuple(
            _norm_symbol(x) for x in raw.get("native_instruments", [])
        ),
        "native_timeframes": tuple(
            canonical_timeframe(x) for x in raw.get("native_timeframes", [])
        ),
        "evaluation_timeframes": tuple(
            canonical_timeframe(x)
            for x in raw.get("evaluation_timeframes", ["1D"])
        ),
        "signal_cadence": str(raw.get("signal_cadence") or "bar"),
        "source_route_verified": bool(raw.get("source_route_verified", False)),
        "requires_multi_timeframe": bool(
            raw.get("requires_multi_timeframe", False)
        ),
        "requires_session_clock": bool(raw.get("requires_session_clock", False)),
        "requires_volume": bool(raw.get("requires_volume", False)),
        "requires_contract_data": bool(raw.get("requires_contract_data", False)),
    }


def classify_track(
    family: dict[str, Any],
    target: dict[str, Any],
) -> dict[str, Any]:
    spec = routing_spec(family)
    tested_tf = canonical_timeframe(target.get("timeframe", "1D"))
    market = str(target.get("market") or "")
    symbol = _norm_symbol(target.get("symbol"))

    evaluation_supported = (
        not spec["evaluation_timeframes"]
        or tested_tf in spec["evaluation_timeframes"]
    )
    native_market_match = (
        not spec["native_markets"] or market in spec["native_markets"]
    )
    native_timeframe_match = (
        not spec["native_timeframes"] or tested_tf in spec["native_timeframes"]
    )
    native_instrument_match = (
        not spec["native_instruments"] or symbol in spec["native_instruments"]
    )
    source_native_match = bool(
        spec["source_route_verified"]
        and native_market_match
        and native_timeframe_match
        and native_instrument_match
    )

    if not evaluation_supported:
        stage = "blocked"
        reason = (
            f"adapter does not support {tested_tf}; "
            f"supported={list(spec['evaluation_timeframes'])}"
        )
    elif not spec["source_route_verified"]:
        stage = "atlas_variant"
        reason = "source-native instrument/timeframe route not verified"
    elif source_native_match:
        stage = "reproduction"
        reason = "source-native market/instrument/timeframe route matched"
    else:
        stage = "transfer"
        reason = "intentional test outside verified source-native route"

    return {
        "stage": stage,
        "reason": reason,
        "tested_timeframe": tested_tf,
        "signal_cadence": spec["signal_cadence"],
        "source_route_verified": spec["source_route_verified"],
        "source_native_match": source_native_match,
        "native_markets": list(spec["native_markets"]),
        "native_instruments": list(spec["native_instruments"]),
        "native_timeframes": list(spec["native_timeframes"]),
        "evaluation_timeframes": list(spec["evaluation_timeframes"]),
        "requires_multi_timeframe": spec["requires_multi_timeframe"],
        "requires_session_clock": spec["requires_session_clock"],
        "requires_volume": spec["requires_volume"],
        "requires_contract_data": spec["requires_contract_data"],
    }


def development_adapter_ready(spec: dict[str, Any]) -> tuple[bool, str]:
    """Gate a reconstructed source spec against today's development adapter."""
    raw_tfs = spec.get("timeframes")
    if not raw_tfs:
        raw = spec.get("timeframe")
        raw_tfs = [] if raw in (None, "", "unknown") else [raw]
    tfs = {
        canonical_timeframe(x)
        for x in raw_tfs
        if str(x or "").strip().lower() != "unknown"
    }
    if not tfs:
        return False, "source timeframe unknown"
    if len(tfs) > 1 or bool(spec.get("requires_multi_timeframe")):
        return False, "multi-timeframe engine/data required"
    only = next(iter(tfs))
    if only != "1D":
        return False, f"{only} data/engine required; current adapter is 1D"
    if bool(spec.get("requires_session_clock")):
        return False, "session-clock engine required"
    return True, "1D adapter compatible"
