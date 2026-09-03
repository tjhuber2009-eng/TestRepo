#!/usr/bin/env python3
"""Free, read-only forward monitor for selected eToro investors.

The source-agnostic core consumes an investor snapshot, stores a daily history,
computes forward metrics, and produces alerts/reports. Production data-source
selection lives in run.py.

The program never logs in to a broker and never places trades.
"""
from __future__ import annotations

import csv
import io
import json
import math
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

import requests

ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config.json"
DATA_DIR = ROOT / "data"
REPORT_DIR = ROOT / "reports"
HISTORY_PATH = DATA_DIR / "history.json"
STATE_PATH = DATA_DIR / "state.json"
ALERTS_PATH = DATA_DIR / "alerts.json"
REPORT_PATH = REPORT_DIR / "latest.md"


@dataclass
class Thresholds:
    daily_loss_pct: float = -5.0
    forward_drawdown_pct: float = -10.0
    risk_score_high: int = 7
    risk_score_jump: int = 2
    top1_concentration_pct: float = 50.0
    top2_concentration_pct: float = 75.0
    concentration_jump_pct_points: float = 20.0
    copier_drop_pct: float = -50.0


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return default


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def fetch_json(url: str) -> dict[str, Any]:
    response = requests.get(url, timeout=45, headers={"User-Agent": "copy-trader-watch/1.0"})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Census endpoint did not return a JSON object")
    return payload


def collected_date(census: dict[str, Any]) -> str:
    raw = census.get("metadata", {}).get("collectedAt")
    if raw:
        try:
            return datetime.fromisoformat(raw.replace("Z", "+00:00")).date().isoformat()
        except ValueError:
            pass
    return datetime.now(timezone.utc).date().isoformat()


def find_investor(census: dict[str, Any], username: str) -> dict[str, Any] | None:
    target = username.casefold()
    for investor in census.get("investors", []):
        if str(investor.get("userName", "")).casefold() == target:
            return investor
    return None


def _pct(v: Any) -> float | None:
    try:
        x = float(v)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def concentration(investor: dict[str, Any]) -> tuple[float | None, float | None, int]:
    """Aggregate many eToro lots into instrument-level weights.

    investmentPct values are percentages of account value. A trader can hold
    many lots in one instrument, so summing by instrument avoids understating
    concentration.
    """
    positions = investor.get("portfolio", {}).get("positions", []) or []
    by_instrument: dict[str, float] = {}
    for pos in positions:
        instrument = str(pos.get("instrumentId", "unknown"))
        weight = _pct(pos.get("investmentPct"))
        if weight is None or weight < 0:
            continue
        by_instrument[instrument] = by_instrument.get(instrument, 0.0) + weight
    weights = sorted(by_instrument.values(), reverse=True)
    if not weights:
        return None, None, 0
    top1 = weights[0]
    top2 = sum(weights[:2])
    return round(top1, 4), round(top2, 4), len(weights)


def snapshot_investor(investor: dict[str, Any] | None) -> dict[str, Any]:
    if investor is None:
        return {"present": False}
    top1, top2, instruments = concentration(investor)
    portfolio = investor.get("portfolio", {}) or {}
    return {
        "present": True,
        "username": investor.get("userName"),
        "name": investor.get("fullName"),
        "country": investor.get("country"),
        "gain_ytd_pct": _pct(investor.get("gain")),
        "daily_gain_pct": _pct(investor.get("dailyGain")),
        "risk_score": investor.get("riskScore"),
        "copiers": investor.get("copiers"),
        "trades": investor.get("trades"),
        "win_ratio_pct": _pct(investor.get("winRatio")),
        "portfolio_value_pct": _pct(portfolio.get("totalValue")),
        "portfolio_pnl_pct": _pct(portfolio.get("profitLossPercentage")),
        "positions_count": portfolio.get("positionsCount"),
        "unique_instruments": instruments,
        "top1_concentration_pct": top1,
        "top2_concentration_pct": top2,
    }


def fetch_stooq_close(symbol: str) -> float | None:
    url = f"https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=csv"
    try:
        response = requests.get(url, timeout=20, headers={"User-Agent": "copy-trader-watch/1.0"})
        response.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(response.text)))
        if not rows:
            return None
        close = rows[0].get("Close")
        return _pct(close)
    except (requests.RequestException, ValueError):
        return None


def forward_return_from_ytd(baseline_ytd: float | None, current_ytd: float | None) -> float | None:
    """Convert two same-calendar-year YTD returns into return between observations."""
    if baseline_ytd is None or current_ytd is None:
        return None
    base_factor = 1.0 + baseline_ytd / 100.0
    current_factor = 1.0 + current_ytd / 100.0
    if base_factor <= 0:
        return None
    return (current_factor / base_factor - 1.0) * 100.0


def pct_change(base: float | None, current: float | None) -> float | None:
    if base in (None, 0) or current is None:
        return None
    return (current / base - 1.0) * 100.0


def candidate_series(history: list[dict[str, Any]], username: str) -> list[float]:
    values: list[float] = []
    baseline_ytd: float | None = None
    target = username.casefold()
    for row in history:
        data = next(
            (v for k, v in row.get("candidates", {}).items() if k.casefold() == target),
            None,
        )
        if not data or not data.get("present"):
            continue
        ytd = _pct(data.get("gain_ytd_pct"))
        if ytd is None:
            continue
        if baseline_ytd is None:
            baseline_ytd = ytd
        fwd = forward_return_from_ytd(baseline_ytd, ytd)
        if fwd is not None:
            values.append(fwd)
    return values


def max_drawdown_from_returns(returns_pct: Iterable[float]) -> float | None:
    values = list(returns_pct)
    if not values:
        return None
    peak = 1.0
    worst = 0.0
    for ret in values:
        equity = 1.0 + ret / 100.0
        peak = max(peak, equity)
        dd = (equity / peak - 1.0) * 100.0
        worst = min(worst, dd)
    return worst


def previous_candidate(history: list[dict[str, Any]], username: str) -> dict[str, Any] | None:
    target = username.casefold()
    for row in reversed(history[:-1]):
        for key, value in row.get("candidates", {}).items():
            if key.casefold() == target and value.get("present"):
                return value
    return None


def first_candidate(history: list[dict[str, Any]], username: str) -> dict[str, Any] | None:
    target = username.casefold()
    for row in history:
        for key, value in row.get("candidates", {}).items():
            if key.casefold() == target and value.get("present"):
                return value
    return None


def make_alerts(
    history: list[dict[str, Any]], candidates: list[str], thresholds: Thresholds
) -> list[dict[str, str]]:
    if not history:
        return []
    current_row = history[-1]
    alerts: list[dict[str, str]] = []

    def add(user: str, kind: str, message: str) -> None:
        alerts.append({"key": f"{user.casefold()}::{kind}", "candidate": user, "type": kind, "message": message})

    for user in candidates:
        current = current_row.get("candidates", {}).get(user, {"present": False})
        if not current.get("present"):
            add(
                user,
                "missing",
                "Candidate could not be resolved by the configured public data sources; this does not by itself prove the eToro account is unavailable to copy.",
            )
            continue

        daily = _pct(current.get("daily_gain_pct"))
        if daily is not None and daily <= thresholds.daily_loss_pct:
            add(user, "daily_loss", f"Daily return is {daily:.2f}% (threshold {thresholds.daily_loss_pct:.2f}%).")

        risk = current.get("risk_score")
        if isinstance(risk, (int, float)) and risk >= thresholds.risk_score_high:
            add(user, "high_risk", f"eToro risk score is {risk}, at/above the configured high-risk threshold.")

        prev = previous_candidate(history, user)
        if prev:
            prev_risk = prev.get("risk_score")
            if isinstance(risk, (int, float)) and isinstance(prev_risk, (int, float)) and risk - prev_risk >= thresholds.risk_score_jump:
                add(user, "risk_jump", f"Risk score jumped from {prev_risk} to {risk}.")

            prev_top1 = _pct(prev.get("top1_concentration_pct"))
            top1 = _pct(current.get("top1_concentration_pct"))
            if top1 is not None and prev_top1 is not None and top1 - prev_top1 >= thresholds.concentration_jump_pct_points:
                add(user, "concentration_jump", f"Largest instrument concentration jumped {top1 - prev_top1:.1f} points to {top1:.1f}%.")

            prev_copiers = _pct(prev.get("copiers"))
            copiers = _pct(current.get("copiers"))
            copier_change = pct_change(prev_copiers, copiers)
            if copier_change is not None and copier_change <= thresholds.copier_drop_pct:
                add(user, "copier_drop", f"Copier count fell {copier_change:.1f}% since the previous observation.")

        top1 = _pct(current.get("top1_concentration_pct"))
        top2 = _pct(current.get("top2_concentration_pct"))
        if top1 is not None and top1 >= thresholds.top1_concentration_pct:
            add(user, "top1_concentration", f"Largest instrument is {top1:.1f}% of tracked portfolio exposure.")
        if top2 is not None and top2 >= thresholds.top2_concentration_pct:
            add(user, "top2_concentration", f"Two largest instruments are {top2:.1f}% of tracked portfolio exposure.")

        dd = max_drawdown_from_returns(candidate_series(history, user))
        if dd is not None and dd <= thresholds.forward_drawdown_pct:
            add(user, "forward_drawdown", f"Forward-test drawdown is {dd:.2f}% (threshold {thresholds.forward_drawdown_pct:.2f}%).")

    return alerts


def current_metrics(history: list[dict[str, Any]], candidates: list[str]) -> list[dict[str, Any]]:
    if not history:
        return []
    row = history[-1]
    metrics: list[dict[str, Any]] = []
    for user in candidates:
        current = row.get("candidates", {}).get(user, {"present": False})
        first = first_candidate(history, user)
        if not current.get("present") or not first:
            metrics.append({"username": user, "present": False})
            continue
        forward_return = forward_return_from_ytd(
            _pct(first.get("gain_ytd_pct")), _pct(current.get("gain_ytd_pct"))
        )
        dd = max_drawdown_from_returns(candidate_series(history, user))
        risk = current.get("risk_score")
        top1 = _pct(current.get("top1_concentration_pct"))
        # A deliberately simple, transparent research score; it does not predict future returns.
        denom = max(abs(dd or 0.0), 5.0)
        return_dd = (forward_return or 0.0) / denom
        risk_penalty = max(0.0, float(risk or 0) - 4.0) * 0.35
        concentration_penalty = max(0.0, (top1 or 0.0) - 35.0) / 25.0
        score = return_dd - risk_penalty - concentration_penalty
        metrics.append({
            "username": user,
            "present": True,
            "forward_return_pct": forward_return,
            "forward_max_drawdown_pct": dd,
            "return_to_drawdown": return_dd,
            "risk_score": risk,
            "top1_concentration_pct": top1,
            "top2_concentration_pct": _pct(current.get("top2_concentration_pct")),
            "copiers": current.get("copiers"),
            "win_ratio_pct": _pct(current.get("win_ratio_pct")),
            "research_score": score,
        })
    return sorted(metrics, key=lambda x: x.get("research_score", -999), reverse=True)


def benchmark_metrics(history: list[dict[str, Any]]) -> dict[str, float | None]:
    if not history:
        return {}
    first = history[0].get("benchmarks", {})
    current = history[-1].get("benchmarks", {})
    return {symbol: pct_change(_pct(first.get(symbol)), _pct(current.get(symbol))) for symbol in current}


def fmt(value: Any, suffix: str = "", digits: int = 2) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}{suffix}"
    return str(value)


def build_report(
    history: list[dict[str, Any]], candidates: list[str], alerts: list[dict[str, str]]
) -> str:
    metrics = current_metrics(history, candidates)
    bench = benchmark_metrics(history)
    start = history[0]["date"] if history else "n/a"
    end = history[-1]["date"] if history else "n/a"
    lines = [
        "# Copy Trader Watch — Latest Report",
        "",
        f"Forward-test window: **{start} → {end}** ({len(history)} observations)",
        "",
        "> Research monitor only. It does not place trades and its score is not an investment recommendation.",
        "",
        "## Candidate ranking",
        "",
        "| Rank | Candidate | Forward return | Max DD | Return/DD | Risk | Top-1 concentration | Copiers | Research score |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for idx, item in enumerate(metrics, 1):
        if not item.get("present"):
            lines.append(f"| {idx} | {item['username']} | missing | n/a | n/a | n/a | n/a | n/a | n/a |")
            continue
        lines.append(
            "| {rank} | {user} | {ret} | {dd} | {rdd} | {risk} | {top1} | {copiers} | {score} |".format(
                rank=idx,
                user=item["username"],
                ret=fmt(item.get("forward_return_pct"), "%"),
                dd=fmt(item.get("forward_max_drawdown_pct"), "%"),
                rdd=fmt(item.get("return_to_drawdown"), "", 3),
                risk=item.get("risk_score", "n/a"),
                top1=fmt(item.get("top1_concentration_pct"), "%"),
                copiers=item.get("copiers", "n/a"),
                score=fmt(item.get("research_score"), "", 3),
            )
        )

    lines.extend(["", "## Benchmarks", ""])
    for symbol, value in bench.items():
        lines.append(f"- **{symbol}:** {fmt(value, '%')} since the same baseline")

    lines.extend(["", "## Active alerts", ""])
    if alerts:
        for alert in alerts:
            lines.append(f"- **{alert['candidate']} — {alert['type']}:** {alert['message']}")
    else:
        lines.append("No configured alert conditions are active.")

    lines.extend([
        "",
        "## Method notes",
        "",
        "- Production candidate data uses the public `weirdapps/etoro_census` per-user endpoint first and its top-1,500 census as a fallback.",
        "- Forward return is derived from the change in same-year YTD return from the first observation; the monitor resets naturally if you start a new history file in a new calendar year.",
        "- Forward max drawdown is calculated from the stored daily path, not from eToro's full historical equity curve.",
        "- Portfolio concentration aggregates multiple lots when full census data is used; the per-user endpoint supplies already aggregated top positions.",
        "- SPY/QQQ quotes use Yahoo Finance's public chart data first and Stooq as a fallback.",
        "- Missing or stale upstream data is surfaced rather than filled with guesses.",
        "",
    ])
    return "\n".join(lines)


def notify_new_alerts(new_alerts: list[dict[str, str]], report_date: str) -> None:
    token = os.getenv("GITHUB_TOKEN")
    repo = os.getenv("GITHUB_REPOSITORY")
    if not token or not repo or not new_alerts:
        return
    body = [f"Copy Trader Watch found {len(new_alerts)} new alert condition(s) on {report_date}:", ""]
    for alert in new_alerts:
        body.append(f"- **{alert['candidate']} — {alert['type']}**: {alert['message']}")
    body.extend(["", "See `copy_trader_watch/reports/latest.md` for the current ranking and forward-test metrics."])
    response = requests.post(
        f"https://api.github.com/repos/{repo}/issues",
        timeout=30,
        headers={
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "copy-trader-watch/1.0",
        },
        json={"title": f"Copy Trader Watch alert — {report_date}", "body": "\n".join(body)},
    )
    response.raise_for_status()


def main() -> int:
    config = load_json(CONFIG_PATH, {})
    candidates: list[str] = config.get("candidates", [])
    thresholds = Thresholds(**config.get("thresholds", {}))
    if not candidates:
        raise SystemExit("No candidates configured")

    census = fetch_json(config["census_url"])
    date = collected_date(census)
    history: list[dict[str, Any]] = load_json(HISTORY_PATH, [])

    row = {
        "date": date,
        "source_collected_at": census.get("metadata", {}).get("collectedAt"),
        "candidates": {
            user: snapshot_investor(find_investor(census, user)) for user in candidates
        },
        "benchmarks": {
            name: fetch_stooq_close(stooq_symbol)
            for name, stooq_symbol in config.get("benchmarks", {}).items()
        },
    }

    # Idempotent daily reruns replace the same source date instead of duplicating it.
    history = [existing for existing in history if existing.get("date") != date]
    history.append(row)
    history.sort(key=lambda x: x.get("date", ""))
    save_json(HISTORY_PATH, history)

    alerts = make_alerts(history, candidates, thresholds)
    save_json(ALERTS_PATH, {"date": date, "alerts": alerts})

    state = load_json(STATE_PATH, {"active_alert_keys": []})
    previous_keys = set(state.get("active_alert_keys", []))
    current_keys = {a["key"] for a in alerts}
    new_alerts = [a for a in alerts if a["key"] not in previous_keys]
    state = {
        "last_run_date": date,
        "active_alert_keys": sorted(current_keys),
        "new_alert_count": len(new_alerts),
    }
    save_json(STATE_PATH, state)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(build_report(history, candidates, alerts), encoding="utf-8")

    try:
        notify_new_alerts(new_alerts, date)
    except requests.RequestException as exc:
        print(f"WARNING: GitHub alert issue could not be created: {exc}")

    print(f"Recorded {date}; {len(alerts)} active alert(s), {len(new_alerts)} new.")
    for item in current_metrics(history, candidates):
        if item.get("present"):
            print(
                f"{item['username']}: return={fmt(item.get('forward_return_pct'), '%')} "
                f"dd={fmt(item.get('forward_max_drawdown_pct'), '%')} "
                f"score={fmt(item.get('research_score'), '', 3)}"
            )
        else:
            print(f"{item['username']}: missing")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
