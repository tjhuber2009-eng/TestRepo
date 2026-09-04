"""Persistent exhaustive NVIDIA AUTORESEARCH orchestrator.

Protocol:
1. Breadth: every runnable family/market/profile track receives a fixed number
   of valid development-only experiments.
2. Depth: a frozen top fraction inside each market/profile receives more search.
3. Elite: a frozen top fraction of depth survivors receives the largest budget.
4. Hidden validation: only after *all* adaptive search is frozen, each champion
   gets one pre-OOS validation look.
5. 2023+ is never downloaded or opened.

State persists on a dedicated GitHub branch between scheduled invocations.
"""

import argparse
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REGISTRY = HERE / "strategy_library" / "registry.json"
CONFIG = HERE / "continuous_config.json"
STATE = HERE / "continuous_state"
TRACKS = STATE / "tracks"
CURSOR = STATE / "cursor.json"
LEDGER = STATE / "cycles.jsonl"
PROGRESS = STATE / "progress.json"
SELECTIONS = STATE / "search_selections.json"

RUNTIME_FILES = [
    "baseline.json",
    "last_run.json",
    "validation_run.json",
    "results.tsv",
    "proposal.txt",
    "STOP",
    "strategy_best.py",
    "seen_hashes.json",
    "experiments.jsonl",
]
PROTOCOL = "nested_chronological_v2"


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def run(cmd, env=None, check=True, stdout=None):
    print("+", " ".join(map(str, cmd)), flush=True)
    return subprocess.run(
        list(map(str, cmd)),
        cwd=HERE,
        env=env,
        check=check,
        text=True,
        stdout=stdout,
        stderr=subprocess.STDOUT if stdout is not None else None,
    )


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def slug(*parts):
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", "__".join(parts))


def safe_harness_env(env):
    out = dict(env)
    for key in list(out):
        upper = key.upper()
        if (
            upper == "NVIDIA_API_KEY"
            or upper == "GH_TOKEN"
            or upper == "GITHUB_TOKEN"
            or upper.endswith("_TOKEN")
            or upper.endswith("_API_KEY")
            or upper.endswith("_SECRET")
        ):
            out.pop(key, None)
    out["PYTHONNOUSERSITE"] = "1"
    return out


def build_tracks():
    registry = load_json(REGISTRY)
    config = load_json(CONFIG)
    families = [x for x in registry["families"] if x.get("status") == "runnable"]
    targets = [x for x in config["targets"] if x.get("enabled")]
    profiles = config["profiles"]
    tracks = []
    for family in sorted(families, key=lambda x: (int(x.get("priority", 50)), x["id"])):
        allowed = set(family.get("markets", []))
        for target in sorted(targets, key=lambda x: x["id"]):
            if allowed and target["market"] not in allowed:
                continue
            for profile_name in ["prop", "private"]:
                tracks.append({
                    "family": family,
                    "target": target,
                    "profile_name": profile_name,
                    "profile": profiles[profile_name],
                    "id": slug(family["id"], target["id"], profile_name),
                })
    return tracks


def read_cursor(total):
    if not CURSOR.exists():
        return 0
    try:
        x = load_json(CURSOR)
    except Exception:
        return 0
    if x.get("protocol") != PROTOCOL:
        return 0
    return int(x.get("next_index", 0)) % max(total, 1)


def write_cursor(index, total):
    save_json(CURSOR, {
        "next_index": index % max(total, 1),
        "track_count": total,
        "updated_at": now(),
        "protocol": PROTOCOL,
    })


def result_counts_at(path):
    path = Path(path)
    counts = {
        "attempts": 0, "valid": 0, "kept": 0, "rejected": 0,
        "crashes": 0, "duplicates": 0, "parameter_only": 0, "too_broad": 0,
        "risk_control_change": 0,
    }
    if not path.exists():
        return counts
    with path.open(encoding="utf-8") as f:
        next(f, None)
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            counts["attempts"] += 1
            verdict = parts[2]
            if verdict == "KEPT":
                counts["kept"] += 1
                counts["valid"] += 1
            elif verdict == "REJECTED":
                counts["rejected"] += 1
                counts["valid"] += 1
            elif verdict == "CRASH":
                counts["crashes"] += 1
            elif verdict == "DUPLICATE":
                counts["duplicates"] += 1
            elif verdict == "PARAMETER_ONLY":
                counts["parameter_only"] += 1
            elif verdict == "TOO_BROAD":
                counts["too_broad"] += 1
            elif verdict == "RISK_CONTROL_CHANGE":
                counts["risk_control_change"] += 1
    return counts


def track_meta(track):
    p = TRACKS / track["id"] / "state_meta.json"
    if not p.exists():
        return None
    try:
        m = load_json(p)
    except Exception:
        return None
    if m.get("protocol") != PROTOCOL:
        return None
    return m


def track_counts(track):
    return result_counts_at(TRACKS / track["id"] / "results.tsv")


def is_terminal_block(track):
    m = track_meta(track)
    return bool(m and m.get("status") in {"seed_blocked", "data_insufficient"})


def validation_state(track):
    p = TRACKS / track["id"] / "validation.json"
    if not p.exists():
        return None
    try:
        return load_json(p)
    except Exception:
        return None


def development_score(track):
    m = track_meta(track)
    if not m:
        return float("-inf")
    b = m.get("baseline") or {}
    try:
        score = float(b.get("score", float("-inf")))
    except Exception:
        return float("-inf")
    return score if math.isfinite(score) else float("-inf")


def target_env(track):
    t = track["target"]
    p = track["profile"]
    cfg = load_json(CONFIG)
    env = dict(os.environ)
    env.update({
        "AUTORESEARCH_HARNESS": "robust_harness.py",
        "AUTORESEARCH_PROGRAM": "program_robust.md",
        "AUTORESEARCH_FAMILY": track["family"]["id"],
        "AUTORESEARCH_SYMBOL": t["symbol"],
        "AUTORESEARCH_MARKET": t["market"],
        "AUTORESEARCH_DATA_FILE": f"data/{t['id']}_1d.csv",
        "AUTORESEARCH_COMMISSION": str(t["commission"]),
        "AUTORESEARCH_MARGIN": str(t["margin"]),
        "AUTORESEARCH_BARS_PER_YEAR": str(t["bars_per_year"]),
        "AUTORESEARCH_PROFILE": track["profile_name"],
        "AUTORESEARCH_MAX_DD_PCT": str(p["max_dd_pct"]),
        "AUTORESEARCH_MIN_TRADES": str(track["family"].get("min_trades", 12)),
        "AUTORESEARCH_MIN_VALIDATION_TRADES": str(
            track["family"].get("min_validation_trades", 2)
        ),
        "AUTORESEARCH_MIN_ACTIVE_FOLDS": "3",
        "AUTORESEARCH_MIN_FOLD_BARS": "100",
        "AUTORESEARCH_VOL_BAND": "0.50",
        "AUTORESEARCH_COST_STRESS_MULT": str(
            cfg.get("protocol", {}).get("cost_stress_multiplier", 2.0)
        ),
        "AUTORESEARCH_IS_START": t.get("start", "2017-08-17"),
        "AUTORESEARCH_VALIDATION_START": t.get("validation_start", "2021-01-01"),
        "AUTORESEARCH_VALIDATION_END": t.get("validation_end", "2022-12-31"),
    })
    return env


def prepare_data(track):
    t = track["target"]
    path = HERE / "data" / f"{t['id']}_1d.csv"
    manifest = HERE / "data" / f"{t['id']}_1d.manifest.json"
    wanted_start = t.get("start", "2017-08-17")
    wanted_end = t.get("validation_end", "2022-12-31")
    if path.exists() and path.stat().st_size > 1000 and manifest.exists():
        try:
            m = load_json(manifest)
            if m.get("requested_start") == wanted_start and m.get("requested_end") == wanted_end:
                return
        except Exception:
            pass
    run([
        sys.executable,
        "prepare_market_data.py",
        "--source", t["source"],
        "--symbol", t["symbol"],
        "--id", t["id"],
        "--start", wanted_start,
        "--end", wanted_end,
    ])


def clean_runtime():
    for name in RUNTIME_FILES:
        p = HERE / name
        if p.is_file():
            p.unlink()
    for d in ["keepers", "logs"]:
        p = HERE / d
        if p.exists():
            shutil.rmtree(p)
        p.mkdir(parents=True, exist_ok=True)


def restore_state(track_dir):
    meta_path = track_dir / "state_meta.json"
    if not meta_path.exists():
        return False
    meta = load_json(meta_path)
    if meta.get("protocol") != PROTOCOL or meta.get("status") != "active":
        return False
    for name in [
        "baseline.json", "last_run.json", "results.tsv",
        "strategy_best.py", "seen_hashes.json", "experiments.jsonl",
    ]:
        src = track_dir / name
        if src.exists():
            shutil.copy2(src, HERE / name)
        elif name not in {"seen_hashes.json", "experiments.jsonl"}:
            return False
    shutil.copy2(track_dir / "strategy_best.py", HERE / "strategy.py")
    src = track_dir / "keepers"
    if src.exists():
        shutil.copytree(src, HERE / "keepers", dirs_exist_ok=True)
    return True


def replace_numeric_assignment(source, name, value):
    pat = re.compile(rf"(^\s*{re.escape(name)}\s*=\s*)([0-9.]+)", re.M)
    m = pat.search(source)
    if not m:
        raise RuntimeError(f"{name} assignment not found in seed")
    return source[:m.start(2)] + f"{value:.8f}" + source[m.end(2):]


def generate_seed(track):
    t = track["target"]
    p = track["profile"]
    run([
        sys.executable,
        "seed_factory.py",
        "--family", track["family"]["id"],
        "--output", "strategy.py",
        "--bars-per-year", str(t["bars_per_year"]),
        "--vol-target", str(p["starting_vol_target"]),
        "--f-max", str(p["f_max"]),
    ])


def initialize_track(track, track_dir, env):
    generate_seed(track)
    limit = float(track["profile"]["max_dd_pct"])
    henv = safe_harness_env(env)

    last = None
    for _ in range(8):
        for name in ["baseline.json", "last_run.json"]:
            p = HERE / name
            if p.exists():
                p.unlink()
        run([sys.executable, "robust_harness.py", "--is"], env=henv)
        last = load_json(HERE / "last_run.json")

        # Insufficient history is a data qualification result, not evidence that
        # the strategy itself is bad.
        details = list(last.get("audit_guard_details", []))
        if int(last.get("active_folds", 0)) < 3:
            reason = "insufficient pre-validation history for >=3 development folds"
            save_json(track_dir / "state_meta.json", {
                "status": "data_insufficient",
                "reason": reason,
                "protocol": PROTOCOL,
                "family": track["family"]["id"],
                "target": track["target"]["id"],
                "profile": track["profile_name"],
                "updated_at": now(),
            })
            return False, reason

        dds = [abs(float(last.get("max_dd_pct", 0.0)))]
        stress = last.get("stress") or {}
        dds.append(abs(float(stress.get("max_dd_pct", 0.0))))
        worst_dd = max(dds)

        if worst_dd <= limit:
            break

        source = (HERE / "strategy.py").read_text(encoding="utf-8")
        m = re.search(r"^\s*vol_target\s*=\s*([0-9.]+)", source, re.M)
        if not m:
            reason = "cannot risk-normalize seed: vol_target missing"
            save_json(track_dir / "state_meta.json", {
                "status": "seed_blocked",
                "reason": reason,
                "protocol": PROTOCOL,
                "updated_at": now(),
            })
            return False, reason
        old = float(m.group(1))
        if worst_dd <= 0:
            break
        factor = min(0.88 * limit / worst_dd, 0.93)
        new = max(0.002, old * factor)
        (HERE / "strategy.py").write_text(
            replace_numeric_assignment(source, "vol_target", new),
            encoding="utf-8",
        )
        print(
            f"[seed risk] {track['id']} worst_dd={worst_dd:.2f}% "
            f"vol_target {old:.6f}->{new:.6f}"
        )
    else:
        reason = "could not normalize seed below development/stress DD cap"
        save_json(track_dir / "state_meta.json", {
            "status": "seed_blocked",
            "reason": reason,
            "protocol": PROTOCOL,
            "updated_at": now(),
        })
        return False, reason

    # Freeze a development baseline even when the original seed is not yet
    # profitable. The agent is then allowed to rescue a weak researched family;
    # candidates themselves must pass the full development robustness gate.
    if not math.isfinite(float(last.get("score", float("-inf")))):
        last["score"] = -1000000.0
    save_json(HERE / "baseline.json", last)
    shutil.copy2(HERE / "strategy.py", HERE / "strategy_best.py")
    shutil.copy2(HERE / "strategy.py", HERE / "keepers" / "000_seed.py")
    save_json(HERE / "seen_hashes.json", {"version": 1, "hashes": []})
    save_track_state(track, track_dir)
    return True, "ok"


def save_track_state(track, track_dir, reason=""):
    track_dir.mkdir(parents=True, exist_ok=True)
    for name in [
        "baseline.json", "last_run.json", "results.tsv",
        "strategy_best.py", "seen_hashes.json", "experiments.jsonl",
    ]:
        src = HERE / name
        if src.exists():
            shutil.copy2(src, track_dir / name)
    if (HERE / "keepers").exists():
        dst = track_dir / "keepers"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(HERE / "keepers", dst)

    data_manifest_src = (
        HERE / "data" / f"{track['target']['id']}_1d.manifest.json"
    )
    if data_manifest_src.exists():
        shutil.copy2(data_manifest_src, track_dir / "data_manifest.json")

    baseline = load_json(HERE / "baseline.json") if (HERE / "baseline.json").exists() else {}
    counts = result_counts_at(HERE / "results.tsv")
    meta = {
        "status": "active",
        "reason": reason,
        "protocol": PROTOCOL,
        "family": track["family"]["id"],
        "family_exactness": track["family"].get("exactness"),
        "family_origin": track["family"].get("origin"),
        "target": track["target"]["id"],
        "symbol": track["target"]["symbol"],
        "market": track["target"]["market"],
        "profile": track["profile_name"],
        "max_dd_limit_pct": track["profile"]["max_dd_pct"],
        **counts,
        "baseline": baseline,
        "data_manifest": (
            load_json(track_dir / "data_manifest.json")
            if (track_dir / "data_manifest.json").exists() else None
        ),
        "updated_at": now(),
    }
    save_json(track_dir / "state_meta.json", meta)
    return meta


def append_cycle(record):
    STATE.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def discard_stale_track_state(track_dir):
    meta = track_dir / "state_meta.json"
    if not meta.exists():
        return
    try:
        payload = load_json(meta)
    except Exception:
        payload = {}
    if payload.get("protocol") != PROTOCOL:
        print(f"[state reset] discarding stale protocol state in {track_dir.name}")
        shutil.rmtree(track_dir)


def process_track(track, iters, model):
    print("\n" + "=" * 88)
    print(
        f"TRACK {track['id']} family={track['family']['id']} "
        f"market={track['target']['id']} profile={track['profile_name']}"
    )
    print("=" * 88, flush=True)

    track_dir = TRACKS / track["id"]
    discard_stale_track_state(track_dir)
    clean_runtime()
    prepare_data(track)
    env = target_env(track)

    restored = restore_state(track_dir)
    if not restored:
        ok, reason = initialize_track(track, track_dir, env)
        if not ok:
            append_cycle({
                "ts": now(), "track_id": track["id"],
                "status": "search_unavailable", "reason": reason,
            })
            print(f"[search unavailable] {reason}")
            return

    before = result_counts_at(HERE / "results.tsv")
    proc = run(
        [sys.executable, "loop.py", "--iters", str(iters), "--model", model],
        env=env,
        check=False,
    )
    after = result_counts_at(HERE / "results.tsv")
    reason = "" if proc.returncode == 0 else f"loop exit {proc.returncode}"
    meta = save_track_state(track, track_dir, reason=reason)
    b = meta.get("baseline", {})
    append_cycle({
        "ts": now(),
        "track_id": track["id"],
        "family": track["family"]["id"],
        "target": track["target"]["id"],
        "profile": track["profile_name"],
        "status": "active" if proc.returncode == 0 else "loop_error",
        "valid_before": before["valid"],
        "valid_after": after["valid"],
        "attempts_after": after["attempts"],
        "score": b.get("score"),
        "return_pct": b.get("return_pct"),
        "max_dd_pct": b.get("max_dd_pct"),
        "sharpe": b.get("sharpe"),
        "pf": b.get("pf"),
    })


def validate_track(track):
    track_dir = TRACKS / track["id"]
    if is_terminal_block(track) or validation_state(track):
        return
    clean_runtime()
    prepare_data(track)
    if not restore_state(track_dir):
        raise RuntimeError("cannot restore frozen champion for hidden validation")
    env = safe_harness_env(target_env(track))
    run([sys.executable, "robust_harness.py", "--validation"], env=env)
    result = load_json(HERE / "validation_run.json")
    save_json(track_dir / "validation.json", result)

    meta = load_json(track_dir / "state_meta.json")
    meta["status"] = "validation_pass" if result.get("guard_ok") else "validation_fail"
    meta["validation_guard_reason"] = result.get("guard_reason")
    meta["validation_return_pct"] = result.get("return_pct")
    meta["validation_sharpe"] = result.get("sharpe")
    meta["validation_max_dd_pct"] = result.get("max_dd_pct")
    meta["validation_pf"] = result.get("pf")
    meta["validated_at"] = now()
    save_json(track_dir / "state_meta.json", meta)
    append_cycle({
        "ts": now(),
        "track_id": track["id"],
        "family": track["family"]["id"],
        "target": track["target"]["id"],
        "profile": track["profile_name"],
        "status": meta["status"],
        "hidden_return_pct": result.get("return_pct"),
        "hidden_sharpe": result.get("sharpe"),
        "hidden_max_dd_pct": result.get("max_dd_pct"),
        "hidden_pf": result.get("pf"),
    })


def all_reached(tracks, ids, target):
    wanted = set(ids)
    for track in tracks:
        if track["id"] not in wanted:
            continue
        if is_terminal_block(track):
            continue
        if track_counts(track)["valid"] < target:
            return False
    return True


def breadth_complete(tracks, target):
    for track in tracks:
        if is_terminal_block(track):
            continue
        if track_counts(track)["valid"] < target:
            return False
    return True


def ranked_viable(tracks, min_valid):
    rows = []
    for track in tracks:
        if is_terminal_block(track):
            continue
        if track_counts(track)["valid"] < min_valid:
            continue
        score = development_score(track)
        if math.isfinite(score):
            rows.append((score, track))
    rows.sort(key=lambda x: x[0], reverse=True)
    return rows


def freeze_depth_selection(tracks, breadth_target, depth_fraction):
    if SELECTIONS.exists():
        x = load_json(SELECTIONS)
        if x.get("protocol") == PROTOCOL and x.get("depth_ids"):
            return x

    grouped = {}
    for score, track in ranked_viable(tracks, breadth_target):
        key = (track["target"]["id"], track["profile_name"])
        grouped.setdefault(key, []).append((score, track))

    depth_ids = []
    for key, rows in sorted(grouped.items()):
        n = max(1, math.ceil(len(rows) * depth_fraction))
        depth_ids.extend(track["id"] for _, track in rows[:n])

    x = {
        "protocol": PROTOCOL,
        "created_at": now(),
        "breadth_target": breadth_target,
        "depth_fraction": depth_fraction,
        "depth_ids": sorted(set(depth_ids)),
        "elite_ids": [],
    }
    save_json(SELECTIONS, x)
    return x


def freeze_elite_selection(tracks, depth_target, elite_fraction):
    x = load_json(SELECTIONS)
    if x.get("elite_ids"):
        return x
    depth_ids = set(x.get("depth_ids", []))
    rows = [
        (score, track)
        for score, track in ranked_viable(tracks, depth_target)
        if track["id"] in depth_ids
    ]
    by_profile = {}
    for score, track in rows:
        by_profile.setdefault(track["profile_name"], []).append((score, track))

    elite_ids = []
    for profile, group in sorted(by_profile.items()):
        n = max(3, math.ceil(len(group) * elite_fraction))
        elite_ids.extend(track["id"] for _, track in group[:n])
    x["elite_fraction"] = elite_fraction
    x["elite_created_at"] = now()
    x["elite_ids"] = sorted(set(elite_ids))
    save_json(SELECTIONS, x)
    return x


def current_search_plan(
    tracks, breadth_target, depth_target, elite_target,
    depth_fraction, elite_fraction,
):
    if not breadth_complete(tracks, breadth_target):
        return "breadth", {t["id"]: breadth_target for t in tracks if not is_terminal_block(t)}

    selections = freeze_depth_selection(tracks, breadth_target, depth_fraction)
    depth_ids = selections.get("depth_ids", [])
    if not all_reached(tracks, depth_ids, depth_target):
        return "depth", {x: depth_target for x in depth_ids}

    selections = freeze_elite_selection(tracks, depth_target, elite_fraction)
    elite_ids = selections.get("elite_ids", [])
    if not all_reached(tracks, elite_ids, elite_target):
        return "elite", {x: elite_target for x in elite_ids}

    return "validation", {}


def next_search_track(tracks, plan, start):
    n = len(tracks)
    for offset in range(n):
        idx = (start + offset) % n
        track = tracks[idx]
        target = plan.get(track["id"])
        if target is None or is_terminal_block(track):
            continue
        if track_counts(track)["valid"] < target:
            return idx, track, target
    return None


def next_validation_track(tracks, start):
    n = len(tracks)
    for offset in range(n):
        idx = (start + offset) % n
        track = tracks[idx]
        if is_terminal_block(track):
            continue
        if validation_state(track) is None:
            return idx, track
    return None


def write_progress(
    tracks, phase, breadth_target, depth_target, elite_target,
):
    registry = load_json(REGISTRY)
    blocked_families = [
        {
            "id": x["id"],
            "exactness": x.get("exactness"),
            "origin": x.get("origin"),
            "requires": x.get("requires", []),
        }
        for x in registry["families"] if x.get("status") != "runnable"
    ]
    rows = []
    counts = {
        "data_insufficient": 0, "seed_blocked": 0,
        "validation_pass": 0, "validation_fail": 0,
        "searching": 0,
    }
    total_valid = total_attempts = total_crashes = total_duplicates = total_parameter_only = total_too_broad = total_risk_changes = 0

    selections = load_json(SELECTIONS) if SELECTIONS.exists() else {}
    depth_ids = set(selections.get("depth_ids", []))
    elite_ids = set(selections.get("elite_ids", []))

    for track in tracks:
        m = track_meta(track)
        rc = track_counts(track)
        total_valid += rc["valid"]
        total_attempts += rc["attempts"]
        total_crashes += rc["crashes"]
        total_duplicates += rc["duplicates"]
        total_parameter_only += rc["parameter_only"]
        total_too_broad += rc["too_broad"]
        total_risk_changes += rc["risk_control_change"]
        val = validation_state(track)

        if m and m.get("status") == "data_insufficient":
            status = "data_insufficient"
        elif m and m.get("status") == "seed_blocked":
            status = "seed_blocked"
        elif val is not None:
            status = "validation_pass" if val.get("guard_ok") else "validation_fail"
        else:
            status = "searching"
        counts[status] += 1

        rows.append({
            "track_id": track["id"],
            "family": track["family"]["id"],
            "target": track["target"]["id"],
            "profile": track["profile_name"],
            "status": status,
            "valid_attempts": rc["valid"],
            "attempts": rc["attempts"],
            "development_score": development_score(track),
            "depth_selected": track["id"] in depth_ids,
            "elite_selected": track["id"] in elite_ids,
        })

    terminal = (
        counts["data_insufficient"] + counts["seed_blocked"]
        + counts["validation_pass"] + counts["validation_fail"]
    )
    payload = {
        "updated_at": now(),
        "protocol": PROTOCOL,
        "phase": phase,
        "breadth_target": breadth_target,
        "depth_target": depth_target,
        "elite_target": elite_target,
        "runnable_track_count": len(tracks),
        "terminal_track_count": terminal,
        "validation_pass_count": counts["validation_pass"],
        "validation_fail_count": counts["validation_fail"],
        "data_insufficient_count": counts["data_insufficient"],
        "seed_blocked_count": counts["seed_blocked"],
        "searching_count": counts["searching"],
        "total_valid_candidates": total_valid,
        "total_model_attempt_rows": total_attempts,
        "total_crashes": total_crashes,
        "total_duplicates": total_duplicates,
        "total_parameter_only": total_parameter_only,
        "total_too_broad": total_too_broad,
        "total_risk_control_changes": total_risk_changes,
        "all_runnable_tracks_terminal": terminal == len(tracks),
        "blocked_family_count": len(blocked_families),
        "blocked_families": blocked_families,
        "rows": rows,
    }
    save_json(PROGRESS, payload)
    return payload


def rebuild_leaderboard(tracks):
    rows = []
    for track in tracks:
        m = track_meta(track)
        if not m or not m.get("baseline"):
            continue
        b = m["baseline"]
        val = validation_state(track)
        rows.append({
            "track_id": track["id"],
            "family": m.get("family"),
            "exactness": m.get("family_exactness"),
            "target": m.get("target"),
            "market": m.get("market"),
            "profile": m.get("profile"),
            "valid_attempts": m.get("valid", m.get("valid_attempts", 0)),
            "development_score": b.get("score"),
            "development_return_pct": b.get("return_pct"),
            "development_sharpe": b.get("sharpe"),
            "development_max_dd_pct": b.get("max_dd_pct"),
            "development_pf": b.get("pf"),
            "hidden_validation_pass": None if val is None else bool(val.get("guard_ok")),
            "hidden_return_pct": None if val is None else val.get("return_pct"),
            "hidden_sharpe": None if val is None else val.get("sharpe"),
            "hidden_max_dd_pct": None if val is None else val.get("max_dd_pct"),
            "hidden_pf": None if val is None else val.get("pf"),
        })
    rows.sort(
        key=lambda x: (
            1 if x["hidden_validation_pass"] is True else 0,
            float(x["development_score"])
            if x["development_score"] is not None and math.isfinite(float(x["development_score"]))
            else -1e99,
        ),
        reverse=True,
    )
    save_json(STATE / "leaderboard_latest.json", {
        "updated_at": now(),
        "protocol": PROTOCOL,
        "count": len(rows),
        "rows": rows,
    })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters-per-visit", type=int, default=2)
    ap.add_argument("--max-visits", type=int, default=12)
    ap.add_argument("--max-seconds", type=int, default=2400)
    ap.add_argument("--breadth-target", type=int, default=10)
    ap.add_argument("--depth-target", type=int, default=30)
    ap.add_argument("--elite-target", type=int, default=60)
    ap.add_argument("--depth-fraction", type=float, default=0.25)
    ap.add_argument("--elite-fraction", type=float, default=0.20)
    ap.add_argument("--model", default="nvidia/nemotron-3-super-120b-a12b")
    args = ap.parse_args()

    if not os.environ.get("NVIDIA_API_KEY"):
        raise SystemExit("NVIDIA_API_KEY missing")
    if not (0 < args.depth_fraction <= 1 and 0 < args.elite_fraction <= 1):
        raise SystemExit("selection fractions must be in (0,1]")
    if not (1 <= args.breadth_target <= args.depth_target <= args.elite_target):
        raise SystemExit("require breadth <= depth <= elite targets")

    STATE.mkdir(parents=True, exist_ok=True)
    TRACKS.mkdir(parents=True, exist_ok=True)
    tracks = build_tracks()
    if not tracks:
        raise SystemExit("no runnable tracks")

    started = time.monotonic()
    cursor = read_cursor(len(tracks))
    visits = 0

    while visits < args.max_visits and (time.monotonic() - started) < args.max_seconds:
        phase, plan = current_search_plan(
            tracks,
            args.breadth_target,
            args.depth_target,
            args.elite_target,
            args.depth_fraction,
            args.elite_fraction,
        )
        progress = write_progress(
            tracks, phase, args.breadth_target, args.depth_target, args.elite_target
        )
        print(
            f"phase={phase} tracks={len(tracks)} valid_candidates="
            f"{progress['total_valid_candidates']} terminal={progress['terminal_track_count']}"
        )

        if phase == "validation":
            nxt = next_validation_track(tracks, cursor)
            if nxt is None:
                print("ALL RUNNABLE TRACKS TERMINAL; hidden validation phase complete")
                break
            idx, track = nxt
            try:
                validate_track(track)
            except Exception as exc:
                append_cycle({
                    "ts": now(), "track_id": track["id"],
                    "status": "validation_runner_error",
                    "reason": f"{type(exc).__name__}: {str(exc)[:500]}",
                })
                print(f"[validation error] {track['id']}: {exc}", file=sys.stderr)
            cursor = (idx + 1) % len(tracks)
            visits += 1
            continue

        nxt = next_search_track(tracks, plan, cursor)
        if nxt is None:
            # Recompute phase on the next loop; this occurs at a phase boundary.
            continue
        idx, track, target = nxt
        current = track_counts(track)["valid"]
        visit_iters = min(args.iters_per_visit, max(1, target - current))
        try:
            process_track(track, visit_iters, args.model)
        except Exception as exc:
            append_cycle({
                "ts": now(), "track_id": track["id"],
                "status": "runner_error",
                "reason": f"{type(exc).__name__}: {str(exc)[:500]}",
            })
            print(f"[runner error] {track['id']}: {exc}", file=sys.stderr)
        cursor = (idx + 1) % len(tracks)
        visits += 1

    write_cursor(cursor, len(tracks))
    phase, _ = current_search_plan(
        tracks,
        args.breadth_target,
        args.depth_target,
        args.elite_target,
        args.depth_fraction,
        args.elite_fraction,
    )
    progress = write_progress(
        tracks, phase, args.breadth_target, args.depth_target, args.elite_target
    )
    rebuild_leaderboard(tracks)
    print(json.dumps({
        "phase": phase,
        "visits_this_run": visits,
        "valid_candidates": progress["total_valid_candidates"],
        "terminal_tracks": progress["terminal_track_count"],
        "runnable_tracks": progress["runnable_track_count"],
        "validation_pass": progress["validation_pass_count"],
        "validation_fail": progress["validation_fail_count"],
        "data_insufficient": progress["data_insufficient_count"],
        "seed_blocked": progress["seed_blocked_count"],
        "next_cursor": cursor,
    }, indent=2))


if __name__ == "__main__":
    main()
