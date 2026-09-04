"""Persistent breadth-first continuous AUTORESEARCH runner.

Each scheduled invocation advances a deterministic cursor through the Cartesian
product of researched runnable strategy families, enabled markets, and the two
risk profiles. Every track keeps its own champion, baseline, result ledger and
keepers under continuous_state/.
"""

import argparse
import csv
import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REGISTRY = HERE / "strategy_library" / "registry.json"
CONFIG = HERE / "continuous_config.json"
STATE = HERE / "continuous_state"
TRACKS = STATE / "tracks"
CURSOR = STATE / "cursor.json"
LEDGER = STATE / "cycles.jsonl"
PROGRESS = STATE / "first_pass_progress.json"

RUNTIME_FILES = [
    "baseline.json",
    "last_run.json",
    "results.tsv",
    "proposal.txt",
    "STOP",
    "strategy_best.py",
]
PROTOCOL = "chronological_robust_v1"


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
    raw = "__".join(parts)
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", raw)


def build_tracks():
    registry = load_json(REGISTRY)
    config = load_json(CONFIG)
    families = [x for x in registry["families"] if x.get("status") == "runnable"]
    targets = [x for x in config["targets"] if x.get("enabled")]
    profiles = config["profiles"]

    tracks = []
    for family in sorted(families, key=lambda x: x["id"]):
        allowed = set(family.get("markets", []))
        for target in sorted(targets, key=lambda x: x["id"]):
            if allowed and target["market"] not in allowed:
                continue
            for profile_name in ["prop", "private"]:
                p = profiles[profile_name]
                tracks.append({
                    "family": family,
                    "target": target,
                    "profile_name": profile_name,
                    "profile": p,
                    "id": slug(family["id"], target["id"], profile_name),
                })
    return tracks


def read_cursor(total):
    if not CURSOR.exists():
        return 0
    x = load_json(CURSOR)
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
    if not path.exists():
        return {"attempts": 0, "valid": 0, "kept": 0, "rejected": 0, "crashes": 0}
    attempts = valid = kept = rejected = crashes = 0
    with path.open(encoding="utf-8") as f:
        next(f, None)
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            attempts += 1
            verdict = parts[2]
            if verdict == "KEPT":
                kept += 1
                valid += 1
            elif verdict == "REJECTED":
                rejected += 1
                valid += 1
            elif verdict == "CRASH":
                crashes += 1
    return {
        "attempts": attempts,
        "valid": valid,
        "kept": kept,
        "rejected": rejected,
        "crashes": crashes,
    }


def track_test_status(track, required_valid_attempts):
    track_dir = TRACKS / track["id"]
    meta_path = track_dir / "state_meta.json"
    if not meta_path.exists():
        return {"complete": False, "status": "pending", "valid": 0, "attempts": 0}
    try:
        meta = load_json(meta_path)
    except Exception:
        return {"complete": False, "status": "corrupt_state", "valid": 0, "attempts": 0}
    if meta.get("protocol") != PROTOCOL:
        return {"complete": False, "status": "stale_protocol", "valid": 0, "attempts": 0}
    if meta.get("status") == "seed_blocked":
        return {
            "complete": True,
            "status": "seed_rejected",
            "valid": 0,
            "attempts": 0,
            "reason": meta.get("reason", ""),
        }
    counts = result_counts_at(track_dir / "results.tsv")
    return {
        "complete": counts["valid"] >= required_valid_attempts,
        "status": "validated" if counts["valid"] >= required_valid_attempts else "researching",
        "valid": counts["valid"],
        "attempts": counts["attempts"],
        "crashes": counts["crashes"],
    }


def select_incomplete_tracks(tracks, start, batch_size, required_valid_attempts):
    selected = []
    n = len(tracks)
    if not n:
        return selected
    for offset in range(n):
        idx = (start + offset) % n
        track = tracks[idx]
        status = track_test_status(track, required_valid_attempts)
        if status["complete"]:
            continue
        selected.append((idx, track, status))
        if len(selected) >= batch_size:
            break
    return selected


def write_progress(tracks, required_valid_attempts):
    registry = load_json(REGISTRY)
    blocked_families = [
        {
            "id": x["id"],
            "exactness": x.get("exactness"),
            "origin": x.get("origin"),
            "requires": x.get("requires", []),
        }
        for x in registry["families"]
        if x.get("status") != "runnable"
    ]
    rows = []
    validated = seed_rejected = pending = crashes = 0
    for track in tracks:
        s = track_test_status(track, required_valid_attempts)
        crashes += int(s.get("crashes", 0) or 0)
        if s["status"] == "validated":
            validated += 1
        elif s["status"] == "seed_rejected":
            seed_rejected += 1
        else:
            pending += 1
        rows.append({
            "track_id": track["id"],
            "family": track["family"]["id"],
            "target": track["target"]["id"],
            "profile": track["profile_name"],
            "status": s["status"],
            "valid_attempts": s.get("valid", 0),
            "attempts": s.get("attempts", 0),
        })
    complete = validated + seed_rejected
    payload = {
        "updated_at": now(),
        "protocol": PROTOCOL,
        "required_valid_attempts_per_viable_track": required_valid_attempts,
        "runnable_track_count": len(tracks),
        "completed_track_count": complete,
        "validated_track_count": validated,
        "seed_rejected_track_count": seed_rejected,
        "pending_track_count": pending,
        "total_crashes_recorded": crashes,
        "runnable_first_pass_complete": pending == 0,
        "blocked_family_count": len(blocked_families),
        "blocked_families": blocked_families,
        "rows": rows,
    }
    save_json(PROGRESS, payload)
    return payload


def target_env(track):
    t = track["target"]
    p = track["profile"]
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
        "AUTORESEARCH_MIN_TRADES": "20",
        "AUTORESEARCH_MIN_FOLD_TRADES": "2",
        "AUTORESEARCH_MIN_ACTIVE_FOLDS": "3",
        "AUTORESEARCH_MIN_FOLD_BARS": "180",
        "AUTORESEARCH_VOL_BAND": "0.20",
        "AUTORESEARCH_IS_START": "2017-08-17",
        "AUTORESEARCH_IS_END": "2022-12-31",
    })
    return env


def prepare_data(track):
    t = track["target"]
    path = HERE / "data" / f"{t['id']}_1d.csv"
    if path.exists() and path.stat().st_size > 1000:
        return
    run([
        sys.executable,
        "prepare_market_data.py",
        "--source", t["source"],
        "--symbol", t["symbol"],
        "--id", t["id"],
        "--start", "2017-08-17",
        "--end", "2022-12-31",
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

    for name in ["baseline.json", "last_run.json", "results.tsv", "strategy_best.py"]:
        src = track_dir / name
        if not src.exists():
            return False
        shutil.copy2(src, HERE / name)
    shutil.copy2(track_dir / "strategy_best.py", HERE / "strategy.py")

    for d in ["keepers"]:
        src = track_dir / d
        dst = HERE / d
        if src.exists():
            shutil.copytree(src, dst, dirs_exist_ok=True)
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

    last = None
    for attempt in range(7):
        for name in ["baseline.json", "last_run.json"]:
            p = HERE / name
            if p.exists():
                p.unlink()
        run([sys.executable, "robust_harness.py", "--is"], env=env)
        last = load_json(HERE / "last_run.json")

        structural_failures = [
            x for x in last.get("audit_guard_details", [])
            if "drawdown" not in x and "volatility" not in x
        ]
        if structural_failures:
            reason = "; ".join(structural_failures)
            save_json(track_dir / "state_meta.json", {
                "status": "seed_blocked",
                "reason": reason,
                "protocol": PROTOCOL,
                "family": track["family"]["id"],
                "target": track["target"]["id"],
                "profile": track["profile_name"],
                "updated_at": now(),
            })
            return False, reason

        dds = [abs(float(last["max_dd_pct"]))]
        dds += [abs(float(x["max_dd_pct"])) for x in last.get("folds", [])]
        worst_dd = max(dds) if dds else 0.0

        if worst_dd <= limit and last.get("guard_ok"):
            break

        if worst_dd <= 0:
            reason = "seed failed robustness guard for non-drawdown reason"
            save_json(track_dir / "state_meta.json", {
                "status": "seed_blocked",
                "reason": reason,
                "protocol": PROTOCOL,
                "updated_at": now(),
            })
            return False, reason

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
        factor = min(0.88 * limit / worst_dd, 0.93)
        new = max(0.003, old * factor)
        (HERE / "strategy.py").write_text(
            replace_numeric_assignment(source, "vol_target", new),
            encoding="utf-8",
        )
        print(
            f"[seed risk] {track['id']} worst_dd={worst_dd:.2f}% "
            f"vol_target {old:.6f}->{new:.6f}"
        )
    else:
        reason = "could not normalize seed below chronological DD constraints"
        save_json(track_dir / "state_meta.json", {
            "status": "seed_blocked",
            "reason": reason,
            "protocol": PROTOCOL,
            "updated_at": now(),
        })
        return False, reason

    # Freeze only after the seed passes every chronological robustness gate.
    run([sys.executable, "robust_harness.py", "--is", "--set-baseline"], env=env)
    shutil.copy2(HERE / "strategy.py", HERE / "strategy_best.py")
    shutil.copy2(HERE / "strategy.py", HERE / "keepers" / "000_seed.py")
    return True, "ok"


def result_row_count():
    return result_counts_at(HERE / "results.tsv")["attempts"]


def save_track_state(track, track_dir, status="active", reason=""):
    track_dir.mkdir(parents=True, exist_ok=True)
    for name in ["baseline.json", "last_run.json", "results.tsv", "strategy_best.py"]:
        src = HERE / name
        if src.exists():
            shutil.copy2(src, track_dir / name)
    if (HERE / "keepers").exists():
        dst = track_dir / "keepers"
        if dst.exists():
            shutil.rmtree(dst)
        shutil.copytree(HERE / "keepers", dst)

    baseline = {}
    if (HERE / "baseline.json").exists():
        baseline = load_json(HERE / "baseline.json")
    counts = result_counts_at(HERE / "results.tsv")
    meta = {
        "status": status,
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
        "attempts": counts["attempts"],
        "valid_attempts": counts["valid"],
        "kept": counts["kept"],
        "rejected": counts["rejected"],
        "crashes": counts["crashes"],
        "baseline": baseline,
        "updated_at": now(),
    }
    save_json(track_dir / "state_meta.json", meta)
    return meta


def append_cycle(record):
    STATE.mkdir(parents=True, exist_ok=True)
    with LEDGER.open("a", encoding="utf-8") as f:
        f.write(json.dumps(record, sort_keys=True) + "\n")


def rebuild_leaderboard():
    rows = []
    for meta_path in TRACKS.glob("*/state_meta.json"):
        try:
            m = load_json(meta_path)
        except Exception:
            continue
        if m.get("status") != "active" or not m.get("baseline"):
            continue
        b = m["baseline"]
        rows.append({
            "track_id": meta_path.parent.name,
            "family": m.get("family"),
            "exactness": m.get("family_exactness"),
            "target": m.get("target"),
            "market": m.get("market"),
            "profile": m.get("profile"),
            "attempts": m.get("attempts", 0),
            "score": b.get("score"),
            "return_pct": b.get("return_pct"),
            "sharpe": b.get("sharpe"),
            "max_dd_pct": b.get("max_dd_pct"),
            "pf": b.get("pf"),
            "active_folds": b.get("active_folds"),
            "worst_fold_k": b.get("worst_fold_k"),
        })
    rows.sort(key=lambda x: float(x["score"]) if x["score"] is not None else -1e99, reverse=True)
    save_json(STATE / "leaderboard_latest.json", {
        "updated_at": now(),
        "protocol": PROTOCOL,
        "count": len(rows),
        "rows": rows,
    })


def process_track(track, iters, model):
    print("\n" + "=" * 88)
    print(
        f"TRACK {track['id']} family={track['family']['id']} "
        f"market={track['target']['id']} profile={track['profile_name']}"
    )
    print("=" * 88, flush=True)

    track_dir = TRACKS / track["id"]
    meta_path = track_dir / "state_meta.json"
    if meta_path.exists():
        old_meta = load_json(meta_path)
        if old_meta.get("status") == "seed_blocked" and old_meta.get("protocol") == PROTOCOL:
            record = {
                "ts": now(), "track_id": track["id"], "status": "skipped_seed_blocked",
                "reason": old_meta.get("reason", ""),
            }
            append_cycle(record)
            print(f"[skip] {old_meta.get('reason')}")
            return

    clean_runtime()
    prepare_data(track)
    env = target_env(track)

    restored = restore_state(track_dir)
    if not restored:
        ok, reason = initialize_track(track, track_dir, env)
        if not ok:
            append_cycle({
                "ts": now(), "track_id": track["id"], "status": "seed_blocked",
                "reason": reason,
            })
            print(f"[seed blocked] {reason}")
            return

    before = result_row_count()
    proc = run(
        [sys.executable, "loop.py", "--iters", str(iters), "--model", model],
        env=env,
        check=False,
    )
    after = result_row_count()
    status = "active" if proc.returncode == 0 else "loop_error"
    reason = "" if proc.returncode == 0 else f"loop exit {proc.returncode}"
    meta = save_track_state(track, track_dir, status="active", reason=reason)

    b = meta.get("baseline", {})
    append_cycle({
        "ts": now(),
        "track_id": track["id"],
        "family": track["family"]["id"],
        "target": track["target"]["id"],
        "profile": track["profile_name"],
        "status": status,
        "iterations_before": before,
        "iterations_after": after,
        "score": b.get("score"),
        "return_pct": b.get("return_pct"),
        "max_dd_pct": b.get("max_dd_pct"),
        "sharpe": b.get("sharpe"),
        "pf": b.get("pf"),
    })


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--batch-size", type=int, default=4)
    ap.add_argument("--iters-per-visit", type=int, default=2)
    ap.add_argument("--required-valid-attempts", type=int, default=10)
    ap.add_argument("--model", default="nvidia/nemotron-3-super-120b-a12b")
    args = ap.parse_args()

    if args.batch_size < 1 or args.iters_per_visit < 1 or args.required_valid_attempts < 1:
        raise SystemExit("batch/iteration targets must be positive")

    if not os.environ.get("NVIDIA_API_KEY"):
        raise SystemExit("NVIDIA_API_KEY missing")

    STATE.mkdir(parents=True, exist_ok=True)
    TRACKS.mkdir(parents=True, exist_ok=True)
    tracks = build_tracks()
    if not tracks:
        raise SystemExit("no runnable continuous tracks")

    progress = write_progress(tracks, args.required_valid_attempts)
    if progress["runnable_first_pass_complete"]:
        print(
            f"ALL RUNNABLE TRACKS TESTED: {progress['completed_track_count']}/"
            f"{progress['runnable_track_count']} tracks complete with "
            f">={args.required_valid_attempts} valid NVIDIA candidates per viable track."
        )
        print(
            f"Blocked special-engine families still catalogued: "
            f"{progress['blocked_family_count']}"
        )
        rebuild_leaderboard()
        return

    start = read_cursor(len(tracks))
    selected = select_incomplete_tracks(
        tracks, start, args.batch_size, args.required_valid_attempts
    )
    print(
        f"continuous universe tracks={len(tracks)} cursor={start} "
        f"pending={progress['pending_track_count']} batch={len(selected)} "
        f"iters_per_visit={args.iters_per_visit} "
        f"required_valid={args.required_valid_attempts}"
    )

    last_index = start
    for idx, track, status_before in selected:
        last_index = idx
        try:
            missing = max(1, args.required_valid_attempts - int(status_before.get("valid", 0)))
            visit_iters = min(args.iters_per_visit, missing)
            process_track(track, visit_iters, args.model)
        except Exception as exc:
            append_cycle({
                "ts": now(),
                "track_id": track["id"],
                "status": "runner_error",
                "reason": f"{type(exc).__name__}: {str(exc)[:500]}",
            })
            print(f"[runner error] {track['id']}: {exc}", file=sys.stderr)

    write_cursor(last_index + 1, len(tracks))
    rebuild_leaderboard()
    progress = write_progress(tracks, args.required_valid_attempts)
    print(
        f"progress={progress['completed_track_count']}/{progress['runnable_track_count']} "
        f"pending={progress['pending_track_count']} "
        f"validated={progress['validated_track_count']} "
        f"seed_rejected={progress['seed_rejected_track_count']}"
    )
    print(f"next_cursor={(last_index + 1) % len(tracks)}")


if __name__ == "__main__":
    main()
