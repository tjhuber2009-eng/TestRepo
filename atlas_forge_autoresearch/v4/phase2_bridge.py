"""Bridge frozen Phase-2 prior-work survivors into AUTORESEARCH v4.

Phase 2 is a finite development-only lane. This bridge consumes only candidates
that completed its v2 survivor follow-up, passed both cohort and
candidate-specific CSCV/PBO gates, and have an exact persisted source artifact
whose SHA-256 matches the replayed strategy.

No hidden-validation or final-OOS state is read here.
"""
from __future__ import annotations

import hashlib
import json
import math
import re
from typing import Iterable

import pandas as pd

from .continuous_bridge import (
    PromotionCandidate,
    _git_show,
    _validate_replay_source,
    replay_private_candidate,
    source_execution_adapter_blocker,
)


STATE_BRANCH = "phase2-autoresearch-state"
PROMOTION_QUEUE_PATH = (
    "atlas_forge_autoresearch/phase2_state/promotion_queue.json"
)

TARGET_SYMBOL = {
    "btc": "BTCUSDT",
    "eth": "ETHUSDT",
    "sol": "SOLUSDT",
    "spy": "SPY",
    "qqq": "QQQ",
    "tqqq": "TQQQ",
    "aapl": "AAPL",
    "nvda": "NVDA",
    "es": "ES=F",
    "nq": "NQ=F",
    "gold": "GC=F",
    "oil": "CL=F",
}

# Only adapters whose signal semantics have been implemented and tested in the
# v4 prop engine belong here. Missing families remain visible in the audit but
# cannot enter the FTMO optimization.
PROP_SIGNAL_ADAPTERS = {
    "bollinger_breakout_20_2": "phase2_bollinger_breakout_20_2",
}


def _finite(value, default=None):
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    return x if math.isfinite(x) else default


def _sha256_text(source: str) -> str:
    return hashlib.sha256(source.encode("utf-8")).hexdigest()


def load_phase2_promotion_queue() -> dict | None:
    raw = _git_show(STATE_BRANCH, PROMOTION_QUEUE_PATH)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if payload.get("protocol") != "nested_chronological_v3":
        return None
    if payload.get("stage") != "adaptive_followup_complete":
        return None
    if int(payload.get("followup_version", 0) or 0) < 2:
        return None
    if payload.get("hidden_validation_opened") is not False:
        raise RuntimeError("Phase-2 promotion queue opened hidden validation")
    if payload.get("final_oos_opened") is not False:
        raise RuntimeError("Phase-2 promotion queue opened final OOS")
    return payload


def _routing_value(row: dict, key: str, default=None):
    value = row.get(key)
    if value is not None:
        return value
    routing = row.get("routing")
    if isinstance(routing, dict) and routing.get(key) is not None:
        return routing.get(key)
    return default


def _strict_pbo(row: dict) -> float | None:
    values = [
        _finite(row.get("cohort_pbo")),
        _finite(row.get("candidate_pbo_when_selected")),
    ]
    values = [x for x in values if x is not None]
    return None if len(values) != 2 else max(values)


def load_promotion_source(row: dict) -> str:
    path = str(row.get("promotion_source_path") or "")
    expected = str(row.get("promotion_source_sha256") or "")
    strategy_sha = str(row.get("strategy_sha256") or "")
    if not path or not expected or not strategy_sha:
        raise RuntimeError(
            f"Phase-2 promotion artifact metadata missing: {row.get('track_id')}"
        )
    if expected != strategy_sha:
        raise RuntimeError(
            f"Phase-2 source hash metadata disagrees: {row.get('track_id')}"
        )
    source = _git_show(STATE_BRANCH, path)
    if source is None:
        raise RuntimeError(
            f"Phase-2 promotion source unavailable: {row.get('track_id')}"
        )
    actual = _sha256_text(source)
    if actual != expected:
        raise RuntimeError(
            f"Phase-2 promotion source hash mismatch for {row.get('track_id')}: "
            f"{actual} != {expected}"
        )
    family = str(row.get("family") or "")
    match = re.search(r'^FAMILY\s*=\s*["\']([^"\']+)["\']', source, re.M)
    if match is None or match.group(1) != family:
        raise RuntimeError(
            f"Phase-2 promotion source family mismatch: {row.get('track_id')}"
        )
    _validate_replay_source(source)
    return source


def ready_rows(
    profile: str,
    *,
    available_symbols: Iterable[str] | None = None,
) -> list[dict]:
    payload = load_phase2_promotion_queue()
    if not payload:
        return []
    allowed = None if available_symbols is None else set(available_symbols)
    rows = []
    for raw in payload.get("rows", []):
        if raw.get("profile") != profile:
            continue
        if raw.get("ready_for_v4_replay") is not True:
            continue
        target = str(raw.get("target") or "")
        symbol = TARGET_SYMBOL.get(target)
        if symbol is None:
            continue
        if allowed is not None and symbol not in allowed:
            continue
        row = dict(raw)
        row["symbol"] = symbol
        rows.append(row)
    rows.sort(
        key=lambda row: (
            _finite(row.get("score"), -1e99),
            _finite(row.get("cagr_pct"), -1e99),
            str(row.get("track_id")),
        ),
        reverse=True,
    )
    return rows


def replay_private_promotions(
    data: dict[str, pd.DataFrame],
    *,
    max_dd_pct: float,
    cost_stress_multiplier: float = 3.0,
) -> tuple[dict[str, pd.Series], dict]:
    rows = ready_rows("private", available_symbols=data.keys())
    eligible = {}
    audit = []
    for row in rows:
        record = dict(row)
        try:
            source = load_promotion_source(row)
            pbo = _strict_pbo(row)
            candidate = PromotionCandidate(
                track_id=str(row["track_id"]),
                profile="private",
                family=str(row["family"]),
                target=str(row["target"]),
                symbol=str(row["symbol"]),
                market=str(row.get("market") or ""),
                exactness="phase2_frozen_exact_source",
                evidence_grade=(
                    None
                    if row.get("evidence_grade") is None
                    else str(row.get("evidence_grade"))
                ),
                development_cagr_pct=float(row.get("cagr_pct") or 0.0),
                development_max_dd_pct=float(row.get("max_dd_pct") or 0.0),
                development_sharpe=float(row.get("sharpe") or 0.0),
                development_pf=float(row.get("pf") or 0.0),
                development_years=float(row.get("development_years") or 0.0),
                development_trades=int(row.get("trades") or 0),
                selection_score=float(row.get("score") or 0.0),
                multiple_test_qvalue=(
                    None
                    if row.get("bootstrap_fdr_qvalue") is None
                    else float(row["bootstrap_fdr_qvalue"])
                ),
                pbo=pbo,
                extreme_stress_return_pct=float(
                    row.get("extreme_stress_return_pct") or 0.0
                ),
                adapter=None,
            )
            returns, replay = replay_private_candidate(
                candidate,
                source,
                data[candidate.symbol],
                commission=float(row.get("commission") or 0.001),
                margin=float(row.get("margin") or 0.25),
                max_dd_pct=float(max_dd_pct),
                cost_stress_multiplier=float(cost_stress_multiplier),
            )
            replay["phase2_evidence"] = {
                "cohort_pbo": row.get("cohort_pbo"),
                "candidate_pbo_when_selected": row.get(
                    "candidate_pbo_when_selected"
                ),
                "strict_pbo": pbo,
                "bootstrap_fdr_qvalue": row.get("bootstrap_fdr_qvalue"),
                "promotion_source_sha256": row.get(
                    "promotion_source_sha256"
                ),
            }
            replay["source_routing"] = {
                "tested_timeframe": _routing_value(
                    row, "tested_timeframe"
                ),
                "route_stage": _routing_value(row, "route_stage",
                    _routing_value(row, "stage")),
                "source_route_verified": _routing_value(
                    row, "source_route_verified", False
                ),
                "source_native_match": _routing_value(
                    row, "source_native_match", False
                ),
                "signal_cadence": _routing_value(
                    row, "signal_cadence", "bar"
                ),
                "routing": row.get("routing"),
            }
            replay["replay_status"] = "completed"
            audit.append(replay)
            if replay["portfolio_eligible"]:
                eligible[f"phase2__{candidate.track_id}"] = returns
        except Exception as exc:
            record["replay_status"] = "error"
            record["error"] = f"{type(exc).__name__}: {exc}"
            audit.append(record)

    return eligible, {
        "available": bool(rows),
        "policy": (
            "consume only Phase-2 v2 frozen survivors with exact persisted "
            "source artifacts; require exact-source v4 replay, positive 3x-cost "
            "replay, private drawdown compliance, and both cohort/candidate PBO "
            "<= 0.55"
        ),
        "candidate_count": len(rows),
        "portfolio_eligible_count": len(eligible),
        "candidates": audit,
        "hidden_validation_opened": False,
        "final_oos_opened": False,
    }


def prop_transfer_candidates(
    available_symbols: Iterable[str],
) -> tuple[list[dict], dict]:
    rows = ready_rows("prop", available_symbols=available_symbols)
    supported = []
    audit = []
    for row in rows:
        record = dict(row)
        family = str(row.get("family") or "")
        adapter = PROP_SIGNAL_ADAPTERS.get(family)
        if adapter is None:
            record["transfer_status"] = "adapter_required"
            record["transfer_reason"] = "missing_phase2_signed_adapter"
            audit.append(record)
            continue
        try:
            source = load_promotion_source(row)
            blocker = source_execution_adapter_blocker(
                source,
                allow_short=True,
            )
            if blocker is not None:
                record["transfer_status"] = "adapter_required"
                record["transfer_reason"] = blocker
                audit.append(record)
                continue
            strict_pbo = _strict_pbo(row)
            if strict_pbo is None or strict_pbo > 0.55:
                record["transfer_status"] = "rejected"
                record["transfer_reason"] = "pbo_missing_or_above_limit"
                audit.append(record)
                continue
            params = {
                "family": "phase2_daily_signal",
                "source_family": family,
                "source_adapter": adapter,
                "source_track_id": str(row["track_id"]),
                "source_target": str(row["symbol"]),
                "source_min_bars": 40,
                "source_vol_lookback": 30,
                "source_pbo": float(strict_pbo),
                "source_artifact_sha256": str(
                    row["promotion_source_sha256"]
                ),
                "source_tested_timeframe": _routing_value(
                    row, "tested_timeframe"
                ),
                "source_route_stage": _routing_value(
                    row, "route_stage", _routing_value(row, "stage")
                ),
                "source_route_verified": _routing_value(
                    row, "source_route_verified", False
                ),
                "source_native_match": _routing_value(
                    row, "source_native_match", False
                ),
                "source_signal_cadence": _routing_value(
                    row, "signal_cadence", "bar"
                ),
            }
            supported.append(params)
            record["transfer_status"] = "supported"
            record["transfer_reason"] = "exact_signed_daily_signal_adapter"
            record["adapter"] = adapter
            record["strict_pbo"] = strict_pbo
            record["source_routing"] = {
                "tested_timeframe": _routing_value(
                    row, "tested_timeframe"
                ),
                "route_stage": _routing_value(row, "route_stage",
                    _routing_value(row, "stage")),
                "source_route_verified": _routing_value(
                    row, "source_route_verified", False
                ),
                "source_native_match": _routing_value(
                    row, "source_native_match", False
                ),
                "signal_cadence": _routing_value(
                    row, "signal_cadence", "bar"
                ),
                "routing": row.get("routing"),
            }
            audit.append(record)
        except Exception as exc:
            record["transfer_status"] = "error"
            record["error"] = f"{type(exc).__name__}: {exc}"
            audit.append(record)

    return supported, {
        "available": bool(rows),
        "policy": (
            "consume only ready Phase-2 prop survivors whose exact source "
            "artifact passes execution audit and whose signed daily signal "
            "has an explicit causal v4 adapter; source sizing is replaced by "
            "the v4 prop exposure optimizer"
        ),
        "candidate_count": len(rows),
        "supported_count": len(supported),
        "candidates": audit,
        "hidden_validation_opened": False,
        "final_oos_opened": False,
    }
