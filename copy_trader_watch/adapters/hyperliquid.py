from __future__ import annotations

import math
from datetime import datetime, timezone
from typing import Any

import requests

from models import AdapterResult, TraderSnapshot, score_snapshot

INFO_URL = "https://api.hyperliquid.xyz/info"
USER_AGENT = "copy-trader-watch/3.3 (+https://github.com/)"


def _num(value: Any) -> float | None:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _post(payload: dict[str, Any], timeout: int = 30) -> Any:
    response = requests.post(INFO_URL, json=payload, timeout=timeout, headers={"User-Agent": USER_AGENT, "Accept": "application/json", "Content-Type": "application/json"})
    response.raise_for_status()
    return response.json()


def _windows(portfolio: Any) -> dict[str, dict[str, Any]]:
    result: dict[str, dict[str, Any]] = {}
    if not isinstance(portfolio, list):
        return result
    for item in portfolio:
        if isinstance(item, list) and len(item) == 2 and isinstance(item[1], dict):
            result[str(item[0])] = item[1]
    return result


def _series(raw: Any) -> list[tuple[int, float]]:
    out: list[tuple[int, float]] = []
    if not isinstance(raw, list):
        return out
    for item in raw:
        if not isinstance(item, list) or len(item) < 2:
            continue
        try:
            ts = int(item[0])
        except (TypeError, ValueError):
            continue
        value = _num(item[1])
        if value is not None:
            out.append((ts, value))
    out.sort()
    return out


def _latest_value(raw: Any) -> float | None:
    values = _series(raw)
    return values[-1][1] if values else None


def _period_metrics(window: dict[str, Any]) -> tuple[float | None, float | None]:
    av = [(t, v) for t, v in _series(window.get("accountValueHistory")) if v > 0]
    pnl = _series(window.get("pnlHistory"))
    if not av or not pnl:
        return None, None
    base = av[0][1]
    p0 = pnl[0][1]
    equity = [base + (p - p0) for _, p in pnl]
    if not equity or base <= 0:
        return None, None
    ret = (equity[-1] / base - 1.0) * 100.0
    peak = equity[0]
    worst = 0.0
    for value in equity:
        peak = max(peak, value)
        if peak > 0:
            worst = min(worst, (value / peak - 1.0) * 100.0)
    return ret, worst


def _age_days(window: dict[str, Any]) -> float | None:
    av = _series(window.get("accountValueHistory"))
    if len(av) < 2:
        return None
    return max(0.0, (av[-1][0] - av[0][0]) / 86_400_000.0)


def _fill_stats(fills: Any) -> dict[str, float | int | None]:
    if not isinstance(fills, list) or not fills:
        return {"trades": 0, "win_rate_pct": None, "profit_factor": None, "activity_per_day": None, "profit_concentration_pct": None}
    closed = []
    times = []
    for fill in fills:
        if not isinstance(fill, dict):
            continue
        t = fill.get("time")
        if isinstance(t, (int, float)):
            times.append(float(t))
        pnl = _num(fill.get("closedPnl"))
        if pnl is not None and abs(pnl) > 1e-12:
            closed.append(pnl)
    wins = [p for p in closed if p > 0]
    losses = [p for p in closed if p < 0]
    wr = (len(wins) / len(closed) * 100.0) if closed else None
    pf = (sum(wins) / abs(sum(losses))) if losses and sum(losses) != 0 else (999.0 if wins else None)
    concentration = (max(wins) / sum(wins) * 100.0) if wins and sum(wins) > 0 else None
    activity = None
    if len(times) >= 2:
        span_days = max((max(times) - min(times)) / 86_400_000.0, 1 / 24)
        activity = len(times) / span_days
    return {"trades": len(fills), "win_rate_pct": wr, "profit_factor": pf, "activity_per_day": activity, "profit_concentration_pct": concentration}


def _current_account_value(state: Any) -> float | None:
    if not isinstance(state, dict):
        return None
    summary = state.get("marginSummary") or {}
    account = _num(summary.get("accountValue"))
    return account if account is not None and account > 0 else None


def _current_leverage(state: Any) -> float | None:
    if not isinstance(state, dict):
        return None
    summary = state.get("marginSummary") or {}
    account = _current_account_value(state)
    notional = _num(summary.get("totalNtlPos"))
    if account is None or notional is None:
        return None
    return abs(notional) / account


def _copyability(activity: float | None, concentration: float | None, leverage: float | None) -> float:
    score = 90.0
    if activity is not None:
        if activity > 200:
            score -= 45
        elif activity > 80:
            score -= 30
        elif activity > 30:
            score -= 15
    if concentration is not None:
        if concentration > 50:
            score -= 30
        elif concentration > 25:
            score -= 15
    if leverage is not None:
        if leverage > 20:
            score -= 30
        elif leverage > 10:
            score -= 15
        elif leverage > 5:
            score -= 5
    return max(0.0, min(100.0, score))


def fetch_wallet(address: str, label: str | None = None) -> TraderSnapshot:
    observed = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    portfolio = _post({"type": "portfolio", "user": address})
    fills = _post({"type": "userFills", "user": address, "aggregateByTime": False})
    state = _post({"type": "clearinghouseState", "user": address})
    windows = _windows(portfolio)
    month = windows.get("month") or windows.get("perpMonth") or {}
    all_time = windows.get("allTime") or windows.get("perpAllTime") or month
    ret, dd = _period_metrics(month)
    age = _age_days(all_time)
    stats = _fill_stats(fills)
    leverage = _current_leverage(state)
    account_value = _current_account_value(state)
    all_time_pnl_index = _latest_value(all_time.get("pnlHistory"))
    copyability = _copyability(
        stats["activity_per_day"] if isinstance(stats["activity_per_day"], (int, float)) else None,
        stats["profit_concentration_pct"] if isinstance(stats["profit_concentration_pct"], (int, float)) else None,
        leverage,
    )
    record = TraderSnapshot(
        platform="hyperliquid",
        trader_id=address.lower(),
        name=label or f"{address[:8]}…{address[-4:]}",
        observed_at=observed,
        source="official-hyperliquid-info-api",
        source_url=INFO_URL,
        source_quality=95.0,
        free=True,
        us_access="no",
        live_evidence="onchain",
        return_pct=ret,
        return_window="month",
        max_drawdown_pct=dd,
        profit_factor=stats["profit_factor"] if isinstance(stats["profit_factor"], (int, float)) else None,
        trades=int(stats["trades"] or 0),
        win_rate_pct=stats["win_rate_pct"] if isinstance(stats["win_rate_pct"], (int, float)) else None,
        age_days=age,
        leverage=leverage,
        activity_per_day=stats["activity_per_day"] if isinstance(stats["activity_per_day"], (int, float)) else None,
        profit_concentration_pct=stats["profit_concentration_pct"] if isinstance(stats["profit_concentration_pct"], (int, float)) else None,
        copyability_score=copyability,
        actionable=False,
        actionable_reason="Research-only for this U.S. workflow; direct Hyperliquid access is not treated as U.S.-actionable.",
        forward_test_eligible=all_time_pnl_index is not None and account_value is not None,
        forward_test_reason="Forward test uses changes in the official all-time P&L index divided by prior observed account value; sample size is confidence-only.",
        metadata={
            "month_volume": _num(month.get("vlm")),
            "all_time_volume": _num(all_time.get("vlm")),
            "current_account_value": account_value,
            "all_time_pnl_index": all_time_pnl_index,
            "forward_metric_kind": "pnl_index" if all_time_pnl_index is not None and account_value is not None else None,
            "forward_metric_value": all_time_pnl_index,
            "forward_metric_base": account_value,
            "forward_period_key": "all",
            "raw_fill_limit_note": "userFills returns at most the most recent 2000 fills",
            "return_method": "PnL-change on initial period account-value base",
            "forward_method": "change in all-time PnL index / prior observed account value",
        },
    )
    record.research_score = score_snapshot(record)
    return record


def collect(config: dict[str, Any]) -> AdapterResult:
    observed = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    records: list[TraderSnapshot] = []
    errors: list[str] = []
    for item in config.get("wallets", []):
        if isinstance(item, str):
            address, label = item, None
        else:
            address, label = item.get("address"), item.get("label")
        if not address:
            continue
        try:
            records.append(fetch_wallet(address, label))
        except (requests.RequestException, ValueError, TypeError) as exc:
            errors.append(f"{address}: {exc}")
    status = "ok" if not errors else ("degraded" if records else "unavailable")
    return AdapterResult(
        platform="hyperliquid",
        observed_at=observed,
        records=records,
        status=status,
        message="; ".join(errors[:5]),
        metadata={"wallets_requested": len(config.get("wallets", [])), "wallets_resolved": len(records)},
    )
