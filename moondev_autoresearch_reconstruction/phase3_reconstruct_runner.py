"""Conservative Phase-3 rule reconstruction from free-source discoveries.

Consumes the isolated Phase-3 discovery queue. It extracts only rules present
in the discovery text. Missing rules remain missing. Nothing in this module
changes the Phase-1 registry or opens hidden/final OOS data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from hashlib import sha256
from pathlib import Path
import argparse
import json
import os
import re

HERE = Path(__file__).resolve().parent
STATE = HERE / "phase3_state"
QUEUE = STATE / "candidate_queue.json"
RESULTS = STATE / "reconstructions.jsonl"
PROGRESS = STATE / "reconstruction_progress.json"
CURSOR = STATE / "reconstruction_cursor.json"
HYDRATED = STATE / "hydrated_sources.jsonl"
RECONSTRUCTION_VERSION = 2
LANE = "phase3_free_reconstruction"
PROTOCOL = "nested_chronological_v3"
NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b"


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )


def parse_json_object(raw):
    text = (raw or "").strip()
    text = re.sub(r"^```(?:json)?\\s*", "", text)
    text = re.sub(r"\\s*```$", "", text)
    try:
        return json.loads(text)
    except Exception:
        left, right = text.find("{"), text.rfind("}")
        if left >= 0 and right > left:
            return json.loads(text[left:right + 1])
        raise


def prior_results():
    rows = {}
    if not RESULTS.exists():
        return rows
    for line in RESULTS.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        key = (row.get("source_url") or row.get("source_title") or "").strip().lower()
        if key:
            rows[key] = row
    return rows


def prior_hydration():
    rows = {}
    if not HYDRATED.exists():
        return rows
    for line in HYDRATED.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        key = (row.get("source_url") or row.get("source_title") or "").strip().lower()
        if key:
            rows[key] = row
    return rows


def append_result(row):
    STATE.mkdir(parents=True, exist_ok=True)
    with RESULTS.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def rule_hash(spec):
    payload = {
        "markets": sorted(str(x).strip().lower() for x in spec.get("markets", [])),
        "timeframe": str(spec.get("timeframe", "")).strip().lower(),
        "universe": [str(x).strip().lower() for x in spec.get("universe_rules", [])],
        "entry": [str(x).strip().lower() for x in spec.get("entry_rules", [])],
        "exit": [str(x).strip().lower() for x in spec.get("exit_rules", [])],
        "sizing": [str(x).strip().lower() for x in spec.get("sizing_rules", [])],
        "execution": [str(x).strip().lower() for x in spec.get("execution_rules", [])],
        "risk": [str(x).strip().lower() for x in spec.get("risk_rules", [])],
        "parameters": spec.get("parameters", {}),
    }
    return sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()


def reconstruct(row, model, hydrated_text=""):
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        raise RuntimeError("NVIDIA_API_KEY missing")
    from openai import OpenAI

    evidence = "\n".join([
        "SOURCE TYPE: " + str(row.get("source_type") or ""),
        "TITLE: " + str(row.get("title") or ""),
        "URL: " + str(row.get("url") or ""),
        "DISCOVERY QUERY: " + str(row.get("query") or ""),
        "SOURCE SNIPPET/ABSTRACT:",
        str(row.get("snippet") or "")[:14000],
    ])
    if hydrated_text:
        evidence += "\n\nHYDRATED PUBLIC SOURCE TEXT:\n" + str(hydrated_text)[:50000]

    schema = {
        "spec_status": "complete_spec|incomplete_spec|not_a_strategy",
        "title": "",
        "markets": [],
        "timeframe": "unknown",
        "universe_rules": [],
        "entry_rules": [],
        "exit_rules": [],
        "sizing_rules": [],
        "execution_rules": [],
        "risk_rules": [],
        "parameters": {},
        "missing_rules": [],
        "reported_metrics": [],
        "evidence_notes": [],
        "extraction_confidence": 0.0,
    }
    prompt = (
        "Extract a mechanical trading-strategy specification ONLY from the source "
        "text below. Never infer missing thresholds, timing, exits, sizing, stops, "
        "targets, universes, or execution rules. If anything needed for a causal "
        "implementation is absent, set spec_status=incomplete_spec and list it in "
        "missing_rules. Source performance is only a reported claim. Do not optimize "
        "or improve the strategy. Return JSON only with exactly this schema:\n"
        + json.dumps(schema, indent=2)
        + "\n\n" + evidence
    )
    client = OpenAI(base_url=NVIDIA_BASE_URL, api_key=key, timeout=180.0)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {
                "role": "system",
                "content": (
                    "You are a conservative quantitative research archivist. "
                    "Missing rules must stay missing."
                ),
            },
            {"role": "user", "content": prompt},
        ],
        temperature=0.0,
        max_tokens=3000,
    )
    return parse_json_object(response.choices[0].message.content or "")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-candidates", type=int, default=8)
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()
    if not QUEUE.exists():
        raise SystemExit("Phase-3 candidate queue missing")

    queue = load_json(QUEUE).get("candidates", [])
    results = prior_results()
    hydration = prior_hydration()
    cursor = 0
    if CURSOR.exists():
        cursor = int(load_json(CURSOR).get("next_index", 0) or 0)
    processed = 0
    scanned = 0

    while processed < max(1, args.max_candidates) and scanned < len(queue):
        idx = cursor % max(len(queue), 1)
        row = queue[idx]
        cursor = (idx + 1) % max(len(queue), 1)
        scanned += 1
        key = (row.get("url") or row.get("title") or "").strip().lower()
        if not key:
            continue
        prior = results.get(key)
        hydrated = hydration.get(key)
        prior_version = int((prior or {}).get("reconstruction_version", 1) or 1)
        if prior is not None and (
            hydrated is None or prior_version >= RECONSTRUCTION_VERSION
        ):
            continue
        hydrated_text = (
            "" if hydrated is None else str(hydrated.get("hydrated_text") or "")
        )
        try:
            spec = reconstruct(row, args.model, hydrated_text=hydrated_text)
            confidence = float(spec.get("extraction_confidence", 0.0) or 0.0)
            missing = [x for x in spec.get("missing_rules", []) if str(x).strip()]
            entries = [x for x in spec.get("entry_rules", []) if str(x).strip()]
            exits = [x for x in spec.get("exit_rules", []) if str(x).strip()]
            admitted = bool(
                spec.get("spec_status") == "complete_spec"
                and confidence >= 0.70
                and entries
                and exits
                and not missing
            )
            result = {
                "ts": now(),
                "lane": LANE,
                "protocol": PROTOCOL,
                "source_type": row.get("source_type"),
                "source_title": row.get("title"),
                "source_url": row.get("url"),
                "source_query": row.get("query"),
                "model": args.model,
                "reconstruction_version": (
                    RECONSTRUCTION_VERSION if hydrated is not None else 1
                ),
                "hydrated_evidence_used": bool(hydrated_text),
                "spec": spec,
                "rules_hash": rule_hash(spec),
                "reconstruction_admitted": admitted,
                "intake_status": (
                    "ready_for_engine_mapping"
                    if admitted else "incomplete_or_unverified"
                ),
                "phase1_registry_mutated": False,
                "hidden_validation_opened": False,
                "final_oos_opened": False,
            }
        except Exception as exc:
            result = {
                "ts": now(),
                "lane": LANE,
                "protocol": PROTOCOL,
                "source_type": row.get("source_type"),
                "source_title": row.get("title"),
                "source_url": row.get("url"),
                "source_query": row.get("query"),
                "model": args.model,
                "reconstruction_version": (
                    RECONSTRUCTION_VERSION if hydrated is not None else 1
                ),
                "hydrated_evidence_used": bool(hydrated_text),
                "reconstruction_admitted": False,
                "intake_status": "reconstruction_error",
                "error": f"{type(exc).__name__}: {str(exc)[:1400]}",
                "phase1_registry_mutated": False,
                "hidden_validation_opened": False,
                "final_oos_opened": False,
            }
        append_result(result)
        results[key] = result
        processed += 1

    save_json(CURSOR, {
        "next_index": cursor,
        "queue_count": len(queue),
        "updated_at": now(),
    })
    vals = list(results.values())
    hydrated_pending = sum(
        1
        for hkey in hydration
        if hkey not in results
        or int(
            (results.get(hkey) or {}).get("reconstruction_version", 1) or 1
        ) < RECONSTRUCTION_VERSION
    )
    base_complete = len(vals) >= len(queue)
    all_current = base_complete and hydrated_pending == 0
    admitted = [x for x in vals if x.get("reconstruction_admitted")]
    unique_hashes = {
        x.get("rules_hash") for x in admitted if x.get("rules_hash")
    }
    progress = {
        "updated_at": now(),
        "lane": LANE,
        "protocol": PROTOCOL,
        "queue_count": len(queue),
        "processed_count": len(vals),
        "completion_pct": round(100.0 * len(vals) / max(len(queue), 1), 2),
        "admitted_complete_specs": len(admitted),
        "unique_admitted_rule_hashes": len(unique_hashes),
        "errors": sum(
            1 for x in vals if x.get("intake_status") == "reconstruction_error"
        ),
        "all_reconstructed": all_current,
        "base_queue_reconstructed": base_complete,
        "hydrated_source_count": len(hydration),
        "hydrated_reconstruction_pending": hydrated_pending,
        "reconstruction_version": RECONSTRUCTION_VERSION,
        "stage": (
            "reconstruction_complete"
            if all_current else "reconstructing"
        ),
        "next_stage": (
            "development_engine_mapping"
            if all_current else "continue_reconstruction"
        ),
        "phase1_registry_mutated": False,
        "hidden_validation_opened": False,
        "final_oos_opened": False,
    }
    save_json(PROGRESS, progress)
    print(json.dumps(progress, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
