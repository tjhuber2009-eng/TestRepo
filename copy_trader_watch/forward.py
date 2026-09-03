from __future__ import annotations

import math
from datetime import datetime
from typing import Any

from models import AdapterResult, TraderSnapshot, score_evidence, score_forward, score_snapshot


def _num(value: Any) -> float | None:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _key(record: TraderSnapshot) -> str:
    return f"{record.platform}:{record.trader_id}".casefold()


def _metric(record: TraderSnapshot, observation_date: str) -> dict[str, Any] | None:
    meta = record.metadata or {}
    kind = meta.get("forward_metric_kind")
    value = _num(meta.get("forward_metric_value"))
    base = _num(meta.get("forward_metric_base"))
    period = meta.get("forward_period_key")

    # Backward-compatible inference for adapters that have not supplied explicit
    # forward metadata yet.
    if not kind and record.platform == "etoro" and record.return_pct is not None:
        kind = "cumulative_pct"
        value = record.return_pct
        period = observation_date[:4]
    elif not kind and record.platform == "mql5" and record.return_pct is not None:
        kind = "cumulative_pct"
        value = record.return_pct
        period = "all"

    if kind is None or value is None:
        return None
    return {
        "date": observation_date,
        "observed_at": record.observed_at,
        "kind": str(kind),
        "value": value,
        "base": base,
        "period": str(period) if period is not None else None,
    }


def _step_return(prev: dict[str, Any], current: dict[str, Any]) -> float | None:
    if prev.get("kind") != current.get("kind"):
        return None
    kind = current.get("kind")
    p = _num(prev.get("value"))
    c = _num(current.get("value"))
    if p is None or c is None:
        return None

    if kind == "cumulative_pct":
        if prev.get("period") != current.get("period"):
            # YTD-style reset: the new period starts from zero.
            return c
        p_factor = 1.0 + p / 100.0
        c_factor = 1.0 + c / 100.0
        if p_factor <= 0:
            return None
        return (c_factor / p_factor - 1.0) * 100.0

    if kind in {"pnl_index", "periodic_pnl_index"}:
        delta = c - p
        if kind == "periodic_pnl_index" and prev.get("period") != current.get("period"):
            # The index resets at each period boundary (e.g. calendar month).
            delta = c
        base = _num(prev.get("base")) or _num(current.get("base"))
        if base is None or base <= 0:
            return None
        return delta / base * 100.0

    return None


def _metrics(observations: list[dict[str, Any]]) -> dict[str, Any]:
    observations = sorted(observations, key=lambda x: (x.get("date", ""), x.get("observed_at", "")))
    if not observations:
        return {
            "forward_observations": 0,
            "forward_return_pct": None,
            "forward_max_drawdown_pct": None,
            "forward_profit_factor": None,
            "forward_win_rate_pct": None,
            "forward_score": None,
        }

    equity = 1.0
    curve = [equity]
    steps: list[float] = []
    for prev, current in zip(observations, observations[1:]):
        step = _step_return(prev, current)
        if step is None:
            continue
        # A <= -100% step is a terminal loss in percentage-return space.
        step = max(-100.0, step)
        equity *= max(0.0, 1.0 + step / 100.0)
        curve.append(equity)
        steps.append(step)

    if not steps:
        return {
            "forward_observations": len(observations),
            "forward_return_pct": None,
            "forward_max_drawdown_pct": None,
            "forward_profit_factor": None,
            "forward_win_rate_pct": None,
            "forward_score": None,
        }

    ret = (equity - 1.0) * 100.0
    peak = curve[0]
    worst = 0.0
    for value in curve:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, (value / peak - 1.0) * 100.0)

    wins = [x for x in steps if x > 0]
    losses = [x for x in steps if x < 0]
    pf = None
    if wins and losses:
        pf = sum(wins) / abs(sum(losses)) if sum(losses) else None
    elif wins:
        pf = 999.0
    win_rate = len(wins) / len(steps) * 100.0 if steps else None

    return {
        "forward_observations": len(observations),
        "forward_return_pct": ret,
        "forward_max_drawdown_pct": worst,
        "forward_profit_factor": pf,
        "forward_win_rate_pct": win_rate,
        "forward_score": score_forward(ret, worst),
    }


def update_tracker(
    results: list[AdapterResult],
    state: dict[str, Any] | None,
    observation_date: str,
) -> dict[str, Any]:
    """Update persistent forward-test state and attach metrics to current records.

    A small historical sample is never a rejection condition. Every returned record
    marked ``forward_test_eligible`` is registered immediately. If its metric can be
    observed twice, it receives a forward score; evidence confidence remains separate.
    """
    state = state if isinstance(state, dict) else {}
    tracks = state.setdefault("tracks", {})

    current: dict[str, TraderSnapshot] = {}
    for result in results:
        for record in result.records:
            record.research_score = score_snapshot(record)
            record.evidence_score = score_evidence(record)
            current[_key(record)] = record
            if not record.forward_test_eligible:
                continue

            key = _key(record)
            track = tracks.setdefault(
                key,
                {
                    "platform": record.platform,
                    "trader_id": record.trader_id,
                    "name": record.name,
                    "first_seen": observation_date,
                    "last_seen": observation_date,
                    "observations": [],
                },
            )
            track["name"] = record.name
            track["last_seen"] = observation_date
            obs = _metric(record, observation_date)
            if obs is not None:
                existing = [o for o in track.get("observations", []) if o.get("date") != observation_date]
                existing.append(obs)
                existing.sort(key=lambda x: (x.get("date", ""), x.get("observed_at", "")))
                # Keep a bounded but long forward history.
                track["observations"] = existing[-730:]

    # Attach metrics only after every current observation has been written.
    for key, record in current.items():
        track = tracks.get(key, {})
        metrics = _metrics(track.get("observations", []))
        record.forward_observations = int(metrics["forward_observations"] or 0)
        record.forward_return_pct = metrics["forward_return_pct"]
        record.forward_max_drawdown_pct = metrics["forward_max_drawdown_pct"]
        record.forward_profit_factor = metrics["forward_profit_factor"]
        record.forward_win_rate_pct = metrics["forward_win_rate_pct"]
        record.forward_score = metrics["forward_score"]
        record.rank_score = record.forward_score if record.forward_score is not None else record.research_score
        track["latest_metrics"] = metrics
        track["evidence_score"] = record.evidence_score
        track["research_score"] = record.research_score
        track["last_current_name"] = record.name

    state["updated_date"] = observation_date
    state["tracked_candidates"] = len(tracks)
    state["current_candidates"] = len(current)
    return state


def forward_rank_key(record: TraderSnapshot) -> tuple[int, float, float]:
    """Forward observations rank first; seed score orders candidates awaiting a result."""
    if record.forward_score is not None:
        return (1, record.forward_score, record.research_score or -999.0)
    return (0, record.research_score or -999.0, record.evidence_score or 0.0)
