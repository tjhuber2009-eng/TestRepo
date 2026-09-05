"""Build the private AUTORESEARCH project dashboard snapshot.

The generated dashboard is static and self-contained except for data.json.
It reads only already-produced research/tournament state and workflow metadata;
it never opens hidden validation or 2023+ market data.
"""

import argparse
import json
import math
import shutil
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
SRC = HERE / "dashboard_src"


def load_json(path, default=None):
    path = Path(path)
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return default


def load_jsonl(path, limit=120):
    path = Path(path)
    if not path.exists():
        return []
    rows = []
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                rows.append(json.loads(line))
            except Exception:
                continue
    return rows[-limit:]


def finite(v):
    try:
        x = float(v)
    except Exception:
        return None
    return x if math.isfinite(x) else None


def workflow_runs(payload, limit=8):
    if not isinstance(payload, dict):
        return []
    out = []
    for r in payload.get("workflow_runs", [])[:limit]:
        out.append({
            "id": r.get("id"),
            "name": r.get("name"),
            "status": r.get("status"),
            "conclusion": r.get("conclusion"),
            "event": r.get("event"),
            "created_at": r.get("created_at"),
            "updated_at": r.get("updated_at"),
            "html_url": r.get("html_url"),
            "run_number": r.get("run_number"),
        })
    return out


def tournament_jobs(payload):
    if not isinstance(payload, dict):
        return []
    out = []
    for j in payload.get("jobs", []):
        name = j.get("name", "")
        if not name.startswith("model ("):
            continue
        current = [
            s.get("name")
            for s in j.get("steps", [])
            if s.get("status") == "in_progress"
        ]
        out.append({
            "id": j.get("id"),
            "name": name,
            "status": j.get("status"),
            "conclusion": j.get("conclusion"),
            "started_at": j.get("started_at"),
            "completed_at": j.get("completed_at"),
            "current_step": current[0] if current else None,
        })
    return out


def summarize_progress(progress):
    rows = progress.get("rows", []) if isinstance(progress, dict) else []
    touched = sum(int(r.get("attempts", 0) or 0) > 0 for r in rows)
    valid1 = sum(int(r.get("valid_attempts", 0) or 0) >= 1 for r in rows)
    valid2 = sum(int(r.get("valid_attempts", 0) or 0) >= 2 for r in rows)
    valid5 = sum(int(r.get("valid_attempts", 0) or 0) >= 5 for r in rows)
    valid10 = sum(int(r.get("valid_attempts", 0) or 0) >= 10 for r in rows)
    runnable = int(progress.get("runnable_track_count", len(rows)) or 0)
    breadth_target = int(progress.get("breadth_target", 10) or 10)
    depth_target = int(progress.get("depth_target", 30) or 30)
    elite_target = int(progress.get("elite_target", 60) or 60)
    valid = int(progress.get("total_valid_candidates", 0) or 0)
    breadth_total = runnable * breadth_target
    return {
        "touched_tracks": touched,
        "tracks_valid_ge_1": valid1,
        "tracks_valid_ge_2": valid2,
        "tracks_valid_ge_5": valid5,
        "tracks_valid_ge_10": valid10,
        "breadth_total_candidates": breadth_total,
        "breadth_pct": round(100 * valid / breadth_total, 3) if breadth_total else 0,
        "breadth_target": breadth_target,
        "depth_target": depth_target,
        "elite_target": elite_target,
    }


def normalize_leaderboard(board):
    rows = board.get("rows", []) if isinstance(board, dict) else []
    out = []
    for r in rows:
        out.append({
            **r,
            "development_score": finite(r.get("development_score")),
            "development_return_pct": finite(r.get("development_return_pct")),
            "development_sharpe": finite(r.get("development_sharpe")),
            "development_pf": finite(r.get("development_pf")),
            "development_max_dd_pct": finite(r.get("development_max_dd_pct")),
            "hidden_return_pct": finite(r.get("hidden_return_pct")),
            "hidden_sharpe": finite(r.get("hidden_sharpe")),
            "hidden_pf": finite(r.get("hidden_pf")),
            "hidden_max_dd_pct": finite(r.get("hidden_max_dd_pct")),
        })
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--state-dir", default="continuous_state")
    ap.add_argument("--tournament-state-dir", default="tournament_state")
    ap.add_argument("--runtime-dir", default="dashboard_runtime")
    ap.add_argument("--output-dir", default="continuous_state/dashboard")
    args = ap.parse_args()

    state = HERE / args.state_dir
    tournament_state = HERE / args.tournament_state_dir
    runtime = HERE / args.runtime_dir
    output = HERE / args.output_dir
    output.mkdir(parents=True, exist_ok=True)

    progress = load_json(state / "progress.json", {}) or {}
    board = load_json(state / "leaderboard_latest.json", {}) or {}
    selections = load_json(state / "search_selections.json", {}) or {}
    cycles = load_jsonl(state / "cycles.jsonl", limit=120)
    tournament = load_json(tournament_state / "tournament-summary.json", None)

    continuous_runs = workflow_runs(
        load_json(runtime / "continuous_runs.json", {}) or {}
    )
    tournament_runs = workflow_runs(
        load_json(runtime / "tournament_runs.json", {}) or {}
    )
    jobs = tournament_jobs(
        load_json(runtime / "tournament_jobs.json", {}) or {}
    )

    # Full progress rows are useful for searchable track coverage, but scrub
    # JSON non-finite sentinels if any stale track carries them.
    tracks = []
    for r in progress.get("rows", []):
        x = dict(r)
        x["development_score"] = finite(x.get("development_score"))
        tracks.append(x)

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "project": "Moon Dev AUTORESEARCH reconstruction",
        "protocol": progress.get("protocol"),
        "phase": progress.get("phase"),
        "progress": progress,
        "progress_derived": summarize_progress(progress),
        "leaderboard": normalize_leaderboard(board),
        "tracks": tracks,
        "selections": selections,
        "recent_cycles": cycles,
        "tournament": tournament,
        "workflow": {
            "continuous_runs": continuous_runs,
            "tournament_runs": tournament_runs,
            "tournament_jobs": jobs,
        },
        "safeguards": {
            "hidden_validation_opened": bool(
                int(progress.get("validation_pass_count", 0) or 0)
                + int(progress.get("validation_fail_count", 0) or 0)
            ),
            "final_oos_start": "2023-01-01",
            "final_oos_opened": False,
            "prop_dd_cap_pct": 10,
            "private_dd_cap_pct": 32,
            "cost_stress_multiplier": 2.0,
            "protocol": progress.get("protocol"),
        },
    }

    (output / "data.json").write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    for name in ["index.html", "styles.css", "app.js"]:
        shutil.copy2(SRC / name, output / name)

    print(f"dashboard -> {output}")
    print(
        f"phase={payload['phase']} valid={progress.get('total_valid_candidates',0)} "
        f"breadth={payload['progress_derived']['breadth_pct']}%"
    )


if __name__ == "__main__":
    main()
