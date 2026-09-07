"""Phase-2 development-only survivor follow-up.

Baseline screening evaluates a broad prior-work universe once. This stage freezes
that baseline survivor set, reruns only the exact frozen strategies to retain
their fixed CSCV slices and bootstrap evidence, and computes cohort-level
selection-overfit diagnostics. It never mutates parameters, opens hidden
validation, or changes the fixed Phase-1 514-track registry.
"""
from __future__ import annotations

import argparse
import hashlib
import itertools
import json
import math
from pathlib import Path

import numpy as np

import phase2_prior_runner as p2

HERE = Path(__file__).resolve().parent
STATE = HERE / "phase2_state"
BASELINE_PROGRESS = STATE / "progress.json"
BASELINE_RESULTS = STATE / "results.jsonl"
SELECTION = STATE / "followup_selection.json"
RESULTS = STATE / "followup_results.jsonl"
PROGRESS = STATE / "followup_progress.json"
PROMOTION = STATE / "promotion_queue.json"
PROMOTION_SOURCES = STATE / "promotion_sources"
CURSOR = STATE / "followup_cursor.json"

PROTOCOL = p2.PROTOCOL
LANE = "phase2_prior_work_followup"
PBO_LIMIT = 0.55
MIN_COHORT = 5
FDR_REPORT_LEVEL = 0.10
FOLLOWUP_VERSION = 2


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path, obj):
    p2.save_json(path, obj)


def json_hash(obj):
    raw = json.dumps(obj, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(raw).hexdigest()


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for block in iter(lambda: fh.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def read_jsonl(path):
    rows = []
    p = Path(path)
    if not p.exists():
        return rows
    for line in p.read_text(encoding="utf-8").splitlines():
        try:
            row = json.loads(line)
        except Exception:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


def append_jsonl(path, row):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")


def baseline_survivors():
    if not BASELINE_PROGRESS.exists():
        raise RuntimeError("Phase-2 baseline progress is missing")
    progress = load_json(BASELINE_PROGRESS)
    if progress.get("protocol") != PROTOCOL:
        raise RuntimeError("Phase-2 baseline protocol mismatch")
    if progress.get("all_tracks_screened") is not True:
        raise RuntimeError("Phase-2 follow-up requires completed baseline screening")
    rows = p2.read_results()
    survivors = [
        row for row in rows.values()
        if row.get("status") == "ok"
        and row.get("guard_ok") is True
        and row.get("lookahead_pass") is True
    ]
    survivors.sort(key=lambda x: x["track_id"])
    return survivors


def baseline_fingerprint(row):
    keys = (
        "track_id", "family", "target", "profile", "score", "cagr_pct",
        "return_pct", "sharpe", "pf", "max_dd_pct", "trades",
        "evidence_grade", "stress_return_pct", "extreme_stress_return_pct",
    )
    return {key: row.get(key) for key in keys}


def freeze_selection():
    survivors = baseline_survivors()
    ids = [x["track_id"] for x in survivors]
    fingerprints = {
        row["track_id"]: baseline_fingerprint(row)
        for row in survivors
    }
    frozen_material = {
        "candidate_ids": ids,
        "baseline_fingerprints": fingerprints,
    }
    payload = {
        "protocol": PROTOCOL,
        "lane": LANE,
        "selection_version": 2,
        "selection_policy": (
            "all baseline strategies passing development robustness and "
            "lookahead audit; no performance top-N truncation"
        ),
        "candidate_ids": ids,
        "candidate_count": len(ids),
        "baseline_track_count": int(load_json(BASELINE_PROGRESS)["track_count"]),
        "baseline_fingerprints": fingerprints,
        "selection_hash": json_hash(frozen_material),
        "hidden_validation_opened": False,
        "final_oos_opened": False,
        "phase1_registry_mutated": False,
    }
    if SELECTION.exists():
        prior = load_json(SELECTION)
        if int(prior.get("selection_version", 1) or 1) < 2:
            if prior.get("candidate_ids") != ids:
                raise RuntimeError(
                    "Phase-2 frozen follow-up survivor IDs changed during selection upgrade"
                )
            save_json(SELECTION, payload)
            return payload
        if prior.get("selection_hash") != payload["selection_hash"]:
            raise RuntimeError(
                "Phase-2 frozen follow-up survivor evidence changed after freeze"
            )
        return prior
    save_json(SELECTION, payload)
    return payload


def track_lookup():
    return {x["id"]: x for x in p2.build_tracks()}


def rerun_detail(track, baseline_row):
    summary = p2.screen_track(track)
    detail = load_json(HERE / "last_run.json")
    replay_keys = (
        "score", "cagr_pct", "return_pct", "sharpe", "pf",
        "max_dd_pct", "trades", "evidence_grade",
        "stress_return_pct", "extreme_stress_return_pct",
    )
    replay_now = {
        **summary,
        "stress_return_pct": p2.safe_number(
            (detail.get("stress") or {}).get("return_pct")
        ),
        "extreme_stress_return_pct": p2.safe_number(
            (detail.get("extreme_stress") or {}).get("return_pct")
        ),
    }
    mismatches = {}
    for key in replay_keys:
        before = baseline_row.get(key)
        after = replay_now.get(key)
        if before == after:
            continue
        try:
            same = (
                before is not None
                and after is not None
                and abs(float(before) - float(after)) <= 1e-9
            )
        except Exception:
            same = False
        if not same:
            mismatches[key] = {"baseline": before, "replay": after}
    if mismatches:
        return {
            "ts": p2.now(),
            "protocol": PROTOCOL,
            "lane": LANE,
            "stage": "development_survivor_followup",
            "track_id": track["id"],
            "family": track["family"],
            "target": track["target"]["id"],
            "market": track["target"]["market"],
            "tested_timeframe": track["routing"]["tested_timeframe"],
            "route_stage": track["routing"]["stage"],
            "routing": track["routing"],
            "profile": track["profile_name"],
            "status": "baseline_replay_mismatch",
            "mismatches": mismatches,
            "parameter_rescue_performed": False,
            "hidden_validation_opened": False,
            "final_oos_opened": False,
            "phase1_registry_mutated": False,
        }
    if detail.get("hidden_validation_opened") is True:
        raise RuntimeError("Phase-2 follow-up unexpectedly opened hidden validation")
    if detail.get("final_oos_opened") is True or detail.get("oos_opened") is True:
        raise RuntimeError("Phase-2 follow-up unexpectedly opened final OOS")
    slices = detail.get("cscv_slices") or []
    slice_k = []
    slice_names = []
    for row in slices:
        try:
            value = float(row["raw_k"])
        except Exception:
            continue
        if not math.isfinite(value):
            continue
        slice_k.append(value)
        slice_names.append(str(row.get("name")))
    return {
        "ts": p2.now(),
        "protocol": PROTOCOL,
        "lane": LANE,
        "stage": "development_survivor_followup",
        "followup_version": FOLLOWUP_VERSION,
        "track_id": track["id"],
        "family": track["family"],
        "target": track["target"]["id"],
        "market": track["target"]["market"],
        "tested_timeframe": track["routing"]["tested_timeframe"],
        "route_stage": track["routing"]["stage"],
        "source_route_verified": track["routing"]["source_route_verified"],
        "source_native_match": track["routing"]["source_native_match"],
        "signal_cadence": track["routing"]["signal_cadence"],
        "routing": track["routing"],
        "profile": track["profile_name"],
        "status": "ok",
        "guard_ok": bool(summary.get("guard_ok")),
        "lookahead_pass": bool(summary.get("lookahead_pass")),
        "score": p2.safe_number(detail.get("score")),
        "cagr_pct": p2.safe_number(detail.get("cagr_pct")),
        "return_pct": p2.safe_number(detail.get("return_pct")),
        "sharpe": p2.safe_number(detail.get("sharpe")),
        "pf": p2.safe_number(detail.get("pf")),
        "max_dd_pct": p2.safe_number(detail.get("max_dd_pct")),
        "trades": p2.safe_number(detail.get("trades")),
        "development_years": p2.safe_number(detail.get("development_years")),
        "bars_per_year": int(track["target"]["bars_per_year"]),
        "commission": float(track["target"]["commission"]),
        "margin": float(track["target"]["margin"]),
        "evidence_grade": detail.get("evidence_grade"),
        "psr_zero": p2.safe_number(detail.get("psr_zero")),
        "bootstrap_mean_positive_pvalue": p2.safe_number(
            detail.get("bootstrap_mean_positive_pvalue")
        ),
        "bootstrap_sharpe_p10": p2.safe_number(
            detail.get("bootstrap_sharpe_p10")
        ),
        "stress_return_pct": p2.safe_number(
            (detail.get("stress") or {}).get("return_pct")
        ),
        "extreme_stress_return_pct": p2.safe_number(
            (detail.get("extreme_stress") or {}).get("return_pct")
        ),
        "cscv_slice_names": slice_names,
        "cscv_slice_k": slice_k,
        "cscv_slice_count": len(slice_k),
        "strategy_sha256": detail.get("strategy_sha256"),
        "harness_sha256": detail.get("harness_sha256"),
        "program_sha256": detail.get("program_sha256"),
        "source_logic": "exact_frozen_phase2_seed_replay",
        "parameter_rescue_performed": False,
        "hidden_validation_opened": False,
        "final_oos_opened": False,
        "phase1_registry_mutated": False,
    }


def cohort_cscv(rows):
    """CSCV PBO plus candidate-specific selection diagnostics."""
    if len(rows) < MIN_COHORT:
        return None
    widths = {}
    for row in rows:
        vals = row.get("cscv_slice_k") or []
        if len(vals) >= 4 and len(vals) % 2 == 0:
            widths[len(vals)] = widths.get(len(vals), 0) + 1
    if not widths:
        return None
    width = max(widths, key=lambda k: (widths[k], k))
    keep = [
        row for row in rows
        if len(row.get("cscv_slice_k") or []) == width
        and all(math.isfinite(float(x)) for x in row["cscv_slice_k"])
    ]
    if len(keep) < MIN_COHORT:
        return None
    matrix = np.asarray([row["cscv_slice_k"] for row in keep], dtype=float)
    n_strat, n_folds = matrix.shape
    half = n_folds // 2
    combos = list(itertools.combinations(range(n_folds), half))
    all_idx = set(range(n_folds))
    candidate = {
        row["track_id"]: {
            "selected_count": 0,
            "below_median_count": 0,
            "oos_percentiles": [],
        }
        for row in keep
    }
    below = 0
    logits = []
    for train_tuple in combos:
        train = np.asarray(train_tuple, dtype=int)
        test = np.asarray(sorted(all_idx - set(train_tuple)), dtype=int)
        train_perf = np.mean(matrix[:, train], axis=1)
        best_value = float(np.max(train_perf))
        tied = [
            i for i, value in enumerate(train_perf)
            if math.isclose(float(value), best_value, rel_tol=0.0, abs_tol=1e-12)
        ]
        best = min(tied, key=lambda i: keep[i]["track_id"])
        test_perf = np.mean(matrix[:, test], axis=1)
        selected = float(test_perf[best])
        less = int(np.sum(test_perf < selected))
        equal = int(np.sum(test_perf == selected))
        percentile = (less + 0.5 * equal) / n_strat
        percentile = min(max(percentile, 1e-9), 1.0 - 1e-9)
        logits.append(math.log(percentile / (1.0 - percentile)))
        is_below = percentile < 0.5
        below += int(is_below)
        cid = keep[best]["track_id"]
        candidate[cid]["selected_count"] += 1
        candidate[cid]["below_median_count"] += int(is_below)
        candidate[cid]["oos_percentiles"].append(percentile)

    details = {}
    for cid, row in candidate.items():
        count = int(row["selected_count"])
        pbo = (
            row["below_median_count"] / count
            if count else None
        )
        details[cid] = {
            "selected_count": count,
            "selection_share": round(count / max(len(combos), 1), 6),
            "candidate_pbo_when_selected": (
                None if pbo is None else round(float(pbo), 6)
            ),
            "median_oos_percentile_when_selected": (
                None
                if not row["oos_percentiles"]
                else round(float(np.median(row["oos_percentiles"])), 6)
            ),
        }
    return {
        "pbo": round(below / max(len(combos), 1), 6),
        "cscv_splits": len(combos),
        "median_oos_logit": round(float(np.median(logits)), 6),
        "candidate_count": n_strat,
        "fold_count": n_folds,
        "partition": "fixed_even_development_slices",
        "candidate_diagnostics": details,
    }


def bh_qvalues(rows):
    valid = []
    for i, row in enumerate(rows):
        try:
            p = float(row.get("bootstrap_mean_positive_pvalue"))
        except Exception:
            continue
        if math.isfinite(p) and 0.0 <= p <= 1.0:
            valid.append((p, i))
    valid.sort()
    out = [None] * len(rows)
    running = 1.0
    m = len(valid)
    for rank in range(m, 0, -1):
        p, idx = valid[rank - 1]
        q = min(running, p * m / rank)
        running = q
        out[idx] = round(min(1.0, q), 6)
    return out


def build_promotion(rows, selection):
    cohorts = {}
    for row in rows:
        if row.get("status") != "ok":
            continue
        key = f"{row.get('target')}::{row.get('profile')}"
        cohorts.setdefault(key, []).append(row)

    cohort_diag = {}
    for key, group in cohorts.items():
        cohort_diag[key] = cohort_cscv(group)

    ordered = sorted(
        [x for x in rows if x.get("status") == "ok"],
        key=lambda x: (
            str(x.get("target")),
            str(x.get("profile")),
            -float(x.get("score") if x.get("score") is not None else -1e99),
            str(x.get("track_id")),
        ),
    )
    qvals = bh_qvalues(ordered)
    queue = []
    for row, qvalue in zip(ordered, qvals):
        cohort_key = f"{row.get('target')}::{row.get('profile')}"
        diag = cohort_diag.get(cohort_key)
        candidate_diag = (
            None if not diag
            else diag.get("candidate_diagnostics", {}).get(row["track_id"])
        )
        pbo = None if not diag else diag.get("pbo")
        candidate_pbo = (
            None if not candidate_diag
            else candidate_diag.get("candidate_pbo_when_selected")
        )
        evidence_ok = str(row.get("evidence_grade") or "D") in {"A", "B"}
        stress_ok = (
            row.get("extreme_stress_return_pct") is not None
            and float(row["extreme_stress_return_pct"]) > 0.0
        )
        pbo_ok = pbo is not None and float(pbo) <= PBO_LIMIT
        candidate_pbo_ok = (
            candidate_pbo is not None and float(candidate_pbo) <= PBO_LIMIT
        )
        ready = bool(
            row.get("guard_ok")
            and row.get("lookahead_pass")
            and candidate_diag is not None
            and int(candidate_diag.get("selected_count", 0)) > 0
            and evidence_ok
            and stress_ok
            and pbo_ok
            and candidate_pbo_ok
        )
        queue.append({
            "track_id": row["track_id"],
            "family": row.get("family"),
            "target": row.get("target"),
            "market": row.get("market"),
            "tested_timeframe": row.get("tested_timeframe"),
            "route_stage": row.get("route_stage"),
            "source_route_verified": row.get("source_route_verified"),
            "source_native_match": row.get("source_native_match"),
            "signal_cadence": row.get("signal_cadence"),
            "routing": row.get("routing"),
            "profile": row.get("profile"),
            "score": row.get("score"),
            "cagr_pct": row.get("cagr_pct"),
            "sharpe": row.get("sharpe"),
            "pf": row.get("pf"),
            "max_dd_pct": row.get("max_dd_pct"),
            "trades": row.get("trades"),
            "development_years": row.get("development_years"),
            "bars_per_year": row.get("bars_per_year"),
            "commission": row.get("commission"),
            "margin": row.get("margin"),
            "evidence_grade": row.get("evidence_grade"),
            "strategy_sha256": row.get("strategy_sha256"),
            "harness_sha256": row.get("harness_sha256"),
            "program_sha256": row.get("program_sha256"),
            "extreme_stress_return_pct": row.get("extreme_stress_return_pct"),
            "bootstrap_fdr_qvalue": qvalue,
            "fdr_report_level": FDR_REPORT_LEVEL,
            "cohort": cohort_key,
            "cohort_pbo": pbo,
            "candidate_pbo_when_selected": candidate_pbo,
            "candidate_selection_share": (
                None if not candidate_diag
                else candidate_diag.get("selection_share")
            ),
            "pbo_limit": PBO_LIMIT,
            "ready_for_v4_replay": ready,
            "research_survivor": bool(
                row.get("guard_ok")
                and row.get("lookahead_pass")
                and stress_ok
            ),
            "provisional_reason": (
                None
                if ready
                else (
                    "pbo_unavailable_small_or_incompatible_cohort"
                    if pbo is None
                    else "development_evidence_not_yet_v4_replay_ready"
                )
            ),
            "parameter_rescue_performed": False,
            "hidden_validation_opened": False,
            "final_oos_opened": False,
        })

    lookup = track_lookup()
    PROMOTION_SOURCES.mkdir(parents=True, exist_ok=True)
    for item in queue:
        track = lookup.get(item["track_id"])
        if track is not None:
            item["bars_per_year"] = int(track["target"]["bars_per_year"])
            item["commission"] = float(track["target"]["commission"])
            item["margin"] = float(track["target"]["margin"])
            item["tested_timeframe"] = track["routing"]["tested_timeframe"]
            item["route_stage"] = track["routing"]["stage"]
            item["routing"] = track["routing"]
        if not item["ready_for_v4_replay"]:
            continue
        if track is None:
            raise RuntimeError(
                f"ready Phase-2 promotion track disappeared: {item['track_id']}"
            )
        source_path = PROMOTION_SOURCES / f"{item['track_id']}.py"
        p2.generate(
            track["family"],
            source_path,
            int(track["target"]["bars_per_year"]),
            float(track["profile"]["starting_vol_target"]),
            float(track["profile"]["f_max"]),
        )
        source_sha = sha256_file(source_path)
        expected_sha = str(item.get("strategy_sha256") or "")
        if not expected_sha or source_sha != expected_sha:
            raise RuntimeError(
                f"Phase-2 promotion source hash mismatch for {item['track_id']}: "
                f"{source_sha} != {expected_sha}"
            )
        item["promotion_source_path"] = (
            f"atlasforge_autoresearch_reconstruction/phase2_state/"
            f"promotion_sources/{item['track_id']}.py"
        )
        item["promotion_source_sha256"] = source_sha

    payload = {
        "protocol": PROTOCOL,
        "lane": LANE,
        "stage": "adaptive_followup_complete",
        "followup_version": FOLLOWUP_VERSION,
        "selection_hash": selection["selection_hash"],
        "candidate_count": len(queue),
        "research_survivor_count": sum(
            1 for x in queue if x["research_survivor"]
        ),
        "ready_for_v4_replay_count": sum(
            1 for x in queue if x["ready_for_v4_replay"]
        ),
        "pbo_limit": PBO_LIMIT,
        "cohorts": cohort_diag,
        "rows": queue,
        "hidden_validation_opened": False,
        "final_oos_opened": False,
        "phase1_registry_mutated": False,
    }
    save_json(PROMOTION, payload)
    return payload


def write_progress(selection, results):
    done_ids = {x.get("track_id") for x in results if x.get("track_id")}
    total = len(selection["candidate_ids"])
    done = len(done_ids.intersection(selection["candidate_ids"]))
    errors = sum(
        1 for x in results
        if x.get("status") in {"error", "baseline_replay_mismatch"}
    )
    complete = done >= total
    promotion = None
    if complete and errors == 0:
        promotion = build_promotion(results, selection)
    payload = {
        "protocol": PROTOCOL,
        "lane": LANE,
        "followup_version": FOLLOWUP_VERSION,
        "stage": (
            "adaptive_followup_complete"
            if complete and errors == 0
            else "adaptive_followup"
        ),
        "selection_hash": selection["selection_hash"],
        "candidate_count": total,
        "processed_count": done,
        "error_count": errors,
        "completion_pct": round(100.0 * done / max(total, 1), 2),
        "all_survivors_followed_up": complete and errors == 0,
        "ready_for_v4_replay_count": (
            0 if promotion is None
            else int(promotion["ready_for_v4_replay_count"])
        ),
        "research_survivor_count": (
            0 if promotion is None
            else int(promotion["research_survivor_count"])
        ),
        "next_stage": (
            "frozen_for_cross_lane_promotion"
            if complete and errors == 0
            else "continue_adaptive_followup"
        ),
        "hidden_validation_opened": False,
        "final_oos_opened": False,
        "phase1_registry_mutated": False,
    }
    save_json(PROGRESS, payload)
    return payload


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--max-candidates", type=int, default=24)
    args = ap.parse_args()

    selection = freeze_selection()
    lookup = track_lookup()
    existing = {
        x.get("track_id"): x
        for x in read_jsonl(RESULTS)
        if x.get("track_id")
        and int(x.get("followup_version", 1) or 1) >= FOLLOWUP_VERSION
    }
    cursor = 0
    if CURSOR.exists():
        try:
            cursor = int(load_json(CURSOR).get("next_index", 0))
        except Exception:
            cursor = 0
    ids = selection["candidate_ids"]
    processed = 0
    visited = 0
    idx = cursor % max(len(ids), 1)
    while ids and processed < args.max_candidates and visited < len(ids):
        track_id = ids[idx]
        if track_id not in existing or existing[track_id].get("status") == "error":
            track = lookup.get(track_id)
            if track is None:
                row = {
                    "ts": p2.now(),
                    "protocol": PROTOCOL,
                    "lane": LANE,
                    "track_id": track_id,
                    "followup_version": FOLLOWUP_VERSION,
                    "status": "error",
                    "error": "frozen Phase-2 track no longer exists",
                    "parameter_rescue_performed": False,
                    "hidden_validation_opened": False,
                    "final_oos_opened": False,
                    "phase1_registry_mutated": False,
                }
            else:
                try:
                    baseline_row = selection["baseline_fingerprints"][track_id]
                    row = rerun_detail(track, baseline_row)
                except Exception as exc:
                    row = {
                        "ts": p2.now(),
                        "protocol": PROTOCOL,
                        "lane": LANE,
                        "track_id": track_id,
                        "followup_version": FOLLOWUP_VERSION,
                        "family": track.get("family"),
                        "target": track.get("target", {}).get("id"),
                        "profile": track.get("profile_name"),
                        "status": "error",
                        "error": f"{type(exc).__name__}: {str(exc)[:1800]}",
                        "parameter_rescue_performed": False,
                        "hidden_validation_opened": False,
                        "final_oos_opened": False,
                        "phase1_registry_mutated": False,
                    }
            append_jsonl(RESULTS, row)
            existing[track_id] = row
            processed += 1
        idx = (idx + 1) % len(ids)
        visited += 1

    save_json(CURSOR, {
        "next_index": idx,
        "selection_hash": selection["selection_hash"],
        "followup_version": FOLLOWUP_VERSION,
        "candidate_count": len(ids),
        "updated_at": p2.now(),
    })
    progress = write_progress(selection, list(existing.values()))
    print(json.dumps(progress, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
