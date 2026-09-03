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
    missing_consecutive_runs: int = 2
    candidate_source_max_age_hours: float = 36.0


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
    response = requests.get(url, timeout=45, headers={"User-Agent": "copy-trader-watch/2.0"})
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError("Census endpoint did not return a JSON object")
    return payload


def parse_iso_datetime(value: Any) -> datetime | None:
    if not value or not isinstance(value, str):
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def source_age_hours(source_timestamp: Any, observed_timestamp: Any) -> float | None:
    source = parse_iso_datetime(source_timestamp)
    observed = parse_iso_datetime(observed_timestamp)
    if source is None or observed is None:
        return None
    return max(0.0, (observed - source).total_seconds() / 3600.0)


def collected_date(census: dict[str, Any]) -> str:
    metadata = census.get("metadata", {}) or {}
    explicit = metadata.get("observationDate")
    if explicit:
        try:
            return datetime.fromisoformat(str(explicit)).date().isoformat()
        except ValueError:
            pass
    raw = metadata.get("collectedAt")
    if raw:
        parsed = parse_iso_datetime(raw)
        if parsed is not None:
            return parsed.date().isoformat()
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
    """Aggregate many eToro lots into instrument-level weights."""
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
    return round(weights[0], 4), round(sum(weights[:2]), 4), len(weights)


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
        "source": investor.get("source"),
        "source_timestamp": investor.get("sourceTimestamp"),
    }


def fetch_stooq_quote(symbol: str) -> dict[str, Any]:
    url = f"https://stooq.com/q/l/?s={symbol}&f=sd2t2ohlcv&h&e=csv"
    try:
        response = requests.get(url, timeout=20, headers={"User-Agent": "copy-trader-watch/2.0"})
        response.raise_for_status()
        rows = list(csv.DictReader(io.StringIO(response.text)))
        if not rows:
            return {"close": None, "as_of": None, "source": "stooq"}
        row = rows[0]
        return {
            "close": _pct(row.get("Close")),
            "as_of": row.get("Date") or None,
            "source": "stooq",
        }
    except (requests.RequestException, ValueError):
        return {"close": None, "as_of": None, "source": "stooq"}


def fetch_stooq_close(symbol: str) -> float | None:
    return _pct(fetch_stooq_quote(symbol).get("close"))


def fetch_benchmark_quote(symbol: str) -> dict[str, Any]:
    return fetch_stooq_quote(symbol)


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


def _candidate_rows(history: list[dict[str, Any]], username: str) -> list[tuple[str, dict[str, Any]]]:
    target = username.casefold()
    found: list[tuple[str, dict[str, Any]]] = []
    for row in history:
        data = next(
            (v for k, v in row.get("candidates", {}).items() if k.casefold() == target),
            None,
        )
        if data and data.get("present") and _pct(data.get("gain_ytd_pct")) is not None:
            found.append((str(row.get("date", "")), data))
    return found


def candidate_observation_count(history: list[dict[str, Any]], username: str) -> int:
    return len(_candidate_rows(history, username))


def candidate_series(history: list[dict[str, Any]], username: str) -> list[float]:
    """Build a cumulative forward-return curve, including calendar-year rollovers.

    Within a calendar year, successive YTD values are converted to period returns.
    At a year boundary, the first new-year YTD value is treated as the return since
    the prior year-end. With daily observations this makes the curve continuous
    without comparing a reset YTD value directly with the prior year's YTD.
    """
    observations = _candidate_rows(history, username)
    if not observations:
        return []

    equity = 1.0
    curve = [0.0]
    prev_date, prev = observations[0]
    prev_ytd = _pct(prev.get("gain_ytd_pct"))

    for current_date, current in observations[1:]:
        current_ytd = _pct(current.get("gain_ytd_pct"))
        if current_ytd is None or prev_ytd is None:
            prev_date, prev_ytd = current_date, current_ytd
            continue

        try:
            same_year = datetime.fromisoformat(prev_date).year == datetime.fromisoformat(current_date).year
        except ValueError:
            same_year = True

        step_return = (
            forward_return_from_ytd(prev_ytd, current_ytd)
            if same_year
            else current_ytd
        )
        if step_return is not None:
            equity *= 1.0 + step_return / 100.0
            curve.append((equity - 1.0) * 100.0)
        prev_date, prev_ytd = current_date, current_ytd

    return curve


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


def consecutive_missing_count(history: list[dict[str, Any]], username: str) -> int:
    target = username.casefold()
    count = 0
    for row in reversed(history):
        data = next(
            (v for k, v in row.get("candidates", {}).items() if k.casefold() == target),
            None,
        )
        if data and data.get("present"):
            break
        count += 1
    return count


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
            missing_runs = consecutive_missing_count(history, user)
            if missing_runs >= max(1, thresholds.missing_consecutive_runs):
                detail = current.get("lookup_error")
                message = (
                    f"Candidate could not be resolved for {missing_runs} consecutive observations by the configured public data sources."
                )
                if detail:
                    message += f" Latest lookup: {detail}."
                message += " This does not by itself prove the eToro account is unavailable to copy."
                add(user, "missing", message)
            continue

        age = source_age_hours(
            current.get("source_timestamp"),
            current_row.get("source_collected_at"),
        )
        if age is not None and age > thresholds.candidate_source_max_age_hours:
            add(
                user,
                "stale_source",
                f"Candidate source data is {age:.1f} hours old (threshold {thresholds.candidate_source_max_age_hours:.1f}h).",
            )

        daily = _pct(current.get("daily_gain_pct"))
        if daily is not None and daily <= thresholds.daily_loss_pct:
            add(user, "daily_loss", f"Observation return is {daily:.2f}% (threshold {thresholds.daily_loss_pct:.2f}%).")

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


def current_metrics(
    history: list[dict[str, Any]],
    candidates: list[str],
    min_score_observations: int = 5,
) -> list[dict[str, Any]]:
    if not history:
        return []
    row = history[-1]
    metrics: list[dict[str, Any]] = []
    for order, user in enumerate(candidates):
        current = row.get("candidates", {}).get(user, {"present": False})
        observations = candidate_observation_count(history, user)
        if not current.get("present") or observations == 0:
            metrics.append({
                "username": user,
                "present": False,
                "observation_count": observations,
                "lookup_error": current.get("lookup_error"),
                "_order": order,
            })
            continue

        curve = candidate_series(history, user)
        forward_return = curve[-1] if curve else None
        dd = max_drawdown_from_returns(curve)
        risk = current.get("risk_score")
        top1 = _pct(current.get("top1_concentration_pct"))
        denom = max(abs(dd or 0.0), 5.0)
        return_dd = (forward_return or 0.0) / denom
        risk_penalty = max(0.0, float(risk or 0) - 4.0) * 0.35
        concentration_penalty = max(0.0, (top1 or 0.0) - 35.0) / 25.0
        score = (
            return_dd - risk_penalty - concentration_penalty
            if observations >= max(1, min_score_observations)
            else None
        )
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
            "observation_count": observations,
            "source": current.get("source"),
            "source_timestamp": current.get("source_timestamp"),
            "source_age_hours": source_age_hours(
                current.get("source_timestamp"),
                row.get("source_collected_at"),
            ),
            "_order": order,
        })

    def sort_key(item: dict[str, Any]) -> tuple[int, float, int]:
        if not item.get("present"):
            return (0, -999.0, -item["_order"])
        score = item.get("research_score")
        if score is None:
            return (1, 0.0, -item["_order"])
        return (2, float(score), -item["_order"])

    ranked = sorted(metrics, key=sort_key, reverse=True)
    for item in ranked:
        item.pop("_order", None)
    return ranked


def _normalize_quote(value: Any) -> dict[str, Any]:
    if isinstance(value, dict):
        return {
            "close": _pct(value.get("close")),
            "as_of": value.get("as_of"),
            "source": value.get("source"),
        }
    return {"close": _pct(value), "as_of": None, "source": "legacy"}


def benchmark_metrics(history: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    if not history:
        return {}
    first = history[0].get("benchmarks", {})
    current = history[-1].get("benchmarks", {})
    result: dict[str, dict[str, Any]] = {}
    for symbol, current_value in current.items():
        first_quote = _normalize_quote(first.get(symbol))
        current_quote = _normalize_quote(current_value)
        result[symbol] = {
            "forward_return_pct": pct_change(first_quote["close"], current_quote["close"]),
            "close": current_quote["close"],
            "as_of": current_quote["as_of"],
            "source": current_quote["source"],
        }
    return result


def fmt(value: Any, suffix: str = "", digits: int = 2) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}{suffix}"
    return str(value)


def build_report(
    history: list[dict[str, Any]],
    candidates: list[str],
    alerts: list[dict[str, str]],
    min_score_observations: int = 5,
) -> str:
    metrics = current_metrics(history, candidates, min_score_observations)
    bench = benchmark_metrics(history)
    start = history[0]["date"] if history else "n/a"
    end = history[-1]["date"] if history else "n/a"
    current_row = history[-1] if history else {}
    lines = [
        "# Copy Trader Watch — Latest Report",
        "",
        f"Forward-test window: **{start} → {end}** ({len(history)} observations)",
        "",
        "> Research monitor only. It does not place trades and its score is not an investment recommendation.",
        "",
        "## Candidate ranking",
        "",
        "| Rank | Candidate | Obs | Forward return | Max DD | Return/DD | Risk | Top-1 concentration | Research score |",
        "|---:|---|---:|---:|---:|---:|---:|---:|---:|",
    ]
    for idx, item in enumerate(metrics, 1):
        if not item.get("present"):
            lines.append(
                f"| {idx} | {item['username']} | {item.get('observation_count', 0)} | unresolved | n/a | n/a | n/a | n/a | n/a |"
            )
            continue
        score = (
            fmt(item.get("research_score"), "", 3)
            if item.get("research_score") is not None
            else f"warming up {item['observation_count']}/{min_score_observations}"
        )
        lines.append(
            "| {rank} | {user} | {obs} | {ret} | {dd} | {rdd} | {risk} | {top1} | {score} |".format(
                rank=idx,
                user=item["username"],
                obs=item["observation_count"],
                ret=fmt(item.get("forward_return_pct"), "%"),
                dd=fmt(item.get("forward_max_drawdown_pct"), "%"),
                rdd=fmt(item.get("return_to_drawdown"), "", 3),
                risk=item.get("risk_score", "n/a"),
                top1=fmt(item.get("top1_concentration_pct"), "%"),
                score=score,
            )
        )

    lines.extend(["", "## Benchmarks", ""])
    for symbol, item in bench.items():
        detail = []
        if item.get("as_of"):
            detail.append(f"as of {item['as_of']}")
        if item.get("source"):
            detail.append(str(item["source"]))
        suffix = f" ({', '.join(detail)})" if detail else ""
        lines.append(
            f"- **{symbol}:** {fmt(item.get('forward_return_pct'), '%')} since baseline; "
            f"close {fmt(item.get('close'))}{suffix}"
        )

    lines.extend(["", "## Data quality", ""])
    lines.append("| Candidate | Status | Source | Source timestamp | Source age | Missing streak |")
    lines.append("|---|---|---|---|---:|---:|")
    for user in candidates:
        current = current_row.get("candidates", {}).get(user, {"present": False})
        if current.get("present"):
            age = source_age_hours(current.get("source_timestamp"), current_row.get("source_collected_at"))
            lines.append(
                f"| {user} | resolved | {current.get('source') or 'unknown'} | "
                f"{current.get('source_timestamp') or 'n/a'} | {fmt(age, 'h', 1)} | 0 |"
            )
        else:
            streak = consecutive_missing_count(history, user)
            error = current.get("lookup_error") or "public lookup unresolved"
            lines.append(f"| {user} | {error} | n/a | n/a | n/a | {streak} |")

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
        "- Production candidate data uses the public `weirdapps/etoro_census` per-user endpoint first and its top-1,500 census as a throttled fallback.",
        "- Forward returns are chained from successive YTD observations so a January YTD reset no longer corrupts a multi-year forward test.",
        f"- Research scores remain disabled until a candidate has at least {min_score_observations} resolved observations.",
        "- A missing-data alert requires consecutive unresolved observations, reducing false alarms from one transient API failure.",
        "- Forward max drawdown is calculated from the stored forward path, not from eToro's full historical equity curve.",
        "- SPY/QQQ quotes use Yahoo Finance public chart data first and Stooq as a fallback; the report records the quote session date.",
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
            "User-Agent": "copy-trader-watch/2.0",
        },
        json={"title": f"Copy Trader Watch alert — {report_date}", "body": "\n".join(body)},
    )
    response.raise_for_status()


def main() -> int:
    config = load_json(CONFIG_PATH, {})
    candidates: list[str] = config.get("candidates", [])
    thresholds = Thresholds(**config.get("thresholds", {}))
    min_score_observations = int(config.get("scoring", {}).get("min_observations", 5))
    if not candidates:
        raise SystemExit("No candidates configured")

    census = fetch_json(config["census_url"])
    date = collected_date(census)
    history: list[dict[str, Any]] = load_json(HISTORY_PATH, [])

    unresolved = (census.get("metadata", {}) or {}).get("unresolved", {}) or {}
    candidate_snapshots: dict[str, dict[str, Any]] = {}
    for user in candidates:
        snapshot = snapshot_investor(find_investor(census, user))
        if not snapshot.get("present") and user in unresolved:
            snapshot["lookup_error"] = unresolved[user]
        candidate_snapshots[user] = snapshot

    row = {
        "date": date,
        "source_collected_at": census.get("metadata", {}).get("collectedAt"),
        "candidates": candidate_snapshots,
        "benchmarks": {
            name: fetch_benchmark_quote(stooq_symbol)
            for name, stooq_symbol in config.get("benchmarks", {}).items()
        },
    }

    # Idempotent reruns replace the same Pacific observation date.
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
    state.update({
        "last_run_date": date,
        "active_alert_keys": sorted(current_keys),
        "new_alert_count": len(new_alerts),
    })
    save_json(STATE_PATH, state)

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(
        build_report(history, candidates, alerts, min_score_observations),
        encoding="utf-8",
    )

    try:
        notify_new_alerts(new_alerts, date)
    except requests.RequestException as exc:
        print(f"WARNING: GitHub alert issue could not be created: {exc}")

    print(f"Recorded {date}; {len(alerts)} active alert(s), {len(new_alerts)} new.")
    for item in current_metrics(history, candidates, min_score_observations):
        if item.get("present"):
            score = (
                fmt(item.get("research_score"), "", 3)
                if item.get("research_score") is not None
                else f"warming-up-{item['observation_count']}/{min_score_observations}"
            )
            print(
                f"{item['username']}: return={fmt(item.get('forward_return_pct'), '%')} "
                f"dd={fmt(item.get('forward_max_drawdown_pct'), '%')} score={score}"
            )
        else:
            print(f"{item['username']}: unresolved")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
