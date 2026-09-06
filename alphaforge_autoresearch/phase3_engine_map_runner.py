"""Phase-3 development engine mapping without inventing strategy rules.

Consumes conservative rule reconstructions and the original free-source queue.
It maps each candidate to an existing research-engine archetype, identifies
missing capabilities/rules, and builds a finite source-hydration queue.
Hidden validation and final OOS remain sealed.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import json
import re

HERE = Path(__file__).resolve().parent
STATE = HERE / "phase3_state"
RECON = STATE / "reconstructions.jsonl"
QUEUE = STATE / "candidate_queue.json"
MAPPING = STATE / "engine_mapping.json"
PROGRESS = STATE / "engine_mapping_progress.json"
HYDRATION_QUEUE = STATE / "hydration_queue.json"
HYDRATED = STATE / "hydrated_sources.jsonl"
PROTOCOL = "nested_chronological_v3"
LANE = "phase3_engine_mapping"
REQUIRED_HYDRATION_VERSION = 2

ARCHETYPES = (
    ("options_volatility", ("option", "variance risk", "volatility risk premium", "straddle", "put-write", "put write")),
    ("pairs_stat_arb", ("pairs trading", "cointegration", "kalman", "hedge ratio", "statistical arbitrage")),
    ("funding_basis", ("funding rate", "perpetual", "basis trade", "carry basis")),
    ("calendar_event", ("turn of month", "seasonality", "calendar", "earnings announcement", "pead")),
    ("cross_asset_rotation", ("dual momentum", "relative momentum", "cross asset", "tactical allocation", "rotation")),
    ("trend_following", ("trend following", "time-series momentum", "time series momentum", "donchian", "turtle")),
    ("breakout", ("breakout", "volatility contraction", "opening range")),
    ("mean_reversion", ("mean reversion", "reversal", "rsi", "oversold", "overbought")),
    ("machine_learning", ("machine learning", "deep learning", "reinforcement learning", "neural network", "classifier")),
)

ENGINE = {
    "cross_asset_rotation": {"engine": "v4_multi_asset_daily", "support": "supported"},
    "trend_following": {"engine": "continuous_daily_or_v4_multi_asset", "support": "supported"},
    "breakout": {"engine": "continuous_daily", "support": "supported"},
    "mean_reversion": {"engine": "continuous_daily", "support": "supported"},
    "calendar_event": {"engine": "event_engine", "support": "partial"},
    "machine_learning": {"engine": "walk_forward_model_engine", "support": "partial"},
    "pairs_stat_arb": {"engine": "synchronized_multi_asset_pairs", "support": "missing"},
    "funding_basis": {"engine": "funding_basis_engine", "support": "missing"},
    "options_volatility": {"engine": "options_surface_engine", "support": "missing"},
    "unclassified": {"engine": "manual_archetype_review", "support": "unknown"},
}

URL_RE = re.compile(r"https?://[^\s<>\"']+", re.I)
GITHUB_RE = re.compile(r"https?://github\.com/[^/\s<>]+/[^/\s<>#?]+", re.I)


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n", encoding="utf-8")


def read_jsonl(path):
    rows = []
    if not Path(path).exists():
        return rows
    for line in Path(path).read_text(encoding="utf-8").splitlines():
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def key_for(row):
    return (row.get("source_url") or row.get("url") or row.get("source_title") or row.get("title") or "").strip().lower()


def classify(text):
    t = (text or "").lower()
    scores = []
    for archetype, needles in ARCHETYPES:
        score = sum(1 for needle in needles if needle in t)
        if score:
            scores.append((score, archetype))
    return max(scores)[1] if scores else "unclassified"


def hydration_is_current(row):
    if not row:
        return False
    return bool(
        row.get("hydration_status") == "hydrated"
        or int(row.get("hydration_version", 1) or 1)
        >= REQUIRED_HYDRATION_VERSION
    )


def register_rule_hash(admitted, rules_hash, seen_rule_hashes):
    """Return duplicate status and register only admitted complete rule sets."""
    duplicate = bool(
        admitted and rules_hash and rules_hash in seen_rule_hashes
    )
    if admitted and rules_hash:
        seen_rule_hashes.add(rules_hash)
    return duplicate


def source_targets(candidate):
    text = "\n".join([
        str(candidate.get("url") or ""),
        str(candidate.get("snippet") or ""),
        str(candidate.get("title") or ""),
    ])
    urls = []
    for match in URL_RE.findall(text):
        cleaned = match.rstrip(".,);]}>")
        if cleaned not in urls:
            urls.append(cleaned)
    repos = []
    for match in GITHUB_RE.findall(text):
        cleaned = match.rstrip(".,);]}>")
        if cleaned not in repos:
            repos.append(cleaned)
    return repos + [u for u in urls if u not in repos]


def main():
    if not RECON.exists():
        raise SystemExit("Phase-3 reconstruction state missing")
    recon = read_jsonl(RECON)
    original = load_json(QUEUE).get("candidates", []) if QUEUE.exists() else []
    original_by_key = {key_for(x): x for x in original if key_for(x)}
    hydrated = {}
    for hrow in read_jsonl(HYDRATED):
        hkey = key_for(hrow)
        if hkey:
            hydrated[hkey] = hrow

    latest = {}
    for row in recon:
        k = key_for(row)
        if k:
            latest[k] = row

    mappings = []
    hydrate = []
    seen_rule_hashes = set()
    for k, row in latest.items():
        spec = row.get("spec") or {}
        orig = original_by_key.get(k, {})
        text = "\n".join([
            str(row.get("source_title") or ""),
            str(row.get("source_query") or ""),
            str(orig.get("snippet") or ""),
            json.dumps(spec, ensure_ascii=False),
        ])
        archetype = classify(text)
        engine = ENGINE[archetype]
        confidence = float(spec.get("extraction_confidence", 0.0) or 0.0)
        missing = [str(x) for x in spec.get("missing_rules", []) if str(x).strip()]
        admitted = bool(row.get("reconstruction_admitted"))
        rules_hash = row.get("rules_hash")
        # Deduplicate only complete/admitted mechanical specifications.
        # Incomplete specs commonly hash to the same sparse structure and must
        # still receive independent source-hydration attempts.
        duplicate_rules = register_rule_hash(
            admitted, rules_hash, seen_rule_hashes
        )

        if admitted and engine["support"] == "supported" and not duplicate_rules:
            status = "ready_for_development_adapter"
        elif admitted and engine["support"] in {"partial", "missing"}:
            status = "engine_capability_required"
        elif duplicate_rules:
            status = "duplicate_rule_set"
        else:
            hrow = hydrated.get(k)
            hydration_current = hydration_is_current(hrow)
            status = (
                "incomplete_after_hydration"
                if hydration_current
                else "source_hydration_required"
            )

        mapped = {
            "source_title": row.get("source_title"),
            "source_url": row.get("source_url"),
            "source_type": row.get("source_type"),
            "archetype": archetype,
            "engine": engine["engine"],
            "engine_support": engine["support"],
            "mapping_status": status,
            "reconstruction_admitted": admitted,
            "extraction_confidence": confidence,
            "missing_rules": missing,
            "rules_hash": rules_hash,
            "duplicate_rules": duplicate_rules,
            "hydration_status": (
                None if k not in hydrated else hydrated[k].get("hydration_status")
            ),
            "hydration_version": (
                None if k not in hydrated
                else int(hydrated[k].get("hydration_version", 1) or 1)
            ),
            "hidden_validation_opened": False,
            "final_oos_opened": False,
        }
        mappings.append(mapped)

        if status == "source_hydration_required":
            targets = source_targets(orig)
            hydrate.append({
                "source_title": row.get("source_title"),
                "source_url": row.get("source_url"),
                "source_type": row.get("source_type"),
                "source_query": row.get("source_query"),
                "archetype": archetype,
                "missing_rules": missing,
                "extraction_confidence": confidence,
                "priority_score": round(
                    float(orig.get("quality_score", 0) or 0)
                    + confidence * 100.0
                    + (50.0 if targets else 0.0),
                    6,
                ),
                "fetch_targets": targets[:8],
                "original_snippet": str(orig.get("snippet") or "")[:14000],
                "hidden_validation_opened": False,
                "final_oos_opened": False,
            })

    mappings.sort(key=lambda x: (x["mapping_status"] == "ready_for_development_adapter", x["extraction_confidence"]), reverse=True)
    hydrate.sort(key=lambda x: x["priority_score"], reverse=True)

    ready = [x for x in mappings if x["mapping_status"] == "ready_for_development_adapter"]
    cap = [x for x in mappings if x["mapping_status"] == "engine_capability_required"]
    exhausted = [
        x for x in mappings
        if x["mapping_status"] == "incomplete_after_hydration"
    ]
    payload = {
        "updated_at": now(),
        "lane": LANE,
        "protocol": PROTOCOL,
        "stage": "engine_mapping_complete",
        "candidate_count": len(mappings),
        "ready_for_development_adapter_count": len(ready),
        "engine_capability_required_count": len(cap),
        "source_hydration_required_count": len(hydrate),
        "incomplete_after_hydration_count": len(exhausted),
        "required_hydration_version": REQUIRED_HYDRATION_VERSION,
        "phase1_registry_mutated": False,
        "hidden_validation_opened": False,
        "final_oos_opened": False,
        "mappings": mappings,
    }
    save_json(MAPPING, payload)
    save_json(HYDRATION_QUEUE, {
        "updated_at": now(),
        "lane": LANE,
        "count": len(hydrate),
        "policy": "hydrate public source evidence only; never infer missing trading rules",
        "candidates": hydrate,
    })
    progress = {
        "updated_at": now(),
        "lane": LANE,
        "protocol": PROTOCOL,
        "stage": "engine_mapping_complete",
        "mapped_count": len(mappings),
        "ready_for_development_adapter_count": len(ready),
        "engine_capability_required_count": len(cap),
        "hydration_queue_count": len(hydrate),
        "incomplete_after_hydration_count": len(exhausted),
        "required_hydration_version": REQUIRED_HYDRATION_VERSION,
        "next_stage": (
            "source_hydration"
            if hydrate
            else (
                "development_adapter"
                if ready
                else (
                    "engine_capability_build"
                    if cap
                    else "research_exhausted_no_complete_specs"
                )
            )
        ),
        "phase1_registry_mutated": False,
        "hidden_validation_opened": False,
        "final_oos_opened": False,
    }
    save_json(PROGRESS, progress)
    print(json.dumps(progress, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
