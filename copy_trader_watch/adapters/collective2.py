from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any

import requests
from bs4 import BeautifulSoup

from models import AdapterResult, TraderSnapshot, score_snapshot

USER_AGENT = "Mozilla/5.0 (compatible; CopyTraderWatch/3.3; +https://github.com/)"
DEFAULT_URLS = [
    "https://collective2.com/lb/320",
    "https://collective2.com/selector/old_timers",
    "https://collective2.com/selector/",
]


def _num(value: Any) -> float | None:
    try:
        x = float(value)
        return x if math.isfinite(x) else None
    except (TypeError, ValueError):
        return None


def _money(text: str, pattern: str) -> float | None:
    match = re.search(pattern, text, re.I)
    return _num(match.group(1).replace(",", "")) if match else None


def _strategy_block(h2):
    candidates = []
    node = h2
    for _ in range(7):
        node = getattr(node, "parent", None)
        if node is None:
            break
        text = " ".join(node.stripped_strings)
        if "Subscription fee" in text and "Maximum drawdown" in text and ("Annual Return" in text or "Cumul. Return" in text):
            candidates.append((len(text), node))
    return min(candidates, key=lambda x: x[0])[1] if candidates else None


def parse_page(html: str, source_url: str) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    rows: list[dict[str, Any]] = []
    for h2 in soup.find_all("h2"):
        name = " ".join(h2.stripped_strings).strip()
        if not name or name.casefold() in {"trading strategies selected", "leader board"}:
            continue
        block = _strategy_block(h2)
        if block is None:
            continue
        text = " ".join(block.stripped_strings).replace("\xa0", " ")
        fee = _money(text, r"\$([\d,]+(?:\.\d+)?)\s*/\s*month\s+Subscription fee")
        dd_match = re.search(r"\(([\d.]+)%\)\s*Maximum drawdown", text, re.I)
        ret_match = re.search(r"([+-]?\d+(?:\.\d+)?)%\s+(Annual Return|Cumul\. Return)", text, re.I)
        age_match = re.search(r"Strategy age\s+([\d.]+)", text, re.I)
        sharpe_match = re.search(r"([-+]?\d+(?:\.\d+)?)\s+Sharpe ratio", text, re.I)
        win_match = re.search(r"([\d.]+)%\s+%?\s*Profitable", text, re.I)
        leverage_match = re.search(r"([\d.]+)\s+Average Leverage", text, re.I)
        wl_match = re.search(r"([\d.]+)\s*:1\s+W:L Ratio", text, re.I)
        suggested = _money(text, r"\$([\d,]+(?:\.\d+)?)\s+Suggested Capital")
        since_match = re.search(r"since\s+([A-Za-z]{3}\s+\d{1,2},\s+\d{4})", text, re.I)
        if fee is None or dd_match is None or ret_match is None:
            continue
        slug = re.sub(r"[^a-z0-9]+", "-", name.casefold()).strip("-")
        link = h2.find("a", href=True)
        href = str(link["href"]) if link else None
        rows.append({
            "strategy_id": slug,
            "name": name,
            "source_url": requests.compat.urljoin(source_url, href) if href else source_url,
            "monthly_fee_usd": fee,
            "free": fee == 0,
            "max_drawdown_pct": _num(dd_match.group(1)),
            "return_pct": _num(ret_match.group(1)),
            "return_kind": ret_match.group(2),
            "age_days": _num(age_match.group(1)) if age_match else None,
            "sharpe": _num(sharpe_match.group(1)) if sharpe_match else None,
            "win_rate_pct": _num(win_match.group(1)) if win_match else None,
            "average_leverage": _num(leverage_match.group(1)) if leverage_match else None,
            "win_loss_ratio": _num(wl_match.group(1)) if wl_match else None,
            "suggested_capital": suggested,
            "since": since_match.group(1) if since_match else None,
            "source_listing": source_url,
        })
    return rows


def _fetch(url: str) -> str:
    response = requests.get(url, timeout=45, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9"})
    response.raise_for_status()
    return response.text


def _copyability(row: dict[str, Any]) -> float:
    """Execution/risk copyability only; sample age is intentionally excluded."""
    score = 80.0
    dd = row.get("max_drawdown_pct")
    lev = row.get("average_leverage")
    capital = row.get("suggested_capital")
    if isinstance(dd, (int, float)):
        if dd > 40:
            score -= 30
        elif dd > 25:
            score -= 15
    if isinstance(lev, (int, float)):
        if lev > 5:
            score -= 20
        elif lev > 2.5:
            score -= 10
    if isinstance(capital, (int, float)) and capital > 100_000:
        score -= 10
    return max(0.0, min(100.0, score))


def normalize(row: dict[str, Any], observed: str) -> TraderSnapshot:
    free = row.get("free") is True
    age = row.get("age_days")
    dd_raw = row.get("max_drawdown_pct")
    dd = -abs(dd_raw) if isinstance(dd_raw, (int, float)) else None
    ret = row.get("return_pct")
    controlled_dd = isinstance(dd_raw, (int, float)) and dd_raw <= 30
    positive = isinstance(ret, (int, float)) and ret > 0

    # Historical age/sample size is deliberately NOT an actionability gate.
    actionable = bool(free and controlled_dd and positive)
    if actionable:
        reason = "Free broker-sponsored Collective2 route; broker compatibility and suggested-capital fit still require verification. Historical sample size is not an exclusion rule."
    elif not free:
        reason = f"Not free: listed subscription fee is {row.get('monthly_fee_usd'):g} USD/month."
    else:
        reason = "Free strategy remains in forward monitoring, but current return/drawdown or broker-route requirements do not yet support practical execution."

    snap = TraderSnapshot(
        platform="collective2",
        trader_id=str(row["strategy_id"]),
        name=str(row["name"]),
        observed_at=observed,
        source="collective2-public-leaderboard",
        source_url=row.get("source_url"),
        source_quality=72.0,
        free=free,
        us_access="conditional",
        live_evidence="hypothetical-platform-track-record",
        return_pct=ret if isinstance(ret, (int, float)) else None,
        return_window=str(row.get("return_kind") or "platform return"),
        max_drawdown_pct=dd,
        profit_factor=None,
        trades=None,
        win_rate_pct=row.get("win_rate_pct") if isinstance(row.get("win_rate_pct"), (int, float)) else None,
        age_days=age if isinstance(age, (int, float)) else None,
        leverage=row.get("average_leverage") if isinstance(row.get("average_leverage"), (int, float)) else None,
        activity_per_day=None,
        profit_concentration_pct=None,
        copyability_score=_copyability(row),
        actionable=actionable,
        actionable_reason=reason,
        forward_test_eligible=True,
        forward_test_reason="Admitted regardless of track-record age; forward metric is used when a cumulative return field is available.",
        metadata={
            "monthly_fee_usd": row.get("monthly_fee_usd"),
            "sharpe": row.get("sharpe"),
            "win_loss_ratio": row.get("win_loss_ratio"),
            "suggested_capital": row.get("suggested_capital"),
            "since": row.get("since"),
            "source_listing": row.get("source_listing"),
            "performance_warning": "Collective2 labels performance results hypothetical; provider performance does not guarantee AutoTrade results.",
        },
    )
    snap.research_score = score_snapshot(snap)
    return snap


def collect(config: dict[str, Any]) -> AdapterResult:
    observed = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    urls = config.get("urls") or DEFAULT_URLS
    keep_top = int(config.get("keep_top", 30))
    parsed: list[dict[str, Any]] = []
    errors: list[str] = []
    for url in urls:
        try:
            parsed.extend(parse_page(_fetch(url), url))
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"{url}: {exc}")
    unique: dict[str, dict[str, Any]] = {}
    for row in parsed:
        key = str(row["strategy_id"])
        current = unique.get(key)
        # Prefer a zero-fee observation if the same strategy appears on multiple lists.
        if current is None or (row.get("free") and not current.get("free")):
            unique[key] = row
    records = [normalize(row, observed) for row in unique.values()]
    records.sort(key=lambda r: (r.research_score is not None, r.research_score or -999.0), reverse=True)
    free_records = [r for r in records if r.free]
    actionables = [r for r in records if r.actionable]
    records = records[:keep_top]
    if not parsed and errors:
        status = "unavailable"
    elif not parsed:
        status = "degraded"
        errors.append("pages fetched but no strategy blocks parsed")
    elif errors:
        status = "degraded"
    else:
        status = "ok"
    return AdapterResult(
        platform="collective2",
        observed_at=observed,
        records=records,
        status=status,
        message="; ".join(errors[:4]),
        metadata={
            "urls_requested": len(urls),
            "rows_parsed": len(parsed),
            "unique_strategies": len(unique),
            "free_strategies_found": len(free_records),
            "free_screen_passed": len(actionables),
            "note": "Sample age does not gate admission. Collective2 publicly states performance results are hypothetical; zero subscription fee can depend on broker-sponsored routing.",
        },
    )
