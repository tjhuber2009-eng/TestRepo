"""Zero-cost public-source discovery adapters for AUTORESEARCH v4.

Discovery records are not trusted evidence. They enter the standardized intake
queue and must later be reconstructed and backtested by the project.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Iterable
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
import json
import os


@dataclass(frozen=True)
class SourceCandidate:
    source_type: str
    title: str
    url: str
    snippet: str = ""
    query: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _get_json(url: str, *, headers: dict | None = None, timeout: int = 15):
    req = Request(url, headers={"User-Agent": "AUTORESEARCH-v4/1.0", **(headers or {})})
    with urlopen(req, timeout=timeout) as response:
        return json.loads(response.read().decode("utf-8"))


def github_search(queries: Iterable[str], *, token: str | None = None, per_query: int = 10) -> list[SourceCandidate]:
    token = token or os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    out = []
    for query in queries:
        url = f"https://api.github.com/search/repositories?q={quote_plus(query)}&sort=stars&order=desc&per_page={per_query}"
        try:
            payload = _get_json(url, headers=headers)
        except Exception:
            continue
        for item in payload.get("items", []):
            out.append(SourceCandidate(
                source_type="github",
                title=item.get("full_name", ""),
                url=item.get("html_url", ""),
                snippet=item.get("description") or "",
                query=query,
            ))
    return out


def reddit_search(queries: Iterable[str], *, limit: int = 10) -> list[SourceCandidate]:
    out = []
    for query in queries:
        url = f"https://www.reddit.com/search.json?q={quote_plus(query)}&sort=relevance&limit={limit}&t=all"
        try:
            payload = _get_json(url)
        except Exception:
            continue
        for child in payload.get("data", {}).get("children", []):
            d = child.get("data", {})
            permalink = d.get("permalink", "")
            out.append(SourceCandidate(
                source_type="reddit",
                title=d.get("title", ""),
                url=("https://www.reddit.com" + permalink) if permalink else d.get("url", ""),
                snippet=(d.get("selftext") or "")[:1200],
                query=query,
            ))
    return out


def crossref_search(queries: Iterable[str], *, rows: int = 10) -> list[SourceCandidate]:
    out = []
    for query in queries:
        url = f"https://api.crossref.org/works?query={quote_plus(query)}&rows={rows}&select=title,URL,abstract"
        try:
            payload = _get_json(url)
        except Exception:
            continue
        for item in payload.get("message", {}).get("items", []):
            title = (item.get("title") or [""])[0]
            out.append(SourceCandidate(
                source_type="academic",
                title=title,
                url=item.get("URL", ""),
                snippet=(item.get("abstract") or "")[:1200],
                query=query,
            ))
    return out


def dedupe(rows: Iterable[SourceCandidate]) -> list[SourceCandidate]:
    seen = set()
    out = []
    for row in rows:
        key = row.url.strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def default_queries() -> list[str]:
    return [
        'trading strategy backtest "profit factor"',
        'algorithmic trading strategy CAGR drawdown',
        'momentum mean reversion trading bot backtest',
        'post earnings announcement drift strategy',
        'leveraged ETF rotation strategy',
        'crypto funding basis trading strategy',
        'intraday opening range breakout backtest',
    ]


def harvest_default() -> list[SourceCandidate]:
    q = default_queries()
    return dedupe(github_search(q) + reddit_search(q) + crossref_search(q))


if __name__ == "__main__":
    import argparse
    from pathlib import Path
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="v4_state/external-source-candidates.json")
    args = ap.parse_args()
    rows = harvest_default()
    p = Path(args.output)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps({
        "protocol": "alpha_generation_v4",
        "policy": "discovery only; not trading evidence",
        "count": len(rows),
        "sources": [r.to_dict() for r in rows],
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"harvested {len(rows)} source candidates -> {p}")
