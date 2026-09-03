from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from models import AdapterResult, TraderSnapshot, score_snapshot
from adapters import collective2, hyperliquid, mql5, polymarket

ROOT = Path(__file__).resolve().parent
PLATFORM_CONFIG_PATH = ROOT / "platform_config.json"
V2_HISTORY_PATH = ROOT / "data" / "history.json"
V3_HISTORY_PATH = ROOT / "data" / "v3_history.json"
V3_REPORT_PATH = ROOT / "reports" / "v3_latest.md"


def load_json(path: Path, default: Any) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return default


def save_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _num(value: Any) -> float | None:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _v2_etoro_records() -> AdapterResult:
    observed = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    history = load_json(V2_HISTORY_PATH, [])
    if not history:
        return AdapterResult(platform="etoro", observed_at=observed, status="unavailable", message="V2 eToro history is empty")
    latest = history[-1]
    records: list[TraderSnapshot] = []
    for username, item in latest.get("candidates", {}).items():
        if not item.get("present"):
            continue
        risk = _num(item.get("risk_score"))
        top1 = _num(item.get("top1_concentration_pct"))
        copyability = 85.0
        if risk is not None and risk >= 7:
            copyability -= 25
        elif risk is not None and risk >= 5:
            copyability -= 10
        if top1 is not None and top1 > 50:
            copyability -= 25
        elif top1 is not None and top1 > 30:
            copyability -= 10
        record = TraderSnapshot(platform="etoro", trader_id=username.casefold(), name=item.get("name") or username, observed_at=item.get("source_timestamp") or latest.get("source_collected_at") or observed, source=item.get("source") or "copy-trader-watch-v2", source_url=f"https://www.etoro.com/people/{username}", source_quality=82.0, free=True, us_access="yes", live_evidence="public-profile", return_pct=_num(item.get("gain_ytd_pct")), return_window="ytd", max_drawdown_pct=None, profit_factor=None, trades=int(item["trades"]) if isinstance(item.get("trades"), int) else None, win_rate_pct=_num(item.get("win_ratio_pct")), age_days=None, leverage=None, activity_per_day=None, profit_concentration_pct=top1, copyability_score=max(0.0, copyability), actionable=True, actionable_reason="Free U.S. eToro CopyTrader candidate; actual per-account copy eligibility still must be visible in eToro.", metadata={"risk_score": item.get("risk_score"), "top1_concentration_pct": item.get("top1_concentration_pct"), "top2_concentration_pct": item.get("top2_concentration_pct"), "v2_observation_date": latest.get("date")})
        record.research_score = score_snapshot(record)
        records.append(record)
    return AdapterResult(platform="etoro", observed_at=observed, records=records, status="ok" if records else "degraded", message="" if records else "No resolved eToro candidates in latest V2 observation", metadata={"source": "existing V2 monitor", "resolved": len(records)})


def _adapter_failure(platform: str, exc: Exception) -> AdapterResult:
    return AdapterResult(platform=platform, observed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"), status="unavailable", message=f"adapter failure: {exc}")


def collect_all(config: dict[str, Any]) -> list[AdapterResult]:
    platforms = config.get("platforms", {})
    results: list[AdapterResult] = []
    if platforms.get("etoro", {}).get("enabled", True):
        results.append(_v2_etoro_records())
    for name, module in (
        ("hyperliquid", hyperliquid),
        ("mql5", mql5),
        ("polymarket", polymarket),
        ("collective2", collective2),
    ):
        cfg = platforms.get(name, {})
        if not cfg.get("enabled"):
            continue
        try:
            results.append(module.collect(cfg))
        except Exception as exc:
            results.append(_adapter_failure(name, exc))
    return results


def all_records(results: list[AdapterResult]) -> list[TraderSnapshot]:
    records = [record for result in results for record in result.records]
    records.sort(key=lambda r: (r.research_score is not None, r.research_score or -999.0), reverse=True)
    return records


def _fmt(value: Any, suffix: str = "", digits: int = 2) -> str:
    if value is None:
        return "n/a"
    if isinstance(value, bool):
        return "yes" if value else "no"
    if isinstance(value, (int, float)):
        return f"{value:.{digits}f}{suffix}"
    return str(value)


def _short_id(record: TraderSnapshot) -> str:
    if record.trader_id.startswith("0x") and len(record.trader_id) > 14:
        return f"{record.trader_id[:8]}…{record.trader_id[-4:]}"
    return record.trader_id


def _source_details(result: AdapterResult) -> str:
    meta = result.metadata or {}
    if result.platform == "mql5":
        return f"parsed={meta.get('rows_parsed', 'n/a')}; free={meta.get('free_signals_found', 'n/a')}"
    if result.platform == "hyperliquid":
        return f"resolved={meta.get('wallets_resolved', 'n/a')}/{meta.get('wallets_requested', 'n/a')}"
    if result.platform == "polymarket":
        return f"resolved={meta.get('candidates_resolved', 'n/a')}/{meta.get('candidates_requested', 'n/a')}; persistent={meta.get('persistent_candidates_in_monthly_top', 'n/a')}"
    if result.platform == "collective2":
        return f"parsed={meta.get('rows_parsed', 'n/a')}; free={meta.get('free_strategies_found', 'n/a')}; screen={meta.get('free_screen_passed', 'n/a')}"
    if result.platform == "etoro":
        return f"resolved={meta.get('resolved', len(result.records))}"
    return ""


def _conditions(record: TraderSnapshot) -> str:
    if record.platform == "collective2":
        capital = record.metadata.get("suggested_capital")
        if isinstance(capital, (int, float)):
            return f"suggested capital ${capital:,.0f}"
        return "broker route required"
    if record.platform == "etoro":
        return "eToro Copy must be available"
    return ""


def build_report(results: list[AdapterResult], config: dict[str, Any]) -> str:
    records = all_records(results)
    actionable = [r for r in records if r.actionable and r.free is True and r.us_access in {"yes", "conditional"}]
    research_only = [r for r in records if r not in actionable]
    top_n = int(config.get("report", {}).get("top_n", 20))
    lines = ["# Copy Trader Watch V3 — Cross-Platform Report", "", "> Read-only research. No broker login or trade execution. `actionable` means the public-data rules passed, not that profitability is expected.", "", "## Source health", "", "| Platform | Status | Records | Details | Message |", "|---|---|---:|---|---|"]
    for result in results:
        msg = result.message.replace("|", "/") if result.message else ""
        lines.append(f"| {result.platform} | {result.status} | {len(result.records)} | {_source_details(result)} | {msg} |")
    lines.extend(["", "## Free U.S.-actionable candidates", "", "| Rank | Platform | Trader | Return | Window | DD | PF | Trades | Win | Copyability | Score | Conditions |", "|---:|---|---|---:|---|---:|---:|---:|---:|---:|---:|---|"])
    if actionable:
        for i, r in enumerate(actionable[:top_n], 1):
            lines.append(f"| {i} | {r.platform} | {r.name} (`{_short_id(r)}`) | {_fmt(r.return_pct, '%')} | {r.return_window or 'n/a'} | {_fmt(r.max_drawdown_pct, '%')} | {_fmt(r.profit_factor)} | {_fmt(r.trades, '', 0)} | {_fmt(r.win_rate_pct, '%')} | {_fmt(r.copyability_score)} | {_fmt(r.research_score)} | {_conditions(r)} |")
    else:
        lines.append("| — | — | No candidate currently satisfies free + U.S.-actionable + evidence rules | — | — | — | — | — | — | — | — | — |")
    lines.extend(["", "## Cross-platform research leaderboard", "", "| Rank | Platform | Trader | Free | U.S. access | Evidence | Performance | Window / metric | DD | PF | Trades | Leverage | Copyability | Score |", "|---:|---|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|"])
    for i, r in enumerate(records[:top_n], 1):
        lines.append(f"| {i} | {r.platform} | {r.name} (`{_short_id(r)}`) | {_fmt(r.free)} | {r.us_access} | {r.live_evidence} | {_fmt(r.return_pct, '%')} | {r.return_window or 'n/a'} | {_fmt(r.max_drawdown_pct, '%')} | {_fmt(r.profit_factor)} | {_fmt(r.trades, '', 0)} | {_fmt(r.leverage, 'x')} | {_fmt(r.copyability_score)} | {_fmt(r.research_score)} |")
    lines.extend(["", "## Non-actionable reasons", ""])
    seen = 0
    for r in research_only[:top_n]:
        if r.actionable_reason:
            lines.append(f"- **{r.platform} / {r.name}:** {r.actionable_reason}")
            seen += 1
    if not seen:
        lines.append("No non-actionable records were returned.")
    lines.extend(["", "## Method", "", "- eToro uses the existing V2 U.S. candidate monitor and keeps its public-source limitations.", "- Hyperliquid uses the official public `info` API. Its return path uses PnL change on the period's initial account-value base to reduce deposit/withdrawal distortion; it is research-only in this U.S. workflow.", "- MQL5 scans the public MT5 Signals list. Paid signals can appear in research; only explicitly free signals can pass the cost gate, and real-vs-demo/broker compatibility still require verification.", "- Polymarket uses the official Data API leaderboard plus closed/current-position endpoints. Its displayed percentage is closed-position realized P&L divided by estimated purchase cost in the sampled closed positions; it is a capital-efficiency proxy, **not account equity return**, and max drawdown is unknown.", "- Collective2 parses public strategy lists. Collective2 labels performance results hypothetical, so C2 records are evidence for further testing rather than independently verified brokerage equity curves. A `$0/month` result can depend on a broker-sponsored route and may require substantial suggested capital.", "- The cross-platform score rewards performance/drawdown, age, trade sample, PF, source quality, and copyability, while penalizing unknown drawdown, leverage, concentration, high drawdown, and demo-only evidence.", "- Missing data is penalized rather than silently imputed.", ""])
    return "\n".join(lines)


def main() -> int:
    config = load_json(PLATFORM_CONFIG_PATH, {})
    if not config:
        raise SystemExit("platform_config.json is missing or invalid")
    results = collect_all(config)
    observed = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    row = {"observed_at": observed, "sources": [result.to_dict() for result in results]}
    history = load_json(V3_HISTORY_PATH, [])
    date = observed[:10]
    history = [r for r in history if str(r.get("observed_at", ""))[:10] != date]
    history.append(row)
    history.sort(key=lambda r: r.get("observed_at", ""))
    save_json(V3_HISTORY_PATH, history)
    V3_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    V3_REPORT_PATH.write_text(build_report(results, config), encoding="utf-8")
    summary = ", ".join(f"{r.platform}:{r.status}/{len(r.records)}" for r in results)
    print(f"V3 collected {summary}")
    actionables = [r for r in all_records(results) if r.actionable and r.free is True and r.us_access in {"yes", "conditional"}]
    print(f"Free U.S.-actionable candidates: {len(actionables)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
