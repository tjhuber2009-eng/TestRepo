"""EvoMind v0.10 research-intelligence bridge for Atlas Forge.

This module intentionally integrates EvoMind *above* the immutable Atlas Forge
backtest/evidence layer. EvoMind may remember concepts, transfer development
insights, and allocate proposal-source attention. It never sees market-data
files, never evaluates its own ideas, and never controls keep/revert,
drawdown/PBO/bootstrap, hidden-validation, or final-OOS gates.

The proposal-source allocator is adapted from EvoMind v0.10.0 meta_search.py
(MIT licensed). Release/source provenance is frozen in vendor/evomind/VENDORED.json.
"""
from __future__ import annotations

import hashlib
import json
import math
import os
import random
import re
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


EVOMIND_VERSION = "0.10.0"
ATLAS_EVOMIND_SCHEMA = 1
EVOMIND_SAFE_DEFAULTS = {
    "adaptive_portfolio": False,
    "compute_cost_penalty": 0.0,
    "islands": 1,
}
EVOMIND_V010_SOURCE_SHA256 = (
    "9ed2ba620ccd4e97db9648244e815fb5e668920d8fd32028006a900f1dcae5e9"
)
EVOMIND_V010_WHEEL_SHA256 = (
    "4d1958b3384b8ed970db3a274b163cc5236839bbb5805fb432f178cb1133796d"
)
EVOMIND_V010_RELEASE_SHA256 = (
    "d336d7a74c5668dc60d55458485420d767d6ab84f0e01271b208d3ba9adcd811"
)
EVOMIND_V010_META_SEARCH_SHA256 = (
    "b0180b371d6247c2d14ee80d5b7c17001e893c1ee0d087011e285b905bf3a8c1"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _finite(value: Any, default: float = 0.0) -> float:
    try:
        x = float(value)
    except (TypeError, ValueError):
        return float(default)
    return x if math.isfinite(x) else float(default)


def _bounded(value: Any, lo: float = 0.0, hi: float = 1.0) -> float:
    return max(lo, min(hi, _finite(value, lo)))


def _json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


class ProposalPortfolioBandit:
    """EvoMind v0.10 proposal-source allocator.

    The evaluator remains authoritative. This controller only decides which
    development/search mechanism gets the next candidate slot and learns from
    Atlas Forge's already-computed outcome credit.
    """

    ARMS = (
        "evolution",
        "synthesis",
        "skill_transfer",
        "immigrant",
        "external_proposal",
    )

    def __init__(self, rng: random.Random) -> None:
        self.rng = rng
        self.counts = {a: 0 for a in self.ARMS}
        self.rewards = {a: 0.0 for a in self.ARMS}
        self.values = {a: 0.5 for a in self.ARMS}
        self.invalid = {a: 0 for a in self.ARMS}
        self.step = 0
        self.last_used = {a: 0 for a in self.ARMS}

    def choose(
        self,
        allowed: tuple[str, ...],
        exploration: float = 0.40,
    ) -> str:
        if (
            not isinstance(allowed, tuple)
            or not allowed
            or len(set(allowed)) != len(allowed)
            or any(a not in self.ARMS for a in allowed)
        ):
            raise ValueError(
                "allowed proposal sources must be a non-empty unique tuple "
                "of known arms"
            )
        if (
            isinstance(exploration, bool)
            or not isinstance(exploration, (int, float))
            or not math.isfinite(float(exploration))
        ):
            raise ValueError("exploration must be finite")
        exploration = max(0.0, min(1.0, float(exploration)))
        self.step += 1
        unseen = [a for a in allowed if self.counts[a] == 0]
        if unseen:
            arm = self.rng.choice(unseen)
            self.last_used[arm] = self.step
            return arm
        if self.rng.random() < 0.035 + 0.10 * exploration:
            arm = self.rng.choice(allowed)
            self.last_used[arm] = self.step
            return arm
        total = max(1, sum(self.counts[a] for a in allowed))
        scored: list[tuple[float, str]] = []
        for arm in allowed:
            n = max(1, self.counts[arm])
            bonus = (
                (0.06 + 0.28 * exploration)
                * math.sqrt(math.log(total + 2) / n)
            )
            stale = min(
                0.10,
                max(0, self.step - self.last_used[arm]) * 0.002,
            )
            invalid_penalty = min(
                0.20,
                self.invalid[arm] / n * 0.20,
            )
            scored.append(
                (
                    self.values[arm]
                    + bonus
                    + stale
                    - invalid_penalty
                    + self.rng.random() * 0.008,
                    arm,
                )
            )
        arm = max(scored)[1]
        self.last_used[arm] = self.step
        return arm

    def observe(
        self,
        arm: str | None,
        reward: float,
        *,
        valid: bool = True,
    ) -> None:
        if arm not in self.counts:
            return
        if (
            isinstance(reward, bool)
            or not isinstance(reward, (int, float))
            or not math.isfinite(float(reward))
        ):
            raise ValueError("proposal-source reward must be finite")
        reward = max(0.0, min(1.0, float(reward)))
        self.counts[arm] += 1
        self.rewards[arm] += reward
        if not valid:
            self.invalid[arm] += 1
        alpha = 0.24
        self.values[arm] = (
            (1.0 - alpha) * self.values[arm] + alpha * reward
        )

    def summary(self) -> dict[str, Any]:
        return {
            "arms": list(self.ARMS),
            "counts": dict(self.counts),
            "rewards": dict(self.rewards),
            "values": dict(self.values),
            "invalid": dict(self.invalid),
            "step": self.step,
            "last_used": dict(self.last_used),
            "rng_state": self.rng.getstate(),
        }

    def restore(self, payload: dict[str, Any]) -> None:
        if (
            not isinstance(payload, dict)
            or tuple(payload.get("arms", ())) != self.ARMS
        ):
            return
        for arm in self.ARMS:
            counts = payload.get("counts")
            rewards = payload.get("rewards")
            values = payload.get("values")
            invalid = payload.get("invalid")
            c = counts.get(arm, 0) if isinstance(counts, dict) else 0
            r = rewards.get(arm, 0.0) if isinstance(rewards, dict) else 0.0
            v = values.get(arm, 0.5) if isinstance(values, dict) else 0.5
            inv = invalid.get(arm, 0) if isinstance(invalid, dict) else 0
            if isinstance(c, int) and not isinstance(c, bool) and c >= 0:
                self.counts[arm] = c
            if (
                isinstance(inv, int)
                and not isinstance(inv, bool)
                and inv >= 0
            ):
                self.invalid[arm] = inv
            if (
                isinstance(r, (int, float))
                and not isinstance(r, bool)
                and math.isfinite(float(r))
                and float(r) >= 0
            ):
                self.rewards[arm] = float(r)
            if (
                isinstance(v, (int, float))
                and not isinstance(v, bool)
                and math.isfinite(float(v))
                and 0 <= float(v) <= 1
            ):
                self.values[arm] = float(v)
        step = payload.get("step", 0)
        if isinstance(step, int) and not isinstance(step, bool) and step >= 0:
            self.step = step
        last = payload.get("last_used")
        if isinstance(last, dict):
            for arm in self.ARMS:
                x = last.get(arm, 0)
                if (
                    isinstance(x, int)
                    and not isinstance(x, bool)
                    and x >= 0
                ):
                    self.last_used[arm] = x
        raw_rng = payload.get("rng_state")
        if raw_rng is not None:
            try:
                def tupleize(value):
                    return (
                        tuple(tupleize(x) for x in value)
                        if isinstance(value, list)
                        else value
                    )
                self.rng.setstate(tupleize(raw_rng))
            except (TypeError, ValueError):
                pass


@dataclass(frozen=True)
class AtlasConcept:
    name: str
    summary: str
    tags: tuple[str, ...]
    domain: str
    evidence_id: str
    score: float
    evidence_count: int


_MODE_INSTRUCTIONS = {
    "evolution": (
        "Evolve the strongest mechanism already present. Make a small "
        "structural improvement, not a parameter-only mutation."
    ),
    "synthesis": (
        "Synthesize two distinct evidence-backed mechanisms into one causal "
        "idea, but still make exactly one conceptual strategy change."
    ),
    "skill_transfer": (
        "Transfer a mechanism that worked in another family/market only when "
        "its causal logic plausibly maps to this instrument. Adapt, do not copy "
        "instrument-specific constants."
    ),
    "immigrant": (
        "Explore a genuinely different causal mechanism from the recent local "
        "lineage. Stay simple and localized."
    ),
    "external_proposal": (
        "Use independent model reasoning to propose a distinct causal market "
        "hypothesis, while respecting all Atlas Forge structural constraints."
    ),
}


_TAG_PATTERNS = (
    ("breakout", r"\bbreakout|donchian|channel break"),
    ("pullback", r"\bpullback|retracement|dip"),
    ("trend", r"\btrend|moving average|\bema\b|\bsma\b"),
    ("momentum", r"momentum|rate of change|\broc\b"),
    ("mean_reversion", r"mean.?reversion|reversal|oversold|overbought"),
    ("volatility", r"volatility|\batr\b|vol target|volatility regime"),
    ("regime", r"regime|state filter|market state"),
    ("rsi", r"\brsi\b"),
    ("adx", r"\badx\b"),
    ("qqe", r"\bqqe\b"),
    ("volume", r"volume|vwap|obv|money flow"),
    ("session", r"session|open|close|intraday time"),
    ("cross_asset", r"cross.?asset|relative strength|rotation"),
    ("exit", r"exit|stop|take profit|trailing"),
    ("entry", r"entry|trigger|confirmation"),
    ("breadth", r"breadth|advance.?decline"),
    ("seasonality", r"seasonal|calendar|day of week|month"),
)


def extract_tags(
    description: str,
    *,
    family: str,
    market: str,
) -> tuple[str, ...]:
    text = " ".join(str(description).lower().split())
    tags = [
        name for name, pattern in _TAG_PATTERNS
        if re.search(pattern, text)
    ]
    family_tag = re.sub(r"[^a-z0-9_]+", "_", family.lower()).strip("_")
    market_tag = re.sub(r"[^a-z0-9_]+", "_", market.lower()).strip("_")
    for tag in (family_tag, market_tag):
        if tag and tag not in tags:
            tags.append(tag)
    if not tags:
        tags = ["structural"]
    return tuple(tags[:8])


def _concept_name(tags: tuple[str, ...]) -> str:
    mechanism = [
        x for x in tags
        if x not in {"crypto", "stock", "forex", "etf", "futures"}
    ]
    return "+".join((mechanism or list(tags))[:3])


def _domain_from_env() -> str:
    market = os.environ.get("AUTORESEARCH_MARKET", "unknown")
    family = os.environ.get("AUTORESEARCH_FAMILY", "unknown")
    profile = os.environ.get("AUTORESEARCH_PROFILE", "unknown")
    return f"{market}:{family}:{profile}"


class EvoMindAtlasBrain:
    """Persistent EvoMind research memory scoped to one Atlas research lane."""

    def __init__(
        self,
        path: str | Path,
        *,
        domain: str,
        track_id: str,
        seed_material: str,
    ) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.domain = str(domain)
        self.track_id = str(track_id)
        seed = int(
            hashlib.sha256(seed_material.encode("utf-8")).hexdigest()[:16],
            16,
        )
        self.bandit = ProposalPortfolioBandit(random.Random(seed))
        self.conn = sqlite3.connect(str(self.path), timeout=30)
        self.conn.row_factory = sqlite3.Row
        self._init_schema()
        self._restore_bandit()

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
                CREATE TABLE IF NOT EXISTS concepts (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    summary TEXT NOT NULL,
                    tags_json TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    evidence_id TEXT NOT NULL,
                    score REAL NOT NULL,
                    evidence_count INTEGER NOT NULL,
                    last_verdict TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(name, domain)
                );
                CREATE TABLE IF NOT EXISTS outcomes (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    ts TEXT NOT NULL,
                    track_id TEXT NOT NULL,
                    domain TEXT NOT NULL,
                    arm TEXT,
                    tag TEXT NOT NULL,
                    reward REAL NOT NULL,
                    valid INTEGER NOT NULL,
                    kept INTEGER NOT NULL,
                    verdict TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS idx_concepts_score
                    ON concepts(score DESC);
                CREATE INDEX IF NOT EXISTS idx_outcomes_domain_tag
                    ON outcomes(domain, tag);
                """
            )
            expected = {
                "schema": str(ATLAS_EVOMIND_SCHEMA),
                "evomind_release": EVOMIND_VERSION,
                "role": "research_intelligence_only",
                "atlas_evaluator_authority": "true",
                "hidden_validation_access": "false",
                "final_oos_access": "false",
            }
            for key, value in expected.items():
                row = self.conn.execute(
                    "SELECT value FROM metadata WHERE key=?",
                    (key,),
                ).fetchone()
                if row is not None and row["value"] != value:
                    raise RuntimeError(
                        f"EvoMind memory metadata mismatch for {key}: "
                        f"{row['value']!r} != {value!r}"
                    )
                self.conn.execute(
                    "INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)",
                    (key, value),
                )

    def _restore_bandit(self) -> None:
        row = self.conn.execute(
            "SELECT value FROM metadata WHERE key='proposal_portfolio_state'"
        ).fetchone()
        if row is None:
            return
        try:
            self.bandit.restore(json.loads(row["value"]))
        except (json.JSONDecodeError, TypeError, ValueError):
            return

    def _persist_bandit(self) -> None:
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)",
                ("proposal_portfolio_state", _json(self.bandit.summary())),
            )

    def _concepts(
        self,
        *,
        same_domain: bool,
        limit: int,
        min_score: float,
    ) -> list[AtlasConcept]:
        op = "=" if same_domain else "<>"
        rows = self.conn.execute(
            f"""
            SELECT * FROM concepts
            WHERE domain {op} ? AND score >= ?
            ORDER BY score DESC, evidence_count DESC, updated_at DESC
            LIMIT ?
            """,
            (self.domain, float(min_score), int(limit)),
        ).fetchall()
        return [
            AtlasConcept(
                name=row["name"],
                summary=row["summary"],
                tags=tuple(json.loads(row["tags_json"])),
                domain=row["domain"],
                evidence_id=row["evidence_id"],
                score=float(row["score"]),
                evidence_count=int(row["evidence_count"]),
            )
            for row in rows
        ]

    def transferable_concepts(self, limit: int = 5) -> list[AtlasConcept]:
        return self._concepts(
            same_domain=False,
            limit=limit,
            min_score=0.50,
        )

    def _avoid_tags(self, limit: int = 5) -> list[tuple[str, int, float]]:
        rows = self.conn.execute(
            """
            SELECT tag, COUNT(*) AS n, AVG(reward) AS avg_reward
            FROM outcomes
            WHERE domain=?
            GROUP BY tag
            HAVING COUNT(*) >= 2 AND AVG(reward) < 0.20
            ORDER BY AVG(reward) ASC, COUNT(*) DESC
            LIMIT ?
            """,
            (self.domain, int(limit)),
        ).fetchall()
        return [
            (str(row["tag"]), int(row["n"]), float(row["avg_reward"]))
            for row in rows
        ]

    def guidance(
        self,
        iteration: int,
        *,
        forced_arm: str | None = None,
    ) -> tuple[str, str]:
        local = self._concepts(
            same_domain=True,
            limit=5,
            min_score=0.30,
        )
        transfer = self.transferable_concepts(limit=5)
        allowed = [
            "evolution",
            "synthesis",
            "immigrant",
            "external_proposal",
        ]
        if transfer:
            allowed.append("skill_transfer")
        if forced_arm is not None:
            if forced_arm not in allowed:
                raise ValueError(
                    f"forced proposal arm {forced_arm!r} is not allowed "
                    f"for this research context"
                )
            arm = forced_arm
        else:
            arm = self.bandit.choose(tuple(allowed), exploration=0.40)
            self._persist_bandit()
        with self.conn:
            self.conn.execute(
                "INSERT OR REPLACE INTO metadata(key,value) VALUES (?,?)",
                (
                    "pending_proposal",
                    _json({
                        "iteration": int(iteration),
                        "track_id": self.track_id,
                        "domain": self.domain,
                        "arm": arm,
                        "ts": utc_now(),
                    }),
                ),
            )

        lines = [
            "## EvoMind v0.10 research guidance",
            f"Proposal mode: {arm}",
            _MODE_INSTRUCTIONS[arm],
            (
                "EvoMind is advisory only. Atlas Forge's immutable evaluator, "
                "risk-control fingerprint, chronological folds, PBO/bootstrap "
                "and keep/revert gates remain authoritative."
            ),
            (
                "Do not increase position sizing, do not access hidden "
                "validation/final OOS, and still make exactly ONE conceptual "
                "change."
            ),
        ]
        if local:
            lines.append("Strong local development concepts:")
            for concept in local:
                lines.append(
                    f"- {concept.name}: {concept.summary} "
                    f"[score={concept.score:.3f}, n={concept.evidence_count}]"
                )
        if transfer:
            lines.append("Transferable concepts from other development domains:")
            for concept in transfer:
                lines.append(
                    f"- {concept.name} from {concept.domain}: "
                    f"{concept.summary} [score={concept.score:.3f}]"
                )
        avoid = self._avoid_tags()
        if avoid:
            lines.append("Repeated weak mechanisms on this domain to avoid:")
            for tag, n, reward in avoid:
                lines.append(
                    f"- {tag}: {n} attempts, mean EvoMind reward {reward:.3f}"
                )
        return "\n".join(lines), arm

    @staticmethod
    def _reward(
        *,
        verdict: str,
        result: dict | None,
        base_score: Any,
        candidate_score: Any,
    ) -> tuple[float, bool]:
        if not isinstance(result, dict):
            return 0.0, False
        if verdict == "KEPT":
            return 1.0, True
        if not bool(result.get("guard_ok")):
            return 0.03, True

        base = _finite(base_score, 0.0)
        candidate = _finite(candidate_score, base)
        scale = max(abs(base), 0.10)
        relative = math.tanh((candidate - base) / scale * 2.0)
        paired = result.get("paired_vs_baseline") or {}
        fold_fraction = _bounded(
            paired.get("improved_fold_fraction"),
            0.0,
            1.0,
        )
        pvalue = _bounded(
            result.get("bootstrap_mean_positive_pvalue"),
            0.0,
            1.0,
        )
        reward = (
            0.32
            + 0.18 * ((relative + 1.0) / 2.0)
            + 0.20 * fold_fraction
            + 0.15 * (1.0 - pvalue)
            + 0.15 * min(_finite(result.get("sharpe"), 0.0) / 2.5, 1.0)
        )
        return min(0.78, max(0.05, reward)), True

    @staticmethod
    def _concept_score(result: dict, reward: float) -> float:
        grade = str(result.get("evidence_grade") or "").upper()
        grade_score = {
            "A": 1.0,
            "B": 0.78,
            "C": 0.55,
            "D": 0.30,
        }.get(grade, 0.40)
        cagr = max(0.0, _finite(result.get("cagr_pct"), 0.0))
        growth = math.tanh(cagr / 50.0)
        sharpe = min(max(_finite(result.get("sharpe"), 0.0), 0.0) / 2.5, 1.0)
        pf = min(max(_finite(result.get("pf"), 0.0), 0.0) / 5.0, 1.0)
        pvalue = _bounded(
            result.get("bootstrap_mean_positive_pvalue"),
            0.0,
            1.0,
        )
        paired = result.get("paired_vs_baseline") or {}
        folds = _bounded(
            paired.get("improved_fold_fraction"),
            0.0,
            1.0,
        )
        return _bounded(
            0.25 * reward
            + 0.20 * growth
            + 0.15 * sharpe
            + 0.10 * pf
            + 0.15 * (1.0 - pvalue)
            + 0.10 * folds
            + 0.05 * grade_score
        )

    def learn(
        self,
        *,
        arm: str | None,
        verdict: str,
        description: str,
        result: dict | None,
        base_score: Any,
        candidate_score: Any,
        evidence_id: str | None,
        family: str,
        market: str,
    ) -> dict[str, Any]:
        reward, valid = self._reward(
            verdict=verdict,
            result=result,
            base_score=base_score,
            candidate_score=candidate_score,
        )
        self.bandit.observe(arm, reward, valid=valid)
        self._persist_bandit()

        tags = extract_tags(
            description,
            family=family,
            market=market,
        )
        with self.conn:
            for tag in tags:
                self.conn.execute(
                    """
                    INSERT INTO outcomes(
                        ts,track_id,domain,arm,tag,reward,valid,kept,verdict
                    ) VALUES (?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        utc_now(),
                        self.track_id,
                        self.domain,
                        arm,
                        tag,
                        float(reward),
                        int(bool(valid)),
                        int(verdict == "KEPT"),
                        verdict,
                    ),
                )

        concept_score = None
        if (
            isinstance(result, dict)
            and bool(result.get("guard_ok"))
            and reward >= 0.25
            and description
        ):
            concept_score = self._concept_score(result, reward)
            name = _concept_name(tags)
            summary = " ".join(str(description).split())[:240]
            evidence = (
                str(evidence_id)
                if evidence_id
                else hashlib.sha256(summary.encode("utf-8")).hexdigest()
            )
            cid = hashlib.sha256(
                f"{self.domain}|{name}".encode("utf-8")
            ).hexdigest()
            with self.conn:
                old = self.conn.execute(
                    "SELECT * FROM concepts WHERE name=? AND domain=?",
                    (name, self.domain),
                ).fetchone()
                if old is None:
                    self.conn.execute(
                        """
                        INSERT INTO concepts(
                            id,name,summary,tags_json,domain,evidence_id,score,
                            evidence_count,last_verdict,updated_at
                        ) VALUES (?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            cid,
                            name,
                            summary,
                            _json(list(tags)),
                            self.domain,
                            evidence,
                            float(concept_score),
                            1,
                            verdict,
                            utc_now(),
                        ),
                    )
                else:
                    old_tags = set(json.loads(old["tags_json"]))
                    merged_tags = sorted(old_tags.union(tags))
                    better = float(concept_score) >= float(old["score"])
                    self.conn.execute(
                        """
                        UPDATE concepts SET
                            summary=?,tags_json=?,evidence_id=?,score=?,
                            evidence_count=evidence_count+1,
                            last_verdict=?,updated_at=?
                        WHERE id=?
                        """,
                        (
                            summary if better else old["summary"],
                            _json(merged_tags),
                            evidence if better else old["evidence_id"],
                            max(float(concept_score), float(old["score"])),
                            verdict,
                            utc_now(),
                            old["id"],
                        ),
                    )

        return {
            "arm": arm,
            "reward": float(reward),
            "valid": bool(valid),
            "concept_score": concept_score,
            "tags": list(tags),
            "bandit": self.bandit.summary(),
        }

    def snapshot(self) -> dict[str, Any]:
        local = self._concepts(
            same_domain=True,
            limit=20,
            min_score=0.0,
        )
        transfer = self.transferable_concepts(limit=20)
        return {
            "version": EVOMIND_VERSION,
            "schema": ATLAS_EVOMIND_SCHEMA,
            "domain": self.domain,
            "track_id": self.track_id,
            "safe_defaults": dict(EVOMIND_SAFE_DEFAULTS),
            "bandit": self.bandit.summary(),
            "local_concepts": [c.__dict__ for c in local],
            "transferable_concepts": [c.__dict__ for c in transfer],
            "avoid_tags": self._avoid_tags(limit=20),
            "hidden_validation_access": False,
            "final_oos_access": False,
        }


def _brain(iteration: int) -> EvoMindAtlasBrain | None:
    if os.environ.get("AUTORESEARCH_EVOMIND_ENABLED", "1") != "1":
        return None
    path = os.environ.get("AUTORESEARCH_EVOMIND_DB")
    if not path:
        return None
    domain = _domain_from_env()
    track_id = os.environ.get("AUTORESEARCH_TRACK_ID", domain)
    seed_material = (
        f"{EVOMIND_VERSION}|{os.environ.get('AUTORESEARCH_PROTOCOL','')}|"
        f"{track_id}|{int(iteration)}"
    )
    return EvoMindAtlasBrain(
        path,
        domain=domain,
        track_id=track_id,
        seed_material=seed_material,
    )


def prompt_guidance(iteration: int) -> tuple[str, str | None]:
    brain = _brain(iteration)
    if brain is None:
        return "", None
    forced_arm = os.environ.get("AUTORESEARCH_FORCED_PROPOSAL_ARM") or None
    if forced_arm and os.environ.get("AUTORESEARCH_CONTROLLED_TOURNAMENT") != "1":
        brain.close()
        raise RuntimeError(
            "forced proposal arms are permitted only in an explicitly "
            "controlled development tournament"
        )
    try:
        return brain.guidance(iteration, forced_arm=forced_arm)
    finally:
        brain.close()


def learn_from_atlas(
    *,
    iteration: int,
    arm: str | None,
    verdict: str,
    description: str,
    result: dict | None,
    base_score: Any,
    candidate_score: Any,
    evidence_id: str | None,
) -> dict[str, Any] | None:
    brain = _brain(iteration)
    if brain is None:
        return None
    try:
        return brain.learn(
            arm=arm,
            verdict=verdict,
            description=description,
            result=result,
            base_score=base_score,
            candidate_score=candidate_score,
            evidence_id=evidence_id,
            family=os.environ.get("AUTORESEARCH_FAMILY", "unknown"),
            market=os.environ.get("AUTORESEARCH_MARKET", "unknown"),
        )
    finally:
        brain.close()
