"""Free public-source strategy discovery for AUTORESEARCH phase 3.

Discovery is hypothesis generation only. Nothing harvested here is trading
evidence or a runnable family until rules are reconstructed causally, deduped,
and admitted through the normal development-only harness.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable
from urllib.parse import quote_plus
from urllib.request import Request, urlopen
import argparse
import json
import os
import xml.etree.ElementTree as ET


UA = "AUTORESEARCH-free-pipeline/1.0"


@dataclass(frozen=True)
class Discovery:
    source_type: str
    title: str
    url: str
    snippet: str = ""
    query: str = ""

    def to_dict(self) -> dict:
        return asdict(self)


def _get(url: str, *, headers: dict | None = None, timeout: int = 20) -> bytes:
    req = Request(url, headers={"User-Agent": UA, **(headers or {})})
    with urlopen(req, timeout=timeout) as response:
        return response.read()


def _json(url: str, *, headers: dict | None = None):
    return json.loads(_get(url, headers=headers).decode("utf-8"))


def github_search(queries: Iterable[str], per_query: int = 10) -> list[Discovery]:
    token = os.getenv("GH_TOKEN") or os.getenv("GITHUB_TOKEN")
    headers = {"Accept": "application/vnd.github+json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    out = []
    for query in queries:
        url = (
            "https://api.github.com/search/repositories?q="
            + quote_plus(query)
            + f"&sort=stars&order=desc&per_page={per_query}"
        )
        try:
            payload = _json(url, headers=headers)
        except Exception:
            continue
        for item in payload.get("items", []):
            out.append(Discovery(
                "github",
                item.get("full_name", ""),
                item.get("html_url", ""),
                item.get("description") or "",
                query,
            ))
    return out


def reddit_search(queries: Iterable[str], limit: int = 10) -> list[Discovery]:
    out = []
    for query in queries:
        url = (
            "https://www.reddit.com/search.json?q="
            + quote_plus(query)
            + f"&sort=relevance&limit={limit}&t=all"
        )
        try:
            payload = _json(url)
        except Exception:
            continue
        for child in payload.get("data", {}).get("children", []):
            d = child.get("data", {})
            permalink = d.get("permalink", "")
            out.append(Discovery(
                "reddit",
                d.get("title", ""),
                ("https://www.reddit.com" + permalink) if permalink else d.get("url", ""),
                (d.get("selftext") or "")[:1600],
                query,
            ))
    return out


def crossref_search(queries: Iterable[str], rows: int = 10) -> list[Discovery]:
    out = []
    for query in queries:
        url = (
            "https://api.crossref.org/works?query="
            + quote_plus(query)
            + f"&rows={rows}&select=title,URL,abstract"
        )
        try:
            payload = _json(url)
        except Exception:
            continue
        for item in payload.get("message", {}).get("items", []):
            out.append(Discovery(
                "crossref",
                (item.get("title") or [""])[0],
                item.get("URL", ""),
                (item.get("abstract") or "")[:1600],
                query,
            ))
    return out


def openalex_search(queries: Iterable[str], per_page: int = 10) -> list[Discovery]:
    out = []
    for query in queries:
        url = (
            "https://api.openalex.org/works?search="
            + quote_plus(query)
            + f"&per-page={per_page}"
        )
        try:
            payload = _json(url)
        except Exception:
            continue
        for item in payload.get("results", []):
            primary = item.get("primary_location") or {}
            out.append(Discovery(
                "openalex",
                item.get("display_name", ""),
                primary.get("landing_page_url") or item.get("id", ""),
                "",
                query,
            ))
    return out


def arxiv_search(queries: Iterable[str], max_results: int = 10) -> list[Discovery]:
    out = []
    ns = {"a": "http://www.w3.org/2005/Atom"}
    for query in queries:
        url = (
            "https://export.arxiv.org/api/query?search_query=all:"
            + quote_plus(query)
            + f"&start=0&max_results={max_results}&sortBy=relevance"
        )
        try:
            root = ET.fromstring(_get(url))
        except Exception:
            continue
        for entry in root.findall("a:entry", ns):
            title = " ".join((entry.findtext("a:title", default="", namespaces=ns)).split())
            summary = " ".join((entry.findtext("a:summary", default="", namespaces=ns)).split())
            link = entry.findtext("a:id", default="", namespaces=ns)
            out.append(Discovery("arxiv", title, link, summary[:1600], query))
    return out


def dedupe(rows: Iterable[Discovery]) -> list[Discovery]:
    seen = set()
    out = []
    for row in rows:
        key = (row.url or row.title).strip().lower()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(row)
    return out


def default_queries() -> list[str]:
    return [
        "systematic trading strategy momentum backtest",
        "time series momentum trend following",
        "cross sectional momentum trading",
        "short term reversal trading strategy",
        "post earnings announcement drift strategy",
        "calendar seasonality trading strategy",
        "volatility managed portfolio strategy",
        "carry strategy futures currencies",
        "statistical arbitrage pairs trading",
        "crypto systematic trading strategy",
        "algorithmic trading strategy profit factor drawdown",
    ]


def harvest(queries: list[str]) -> list[Discovery]:
    return dedupe(
        crossref_search(queries)
        + openalex_search(queries)
        + arxiv_search(queries)
        + github_search(queries)
        + reddit_search(queries)
    )


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--output", default="continuous_state/free_source_candidates.json")
    ap.add_argument("--query", action="append", default=[])
    args = ap.parse_args()
    queries = args.query or default_queries()
    rows = harvest(queries)
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({
        "policy": "free discovery only; no paid API; not trading evidence",
        "sources": ["crossref", "openalex", "arxiv", "github", "reddit"],
        "query_count": len(queries),
        "candidate_count": len(rows),
        "candidates": [row.to_dict() for row in rows],
    }, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"harvested {len(rows)} free-source hypotheses -> {path}")


if __name__ == "__main__":
    main()
