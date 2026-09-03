from __future__ import annotations

import math
import re
from datetime import datetime, timezone
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

from models import AdapterResult, TraderSnapshot, score_snapshot

DEFAULT_URL = "https://www.mql5.com/en/signals/mt5/list"
USER_AGENT = "Mozilla/5.0 (compatible; CopyTraderWatch/3.0; +https://github.com/)"


def _text(node) -> str:
    return " ".join(node.stripped_strings).replace("\xa0", " ").strip()


def _num(text: Any) -> float | None:
    if text is None:
        return None
    raw = str(text).strip().replace("\xa0", " ").replace("%", "").replace(",", "")
    raw = re.sub(r"\s+", "", raw)
    if not raw:
        return None
    mult = 1.0
    if raw[-1:].upper() == "K":
        mult, raw = 1_000.0, raw[:-1]
    elif raw[-1:].upper() == "M":
        mult, raw = 1_000_000.0, raw[:-1]
    match = re.search(r"[-+]?\d+(?:\.\d+)?", raw)
    if not match:
        return None
    try:
        x = float(match.group()) * mult
        return x if math.isfinite(x) else None
    except ValueError:
        return None


def _int(text: Any) -> int | None:
    value = _num(text)
    return int(round(value)) if value is not None else None


def _fee(text: str) -> tuple[bool | None, float | None]:
    low = text.casefold()
    if "free" in low:
        return True, 0.0
    value = _num(text)
    if value is None:
        return None, None
    return value == 0, value


def _leverage(text: str) -> float | None:
    match = re.search(r"1\s*:\s*(\d+(?:\.\d+)?)", text)
    return float(match.group(1)) if match else None


def _header_name(text: str) -> str:
    t = re.sub(r"\s+", " ", text.strip().casefold())
    aliases = {"#": "rank", "signal": "signal", "signals": "signal", "price": "price", "growth": "growth", "subscribers": "subscribers", "funds": "funds", "balance": "balance", "weeks": "weeks", "expert advisors": "expert_advisors", "trades": "trades", "win %": "win", "activity": "activity", "pf": "pf", "expected payoff": "expected_payoff", "drawdown": "drawdown", "leverage": "leverage"}
    return aliases.get(t, t)


def parse_table(html: str, base_url: str = DEFAULT_URL) -> list[dict[str, Any]]:
    soup = BeautifulSoup(html, "html.parser")
    records: list[dict[str, Any]] = []
    for table in soup.find_all("table"):
        headers = [_header_name(_text(th)) for th in table.find_all("th")]
        if not {"signal", "price", "growth", "drawdown"}.issubset(set(headers)):
            continue
        for tr in table.find_all("tr"):
            cells = tr.find_all("td", recursive=False)
            if len(cells) < min(8, len(headers)):
                continue
            values = [_text(cell) for cell in cells]
            mapping = {headers[i]: values[i] for i in range(min(len(headers), len(values)))}
            signal_cell = cells[headers.index("signal")] if "signal" in headers and headers.index("signal") < len(cells) else cells[0]
            link = signal_cell.find("a", href=True)
            if not link:
                continue
            href = str(link["href"])
            match = re.search(r"/signals/(\d+)", href)
            if not match:
                continue
            signal_id = match.group(1)
            name = _text(link) or mapping.get("signal") or f"MQL5 {signal_id}"
            is_free, monthly_fee = _fee(mapping.get("price", ""))
            records.append({"signal_id": signal_id, "name": name, "url": urljoin(base_url, href), "price_text": mapping.get("price"), "free": is_free, "monthly_fee_usd": monthly_fee, "growth_pct": _num(mapping.get("growth")), "subscribers": _int(mapping.get("subscribers")), "weeks": _num(mapping.get("weeks")), "expert_advisor_pct": _num(mapping.get("expert_advisors")), "trades": _int(mapping.get("trades")), "win_rate_pct": _num(mapping.get("win")), "activity_pct": _num(mapping.get("activity")), "profit_factor": _num(mapping.get("pf")), "drawdown_pct": _num(mapping.get("drawdown")), "leverage": _leverage(mapping.get("leverage", ""))})
    return records


def _fetch_page(url: str) -> str:
    response = requests.get(url, timeout=45, headers={"User-Agent": USER_AGENT, "Accept": "text/html,application/xhtml+xml", "Accept-Language": "en-US,en;q=0.9"})
    response.raise_for_status()
    return response.text


def _copyability(record: dict[str, Any]) -> float:
    score = 80.0
    weeks = record.get("weeks") or 0
    trades = record.get("trades") or 0
    trades_per_day = trades / max(weeks * 7.0, 1.0)
    if trades_per_day > 50:
        score -= 35
    elif trades_per_day > 20:
        score -= 20
    elif trades_per_day > 8:
        score -= 10
    lev = record.get("leverage")
    if lev and lev > 200:
        score -= 20
    elif lev and lev > 100:
        score -= 10
    activity = record.get("activity_pct")
    if activity is not None and activity > 90:
        score -= 10
    return max(0.0, min(100.0, score))


def _normalize(record: dict[str, Any], observed: str) -> TraderSnapshot:
    free = record.get("free")
    fee = record.get("monthly_fee_usd")
    dd_raw = record.get("drawdown_pct")
    dd = -abs(dd_raw) if isinstance(dd_raw, (int, float)) else None
    weeks = record.get("weeks")
    age_days = weeks * 7.0 if isinstance(weeks, (int, float)) else None
    trades = record.get("trades")
    activity_per_day = trades / age_days if isinstance(trades, int) and age_days and age_days > 0 else None
    if free:
        reason = "Free signal discovered, but real-vs-demo and broker compatibility require verification before actionability."
    else:
        reason = f"Not free: listed subscription fee is {fee:g} USD/month." if isinstance(fee, (int, float)) else "Subscription fee is not verified as free."
    snap = TraderSnapshot(platform="mql5", trader_id=str(record["signal_id"]), name=str(record["name"]), observed_at=observed, source="mql5-public-signals-table", source_url=record.get("url"), source_quality=80.0, free=free, us_access="conditional", live_evidence="unknown", return_pct=record.get("growth_pct"), return_window="since-inception", max_drawdown_pct=dd, profit_factor=record.get("profit_factor"), trades=trades, win_rate_pct=record.get("win_rate_pct"), age_days=age_days, leverage=record.get("leverage"), activity_per_day=activity_per_day, profit_concentration_pct=None, copyability_score=_copyability(record), actionable=False, actionable_reason=reason, metadata={"monthly_fee_usd": fee, "price_text": record.get("price_text"), "subscribers": record.get("subscribers"), "activity_pct": record.get("activity_pct"), "expert_advisor_pct": record.get("expert_advisor_pct")})
    snap.research_score = score_snapshot(snap)
    return snap


def collect(config: dict[str, Any]) -> AdapterResult:
    observed = datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
    base_url = config.get("list_url", DEFAULT_URL).rstrip("/")
    max_pages = int(config.get("max_pages", 3))
    include_paid = bool(config.get("include_paid_research", True))
    keep_top = int(config.get("keep_top", 30))
    parsed: list[dict[str, Any]] = []
    errors: list[str] = []
    for page in range(1, max_pages + 1):
        url = base_url if page == 1 else f"{base_url}/page{page}"
        try:
            parsed.extend(parse_table(_fetch_page(url), base_url))
        except (requests.RequestException, ValueError) as exc:
            errors.append(f"page{page}: {exc}")
    unique: dict[str, dict[str, Any]] = {}
    for item in parsed:
        unique.setdefault(str(item["signal_id"]), item)
    normalized = [_normalize(item, observed) for item in unique.values()]
    free_records = [r for r in normalized if r.free is True]
    selected = normalized if include_paid else free_records
    selected.sort(key=lambda r: (r.research_score is not None, r.research_score or -999), reverse=True)
    selected = selected[:keep_top]
    if not parsed and errors:
        status = "unavailable"
    elif errors:
        status = "degraded"
    else:
        status = "ok"
    return AdapterResult(platform="mql5", observed_at=observed, records=selected, status=status, message="; ".join(errors[:5]), metadata={"pages_requested": max_pages, "rows_parsed": len(parsed), "unique_signals": len(unique), "free_signals_found": len(free_records), "include_paid_research": include_paid, "note": "MQL5 list-page free status is not sufficient to establish real-account evidence."})
