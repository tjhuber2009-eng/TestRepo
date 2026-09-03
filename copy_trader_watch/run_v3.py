from __future__ import annotations

import json
import math
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from models import AdapterResult, TraderSnapshot
from forward import forward_rank_key, update_tracker
from adapters import collective2, hyperliquid, mql5, polymarket

ROOT = Path(__file__).resolve().parent
PLATFORM_CONFIG_PATH = ROOT / "platform_config.json"
V2_HISTORY_PATH = ROOT / "data" / "history.json"
V3_HISTORY_PATH = ROOT / "data" / "v3_history.json"
V3_FORWARD_PATH = ROOT / "data" / "v3_forward.json"
V3_REPORT_PATH = ROOT / "reports" / "v3_latest.md"
PACIFIC = ZoneInfo("America/Los_Angeles")


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


def _pacific_date() -> str:
    return datetime.now(timezone.utc).astimezone(PACIFIC).date().isoformat()


def _v2_etoro_records() -> AdapterResult:
    observed = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    history = load_json(V2_HISTORY_PATH, [])
    if not history:
        return AdapterResult(platform="etoro", observed_at=observed, status="unavailable", message="V2 eToro history is empty")
    latest = history[-1]
    observation_date = str(latest.get("date") or _pacific_date())
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
        ytd = _num(item.get("gain_ytd_pct"))
        record = TraderSnapshot(
            platform="etoro",
            trader_id=username.casefold(),
            name=item.get("name") or username,
            observed_at=item.get("source_timestamp") or latest.get("source_collected_at") or observed,
            source=item.get("source") or "copy-trader-watch-v2",
            source_url=f"https://www.etoro.com/people/{username}",
            source_quality=82.0,
            free=True,
            us_access="yes",
            live_evidence="public-profile",
            return_pct=ytd,
            return_window="ytd",
            max_drawdown_pct=None,
            profit_factor=None,
            trades=int(item["trades"]) if isinstance(item.get("trades"), int) else None,
            win_rate_pct=_num(item.get("win_ratio_pct")),
            age_days=None,
            leverage=None,
            activity_per_day=None,
            profit_concentration_pct=top1,
            copyability_score=max(0.0, copyability),
            actionable=True,
            actionable_reason="Free U.S. eToro CopyTrader candidate; actual per-account copy eligibility still must be visible in eToro.",
            forward_test_eligible=True,
            forward_test_reason="Forward test chains successive public YTD observations; sample size is confidence-only.",
            metadata={
                "risk_score": item.get("risk_score"),
                "top1_concentration_pct": item.get("top1_concentration_pct"),
                "top2_concentration_pct": item.get("top2_concentration_pct"),
                "v2_observation_date": observation_date,
                "forward_metric_kind": "cumulative_pct",
                "forward_metric_value": ytd,
                "forward_period_key": observation_date[:4],
            },
        )
        records.append(record)
    return AdapterResult(
        platform="etoro",
        observed_at=observed,
        records=records,
        status="ok" if records else "degraded",
        message="" if records else "No resolved eToro candidates in latest V2 observation",
        metadata={"source": "existing V2 monitor", "resolved": len(records)},
    )


def _adapter_failure(platform: str, exc: Exception) -> AdapterResult:
    return AdapterResult(
        platform=platform,
        observed_at=datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        status="unavailable",
        message=f"adapter failure: {exc}",
    )


def _enrich_forward_metadata(result: AdapterResult, observation_date: str) -> None:
    """Standardize forward metrics without changing each source's displayed history."""
    for record in result.records:
        meta = record.metadata
        if record.platform == "mql5" and record.return_pct is not None:
            meta.setdefault("forward_metric_kind", "cumulative_pct")
            meta.setdefault("forward_metric_value", record.return_pct)
            meta.setdefault("forward_period_key", "all")
            record.forward_test_reason = "Every discovered signal is admitted; listed history size only changes evidence confidence."
        elif record.platform == "polymarket":
            pnl = _num(meta.get("monthly_pnl"))
            base = _num(meta.get("current_portfolio_value"))
            if pnl is not None and base is not None and base > 0:
                meta.setdefault("forward_metric_kind", "periodic_pnl_index")
                meta.setdefault("forward_metric_value", pnl)
                meta.setdefault("forward_metric_base", base)
                meta.setdefault("forward_period_key", observation_date[:7])
                record.forward_test_reason = "Forward test uses changes in official monthly P&L scaled by prior observed portfolio value."
            else:
                record.forward_test_reason = "Waiting for a usable public P&L/base pair; not rejected for sample size."
        elif record.platform == "collective2":
            kind = str(record.return_window or "").casefold()
            if "cumul" in kind and record.return_pct is not None:
                meta.setdefault("forward_metric_kind", "cumulative_pct")
                meta.setdefault("forward_metric_value", record.return_pct)
                meta.setdefault("forward_period_key", "all")
            record.forward_test_reason = "Tracked when a cumulative metric is public; age/trade count are not admission gates."
        elif record.platform == "hyperliquid":
            # The adapter exposes rolling/month-window returns today. Do not pretend
            # changes in a rolling window are a realized follower equity curve.
            if not meta.get("forward_metric_kind"):
                record.forward_test_eligible = False
                record.forward_test_reason = "Needs a non-rolling P&L index before forward return can be computed; this is a metric-validity issue, not a sample-size filter."


def collect_all(config: dict[str, Any], observation_date: str) -> list[AdapterResult]:
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
            result = module.collect(cfg)
            _enrich_forward_metadata(result, observation_date)
            results.append(result)
        except Exception as exc:
            results.append(_adapter_failure(name, exc))
    return results


def all_records(results: list[AdapterResult]) -> list[TraderSnapshot]:
    records = [record for result in results for record in result.records]
    records.sort(key=forward_rank_key, reverse=True)
    return records


def historical_records(results: list[AdapterResult]) -> list[TraderSnapshot]:
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
    if record.platform == "mql5":
        return "verify real/demo + broker compatibility"
    return ""


def build_report(results: list[AdapterResult], config: dict[str, Any], observation_date: str, forward_state: dict[str, Any]) -> str:
    records = all_records(results)
    historical = historical_records(results)
    forward_ranked = [r for r in records if r.forward_score is not None]
    awaiting = [r for r in records if r.forward_test_eligible and r.forward_score is None]
    free_forward = [r for r in records if r.free is True and r.us_access in {"yes", "conditional"} and r.forward_test_eligible]
    practical = [r for r in records if r.actionable and r.free is True and r.us_access in {"yes", "conditional"}]
    research_only = [r for r in records if r not in practical]
    top_n = int(config.get("report", {}).get("top_n", 40))

    lines = [
        "# Copy Trader Watch V3 — Forward-First Cross-Platform Report",
        "",
        f"> Observation date: **{observation_date} America/Los_Angeles**. Read-only research; no broker login or trade execution.",
        "",
        "> **Small samples are never an exclusion rule.** Age/trade count affect only Evidence Confidence. Once a candidate has two usable public observations, its Forward Score—not sample size—controls ranking.",
        "",
        "## Source health",
        "",
        "| Platform | Status | Records | Details | Message |",
        "|---|---|---:|---|---|",
    ]
    for result in results:
        msg = result.message.replace("|", "/") if result.message else ""
        lines.append(f"| {result.platform} | {result.status} | {len(result.records)} | {_source_details(result)} | {msg} |")

    lines.extend([
        "",
        "## Forward-test leaderboard",
        "",
        "| Rank | Platform | Trader | Obs | Forward return | Forward DD | Forward PF | Forward win | Forward score | Evidence | Seed |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
    ])
    if forward_ranked:
        for i, r in enumerate(forward_ranked[:top_n], 1):
            lines.append(
                f"| {i} | {r.platform} | {r.name} (`{_short_id(r)}`) | {r.forward_observations} | "
                f"{_fmt(r.forward_return_pct, '%')} | {_fmt(r.forward_max_drawdown_pct, '%')} | {_fmt(r.forward_profit_factor)} | "
                f"{_fmt(r.forward_win_rate_pct, '%')} | {_fmt(r.forward_score)} | {_fmt(r.evidence_score)} | {_fmt(r.research_score)} |"
            )
    else:
        lines.append("| — | — | Forward test warming up; a second usable observation is required to calculate a return | — | — | — | — | — | — | — | — |")

    lines.extend([
        "",
        "## Free forward-test universe",
        "",
        "These candidates are admitted to monitoring regardless of historical sample size. Execution eligibility is a separate question.",
        "",
        "| Platform | Trader | Obs | Forward | DD | Historical metric | Trades | Age days | Evidence | Seed | Execution conditions |",
        "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---|",
    ])
    if free_forward:
        for r in free_forward[:top_n]:
            lines.append(
                f"| {r.platform} | {r.name} (`{_short_id(r)}`) | {r.forward_observations} | {_fmt(r.forward_return_pct, '%')} | "
                f"{_fmt(r.forward_max_drawdown_pct, '%')} | {_fmt(r.return_pct, '%')} | {_fmt(r.trades, '', 0)} | {_fmt(r.age_days, '', 0)} | "
                f"{_fmt(r.evidence_score)} | {_fmt(r.research_score)} | {_conditions(r)} |"
            )
    else:
        lines.append("| — | No current free U.S./conditional candidate has a usable forward metric | — | — | — | — | — | — | — | — | — |")

    lines.extend([
        "",
        "## Awaiting forward result",
        "",
        "| Platform | Trader | Historical metric | DD | Trades | Age days | Evidence | Seed | Forward-test status |",
        "|---|---|---:|---:|---:|---:|---:|---:|---|",
    ])
    if awaiting:
        for r in awaiting[:top_n]:
            lines.append(
                f"| {r.platform} | {r.name} (`{_short_id(r)}`) | {_fmt(r.return_pct, '%')} | {_fmt(r.max_drawdown_pct, '%')} | "
                f"{_fmt(r.trades, '', 0)} | {_fmt(r.age_days, '', 0)} | {_fmt(r.evidence_score)} | {_fmt(r.research_score)} | {r.forward_test_reason or 'waiting for next observation'} |"
            )
    else:
        lines.append("| — | None | — | — | — | — | — | — | — |")

    lines.extend([
        "",
        "## Free U.S.-practical candidates",
        "",
        "This section applies platform/access requirements but **does not use sample size as a gate**.",
        "",
        "| Rank | Platform | Trader | Forward | DD | Historical metric | Copyability | Evidence | Rank score | Conditions |",
        "|---:|---|---|---:|---:|---:|---:|---:|---:|---|",
    ])
    if practical:
        for i, r in enumerate(practical[:top_n], 1):
            lines.append(
                f"| {i} | {r.platform} | {r.name} (`{_short_id(r)}`) | {_fmt(r.forward_return_pct, '%')} | {_fmt(r.forward_max_drawdown_pct, '%')} | "
                f"{_fmt(r.return_pct, '%')} | {_fmt(r.copyability_score)} | {_fmt(r.evidence_score)} | {_fmt(r.rank_score)} | {_conditions(r)} |"
            )
    else:
        lines.append("| — | — | No current candidate satisfies platform/access requirements | — | — | — | — | — | — | — |")

    lines.extend([
        "",
        "## Historical discovery leaderboard (seed only)",
        "",
        "This ranking is only for new-candidate discovery before enough forward observations exist. Sample size is not part of Seed Score.",
        "",
        "| Rank | Platform | Trader | Free | U.S. access | Historical metric | Window | DD | PF | Trades | Evidence | Seed |",
        "|---:|---|---|---|---|---:|---|---:|---:|---:|---:|---:|",
    ])
    for i, r in enumerate(historical[:top_n], 1):
        lines.append(
            f"| {i} | {r.platform} | {r.name} (`{_short_id(r)}`) | {_fmt(r.free)} | {r.us_access} | {_fmt(r.return_pct, '%')} | "
            f"{r.return_window or 'n/a'} | {_fmt(r.max_drawdown_pct, '%')} | {_fmt(r.profit_factor)} | {_fmt(r.trades, '', 0)} | {_fmt(r.evidence_score)} | {_fmt(r.research_score)} |"
        )

    lines.extend(["", "## Non-practical / research-only reasons", ""])
    seen = 0
    for r in research_only[:top_n]:
        if r.actionable_reason:
            lines.append(f"- **{r.platform} / {r.name}:** {r.actionable_reason}")
            seen += 1
    if not seen:
        lines.append("No non-practical records were returned.")

    lines.extend([
        "",
        "## Method",
        "",
        "- **Forward Score is sample-size neutral.** As soon as two usable observations exist, rank is based on observed forward return and forward drawdown. Observation count does not multiply or discount the score.",
        "- **Evidence Confidence is separate.** Track-record age, trade count, source quality, and metric completeness only tell us how much context exists; they do not eliminate a candidate or lower its Forward Score.",
        "- eToro chains successive public YTD observations with calendar-year reset handling.",
        "- MQL5 chains the public cumulative Growth metric. All discovered free signals are intended to be retained for forward monitoring; real/demo and broker compatibility remain execution checks.",
        "- Polymarket uses changes in official monthly P&L scaled by prior observed portfolio value; this remains research-only and is not presented as an account equity curve.",
        "- Hyperliquid is kept in historical research until the adapter exposes a non-rolling P&L index; rolling monthly-window changes are deliberately not mislabeled as forward returns.",
        "- Collective2 remains unavailable from GitHub Actions while its public pages return HTTP 403; the adapter is retained for a future authenticated API route.",
        f"- Persistent tracker currently contains **{forward_state.get('tracked_candidates', 0)}** candidates.",
        "",
    ])
    return "\n".join(lines)


def main() -> int:
    config = load_json(PLATFORM_CONFIG_PATH, {})
    if not config:
        raise SystemExit("platform_config.json is missing or invalid")

    observation_date = _pacific_date()
    results = collect_all(config, observation_date)

    forward_state = update_tracker(results, load_json(V3_FORWARD_PATH, {}), observation_date)
    save_json(V3_FORWARD_PATH, forward_state)

    observed = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    row = {"observed_at": observed, "date": observation_date, "sources": [result.to_dict() for result in results]}
    history = load_json(V3_HISTORY_PATH, [])
    history = [
        r for r in history
        if str(r.get("date") or str(r.get("observed_at", ""))[:10]) != observation_date
    ]
    history.append(row)
    history.sort(key=lambda r: (r.get("date", ""), r.get("observed_at", "")))
    save_json(V3_HISTORY_PATH, history)

    V3_REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    V3_REPORT_PATH.write_text(build_report(results, config, observation_date, forward_state), encoding="utf-8")

    summary = ", ".join(f"{r.platform}:{r.status}/{len(r.records)}" for r in results)
    print(f"V3 collected {summary}")
    ranked = [r for r in all_records(results) if r.forward_score is not None]
    free_forward = [r for r in all_records(results) if r.free is True and r.us_access in {"yes", "conditional"} and r.forward_test_eligible]
    print(f"Forward-ranked candidates: {len(ranked)}; free forward-test universe: {len(free_forward)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
