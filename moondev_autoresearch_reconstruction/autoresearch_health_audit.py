"""Cross-lane AUTORESEARCH health audit.

This is an integrity/operability audit, not a performance gate. It distinguishes
internal project defects from legitimate unfinished research and external-data
requirements. It never opens hidden validation or final OOS data.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import continuous_runner
import phase2_prior_runner

HERE = Path(__file__).resolve().parent
REGISTRY = HERE / "strategy_library" / "registry.json"


def load(path):
    if not path:
        return None
    p = Path(path)
    if not p.exists() or p.stat().st_size == 0:
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError):
        return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--continuous-progress")
    ap.add_argument("--phase2-progress")
    ap.add_argument("--phase2-followup-progress")
    ap.add_argument("--phase2-promotion")
    ap.add_argument("--phase3-hydration")
    ap.add_argument("--phase3-reconstruction")
    ap.add_argument("--phase3-mapping")
    ap.add_argument("--v4-bootstrap")
    ap.add_argument("--v4-prop")
    ap.add_argument("--output", default="health_report.json")
    args = ap.parse_args()

    errors = []
    pending = []
    notes = []

    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    families = registry.get("families", [])
    ids = [x.get("id") for x in families]
    if len(ids) != len(set(ids)):
        errors.append("registry contains duplicate family ids")

    phase1 = continuous_runner.build_tracks()
    if len(phase1) != 514:
        errors.append(f"phase1 universe is {len(phase1)}, expected exactly 514")

    phase2 = phase2_prior_runner.build_tracks()
    if len({x["id"] for x in phase2}) != len(phase2):
        errors.append("phase2 contains duplicate track ids")
    if set(x["id"] for x in phase1) & set(x["id"] for x in phase2):
        errors.append("phase1 and phase2 track ids overlap")

    unresolved_tokens = ("blocked", "pending", "recovery", "incomplete")
    for row in families:
        status = str(row.get("status", ""))
        if any(tok in status.lower() for tok in unresolved_tokens):
            req = row.get("requires") or []
            if not req:
                errors.append(
                    f"{row.get('id')}: unresolved status {status!r} has no explicit requirements"
                )
            else:
                pending.append({
                    "type": "strategy_requirement",
                    "id": row.get("id"),
                    "status": status,
                    "requires": req,
                })

    by_id = {x["id"]: x for x in families}
    hr = by_id.get("hr_dual_alpha", {})
    if hr.get("status") != "prior_frozen_superior_pass":
        errors.append("HR-DUAL recovered prior-positive classification is not locked")
    if (hr.get("source_lock") or {}).get("implementation_blob_sha") != (
        "27a24f0bc1883c497af23ff3a27918e35f3f4c11"
    ):
        errors.append("HR-DUAL source implementation lock changed")
    finlab = by_id.get("finlab_rotation_exact", {})
    if finlab.get("status") != "prior_rejected":
        errors.append("FinLab frozen failed candidate was reopened")

    states = {
        "continuous": load(args.continuous_progress),
        "phase2": load(args.phase2_progress),
        "phase2_followup": load(args.phase2_followup_progress),
        "phase2_promotion": load(args.phase2_promotion),
        "phase3_hydration": load(args.phase3_hydration),
        "phase3_reconstruction": load(args.phase3_reconstruction),
        "phase3_mapping": load(args.phase3_mapping),
        "v4": load(args.v4_bootstrap),
        "v4_prop": load(args.v4_prop),
    }

    for name, state in states.items():
        if state is None:
            notes.append(f"{name}: state snapshot not supplied")
            continue
        if state.get("hidden_validation_opened") is True:
            errors.append(f"{name}: hidden validation unexpectedly opened")
        if state.get("final_oos_opened") is True:
            errors.append(f"{name}: final OOS unexpectedly opened")

    c = states["continuous"]
    if c:
        if int(c.get("runnable_track_count", -1)) != 514:
            errors.append("continuous state no longer reports 514 runnable tracks")
        if c.get("hidden_validation_allowed_by_universe_plan") is True:
            errors.append("continuous state allows hidden validation before universe freeze")

    p2 = states["phase2"]
    p2_follow = states["phase2_followup"]
    if p2:
        if int(p2.get("error_count", 0)) != 0:
            errors.append(f"phase2 has {p2.get('error_count')} recorded internal errors")
        if int(p2.get("data_blocked_count", 0)) != 0:
            errors.append(f"phase2 has {p2.get('data_blocked_count')} unresolved data blocks")
        if not p2.get("all_tracks_screened"):
            pending.append({
                "type": "finite_lane_progress",
                "id": "phase2",
                "screened": int(p2.get("screened_count", 0)),
                "total": int(p2.get("track_count", 0)),
            })
        elif p2_follow is None:
            pending.append({
                "type": "finite_lane_progress",
                "id": "phase2_followup",
                "processed": 0,
                "total": int(p2.get("guard_pass_count", 0)),
            })
    if p2_follow:
        if int(p2_follow.get("error_count", 0)) != 0:
            errors.append(
                f"phase2 follow-up has {p2_follow.get('error_count')} internal errors"
            )
        if not p2_follow.get("all_survivors_followed_up"):
            pending.append({
                "type": "finite_lane_progress",
                "id": "phase2_followup",
                "processed": int(p2_follow.get("processed_count", 0)),
                "total": int(p2_follow.get("candidate_count", 0)),
            })
        if p2_follow.get("hidden_validation_opened") is True:
            errors.append("phase2 follow-up unexpectedly opened hidden validation")
        if p2_follow.get("final_oos_opened") is True:
            errors.append("phase2 follow-up unexpectedly opened final OOS")

    p2_promotion = states["phase2_promotion"]
    ready_private = []
    ready_prop = []
    if p2_follow and p2_follow.get("all_survivors_followed_up"):
        expected_ready = int(p2_follow.get("ready_for_v4_replay_count", 0))
        if p2_promotion is None:
            if expected_ready:
                errors.append(
                    "phase2 follow-up is frozen with V4-ready candidates but "
                    "promotion_queue.json is missing"
                )
        else:
            if p2_promotion.get("stage") != "adaptive_followup_complete":
                errors.append(
                    f"phase2 promotion unexpected stage: "
                    f"{p2_promotion.get('stage')!r}"
                )
            rows = list(p2_promotion.get("rows") or [])
            ready = [x for x in rows if x.get("ready_for_v4_replay") is True]
            if len(ready) != expected_ready:
                errors.append(
                    f"phase2 promotion ready count {len(ready)} != "
                    f"follow-up count {expected_ready}"
                )
            for row in ready:
                source_path = row.get("promotion_source_path")
                source_sha = row.get("promotion_source_sha256")
                strategy_sha = row.get("strategy_sha256")
                if not source_path or not source_sha or not strategy_sha:
                    errors.append(
                        f"{row.get('track_id')}: V4-ready Phase-2 candidate "
                        "lacks exact promotion-source metadata"
                    )
                elif source_sha != strategy_sha:
                    errors.append(
                        f"{row.get('track_id')}: Phase-2 promotion source hash "
                        "does not match replayed strategy hash"
                    )
            ready_private = [
                x for x in ready if x.get("profile") == "private"
            ]
            ready_prop = [
                x for x in ready if x.get("profile") == "prop"
            ]

    v4 = states["v4"]
    if ready_private:
        transfer = None if v4 is None else v4.get("phase2_private_transfer")
        if not transfer:
            pending.append({
                "type": "finite_lane_progress",
                "id": "phase2_private_v4_handoff",
                "consumed": 0,
                "total": len(ready_private),
            })
        else:
            for row in transfer.get("candidates") or []:
                if row.get("replay_status") == "error":
                    errors.append(
                        f"phase2 private V4 replay error: "
                        f"{row.get('error') or row.get('track_id')}"
                    )
            consumed = int(transfer.get("candidate_count", 0))
            if consumed < len(ready_private):
                pending.append({
                    "type": "finite_lane_progress",
                    "id": "phase2_private_v4_handoff",
                    "consumed": consumed,
                    "total": len(ready_private),
                })

    v4_prop = states["v4_prop"]
    if ready_prop:
        transfer = (
            None if v4_prop is None
            else v4_prop.get("phase2_prop_transfer")
        )
        if not transfer:
            pending.append({
                "type": "finite_lane_progress",
                "id": "phase2_prop_v4_handoff",
                "consumed": 0,
                "total": len(ready_prop),
            })
        else:
            for row in transfer.get("candidates") or []:
                if row.get("transfer_status") == "error":
                    errors.append(
                        f"phase2 prop V4 transfer error: "
                        f"{row.get('error') or row.get('track_id')}"
                    )
            consumed = int(transfer.get("candidate_count", 0))
            if consumed < len(ready_prop):
                pending.append({
                    "type": "finite_lane_progress",
                    "id": "phase2_prop_v4_handoff",
                    "consumed": consumed,
                    "total": len(ready_prop),
                })

    h = states["phase3_hydration"]
    r = states["phase3_reconstruction"]
    m = states["phase3_mapping"]
    if h:
        if int(h.get("retry_pending_count", 0)) > 0:
            pending.append({
                "type": "finite_lane_progress",
                "id": "phase3_hydration_retry",
                "pending": int(h.get("retry_pending_count", 0)),
            })
        elif int(h.get("hydration_version", 1) or 1) < 2:
            pending.append({
                "type": "finite_lane_progress",
                "id": "phase3_hydration_v2",
                "pending": int(h.get("attempted_no_text_count", 0)),
            })
    if r and not r.get("all_reconstructed"):
        pending.append({
            "type": "finite_lane_progress",
            "id": "phase3_reconstruction",
            "pending": int(r.get("hydrated_reconstruction_pending", 0)),
        })
    if m and m.get("next_stage") == "source_hydration":
        mapper_pending = int(m.get("hydration_queue_count", 0))
        hydration_pending = 0 if not h else int(h.get("retry_pending_count", 0))
        hydration_is_v2 = bool(
            h and int(h.get("hydration_version", 1) or 1) >= 2
        )
        # Mapping is upstream of hydration. Once the current-version hydrator
        # is actively consuming that queue, reporting both as separate pending
        # work double-counts one dependency.
        if mapper_pending and not (hydration_is_v2 and hydration_pending >= 0):
            pending.append({
                "type": "finite_lane_progress",
                "id": "phase3_mapping",
                "pending": mapper_pending,
            })

    if v4:
        data_end = str(v4.get("data_end", ""))
        if data_end and data_end > "2020-12-31":
            errors.append(f"v4 development data_end crossed sealed boundary: {data_end}")
        if v4.get("stage") != "development_only":
            errors.append(f"v4 unexpected stage: {v4.get('stage')!r}")
    if v4_prop:
        prop_end = str(v4_prop.get("data_end", ""))
        if prop_end and prop_end > "2020-12-31":
            errors.append(
                f"v4 prop development data_end crossed sealed boundary: {prop_end}"
            )
        if v4_prop.get("stage") != "development_only":
            errors.append(
                f"v4 prop unexpected stage: {v4_prop.get('stage')!r}"
            )

    report = {
        "status": "PASS" if not errors else "FAIL",
        "internal_error_count": len(errors),
        "errors": errors,
        "pending_count": len(pending),
        "pending": pending,
        "notes": notes,
        "phase1_track_count": len(phase1),
        "phase2_track_count": len(phase2),
        "hidden_validation_opened": False,
        "final_oos_opened": False,
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    if errors:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
