"""YouTube Intelligence advisory bridge for Atlas Forge AUTORESEARCH.

YouTube Intelligence supplies external strategy hypotheses. EvoMind decides when
external proposals deserve research attention. Atlas Forge remains the sole
authority for backtests, keep/revert, risk, PBO/bootstrap, hidden validation,
and final OOS.

The bridge is deliberately offline: it consumes a structured JSON/JSONL export
produced by the existing YouTube Intelligence product. It never calls YouTube,
never downloads market data, and never exposes claimed performance as evidence.

Chronology rule:
An external idea may enter a research track only when the video's published_at
is on or before that track's adaptive development cutoff. Later videos are
stored as quarantined discoveries for a future chronology-safe research lane.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import sqlite3
import re
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any


YOUTUBE_INTELLIGENCE_VERSION = "3.1.0"
ATLAS_YOUTUBE_SCHEMA = 1
YOUTUBE_INTELLIGENCE_SOURCE_BRANCH = "yke-v3.1-windows-build"
YOUTUBE_INTELLIGENCE_SOURCE_COMMIT = (
    "1f7673b00994fb321fda0b7077c5405529441691"
)

# Reproduction routing is deliberately stricter than broad market compatibility.
# Explicit creator instruments must match the Atlas test instrument. Generic
# market labels (for example "Forex") may match any instrument in that class.
_INDEX_INSTRUMENT_GROUPS = {
    "sp500": (
        ("spx500", "s&p 500", "s&p500", "sp500", "s&p 500 index", "spx"),
        {"SPY", "ES", "ES1", "SPX", "SPX500", "US500"},
    ),
    "nasdaq100": (
        ("nas100", "nasdaq 100", "nasdaq-100", "ndx", "us100"),
        {"QQQ", "NQ", "NQ1", "NDX", "NAS100", "US100"},
    ),
    "dow30": (
        ("us30", "dow jones", "dow 30", "djia", "dow"),
        {"DIA", "YM", "YM1", "DJI", "DJIA", "US30"},
    ),
    "russell2000": (
        ("russell 2000", "russell2000", "us2000", "rut"),
        {"IWM", "RTY", "RTY1", "RUT", "US2000"},
    ),
    "gold": (
        ("xauusd", "xau/usd", "gold"),
        {"XAUUSD", "GC", "GCF", "GLD"},
    ),
}
_MAJOR_CCY = "EUR|GBP|USD|JPY|AUD|NZD|CAD|CHF"
_FX_PAIR_RE = re.compile(
    rf"\b({_MAJOR_CCY})\s*[/_\-]?\s*({_MAJOR_CCY})\b",
    re.IGNORECASE,
)


def _norm_symbol(value: Any) -> str:
    return re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()


def _canonical_timeframe(value: Any) -> str:
    raw = str(value or "").strip().lower()
    compact = re.sub(r"[\s_\-]+", "", raw)
    aliases = {
        "daily": "1D",
        "day": "1D",
        "1d": "1D",
        "d1": "1D",
        "4hour": "4H",
        "4hours": "4H",
        "4h": "4H",
        "h4": "4H",
        "1hour": "1H",
        "1hours": "1H",
        "1h": "1H",
        "h1": "1H",
        "30minute": "30M",
        "30minutes": "30M",
        "30min": "30M",
        "30m": "30M",
        "15minute": "15M",
        "15minutes": "15M",
        "15min": "15M",
        "15m": "15M",
        "5minute": "5M",
        "5minutes": "5M",
        "5min": "5M",
        "5m": "5M",
    }
    return aliases.get(compact, str(value or "").strip().upper())


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _text(value: Any, limit: int) -> str:
    return " ".join(str(value or "").split())[:limit]


def _float01(value: Any, default: float = 0.5) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(x):
        return default
    return max(0.0, min(1.0, x))


def _date(value: Any) -> date | None:
    text = str(value or "").strip()
    if not text:
        return None
    try:
        return date.fromisoformat(text[:10])
    except ValueError:
        return None


def _items(value: Any, *, limit: int, item_limit: int) -> tuple[str, ...]:
    if isinstance(value, str):
        value = [value]
    if not isinstance(value, list):
        return ()
    out: list[str] = []
    for item in value:
        x = _text(item, item_limit)
        if x and x.lower() not in {y.lower() for y in out}:
            out.append(x)
        if len(out) >= limit:
            break
    return tuple(out)


def _idea_id(row: dict[str, Any]) -> str:
    supplied = _text(row.get("idea_id"), 120)
    if supplied:
        return supplied
    material = "|".join([
        _text(row.get("video_id"), 64),
        _text(row.get("published_at"), 32),
        _text(row.get("title"), 180),
        _text(row.get("summary"), 500),
        json.dumps(row.get("strategy_rules") or [], sort_keys=True),
    ])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:24]


@dataclass(frozen=True)
class YouTubeIdea:
    idea_id: str
    video_id: str
    channel_id: str
    channel_title: str
    published_at: str
    title: str
    summary: str
    rules: tuple[str, ...]
    markets: tuple[str, ...]
    timeframes: tuple[str, ...]
    tags: tuple[str, ...]
    source_kind: str
    specification_quality: float
    eligible: bool


class YouTubeAtlasBridge:
    """Persistent, chronology-safe YouTube hypothesis queue for one Atlas lane."""

    def __init__(
        self,
        db_path: str | Path,
        *,
        feed_path: str | Path | None,
        track_id: str,
        domain: str,
        published_cutoff: str,
        symbol: str | None = None,
        timeframe: str | None = None,
        routing_stage: str = "reproduction",
        multi_timeframe_capable: bool = False,
    ) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.feed_path = Path(feed_path) if feed_path else None
        self.track_id = str(track_id)
        self.domain = str(domain)
        inferred_symbol = ""
        parts = self.track_id.split("__")
        if len(parts) >= 2:
            inferred_symbol = parts[-2]
        self.symbol = _norm_symbol(symbol or inferred_symbol)
        self.timeframe = _canonical_timeframe(timeframe or "1D")
        self.routing_stage = str(routing_stage or "reproduction").strip().lower()
        if self.routing_stage not in {"reproduction", "transfer"}:
            raise ValueError("YouTube routing_stage must be reproduction or transfer")
        self.multi_timeframe_capable = bool(multi_timeframe_capable)
        cutoff = _date(published_cutoff)
        if cutoff is None:
            raise ValueError("YouTube Intelligence published cutoff must be YYYY-MM-DD")
        self.cutoff = cutoff
        self.conn = sqlite3.connect(str(self.db_path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()

    def close(self) -> None:
        self.conn.close()

    def _init_schema(self) -> None:
        with self.conn:
            self.conn.executescript(
                """
                PRAGMA journal_mode=WAL;
                CREATE TABLE IF NOT EXISTS metadata (
                    key TEXT PRIMARY KEY,
                    value TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS ideas (
                    idea_id TEXT PRIMARY KEY,
                    video_id TEXT NOT NULL,
                    channel_id TEXT NOT NULL,
                    channel_title TEXT NOT NULL,
                    published_at TEXT NOT NULL,
                    title TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    rules_json TEXT NOT NULL,
                    markets_json TEXT NOT NULL,
                    timeframes_json TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    source_kind TEXT NOT NULL,
                    specification_quality REAL NOT NULL,
                    claims_json TEXT NOT NULL,
                    eligible INTEGER NOT NULL,
                    quarantined_reason TEXT NOT NULL,
                    attempts INTEGER NOT NULL DEFAULT 0,
                    keepers INTEGER NOT NULL DEFAULT 0,
                    updated_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    idea_id TEXT NOT NULL,
                    track_id TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    verdict TEXT NOT NULL,
                    kept INTEGER NOT NULL,
                    guard_ok INTEGER NOT NULL,
                    delta_k REAL,
                    FOREIGN KEY(idea_id) REFERENCES ideas(idea_id)
                );
                CREATE INDEX IF NOT EXISTS idx_youtube_ideas_eligible
                    ON ideas(eligible, specification_quality DESC, attempts ASC);
                CREATE INDEX IF NOT EXISTS idx_youtube_outcomes_track
                    ON outcomes(track_id, idea_id);
                """
            )
            expected = {
                "schema": str(ATLAS_YOUTUBE_SCHEMA),
                "youtube_intelligence_version": YOUTUBE_INTELLIGENCE_VERSION,
                "source_branch": YOUTUBE_INTELLIGENCE_SOURCE_BRANCH,
                "source_commit": YOUTUBE_INTELLIGENCE_SOURCE_COMMIT,
                "role": "external_hypothesis_source_only",
                "atlas_evaluator_authority": "true",
                "claimed_performance_is_evidence": "false",
                "hidden_validation_access": "false",
                "final_oos_access": "false",
            }
            for key, value in expected.items():
                row = self.conn.execute(
                    "SELECT value FROM metadata WHERE key=?", (key,)
                ).fetchone()
                if row is not None and row["value"] != value:
                    raise RuntimeError(
                        f"YouTube Intelligence metadata mismatch for {key}: "
                        f"{row['value']!r} != {value!r}"
                    )
                self.conn.execute(
                    "INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)",
                    (key, value),
                )

    def _load_feed(self) -> list[dict[str, Any]]:
        if self.feed_path is None or not self.feed_path.exists():
            return []
        raw = self.feed_path.read_text(encoding="utf-8").strip()
        if not raw:
            return []
        if raw.startswith("["):
            payload = json.loads(raw)
            return [x for x in payload if isinstance(x, dict)]
        rows = []
        for line_no, line in enumerate(raw.splitlines(), 1):
            text = line.strip()
            if not text:
                continue
            row = json.loads(text)
            if not isinstance(row, dict):
                raise ValueError(
                    f"YouTube Intelligence feed line {line_no} is not an object"
                )
            rows.append(row)
        return rows

    def ingest_feed(self) -> dict[str, int]:
        rows = self._load_feed()
        imported = quarantined = 0
        with self.conn:
            for row in rows:
                published = _date(row.get("published_at"))
                eligible = published is not None and published <= self.cutoff
                reason = ""
                if published is None:
                    reason = "missing_or_invalid_published_at"
                elif published > self.cutoff:
                    reason = (
                        f"published_after_adaptive_cutoff:{self.cutoff.isoformat()}"
                    )
                rules = _items(
                    row.get("strategy_rules") or row.get("rules"),
                    limit=12,
                    item_limit=360,
                )
                summary = _text(
                    row.get("summary")
                    or row.get("strategy_summary")
                    or row.get("recommendation"),
                    1600,
                )
                # A video without an actionable hypothesis is kept in the source
                # archive but cannot consume Atlas Forge research compute.
                if not rules and not summary:
                    eligible = False
                    reason = reason or "no_actionable_strategy_hypothesis"
                idea_id = _idea_id(row)
                values = (
                    idea_id,
                    _text(row.get("video_id"), 64),
                    _text(row.get("channel_id"), 120),
                    _text(row.get("channel_title"), 180),
                    str(row.get("published_at") or "")[:32],
                    _text(row.get("title"), 240),
                    summary,
                    json.dumps(rules, ensure_ascii=False),
                    json.dumps(
                        _items(row.get("markets"), limit=12, item_limit=40),
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        _items(row.get("timeframes"), limit=12, item_limit=40),
                        ensure_ascii=False,
                    ),
                    json.dumps(
                        _items(row.get("tags"), limit=16, item_limit=48),
                        ensure_ascii=False,
                    ),
                    _text(row.get("source_kind") or "transcript_analysis", 80),
                    _float01(row.get("specification_quality"), 0.5),
                    json.dumps(
                        row.get("claimed_metrics") or {},
                        sort_keys=True,
                        ensure_ascii=False,
                    )[:4000],
                    int(eligible),
                    reason,
                    utc_now(),
                )
                self.conn.execute(
                    """
                    INSERT INTO ideas(
                        idea_id,video_id,channel_id,channel_title,published_at,
                        title,summary,rules_json,markets_json,timeframes_json,
                        tags_json,source_kind,specification_quality,claims_json,
                        eligible,quarantined_reason,updated_at
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    ON CONFLICT(idea_id) DO UPDATE SET
                        video_id=excluded.video_id,
                        channel_id=excluded.channel_id,
                        channel_title=excluded.channel_title,
                        published_at=excluded.published_at,
                        title=excluded.title,
                        summary=excluded.summary,
                        rules_json=excluded.rules_json,
                        markets_json=excluded.markets_json,
                        timeframes_json=excluded.timeframes_json,
                        tags_json=excluded.tags_json,
                        source_kind=excluded.source_kind,
                        specification_quality=excluded.specification_quality,
                        claims_json=excluded.claims_json,
                        eligible=excluded.eligible,
                        quarantined_reason=excluded.quarantined_reason,
                        updated_at=excluded.updated_at
                    """,
                    values,
                )
                imported += 1
                quarantined += int(not eligible)
            self.conn.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)",
                ("last_feed_ingest", utc_now()),
            )
            self.conn.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)",
                ("published_cutoff", self.cutoff.isoformat()),
            )
        return {"imported": imported, "quarantined": quarantined}

    def _broad_market_compatible(self, markets: tuple[str, ...]) -> bool:
        if not markets:
            return True
        market = self.domain.split(":", 1)[0].lower()
        aliases = {
            "crypto": {"crypto", "bitcoin", "btc", "ethereum", "eth"},
            "stock": {
                "stock", "stocks", "equity", "equities", "etf",
                "index", "indices",
            },
            "forex": {"forex", "fx", "currency", "currencies"},
            "futures_proxy": {
                "futures", "index futures", "commodities", "commodity",
                "index", "indices",
            },
        }
        allowed = aliases.get(market, {market})
        normalized = [str(x).strip().lower() for x in markets if str(x).strip()]
        for value in normalized:
            for alias in allowed:
                if value == alias:
                    return True
                if re.search(rf"\b{re.escape(alias)}\b", value):
                    return True
        return False

    def _explicit_instruments(self, markets: tuple[str, ...]) -> set[str]:
        explicit: set[str] = set()
        for raw in markets:
            value = str(raw or "").strip().lower()
            if not value:
                continue
            for match in _FX_PAIR_RE.finditer(value):
                explicit.add(
                    _norm_symbol(match.group(1) + match.group(2))
                )
            for _, (tokens, symbols) in _INDEX_INSTRUMENT_GROUPS.items():
                if any(token in value for token in tokens):
                    explicit.update(symbols)
        return explicit

    def _market_compatible(self, markets: tuple[str, ...]) -> bool:
        if not self._broad_market_compatible(markets):
            return False
        explicit = self._explicit_instruments(markets)
        if self.routing_stage == "reproduction" and explicit:
            return bool(self.symbol and self.symbol in explicit)
        return True

    def _timeframe_compatible(self, timeframes: tuple[str, ...]) -> bool:
        if not timeframes:
            return True
        wanted = {
            _canonical_timeframe(value)
            for value in timeframes
            if str(value or "").strip()
        }
        if self.timeframe not in wanted:
            return False
        # A single-bar-series Atlas run must not pretend to reproduce a
        # strategy that explicitly specifies multiple resolutions.
        if len(wanted) > 1 and not self.multi_timeframe_capable:
            return False
        return True

    def choose(self, iteration: int, evomind_arm: str | None) -> YouTubeIdea | None:
        # EvoMind owns proposal-source allocation. YouTube Intelligence supplies
        # a candidate only when EvoMind explicitly requests an external proposal.
        if evomind_arm != "external_proposal":
            return None
        attempted = {
            row["idea_id"]
            for row in self.conn.execute(
                "SELECT idea_id FROM outcomes WHERE track_id=?", (self.track_id,)
            ).fetchall()
        }
        rows = self.conn.execute(
            """
            SELECT * FROM ideas
            WHERE eligible=1 AND substr(published_at,1,10) <= ?
            ORDER BY specification_quality DESC, attempts ASC, keepers DESC,
                     published_at ASC, idea_id ASC
            """,
            (self.cutoff.isoformat(),),
        ).fetchall()
        for row in rows:
            if row["idea_id"] in attempted:
                continue
            markets = tuple(json.loads(row["markets_json"]))
            timeframes = tuple(json.loads(row["timeframes_json"]))
            if not self._market_compatible(markets):
                continue
            if not self._timeframe_compatible(timeframes):
                continue
            idea = YouTubeIdea(
                idea_id=row["idea_id"],
                video_id=row["video_id"],
                channel_id=row["channel_id"],
                channel_title=row["channel_title"],
                published_at=row["published_at"],
                title=row["title"],
                summary=row["summary"],
                rules=tuple(json.loads(row["rules_json"])),
                markets=markets,
                timeframes=timeframes,
                tags=tuple(json.loads(row["tags_json"])),
                source_kind=row["source_kind"],
                specification_quality=float(row["specification_quality"]),
                eligible=bool(row["eligible"]),
            )
            with self.conn:
                self.conn.execute(
                    "INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)",
                    (
                        "pending_youtube_idea",
                        json.dumps(
                            {
                                "iteration": int(iteration),
                                "track_id": self.track_id,
                                "idea_id": idea.idea_id,
                                "routing_stage": self.routing_stage,
                                "symbol": self.symbol,
                                "timeframe": self.timeframe,
                                "ts": utc_now(),
                            },
                            sort_keys=True,
                        ),
                    ),
                )
            return idea
        return None

    def guidance(
        self, iteration: int, evomind_arm: str | None
    ) -> tuple[str, str | None]:
        self.ingest_feed()
        idea = self.choose(iteration, evomind_arm)
        if idea is None:
            return "", None
        lines = [
            "## YouTube Intelligence external hypothesis",
            (
                "This is an UNVERIFIED idea source, not performance evidence. "
                "Atlas Forge must independently test it."
            ),
            f"Idea id: {idea.idea_id}",
            f"Published: {idea.published_at}",
            f"Source type: {idea.source_kind}",
            (
                f"Atlas route: {self.routing_stage} on "
                f"{self.symbol or 'unspecified'} @ {self.timeframe}"
            ),
        ]
        if idea.channel_title:
            lines.append(f"Channel: {idea.channel_title}")
        if idea.title:
            lines.append(f"Video: {idea.title}")
        if idea.summary:
            lines.append(f"Hypothesis summary: {idea.summary}")
        if idea.rules:
            lines.append("Extracted strategy rules:")
            lines.extend(f"- {x}" for x in idea.rules)
        if idea.markets:
            lines.append("Markets mentioned: " + ", ".join(idea.markets))
        if idea.timeframes:
            lines.append("Timeframes mentioned: " + ", ".join(idea.timeframes))
        lines.extend([
            (
                "Do not copy reported win rates, returns, profit factor, or other "
                "creator claims into the evaluation. They are intentionally "
                "withheld from this prompt."
            ),
            (
                "Translate the hypothesis into at most ONE causal conceptual "
                "change that obeys all Atlas Forge safety and risk constraints."
            ),
        ])
        return "\n".join(lines), idea.idea_id

    def record_outcome(
        self,
        *,
        idea_id: str | None,
        verdict: str,
        result: dict[str, Any] | None,
        base_score: Any,
        candidate_score: Any,
    ) -> dict[str, Any] | None:
        if not idea_id:
            return None
        row = self.conn.execute(
            "SELECT idea_id FROM ideas WHERE idea_id=?", (idea_id,)
        ).fetchone()
        if row is None:
            return None
        kept = verdict == "KEPT"
        guard_ok = bool((result or {}).get("guard_ok"))
        try:
            delta = float(candidate_score) - float(base_score)
            if not math.isfinite(delta):
                delta = None
        except (TypeError, ValueError):
            delta = None
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO outcomes(
                    ts,idea_id,track_id,domain,verdict,kept,guard_ok,delta_k
                ) VALUES (?,?,?,?,?,?,?,?)
                """,
                (
                    utc_now(), idea_id, self.track_id, self.domain, verdict,
                    int(kept), int(guard_ok), delta,
                ),
            )
            self.conn.execute(
                """
                UPDATE ideas
                SET attempts=attempts+1, keepers=keepers+?, updated_at=?
                WHERE idea_id=?
                """,
                (int(kept), utc_now(), idea_id),
            )
        return {
            "idea_id": idea_id,
            "kept": kept,
            "guard_ok": guard_ok,
            "delta_k": delta,
        }

    def snapshot(self) -> dict[str, Any]:
        def count(where: str = "1=1") -> int:
            return int(
                self.conn.execute(
                    f"SELECT COUNT(*) FROM ideas WHERE {where}"
                ).fetchone()[0]
            )
        return {
            "version": YOUTUBE_INTELLIGENCE_VERSION,
            "source_commit": YOUTUBE_INTELLIGENCE_SOURCE_COMMIT,
            "ideas": count(),
            "eligible": count("eligible=1"),
            "quarantined": count("eligible=0"),
            "tested_outcomes": int(
                self.conn.execute("SELECT COUNT(*) FROM outcomes").fetchone()[0]
            ),
            "keeper_outcomes": int(
                self.conn.execute(
                    "SELECT COUNT(*) FROM outcomes WHERE kept=1"
                ).fetchone()[0]
            ),
            "published_cutoff": self.cutoff.isoformat(),
            "routing_stage": self.routing_stage,
            "symbol": self.symbol,
            "timeframe": self.timeframe,
            "multi_timeframe_capable": self.multi_timeframe_capable,
            "hidden_validation_access": False,
            "final_oos_access": False,
        }


def _brain() -> YouTubeAtlasBridge | None:
    if os.environ.get("AUTORESEARCH_YOUTUBE_INTELLIGENCE_ENABLED", "0") != "1":
        return None
    db = os.environ.get("AUTORESEARCH_YOUTUBE_INTELLIGENCE_DB")
    feed = os.environ.get("AUTORESEARCH_YOUTUBE_INTELLIGENCE_FEED")
    cutoff = os.environ.get("AUTORESEARCH_YOUTUBE_PUBLISHED_CUTOFF")
    if not db or not cutoff:
        return None
    market = os.environ.get("AUTORESEARCH_MARKET", "unknown")
    family = os.environ.get("AUTORESEARCH_FAMILY", "unknown")
    profile = os.environ.get("AUTORESEARCH_PROFILE", "unknown")
    return YouTubeAtlasBridge(
        db,
        feed_path=feed,
        track_id=os.environ.get("AUTORESEARCH_TRACK_ID", "unknown"),
        domain=f"{market}:{family}:{profile}",
        published_cutoff=cutoff,
        symbol=os.environ.get("AUTORESEARCH_SYMBOL"),
        timeframe=os.environ.get("AUTORESEARCH_TIMEFRAME", "1D"),
        routing_stage=os.environ.get(
            "AUTORESEARCH_YOUTUBE_ROUTING_STAGE", "reproduction"
        ),
        multi_timeframe_capable=(
            os.environ.get(
                "AUTORESEARCH_YOUTUBE_MULTI_TIMEFRAME_CAPABLE", "0"
            ) == "1"
        ),
    )


def prompt_guidance(
    iteration: int, evomind_arm: str | None
) -> tuple[str, str | None]:
    brain = _brain()
    if brain is None:
        return "", None
    try:
        return brain.guidance(iteration, evomind_arm)
    finally:
        brain.close()


def learn_from_atlas(
    *,
    idea_id: str | None,
    verdict: str,
    result: dict[str, Any] | None,
    base_score: Any,
    candidate_score: Any,
) -> dict[str, Any] | None:
    brain = _brain()
    if brain is None:
        return None
    try:
        brain.ingest_feed()
        return brain.record_outcome(
            idea_id=idea_id,
            verdict=verdict,
            result=result,
            base_score=base_score,
            candidate_score=candidate_score,
        )
    finally:
        brain.close()
