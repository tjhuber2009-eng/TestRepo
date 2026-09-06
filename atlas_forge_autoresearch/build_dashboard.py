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
ACTIVE_PROTOCOL = "nested_chronological_v3"


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


def development_period_metrics(state_dir, track_id, total_return_pct):
    meta = load_json(Path(state_dir) / "tracks" / str(track_id) / "state_meta.json", {}) or {}
    baseline = meta.get("baseline") or {}
    start = baseline.get("start")
    end = baseline.get("end") or baseline.get("adaptive_development_end")
    try:
        start_dt = datetime.fromisoformat(str(start).replace("Z", "+00:00"))
        end_dt = datetime.fromisoformat(str(end).replace("Z", "+00:00"))
        years = (end_dt - start_dt).total_seconds() / (365.2425 * 86400.0)
    except Exception:
        return {
            "development_start": start,
            "development_end": end,
            "development_years": None,
            "development_cagr_pct": None,
        }
    total = finite(total_return_pct)
    cagr = None
    if years > 0 and total is not None:
        multiple = 1.0 + total / 100.0
        if multiple > 0:
            cagr = (multiple ** (1.0 / years) - 1.0) * 100.0
    return {
        "development_start": start,
        "development_end": end,
        "development_years": round(years, 4),
        "development_cagr_pct": round(cagr, 6) if cagr is not None else None,
    }


def sanitize_json(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: sanitize_json(v) for k, v in value.items()}
    if isinstance(value, list):
        return [sanitize_json(v) for v in value]
    if isinstance(value, tuple):
        return [sanitize_json(v) for v in value]
    return value


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


def normalize_leaderboard(board, state_dir):
    rows = board.get("rows", []) if isinstance(board, dict) else []
    out = []
    for r in rows:
        period = development_period_metrics(
            state_dir,
            r.get("track_id"),
            r.get("development_return_pct"),
        )
        out.append({
            **r,
            **period,
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
    ap.add_argument("--stock-fx-state-dir", default="stock_fx_state")
    ap.add_argument("--v4-state-dir", default="v4_state")
    ap.add_argument("--tournament-state-dir", default="tournament_state")
    ap.add_argument("--runtime-dir", default="dashboard_runtime")
    ap.add_argument("--output-dir", default="continuous_state/dashboard")
    args = ap.parse_args()

    state = HERE / args.state_dir
    stock_fx_state = HERE / args.stock_fx_state_dir
    v4_state = HERE / args.v4_state_dir
    tournament_state = HERE / args.tournament_state_dir
    runtime = HERE / args.runtime_dir
    output = HERE / args.output_dir
    output.mkdir(parents=True, exist_ok=True)

    progress = load_json(state / "progress.json", {}) or {}
    board = load_json(state / "leaderboard_latest.json", {}) or {}
    selections = load_json(state / "search_selections.json", {}) or {}
    cycles = load_jsonl(state / "cycles.jsonl", limit=120)
    tournament = load_json(tournament_state / "tournament-summary.json", None)
    stock_fx_progress = load_json(stock_fx_state / "progress.json", {}) or {}
    stock_fx_board = load_json(stock_fx_state / "leaderboard_latest.json", {}) or {}
    stock_fx_config = load_json(HERE / "stock_fx_config.json", {}) or {}
    stock_fx_plan = load_json(
        HERE / "strategy_library" / "stock_fx_universe_plan.json", {}
    ) or {}
    v4_private = load_json(v4_state / "development-bootstrap.json", None)
    v4_prop = load_json(v4_state / "prop-intraday-bootstrap.json", None)

    # Never render protocol-stale state as the active control room. The v2
    # archive is useful historical evidence, but its scores are not comparable
    # to v3 and must not silently populate the v3 leaderboard before the first
    # v3 continuous cycle has initialized persistent state.
    stale_state = None
    state_protocol = progress.get("protocol")
    board_protocol = board.get("protocol")
    state_is_active = (
        state_protocol == ACTIVE_PROTOCOL and board_protocol == ACTIVE_PROTOCOL
    )
    if not state_is_active:
        stale_state = {
            "progress_protocol": state_protocol,
            "leaderboard_protocol": board_protocol,
            "leaderboard_count": len(board.get("rows", []) or []),
            "progress_updated_at": progress.get("updated_at"),
        }
        progress = {
            "protocol": ACTIVE_PROTOCOL,
            "phase": "initializing",
            "rows": [],
            "runnable_track_count": 0,
            "total_valid_candidates": 0,
            "terminal_track_count": 0,
            "validation_pass_count": 0,
            "validation_fail_count": 0,
            "breadth_target": 10,
            "depth_target": 30,
            "elite_target": 60,
        }
        board = {"protocol": ACTIVE_PROTOCOL, "rows": []}
        selections = {}
        cycles = []

    if tournament and tournament.get("protocol") != ACTIVE_PROTOCOL:
        tournament = None

    continuous_runs = workflow_runs(
        load_json(runtime / "continuous_runs.json", {}) or {}
    )
    v4_runs = workflow_runs(
        load_json(runtime / "v4_runs.json", {}) or {}
    )
    stock_fx_runs = workflow_runs(
        load_json(runtime / "stock_fx_runs.json", {}) or {}
    )
    tournament_runs = workflow_runs(
        load_json(runtime / "tournament_runs.json", {}) or {}
    )
    jobs = tournament_jobs(
        load_json(runtime / "tournament_jobs.json", {}) or {}
    )

    stock_fx_state_is_active = bool(
        stock_fx_progress
        and stock_fx_progress.get("protocol") == ACTIVE_PROTOCOL
        and stock_fx_board.get("protocol") == ACTIVE_PROTOCOL
    )
    stock_fx_phase = next(
        (
            x for x in (stock_fx_plan.get("phases") or [])
            if x.get("id") == stock_fx_plan.get("current_stage")
        ),
        {},
    )
    stock_fx_targets = stock_fx_config.get("targets") or []
    stock_fx_summary = {
        "configured": bool(stock_fx_config),
        "state_is_active": stock_fx_state_is_active,
        "phase": stock_fx_progress.get("phase") or (
            "breadth" if stock_fx_runs and stock_fx_runs[0].get("status") in {"queued", "pending", "in_progress"} else "initializing"
        ),
        "expected_track_count": int(
            stock_fx_phase.get(
                "expected_track_count",
                (stock_fx_config.get("universe_metadata") or {}).get(
                    "expected_track_count", 0
                ),
            ) or 0
        ),
        "target_count": int(
            stock_fx_phase.get("target_count", len(stock_fx_targets)) or 0
        ),
        "stock_target_count": int(
            stock_fx_phase.get(
                "stock_target_count",
                sum(x.get("market") == "stock" for x in stock_fx_targets),
            ) or 0
        ),
        "forex_target_count": int(
            stock_fx_phase.get(
                "forex_target_count",
                sum(x.get("market") == "forex" for x in stock_fx_targets),
            ) or 0
        ),
        "stock_track_count": int(
            stock_fx_phase.get("stock_target_count", 0) or 0
        ) * int(stock_fx_phase.get("stock_family_count", 0) or 0) * 2,
        "forex_track_count": int(
            stock_fx_phase.get("forex_target_count", 0) or 0
        ) * int(stock_fx_phase.get("forex_family_count", 0) or 0) * 2,
        "forex_development_period": stock_fx_phase.get(
            "forex_development_period"
        ),
        "forex_hidden_validation_period": stock_fx_phase.get(
            "forex_hidden_validation_period"
        ),
        "hidden_validation_opened": bool(
            int(stock_fx_progress.get("validation_pass_count", 0) or 0)
            + int(stock_fx_progress.get("validation_fail_count", 0) or 0)
        ),
        "final_oos_opened": False,
    }

    # Full progress rows are useful for searchable track coverage, but scrub
    # JSON non-finite sentinels if any stale track carries them.
    tracks = []
    for r in progress.get("rows", []):
        x = dict(r)
        x["development_score"] = finite(x.get("development_score"))
        x.update(
            development_period_metrics(
                state,
                x.get("track_id"),
                x.get("development_return_pct"),
            )
        )
        tracks.append(x)

    payload = {
        "generated_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "project": "Atlas Forge AUTORESEARCH reconstruction",
        "protocol": ACTIVE_PROTOCOL,
        "state_is_active": state_is_active,
        "stale_state": stale_state,
        "phase": progress.get("phase"),
        "progress": progress,
        "progress_derived": summarize_progress(progress),
        "leaderboard": normalize_leaderboard(board, state),
        "tracks": tracks,
        "selections": selections,
        "recent_cycles": cycles,
        "tournament": tournament,
        "expansion": {
            **stock_fx_summary,
            "progress": stock_fx_progress if stock_fx_state_is_active else {},
            "progress_derived": (
                summarize_progress(stock_fx_progress)
                if stock_fx_state_is_active else {}
            ),
            "leaderboard": (
                normalize_leaderboard(stock_fx_board, stock_fx_state)
                if stock_fx_state_is_active else []
            ),
            "metadata": stock_fx_config.get("universe_metadata") or {},
        },
        "v4": {
            "available": bool(v4_private or v4_prop),
            "private": v4_private,
            "prop": v4_prop,
        },
        "workflow": {
            "continuous_runs": continuous_runs,
            "stock_fx_runs": stock_fx_runs,
            "v4_runs": v4_runs,
            "tournament_runs": tournament_runs,
            "tournament_jobs": jobs,
        },
        "safeguards": {
            "hidden_validation_opened": bool(
                int(progress.get("validation_pass_count", 0) or 0)
                + int(progress.get("validation_fail_count", 0) or 0)
                or stock_fx_summary["hidden_validation_opened"]
                or (v4_private or {}).get("hidden_validation_opened")
                or (v4_prop or {}).get("hidden_validation_opened")
            ),
            "final_oos_start": "2023-01-01",
            "final_oos_opened": bool(
                (v4_private or {}).get("final_oos_opened")
                or (v4_prop or {}).get("final_oos_opened")
            ),
            "prop_dd_cap_pct": 10,
            "private_dd_cap_pct": 32,
            "cost_stress_multiplier": 2.0,
            "protocol": ACTIVE_PROTOCOL,
            "protocol_stale_state_detected": not state_is_active,
        },
    }

    payload = sanitize_json(payload)
    data_text = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    (output / "data.json").write_text(data_text, encoding="utf-8")
    for name in ["index.html", "styles.css", "app.js"]:
        shutil.copy2(SRC / name, output / name)

    # Also emit one genuinely self-contained file for local use. Browsers may
    # block fetch("data.json") and external sibling CSS/JS when opened via
    # file://, so embed all three resources directly.
    html = (SRC / "index.html").read_text(encoding="utf-8")
    css = (SRC / "styles.css").read_text(encoding="utf-8")
    js = (SRC / "app.js").read_text(encoding="utf-8")
    html = html.replace(
        '<link rel="stylesheet" href="styles.css">',
        "<style>\n" + css + "\n</style>",
    )
    embedded = json.dumps(payload, separators=(",", ":"), ensure_ascii=False)
    embedded = embedded.replace("</", "<\\/")
    html = html.replace(
        '<script src="app.js"></script>',
        "<script>window.__AUTORESEARCH_DATA__=" + embedded + ";</script>\n"
        "<script>\n" + js + "\n</script>",
    )
    (output / "dashboard.html").write_text(html, encoding="utf-8")

    print(f"dashboard -> {output}")
    print(
        f"phase={payload['phase']} valid={progress.get('total_valid_candidates',0)} "
        f"breadth={payload['progress_derived']['breadth_pct']}%"
    )


if __name__ == "__main__":
    main()
