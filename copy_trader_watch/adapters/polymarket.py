from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import requests

from models import AdapterResult, TraderSnapshot, score_snapshot

BASE_URL = "https://data-api.polymarket.com"
USER_AGENT = "copy-trader-watch/3.3 (+https://github.com/)"


def _num(value: Any) -> float | None:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _get(path: str, params: dict[str, Any] | None = None) -> Any:
    response = requests.get(
        f"{BASE_URL}{path}",
        params=params or {},
        timeout=30,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    response.raise_for_status()
    return response.json()


def leaderboard(period: str, limit: int = 50) -> list[dict[str, Any]]:
    payload = _get(
        "/v1/leaderboard",
        {"category": "OVERALL", "timePeriod": period.upper(), "orderBy": "PNL", "limit": min(50, max(1, limit)), "offset": 0},
    )
    return payload if isinstance(payload, list) else []


def closed_positions(wallet: str, max_positions: int = 200) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    max_positions = min(max(1, max_positions), 1000)
    for offset in range(0, max_positions, 50):
        batch = _get(
            "/closed-positions",
            {"user": wallet, "limit": min(50, max_positions - offset), "offset": offset, "sortBy": "TIMESTAMP", "sortDirection": "DESC"},
        )
        if not isinstance(batch, list) or not batch:
            break
        rows.extend(item for item in batch if isinstance(item, dict))
        if len(batch) < 50:
            break
    return rows[:max_positions]


def current_positions(wallet: str, limit: int = 100) -> list[dict[str, Any]]:
    payload = _get(
        "/positions",
        {"user": wallet, "limit": min(500, max(1, limit)), "offset": 0, "sortBy": "CURRENT", "sortDirection": "DESC"},
    )
    return payload if isinstance(payload, list) else []


def portfolio_value(wallet: str) -> float | None:
    payload = _get("/value", {"user": wallet})
    if isinstance(payload, list) and payload:
        return _num(payload[0].get("value")) if isinstance(payload[0], dict) else None
    if isinstance(payload, dict):
        return _num(payload.get("value"))
    return None


def position_stats(rows: list[dict[str, Any]]) -> dict[str, Any]:
    pnls: list[float] = []
    costs: list[float] = []
    timestamps: list[int] = []
    for row in rows:
        pnl = _num(row.get("realizedPnl"))
        bought = _num(row.get("totalBought"))
        avg_price = _num(row.get("avgPrice"))
        ts = row.get("timestamp")
        if pnl is not None:
            pnls.append(pnl)
        if bought is not None and avg_price is not None and bought >= 0 and avg_price >= 0:
            costs.append(bought * avg_price)
        if isinstance(ts, (int, float)):
            timestamps.append(int(ts))

    wins = [p for p in pnls if p > 0]
    losses = [p for p in pnls if p < 0]
    total_cost = sum(costs)
    realized = sum(pnls)
    cost_roi = realized / total_cost * 100.0 if total_cost > 0 else None
    win_rate = len(wins) / len(pnls) * 100.0 if pnls else None
    pf = sum(wins) / abs(sum(losses)) if losses and sum(losses) != 0 else (None if not wins else 50.0)
    concentration = max(wins) / sum(wins) * 100.0 if wins and sum(wins) > 0 else None
    age_days = None
    if len(timestamps) >= 2:
        age_days = max(timestamps) - min(timestamps)
        age_days = max(0.0, age_days / 86_400.0)
    activity = len(rows) / max(age_days, 1.0) if age_days is not None else None
    return {
        "closed_positions": len(rows),
        "realized_pnl": realized,
        "estimated_cost": total_cost,
        "cost_roi_pct": cost_roi,
        "win_rate_pct": win_rate,
        "profit_factor": pf,
        "profit_concentration_pct": concentration,
        "sample_age_days": age_days,
        "closed_positions_per_day": activity,
    }


def open_concentration(rows: list[dict[str, Any]]) -> tuple[float | None, float | None]:
    values = [v for row in rows if (v := _num(row.get("currentValue"))) is not None and v > 0]
    if not values:
        return None, None
    total = sum(values)
    weights = sorted((v / total * 100.0 for v in values), reverse=True)
    return weights[0], sum(weights[:2])


def copyability_score(stats: dict[str, Any], top1: float | None, persistent: bool | None = None) -> float:
    """Execution copyability only; sample count/persistence do not affect it."""
    score = 75.0
    activity = _num(stats.get("closed_positions_per_day"))
    if activity is not None:
        if activity > 50:
            score -= 35
        elif activity > 15:
            score -= 20
        elif activity > 5:
            score -= 10
    concentration = _num(stats.get("profit_concentration_pct"))
    if concentration is not None:
        if concentration > 60:
            score -= 30
        elif concentration > 35:
            score -= 15
    if top1 is not None:
        if top1 > 70:
            score -= 25
        elif top1 > 45:
            score -= 10
    return max(0.0, min(100.0, score))


def normalize_candidate(
    row: dict[str, Any],
    all_time_ranks: dict[str, int],
    max_closed_positions: int,
) -> TraderSnapshot:
    observed = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    wallet = str(row.get("proxyWallet") or "").lower()
    if not wallet.startswith("0x"):
        raise ValueError("leaderboard row has no valid proxy wallet")
    closed = closed_positions(wallet, max_closed_positions)
    opens = current_positions(wallet, 100)
    value = portfolio_value(wallet)
    stats = position_stats(closed)
    top1, top2 = open_concentration(opens)
    all_rank = all_time_ranks.get(wallet)
    persistent = all_rank is not None
    copyability = copyability_score(stats, top1, persistent)
    cost_roi = _num(stats.get("cost_roi_pct"))
    record = TraderSnapshot(
        platform="polymarket",
        trader_id=wallet,
        name=str(row.get("userName") or f"{wallet[:8]}…{wallet[-4:]}"),
        observed_at=observed,
        source="official-polymarket-data-api",
        source_url=f"https://polymarket.com/profile/{wallet}",
        source_quality=95.0,
        free=True,
        us_access="no",
        live_evidence="public-ledger-api",
        return_pct=cost_roi,
        return_window=f"closed-position cost ROI ({len(closed)}-position sample; not account return)",
        max_drawdown_pct=None,
        profit_factor=_num(stats.get("profit_factor")),
        trades=len(closed),
        win_rate_pct=_num(stats.get("win_rate_pct")),
        age_days=_num(stats.get("sample_age_days")),
        leverage=None,
        activity_per_day=_num(stats.get("closed_positions_per_day")),
        profit_concentration_pct=_num(stats.get("profit_concentration_pct")),
        copyability_score=copyability,
        actionable=False,
        actionable_reason="Research-only: polymarket.com is not treated as U.S.-actionable in this workflow; Polymarket US is a separate product.",
        forward_test_eligible=True,
        forward_test_reason="Admitted from monthly rank regardless of closed-position sample size or all-time persistence.",
        metadata={
            "monthly_rank": row.get("rank"),
            "monthly_pnl": _num(row.get("pnl")),
            "monthly_volume": _num(row.get("vol")),
            "all_time_top50_rank": all_rank,
            "leaderboard_persistent": persistent,
            "closed_sample_realized_pnl": stats.get("realized_pnl"),
            "closed_sample_estimated_cost": stats.get("estimated_cost"),
            "current_portfolio_value": value,
            "open_positions": len(opens),
            "open_top1_concentration_pct": top1,
            "open_top2_concentration_pct": top2,
            "verified_badge": row.get("verifiedBadge"),
            "performance_metric_warning": "closed-position cost ROI is a capital-efficiency proxy, not an account equity return",
        },
    )
    record.research_score = score_snapshot(record)
    return record


def collect(config: dict[str, Any]) -> AdapterResult:
    observed = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    leaderboard_limit = int(config.get("leaderboard_limit", 50))
    candidate_limit = int(config.get("candidate_limit", 12))
    max_closed = int(config.get("max_closed_positions", 200))
    try:
        monthly = leaderboard("MONTH", leaderboard_limit)
        all_time = leaderboard("ALL", leaderboard_limit)
    except (requests.RequestException, ValueError) as exc:
        return AdapterResult(platform="polymarket", observed_at=observed, status="unavailable", message=f"leaderboard fetch failed: {exc}")

    all_ranks: dict[str, int] = {}
    for item in all_time:
        wallet = str(item.get("proxyWallet") or "").lower()
        try:
            rank = int(item.get("rank"))
        except (TypeError, ValueError):
            continue
        if wallet:
            all_ranks[wallet] = rank

    # Candidate admission follows current monthly P&L rank only. All-time persistence
    # remains evidence context, never a gate that can exclude a new high-performing wallet.
    candidates = monthly[:candidate_limit]
    persistent_count = sum(1 for r in candidates if str(r.get("proxyWallet") or "").lower() in all_ranks)

    records: list[TraderSnapshot] = []
    errors: list[str] = []
    for row in candidates:
        try:
            records.append(normalize_candidate(row, all_ranks, max_closed))
        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append(f"{row.get('userName') or row.get('proxyWallet')}: {exc}")

    records.sort(key=lambda r: (r.research_score is not None, r.research_score or -999.0), reverse=True)
    status = "ok" if not errors else ("degraded" if records else "unavailable")
    return AdapterResult(
        platform="polymarket",
        observed_at=observed,
        records=records,
        status=status,
        message="; ".join(errors[:5]),
        metadata={
            "monthly_leaderboard_rows": len(monthly),
            "all_time_leaderboard_rows": len(all_time),
            "persistent_candidates_in_monthly_top": persistent_count,
            "candidates_requested": len(candidates),
            "candidates_resolved": len(records),
            "max_closed_positions_per_candidate": max_closed,
            "metric_warning": "cost ROI is not directly comparable to account return",
            "sample_policy": "no minimum closed-position count or persistence requirement for admission",
        },
    )
