"""Standardized external-strategy hypothesis intake for AUTORESEARCH v4."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from pathlib import Path
from typing import Iterable, Mapping
import json
import re


@dataclass(frozen=True)
class StrategyHypothesis:
    source_type: str
    source_url: str
    title: str
    markets: tuple[str, ...]
    timeframe: str
    entry_rules: tuple[str, ...]
    exit_rules: tuple[str, ...]
    sizing_rules: tuple[str, ...] = ()
    rationale: str = ""
    reported_return_pct: float | None = None
    reported_period: str | None = None
    reported_max_dd_pct: float | None = None
    evidence_tier: str = "unverified_claim"
    extraction_confidence: float = 0.0
    tags: tuple[str, ...] = ()

    @property
    def rules_hash(self) -> str:
        normalized = {
            "markets": sorted(x.lower().strip() for x in self.markets),
            "timeframe": self.timeframe.lower().strip(),
            "entry": [_norm_rule(x) for x in self.entry_rules],
            "exit": [_norm_rule(x) for x in self.exit_rules],
            "sizing": [_norm_rule(x) for x in self.sizing_rules],
        }
        return sha256(json.dumps(normalized, sort_keys=True).encode()).hexdigest()

    @property
    def id(self) -> str:
        return self.rules_hash[:20]

    def to_dict(self) -> dict:
        x = asdict(self)
        x["id"] = self.id
        x["rules_hash"] = self.rules_hash
        return x


def _norm_rule(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def validate_hypothesis(h: StrategyHypothesis) -> list[str]:
    errors = []
    if not h.source_type or not h.source_url:
        errors.append("source_required")
    if not h.markets:
        errors.append("market_required")
    if not h.timeframe:
        errors.append("timeframe_required")
    if not h.entry_rules:
        errors.append("entry_rules_required")
    if not h.exit_rules:
        errors.append("exit_rules_required")
    if not (0.0 <= h.extraction_confidence <= 1.0):
        errors.append("confidence_out_of_range")
    return errors


def from_mapping(x: Mapping) -> StrategyHypothesis:
    return StrategyHypothesis(
        source_type=str(x.get("source_type", "unknown")),
        source_url=str(x.get("source_url", "")),
        title=str(x.get("title", "untitled")),
        markets=tuple(map(str, x.get("markets", []))),
        timeframe=str(x.get("timeframe", "")),
        entry_rules=tuple(map(str, x.get("entry_rules", []))),
        exit_rules=tuple(map(str, x.get("exit_rules", []))),
        sizing_rules=tuple(map(str, x.get("sizing_rules", []))),
        rationale=str(x.get("rationale", "")),
        reported_return_pct=(None if x.get("reported_return_pct") is None else float(x["reported_return_pct"])),
        reported_period=(None if x.get("reported_period") is None else str(x["reported_period"])),
        reported_max_dd_pct=(None if x.get("reported_max_dd_pct") is None else float(x["reported_max_dd_pct"])),
        evidence_tier=str(x.get("evidence_tier", "unverified_claim")),
        extraction_confidence=float(x.get("extraction_confidence", 0.0)),
        tags=tuple(map(str, x.get("tags", []))),
    )


class HypothesisQueue:
    def __init__(self, path: str | Path):
        self.path = Path(path)

    def read(self) -> list[StrategyHypothesis]:
        if not self.path.exists():
            return []
        payload = json.loads(self.path.read_text(encoding="utf-8"))
        return [from_mapping(x) for x in payload.get("hypotheses", [])]

    def add(self, hypotheses: Iterable[StrategyHypothesis]) -> dict:
        existing = {h.rules_hash: h for h in self.read()}
        added = rejected = 0
        for h in hypotheses:
            if validate_hypothesis(h):
                rejected += 1
                continue
            if h.rules_hash not in existing:
                existing[h.rules_hash] = h
                added += 1
        rows = sorted(existing.values(), key=lambda h: (h.evidence_tier, h.title, h.id))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(
            json.dumps(
                {
                    "protocol": "alpha_generation_v4",
                    "policy": "claims are hypotheses only; exact causal backtest required",
                    "hypotheses": [h.to_dict() for h in rows],
                },
                indent=2,
                sort_keys=True,
            ) + "\n",
            encoding="utf-8",
        )
        return {"added": added, "rejected": rejected, "total": len(rows)}


def prioritize(hypotheses: Iterable[StrategyHypothesis]) -> list[StrategyHypothesis]:
    tier = {
        "peer_reviewed": 5,
        "working_paper": 4,
        "reproducible_code": 3,
        "documented_backtest": 2,
        "unverified_claim": 1,
    }
    def score(h: StrategyHypothesis):
        cap_eff = 0.0
        if h.reported_return_pct is not None:
            cap_eff += min(max(h.reported_return_pct, 0.0), 10_000.0) / 1000.0
        if h.reported_max_dd_pct is not None and h.reported_max_dd_pct != 0:
            cap_eff += min(abs(h.reported_return_pct or 0.0) / abs(h.reported_max_dd_pct), 20.0) / 5.0
        return (
            tier.get(h.evidence_tier, 0),
            h.extraction_confidence,
            cap_eff,
            h.id,
        )
    return sorted(hypotheses, key=score, reverse=True)
