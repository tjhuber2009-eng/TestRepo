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
import hashlib
import json
import math
import os
import random
import re

import overfit_diagnostics
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
REGISTRY = HERE / "strategy_library" / "registry.json"
CONFIG = HERE / "continuous_config.json"
UNIVERSE_PLAN = HERE / "strategy_library" / "universe_plan.json"
STATE = HERE / "continuous_state"
TRACKS = STATE / "tracks"
CURSOR = STATE / "cursor.json"
LEDGER = STATE / "cycles.jsonl"
PROGRESS = STATE / "progress.json"
SELECTIONS = STATE / "search_selections.json"
LEADERBOARD = STATE / "leaderboard_latest.json"
TOURNAMENT_STATE = HERE / "tournament_state" / "tournament-summary.json"
DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b"

RUNTIME_FILES = [
    "baseline.json",
    "last_run.json",
    "validation_run.json",
    "lookahead_audit.json",
    "results.tsv",
    "proposal.txt",
    "STOP",
    "strategy_best.py",
    "seen_hashes.json",
    "experiments.jsonl",
]
PROTOCOL = "nested_chronological_v3"


def _inside_here(relative):
    path = (HERE / relative).resolve()
    try:
        path.relative_to(HERE.resolve())
    except ValueError as exc:
        raise RuntimeError(f"runtime path escapes project root: {relative}") from exc
    return path


def configure_paths(
    config="continuous_config.json",
    state_dir="continuous_state",
    universe_plan="strategy_library/universe_plan.json",
):
    """Select an isolated research universe while preserving legacy defaults."""
    global CONFIG, UNIVERSE_PLAN, STATE, TRACKS, CURSOR, LEDGER, PROGRESS
    global SELECTIONS, LEADERBOARD
    CONFIG = _inside_here(config)
    UNIVERSE_PLAN = _inside_here(universe_plan)
    STATE = _inside_here(state_dir)
    TRACKS = STATE / "tracks"
    CURSOR = STATE / "cursor.json"
    LEDGER = STATE / "cycles.jsonl"
    PROGRESS = STATE / "progress.json"
    SELECTIONS = STATE / "search_selections.json"
    LEADERBOARD = STATE / "leaderboard_latest.json"


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


def json_safe(value):
    """Convert non-finite numeric sentinels to JSON null recursively."""
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [json_safe(v) for v in value]
    return value


def save_json(path, obj):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(
        json.dumps(json_safe(obj), indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
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


def load_universe_plan():
    if not UNIVERSE_PLAN.exists():
        return {
            "current_stage": "legacy",
            "hidden_validation_policy": "legacy",
            "phases": [],
        }
    return load_json(UNIVERSE_PLAN)


def current_universe_stage():
    return str(load_universe_plan().get("current_stage", "legacy"))


def hidden_validation_allowed_by_universe_plan():
    return current_universe_stage() == "hidden_validation"


def assert_universe_lock(tracks):
    """Fail closed when a configured frozen universe changes track count."""
    plan = load_universe_plan()
    stage = str(plan.get("current_stage", ""))
    phase = next(
        (x for x in plan.get("phases", []) if x.get("id") == stage),
        {},
    )
    if "expected_track_count" not in phase:
        return
    expected = int(phase["expected_track_count"])
    if len(tracks) != expected:
        raise RuntimeError(
            f"{stage or 'configured'} universe lock violated: "
            f"expected {expected} tracks, got {len(tracks)}"
        )



def balanced_market_track_order(tracks):
    """Interleave market groups proportionally without changing track membership."""
    groups = {}
    market_order = []
    for track in tracks:
        market = track["target"]["market"]
        if market not in groups:
            groups[market] = []
            market_order.append(market)
        groups[market].append(track)
    if len(groups) <= 1:
        return list(tracks)

    emitted = {market: 0 for market in market_order}
    ordered = []
    while len(ordered) < len(tracks):
        available = [
            market for market in market_order
            if emitted[market] < len(groups[market])
        ]
        market = min(
            available,
            key=lambda name: (
                emitted[name] / len(groups[name]),
                market_order.index(name),
            ),
        )
        ordered.append(groups[market][emitted[market]])
        emitted[market] += 1
    return ordered


def build_tracks():
    registry = load_json(REGISTRY)
    config = load_json(CONFIG)
    families = [x for x in registry["families"] if x.get("status") == "runnable"]
    targets = [x for x in config["targets"] if x.get("enabled")]
    profiles = config["profiles"]
    family_allowlists = {
        market: set(ids)
        for market, ids in (config.get("family_allowlist_by_market") or {}).items()
    }
    tracks = []
    for family in sorted(families, key=lambda x: (int(x.get("priority", 50)), x["id"])):
        allowed = set(family.get("markets", []))
        for target in sorted(targets, key=lambda x: x["id"]):
            if allowed and target["market"] not in allowed:
                continue
            allowlist = family_allowlists.get(target["market"])
            if allowlist is not None and family["id"] not in allowlist:
                continue
            for profile_name in ["prop", "private"]:
                tracks.append({
                    "family": family,
                    "target": target,
                    "profile_name": profile_name,
                    "profile": profiles[profile_name],
                    "id": slug(family["id"], target["id"], profile_name),
                })
    if bool(config.get("balanced_market_scheduling", False)):
        tracks = balanced_market_track_order(tracks)
    assert_universe_lock(tracks)
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
        "attempts": 0, "valid": 0, "backtested": 0,
        "guard_passed": 0, "kept": 0, "rejected": 0,
        "crashes": 0, "duplicates": 0, "parameter_only": 0, "too_broad": 0,
        "risk_control_change": 0,
    }
    if not path.exists():
        return counts
    with path.open(encoding="utf-8") as f:
        header_line = next(f, "").rstrip("\n")
        header = header_line.split("\t") if header_line else []
        index = {name: i for i, name in enumerate(header)}
        for line in f:
            parts = line.rstrip("\n").split("\t")
            if len(parts) < 3:
                continue
            counts["attempts"] += 1
            verdict = parts[index.get("verdict", 2)]
            if verdict == "KEPT":
                counts["kept"] += 1
                counts["valid"] += 1
                counts["backtested"] += 1
                counts["guard_passed"] += 1
            elif verdict == "REJECTED":
                counts["rejected"] += 1
                counts["valid"] += 1
                counts["backtested"] += 1
                reason_i = index.get("guard")
                reason = parts[reason_i] if reason_i is not None and reason_i < len(parts) else ""
                if not str(reason).startswith("guard:"):
                    counts["guard_passed"] += 1
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


def stored_target_source(meta):
    """Read source identity from new metadata or the persisted data manifest."""
    if not isinstance(meta, dict):
        return None
    direct = meta.get("target_source")
    if direct:
        return str(direct)
    manifest = meta.get("data_manifest") or {}
    source = manifest.get("source")
    return None if not source else str(source)


def track_state_identity_matches(track, meta):
    """Reject evidence produced from a different configured provider path."""
    if not isinstance(meta, dict) or meta.get("protocol") != PROTOCOL:
        return False
    stored_source = stored_target_source(meta)
    configured_source = str(track.get("target", {}).get("source") or "")
    # Legacy state without a stored source is left intact. Current state writes
    # source identity explicitly, while old manifests normally provide it.
    if stored_source and configured_source and stored_source != configured_source:
        return False
    stored_symbol = meta.get("symbol")
    configured_symbol = track.get("target", {}).get("symbol")
    if stored_symbol and configured_symbol and str(stored_symbol) != str(configured_symbol):
        return False
    return True


def track_meta(track):
    p = TRACKS / track["id"] / "state_meta.json"
    if not p.exists():
        return None
    try:
        m = load_json(p)
    except Exception:
        return None
    if not track_state_identity_matches(track, m):
        return None
    return m


def track_counts(track):
    track_dir = TRACKS / track["id"]
    meta_path = track_dir / "state_meta.json"
    if meta_path.exists():
        try:
            meta = load_json(meta_path)
        except Exception:
            meta = {}
        if not track_state_identity_matches(track, meta):
            return result_counts_at(Path("__missing_stale_protocol_results__"))
    return result_counts_at(track_dir / "results.tsv")


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


def development_overfit(track):
    p = TRACKS / track["id"] / "experiments.jsonl"
    try:
        return overfit_diagnostics.track_pbo(p)
    except Exception:
        return None


def development_selection_score(track):
    score = development_score(track)
    if not math.isfinite(score):
        return score
    diag = development_overfit(track)
    if not diag:
        return score
    excess = max(0.0, float(diag.get("pbo", 0.0)) - 0.50)
    penalty = min(0.25, 0.50 * excess)
    return score * (1.0 - penalty) if score >= 0 else score * (1.0 + penalty)


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
        "AUTORESEARCH_EXTREME_COST_STRESS_MULT": str(
            cfg.get("protocol", {}).get("extreme_cost_stress_multiplier", 3.0)
        ),
        "AUTORESEARCH_EVOMIND_ENABLED": "1",
        "AUTORESEARCH_EVOMIND_DB": str(STATE / "evomind.db"),
        "AUTORESEARCH_YOUTUBE_INTELLIGENCE_ENABLED": "1",
        "AUTORESEARCH_YOUTUBE_INTELLIGENCE_DB": str(
            STATE / "youtube_intelligence.db"
        ),
        "AUTORESEARCH_YOUTUBE_INTELLIGENCE_FEED": str(
            STATE / "youtube_intelligence_feed.jsonl"
        ),
        "AUTORESEARCH_YOUTUBE_PUBLISHED_CUTOFF": development_end(track),
        "AUTORESEARCH_TRACK_ID": track["id"],
        "AUTORESEARCH_PROTOCOL": PROTOCOL,
        "AUTORESEARCH_BOOTSTRAP_REPS": str(
            cfg.get("protocol", {}).get("bootstrap_reps", 500)
        ),
        "AUTORESEARCH_IS_START": t.get("start", "2017-08-17"),
        "AUTORESEARCH_VALIDATION_START": t.get("validation_start", "2021-01-01"),
        "AUTORESEARCH_VALIDATION_END": t.get("validation_end", "2022-12-31"),
    })
    return env


def development_end(track):
    """Last bar adaptive research may physically possess for this track."""
    t = track["target"]
    validation_start = datetime.strptime(
        t.get("validation_start", "2021-01-01"), "%Y-%m-%d"
    )
    return (validation_start - timedelta(days=1)).strftime("%Y-%m-%d")


def prepare_data(track, include_validation=False):
    """Prepare only the chronology the current stage is allowed to possess.

    Adaptive research and model tournaments get a physically development-only
    CSV. Hidden pre-OOS validation is downloaded/extended only after the global
    elite set is frozen and validate_track explicitly opens it.
    """
    t = track["target"]
    data_dir_name = "validation_data" if include_validation else "data"
    data_dir = HERE / data_dir_name
    path = data_dir / f"{t['id']}_1d.csv"
    manifest = data_dir / f"{t['id']}_1d.manifest.json"
    wanted_start = t.get("start", "2017-08-17")
    wanted_end = (
        t.get("validation_end", "2022-12-31")
        if include_validation
        else development_end(track)
    )
    if wanted_end >= "2023-01-01":
        raise RuntimeError("refusing to prepare 2023+ final OOS data")
    if path.exists() and path.stat().st_size > 1000 and manifest.exists():
        try:
            m = load_json(manifest)
            if (
                m.get("requested_start") == wanted_start
                and m.get("requested_end") == wanted_end
            ):
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
        "--output-dir", data_dir_name,
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
                "symbol": track["target"]["symbol"],
                "target_source": track["target"].get("source"),
                "updated_at": now(),
            })
            return False, reason

        dds = [abs(float(last.get("max_dd_pct", 0.0)))]
        stress = last.get("stress") or {}
        extreme = last.get("extreme_stress") or {}
        dds.append(abs(float(stress.get("max_dd_pct", 0.0))))
        dds.append(abs(float(stress.get("intrabar_dd_proxy_pct", 0.0))))
        dds.append(abs(float(extreme.get("max_dd_pct", 0.0))))
        dds.append(abs(float(extreme.get("intrabar_dd_proxy_pct", 0.0))))
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
                "symbol": track["target"]["symbol"],
                "target_source": track["target"].get("source"),
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
            "symbol": track["target"]["symbol"],
            "target_source": track["target"].get("source"),
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
        "target_source": track["target"].get("source"),
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
        f.write(
            json.dumps(json_safe(record), sort_keys=True, allow_nan=False) + "\n"
        )


def discard_stale_track_state(track, track_dir):
    meta = track_dir / "state_meta.json"
    if not meta.exists():
        return
    try:
        payload = load_json(meta)
    except Exception:
        payload = {}
    if not track_state_identity_matches(track, payload):
        old_source = stored_target_source(payload)
        new_source = track.get("target", {}).get("source")
        print(
            f"[state reset] discarding stale identity state in {track_dir.name} "
            f"source={old_source!r}->{new_source!r}"
        )
        shutil.rmtree(track_dir)


def reset_stale_protocol_state():
    """Discard active-state files from older scoring protocols.

    The exact v2 state is preserved on the dedicated archive branch before v3
    starts. The active persistent branch must become protocol-pure: tracks,
    cursor, selections, progress, leaderboard, cycle ledger, completion marker,
    and cached dashboard are all reset together when stale v2 state is found.
    """
    STATE.mkdir(parents=True, exist_ok=True)
    TRACKS.mkdir(parents=True, exist_ok=True)
    removed = 0
    stale_detected = False

    for track_dir in list(TRACKS.iterdir()):
        if not track_dir.is_dir():
            continue
        meta = track_dir / "state_meta.json"
        try:
            payload = load_json(meta) if meta.exists() else {}
        except Exception:
            payload = {}
        if payload.get("protocol") != PROTOCOL:
            shutil.rmtree(track_dir)
            removed += 1
            stale_detected = True

    for path in [CURSOR, SELECTIONS, PROGRESS, LEADERBOARD]:
        if not path.exists():
            continue
        try:
            payload = load_json(path)
        except Exception:
            payload = {}
        if payload.get("protocol") != PROTOCOL:
            path.unlink()
            stale_detected = True

    # v2 cycle rows predate reliable per-row protocol tags, so once any stale
    # active-state artifact is detected the whole active ledger must reset.
    if stale_detected and LEDGER.exists():
        LEDGER.unlink()

    if stale_detected:
        marker = STATE / "ALL_RUNNABLE_TRACKS_TERMINAL"
        if marker.exists():
            marker.unlink()
        legacy_first_pass = STATE / "first_pass_progress.json"
        if legacy_first_pass.exists():
            legacy_first_pass.unlink()
        stale_dashboard = STATE / "dashboard"
        if stale_dashboard.exists():
            shutil.rmtree(stale_dashboard)

    if stale_detected:
        print(
            f"[protocol migration] reset stale active state; "
            f"removed {removed} stale track states"
        )
    return removed


def finalized_tournament_rows():
    if not TOURNAMENT_STATE.exists():
        return []
    try:
        payload = load_json(TOURNAMENT_STATE)
    except Exception:
        return []
    if payload.get("protocol") != PROTOCOL:
        return []
    # A provisional tournament is informational only. It must never steer
    # adaptive research because its model set/ranking is still incomplete.
    if payload.get("provisional"):
        return []
    rows = []
    for row in payload.get("ranking", []):
        if row.get("provider") != "nvidia":
            continue
        model = row.get("model")
        if not model:
            continue
        if int(row.get("admitted", 0) or 0) <= 0:
            continue
        rows.append(row)
    return rows


def tournament_model_pool():
    """Compatibility helper: all finalized NVIDIA tournament candidates."""
    return [row["model"] for row in finalized_tournament_rows()]


def tournament_bandit_priors():
    """Build Beta-Bernoulli priors at the matched-case level.

    Each tournament case contains repeated trials. A case is a success when at
    least one trial produced a keeper. This avoids treating repeated LLM draws
    on the same strategy case as independent evidence.
    """
    out = []
    for row in finalized_tournament_rows():
        case_map = row.get("case_aggregates") or {}
        if case_map:
            cases = len(case_map)
            successes = sum(
                1 for x in case_map.values()
                if float(x.get("keep_rate") or 0.0) > 0.0
            )
        else:
            # Backward-compatible fallback for older summaries that did not
            # persist case aggregates. New v3 tournament summaries do.
            cases = int(row.get("attempts", 0) or 0)
            successes = int(row.get("would_keep", 0) or 0)
        if cases <= 0:
            continue
        successes = min(max(successes, 0), cases)
        out.append({
            "model": row["model"],
            "tournament_cases": cases,
            "tournament_successes": successes,
            "prior_alpha": 1.0 + successes,
            "prior_beta": 1.0 + (cases - successes),
        })
    return out


def online_bandit_observations():
    """Read one reward per model-selection visit from the persistent cycle log."""
    counts = {}
    if not LEDGER.exists():
        return counts
    with LEDGER.open(encoding="utf-8") as fh:
        for line in fh:
            try:
                row = json.loads(line)
            except Exception:
                continue
            model = row.get("model")
            reward = row.get("bandit_reward")
            if not model or reward not in (0, 1, False, True):
                continue
            x = counts.setdefault(model, {"visits": 0, "successes": 0})
            x["visits"] += 1
            x["successes"] += int(bool(reward))
    return counts


def model_bandit_snapshot():
    """Posterior state used by Thompson sampling.

    Tournament evidence initializes the posterior. Every subsequent continuous
    research visit updates it online. The posterior therefore learns which
    model is most likely to produce a *new robust keeper*, not merely which
    model had the best frozen tournament rank.
    """
    live = online_bandit_observations()
    rows = []
    for prior in tournament_bandit_priors():
        model = prior["model"]
        obs = live.get(model, {"visits": 0, "successes": 0})
        failures = max(0, obs["visits"] - obs["successes"])
        alpha = float(prior["prior_alpha"]) + obs["successes"]
        beta = float(prior["prior_beta"]) + failures
        rows.append({
            **prior,
            "online_visits": obs["visits"],
            "online_successes": obs["successes"],
            "posterior_alpha": alpha,
            "posterior_beta": beta,
            "posterior_keeper_probability": alpha / (alpha + beta),
        })
    rows.sort(
        key=lambda x: (
            x["posterior_keeper_probability"],
            x["tournament_successes"],
            -x["tournament_cases"],
            x["model"],
        ),
        reverse=True,
    )
    return rows


def select_research_model(track, requested_model, valid_count):
    if requested_model != "auto":
        return requested_model
    candidates = model_bandit_snapshot()
    if not candidates:
        return DEFAULT_MODEL

    # Thompson sampling: draw one plausible keeper probability from every
    # model's current Beta posterior and use the model with the largest draw.
    # The seed is deterministic from persistent state + track context so a
    # rerun from the same exact state makes the same allocation decision.
    posterior_fingerprint = "|".join(
        f"{x['model']}:{x['posterior_alpha']:.6f}:{x['posterior_beta']:.6f}"
        for x in sorted(candidates, key=lambda x: x["model"])
    )
    material = (
        f"{track['id']}|{valid_count}|{PROTOCOL}|{posterior_fingerprint}"
    ).encode()
    seed = int(hashlib.sha256(material).hexdigest()[:16], 16)
    rng = random.Random(seed)

    draws = []
    for row in candidates:
        draw = rng.betavariate(
            float(row["posterior_alpha"]),
            float(row["posterior_beta"]),
        )
        draws.append((
            draw,
            row["posterior_keeper_probability"],
            row["model"],
        ))
    draws.sort(reverse=True)
    return draws[0][2]


def process_track(track, iters, model):
    print("\n" + "=" * 88)
    print(
        f"TRACK {track['id']} family={track['family']['id']} "
        f"market={track['target']['id']} profile={track['profile_name']} "
        f"model={model}"
    )
    print("=" * 88, flush=True)

    track_dir = TRACKS / track["id"]
    discard_stale_track_state(track, track_dir)
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
        "model": model,
        "bandit_reward": int(after["kept"] > before["kept"]),
        "kept_before": before["kept"],
        "kept_after": after["kept"],
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
    # This is the only normal continuous-runner path allowed to extend the
    # physical dataset into hidden pre-OOS validation, and it is reachable only
    # after breadth/depth/elite adaptive search is globally frozen.
    prepare_data(track, include_validation=True)
    if not restore_state(track_dir):
        shutil.rmtree(HERE / "validation_data", ignore_errors=True)
        raise RuntimeError("cannot restore frozen champion for hidden validation")
    env = target_env(track)
    env["AUTORESEARCH_DATA_FILE"] = (
        f"validation_data/{track['target']['id']}_1d.csv"
    )
    env = safe_harness_env(env)
    try:
        run([sys.executable, "robust_harness.py", "--validation"], env=env)
        result = load_json(HERE / "validation_run.json")
    finally:
        # Hidden validation data is ephemeral: never cache or leave it in the
        # working tree after the one frozen-champion validation call.
        shutil.rmtree(HERE / "validation_data", ignore_errors=True)
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
        m = track_meta(track) or {}
        baseline = m.get("baseline") or {}
        if not baseline.get("guard_ok"):
            continue
        score = development_score(track)
        selection = development_selection_score(track)
        if math.isfinite(score) and math.isfinite(selection):
            rows.append((selection, score, track))
    rows.sort(key=lambda x: (x[0], x[1]), reverse=True)
    return rows


def freeze_depth_selection(tracks, breadth_target, depth_fraction):
    if SELECTIONS.exists():
        x = load_json(SELECTIONS)
        if x.get("protocol") == PROTOCOL and x.get("depth_ids"):
            return x

    grouped = {}
    for selection_score, score, track in ranked_viable(tracks, breadth_target):
        key = (track["target"]["id"], track["profile_name"])
        grouped.setdefault(key, []).append((selection_score, score, track))

    depth_ids = []
    for key, rows in sorted(grouped.items()):
        n = max(1, math.ceil(len(rows) * depth_fraction))
        depth_ids.extend(track["id"] for _, _, track in rows[:n])

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
        (selection_score, score, track)
        for selection_score, score, track in ranked_viable(tracks, depth_target)
        if track["id"] in depth_ids
    ]
    by_profile = {}
    for selection_score, score, track in rows:
        by_profile.setdefault(track["profile_name"], []).append(
            (selection_score, score, track)
        )

    elite_ids = []
    for profile, group in sorted(by_profile.items()):
        n = max(3, math.ceil(len(group) * elite_fraction))
        elite_ids.extend(track["id"] for _, _, track in group[:n])
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
    if not depth_ids:
        return "complete", {}
    if not all_reached(tracks, depth_ids, depth_target):
        return "depth", {x: depth_target for x in depth_ids}

    selections = freeze_elite_selection(tracks, depth_target, elite_fraction)
    elite_ids = selections.get("elite_ids", [])
    if not elite_ids:
        return "complete", {}
    if not all_reached(tracks, elite_ids, elite_target):
        return "elite", {x: elite_target for x in elite_ids}

    # The user explicitly expanded the research universe after the original
    # 514-track plan. Do not spend the one-look hidden validation until the
    # prior-work expansion and free-source expansion are also complete/frozen.
    if not hidden_validation_allowed_by_universe_plan():
        return "expansion_pending", {}

    return "validation", {}


def _money_opportunity_value(track):
    """Expected-money research utility from development evidence only.

    This value controls only a bounded minority of research visits. It never
    changes promotion, PBO, validation, drawdown, or OOS gates.
    """
    meta = track_meta(track) or {}
    baseline = meta.get("baseline") or {}
    if not bool(baseline.get("guard_ok")):
        return None

    selection = development_selection_score(track)
    if not math.isfinite(selection):
        return None

    def finite_float(value, default=0.0):
        try:
            x = float(value)
        except Exception:
            return float(default)
        return x if math.isfinite(x) else float(default)

    # Prefer growth that survives the harshest already-computed cost stress.
    extreme = baseline.get("extreme_stress") or {}
    stress_cagr = finite_float(
        extreme.get("cagr_pct"),
        finite_float(baseline.get("cagr_pct"), 0.0),
    )
    calmar = max(0.0, finite_float(baseline.get("calmar"), 0.0))

    diag = development_overfit(track)
    pbo = None if not diag else diag.get("pbo")
    if pbo is None:
        # Unknown PBO keeps some evidence-completion value but no longer
        # outranks a proven low-PBO high-growth track by construction.
        pbo_quality = 0.60
        pbo_missing = 1
    else:
        pbo_value = min(max(finite_float(pbo, 1.0), 0.0), 1.0)
        pbo_quality = 1.0 - pbo_value
        pbo_missing = 0

    grade = str(baseline.get("evidence_grade") or "").upper()
    evidence_quality = {
        "A": 1.00,
        "B": 0.75,
        "C": 0.50,
        "D": 0.25,
    }.get(grade, 0.40)

    counts = track_counts(track)
    valid = max(int(counts.get("valid", 0)), 1)
    guard_density = min(
        1.0,
        max(0.0, float(counts.get("guard_passed", 0)) / valid),
    )

    # Log transforms stop one spectacular CAGR/Calmar estimate from consuming
    # all exploitation budget while still strongly rewarding capital growth.
    growth_term = math.log1p(max(stress_cagr, 0.0) / 100.0)
    calmar_term = math.log1p(min(calmar, 20.0))
    selection_term = max(0.0, min(float(selection), 3.0))

    value = (
        0.42 * growth_term
        + 0.18 * calmar_term
        + 0.20 * selection_term
        + 0.12 * pbo_quality
        + 0.05 * evidence_quality
        + 0.03 * guard_density
    )
    return float(value), pbo_missing


def _opportunity_rank(track):
    """Rank bounded exploitation by expected-money development utility."""
    valued = _money_opportunity_value(track)
    if valued is None:
        return None
    value, pbo_missing = valued
    counts = track_counts(track)
    score = development_selection_score(track)
    return (
        float(value),
        float(score),
        int(pbo_missing),
        int(counts.get("guard_passed", 0)),
        int(counts.get("valid", 0)),
        -int(counts.get("attempts", 0)),
        track["id"],
    )


def next_search_track(
    tracks,
    plan,
    start,
    *,
    prefer_opportunity=False,
    opportunity_market=None,
):
    n = len(tracks)
    if prefer_opportunity:
        ranked = []
        for idx, track in enumerate(tracks):
            target = plan.get(track["id"])
            if target is None or is_terminal_block(track):
                continue
            if track_counts(track)["valid"] >= target:
                continue
            if (
                opportunity_market is not None
                and track.get("target", {}).get("market")
                != opportunity_market
            ):
                continue
            rank = _opportunity_rank(track)
            if rank is not None:
                ranked.append((rank, idx, track, target))
        if ranked:
            ranked.sort(key=lambda x: x[0], reverse=True)
            _, idx, track, target = ranked[0]
            return idx, track, target

    # Exploration path remains the original persistent round-robin scheduler.
    for offset in range(n):
        idx = (start + offset) % n
        track = tracks[idx]
        target = plan.get(track["id"])
        if target is None or is_terminal_block(track):
            continue
        if track_counts(track)["valid"] < target:
            return idx, track, target
    return None


def opportunity_visit_indices(max_visits, fraction):
    """Evenly spread a bounded number of exploitation visits through a run."""
    max_visits = max(0, int(max_visits))
    fraction = float(fraction)
    if max_visits == 0 or fraction <= 0.0:
        return set()
    count = min(max_visits, max(1, int(round(max_visits * fraction))))
    slots = set()
    for k in range(count):
        slot = int(round((k + 1) * (max_visits + 1) / (count + 1))) - 1
        slots.add(min(max_visits - 1, max(0, slot)))
    return slots


def next_cursor_after_visit(
    selected_idx,
    n_tracks,
    *,
    prefer_opportunity=False,
    scheduled_cursor_next=None,
):
    """Keep exploitation from moving the persistent exploration sequence."""
    if prefer_opportunity and scheduled_cursor_next is not None:
        return int(scheduled_cursor_next) % int(n_tracks)
    return (int(selected_idx) + 1) % int(n_tracks)


def next_validation_track(tracks, start):
    selections = load_json(SELECTIONS) if SELECTIONS.exists() else {}
    elite_ids = set(selections.get("elite_ids", []))
    if not elite_ids:
        return None
    n = len(tracks)
    for offset in range(n):
        idx = (start + offset) % n
        track = tracks[idx]
        if track["id"] not in elite_ids or is_terminal_block(track):
            continue
        if validation_state(track) is None:
            return idx, track
    return None


def aggregate_model_performance(tracks):
    models = {}
    for track in tracks:
        path = TRACKS / track["id"] / "experiments.jsonl"
        if not path.exists():
            continue
        with path.open(encoding="utf-8") as fh:
            for line in fh:
                try:
                    row = json.loads(line)
                except Exception:
                    continue
                model = row.get("model") or "unknown"
                x = models.setdefault(model, {
                    "attempts": 0, "backtested": 0, "kept": 0,
                    "crashes": 0, "guard_pass": 0, "delta_k": [],
                    "unique_ideas": set(),
                })
                x["attempts"] += 1
                verdict = row.get("verdict")
                if verdict == "CRASH":
                    x["crashes"] += 1
                candidate = row.get("candidate_score")
                try:
                    cand = float(candidate)
                    base = float(row.get("base_score"))
                    if math.isfinite(cand) and math.isfinite(base):
                        x["backtested"] += 1
                        x["delta_k"].append(cand - base)
                except Exception:
                    pass
                if verdict == "KEPT":
                    x["kept"] += 1
                    x["guard_pass"] += 1
                elif verdict == "REJECTED" and not str(row.get("reason","")).startswith("guard:"):
                    x["guard_pass"] += 1
                idea = row.get("idea_sha256")
                if idea:
                    x["unique_ideas"].add(idea)
    out = []
    for model, x in models.items():
        deltas = x.pop("delta_k")
        ideas = x.pop("unique_ideas")
        attempts = max(x["attempts"], 1)
        out.append({
            "model": model,
            **x,
            "admission_rate": round(x["backtested"] / attempts, 4),
            "keeper_rate": round(x["kept"] / attempts, 4),
            "crash_rate": round(x["crashes"] / attempts, 4),
            "unique_ideas": len(ideas),
            "mean_delta_k": round(sum(deltas) / len(deltas), 6) if deltas else None,
        })
    out.sort(
        key=lambda r: (
            r["keeper_rate"],
            r["guard_pass"] / max(r["attempts"], 1),
            r["mean_delta_k"] if r["mean_delta_k"] is not None else -1e99,
        ),
        reverse=True,
    )
    return out


def write_progress(
    tracks, phase, breadth_target, depth_target, elite_target,
):
    registry = load_json(REGISTRY)
    unresolved_status_tokens = (
        "blocked", "pending", "recovery", "incomplete",
    )
    blocked_families = [
        {
            "id": x["id"],
            "status": x.get("status"),
            "exactness": x.get("exactness"),
            "origin": x.get("origin"),
            "requires": x.get("requires", []),
        }
        for x in registry["families"]
        if any(
            token in str(x.get("status", "")).lower()
            for token in unresolved_status_tokens
        )
    ]
    prior_positive_families = [
        {
            "id": x["id"],
            "status": x.get("status"),
            "origin": x.get("origin"),
            "prior_classification": x.get("prior_classification"),
            "source_lock": x.get("source_lock"),
        }
        for x in registry["families"]
        if str(x.get("status", "")).startswith("prior_frozen")
    ]
    prior_terminal_families = [
        {
            "id": x["id"],
            "status": x.get("status"),
            "evidence": x.get("evidence"),
            "origin": x.get("origin"),
        }
        for x in registry["families"]
        if str(x.get("status", "")).startswith("prior_rejected")
    ]
    rows = []
    counts = {
        "data_insufficient": 0, "seed_blocked": 0,
        "breadth_eliminated": 0, "depth_eliminated": 0,
        "validation_pending": 0,
        "validation_pass": 0, "validation_fail": 0,
        "searching": 0,
    }
    total_valid = total_attempts = total_guard_passed = total_kept = 0
    total_crashes = total_duplicates = total_parameter_only = total_too_broad = total_risk_changes = 0

    selections = load_json(SELECTIONS) if SELECTIONS.exists() else {}
    depth_ids = set(selections.get("depth_ids", []))
    elite_ids = set(selections.get("elite_ids", []))

    for track in tracks:
        m = track_meta(track)
        rc = track_counts(track)
        total_valid += rc["valid"]
        total_attempts += rc["attempts"]
        total_guard_passed += rc.get("guard_passed", 0)
        total_kept += rc.get("kept", 0)
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
        elif elite_ids:
            status = "validation_pending" if track["id"] in elite_ids else (
                "depth_eliminated" if track["id"] in depth_ids else "breadth_eliminated"
            )
        elif phase == "complete":
            status = "depth_eliminated" if track["id"] in depth_ids else "breadth_eliminated"
        elif depth_ids and phase in {"depth", "elite", "validation"}:
            status = "searching" if track["id"] in depth_ids else "breadth_eliminated"
        else:
            status = "searching"
        counts[status] += 1

        rows.append({
            "track_id": track["id"],
            "family": track["family"]["id"],
            "target": track["target"]["id"],
            "profile": track["profile_name"],
            "data_quality_grade": track["target"].get("data_quality_grade"),
            "instrument_fidelity": track["target"].get("instrument_fidelity"),
            "status": status,
            "valid_attempts": rc["valid"],
            "attempts": rc["attempts"],
            "development_score": development_score(track),
            "selection_score": development_selection_score(track),
            "pbo": (
                (development_overfit(track) or {}).get("pbo")
            ),
            "development_cagr_pct": (
                (m.get("baseline") or {}).get("cagr_pct") if m else None
            ),
            "evidence_grade": (
                (m.get("baseline") or {}).get("evidence_grade") if m else None
            ),
            "psr_zero": (
                (m.get("baseline") or {}).get("psr_zero") if m else None
            ),
            "depth_selected": track["id"] in depth_ids,
            "elite_selected": track["id"] in elite_ids,
        })

    terminal = (
        counts["data_insufficient"] + counts["seed_blocked"]
        + counts["breadth_eliminated"] + counts["depth_eliminated"]
        + counts["validation_pass"] + counts["validation_fail"]
    )
    pbo_ready = sum(1 for row in rows if row.get("pbo") is not None)
    pbo_baselined = sum(
        1 for row in rows
        if row.get("development_score") is not None
        and math.isfinite(float(row.get("development_score")))
    )
    payload = {
        "updated_at": now(),
        "protocol": PROTOCOL,
        "phase": phase,
        "universe_stage": current_universe_stage(),
        "expansion_pending": phase == "expansion_pending",
        "hidden_validation_allowed_by_universe_plan": hidden_validation_allowed_by_universe_plan(),
        "breadth_target": breadth_target,
        "depth_target": depth_target,
        "elite_target": elite_target,
        "runnable_track_count": len(tracks),
        "terminal_track_count": terminal,
        "validation_pass_count": counts["validation_pass"],
        "validation_fail_count": counts["validation_fail"],
        "data_insufficient_count": counts["data_insufficient"],
        "seed_blocked_count": counts["seed_blocked"],
        "breadth_eliminated_count": counts["breadth_eliminated"],
        "depth_eliminated_count": counts["depth_eliminated"],
        "validation_pending_count": counts["validation_pending"],
        "searching_count": counts["searching"],
        "total_valid_candidates": total_valid,
        "pbo_ready_track_count": pbo_ready,
        "pbo_baselined_track_count": pbo_baselined,
        "pbo_min_strategy_count": 5,
        "pbo_readiness_definition": "baseline_plus_at_least_four_unique_selection_eligible_backtests",
        "total_model_attempt_rows": total_attempts,
        "total_guard_passed_candidates": total_guard_passed,
        "total_kept_candidates": total_kept,
        "total_crashes": total_crashes,
        "total_duplicates": total_duplicates,
        "total_parameter_only": total_parameter_only,
        "total_too_broad": total_too_broad,
        "total_risk_control_changes": total_risk_changes,
        "all_runnable_tracks_terminal": terminal == len(tracks),
        "blocked_family_count": len(blocked_families),
        "blocked_families": blocked_families,
        "prior_positive_family_count": len(prior_positive_families),
        "prior_positive_families": prior_positive_families,
        "prior_terminal_family_count": len(prior_terminal_families),
        "prior_terminal_families": prior_terminal_families,
        "model_performance": aggregate_model_performance(tracks),
        "universe_metadata": load_json(CONFIG).get("universe_metadata", {}),
        "model_allocator": {
            "method": "deterministic_thompson_sampling_beta_bernoulli",
            "reward_unit": "two-iteration visit with at least one keeper",
            "tournament_prior_unit": "matched strategy case with at least one keeper",
            "candidates": model_bandit_snapshot(),
        },
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
        pbo = development_overfit(track) or {}
        rows.append({
            "track_id": track["id"],
            "family": m.get("family"),
            "exactness": m.get("family_exactness"),
            "target": m.get("target"),
            "market": m.get("market"),
            "profile": m.get("profile"),
            "data_quality_grade": track["target"].get("data_quality_grade"),
            "data_quality_note": track["target"].get("data_quality_note"),
            "instrument_fidelity": track["target"].get("instrument_fidelity"),
            "valid_attempts": m.get("valid", m.get("valid_attempts", 0)),
            "development_guard_ok": bool(b.get("guard_ok")),
            "development_score": b.get("score"),
            "selection_score": development_selection_score(track),
            "pbo": pbo.get("pbo"),
            "pbo_candidate_count": pbo.get("candidate_count"),
            "pbo_fold_count": pbo.get("fold_count"),
            "pbo_cscv_splits": pbo.get("cscv_splits"),
            "development_return_pct": b.get("return_pct"),
            "development_cagr_pct": b.get("cagr_pct"),
            "development_years": b.get("development_years"),
            "development_sharpe": b.get("sharpe"),
            "development_max_dd_pct": b.get("max_dd_pct"),
            "development_pf": b.get("pf"),
            "development_trades": b.get("trades"),
            "development_trades_per_year": b.get("trades_per_year"),
            "development_calmar": b.get("calmar"),
            "development_ulcer_index_pct": b.get("ulcer_index_pct"),
            "development_psr_zero": b.get("psr_zero"),
            "development_bootstrap_pvalue": b.get("bootstrap_mean_positive_pvalue"),
            "evidence_grade": b.get("evidence_grade"),
            "benchmark_cagr_pct": b.get("benchmark_cagr_pct"),
            "excess_cagr_vs_buyhold_pct": b.get("excess_cagr_vs_buyhold_pct"),
            "sharpe_minus_buyhold": b.get("sharpe_minus_buyhold"),
            "extreme_stress_return_pct": (b.get("extreme_stress") or {}).get("return_pct"),
            "hidden_validation_pass": None if val is None else bool(val.get("guard_ok")),
            "hidden_return_pct": None if val is None else val.get("return_pct"),
            "hidden_cagr_pct": None if val is None else val.get("cagr_pct"),
            "hidden_sharpe": None if val is None else val.get("sharpe"),
            "hidden_max_dd_pct": None if val is None else val.get("max_dd_pct"),
            "hidden_pf": None if val is None else val.get("pf"),
        })
    # Benjamini-Hochberg FDR diagnostic across current champions. This is
    # reported, not used as a hard gate, so low-sample ideas remain eligible
    # for the user's requested forward/hidden validation process.
    p_rows = []
    for idx, row in enumerate(rows):
        try:
            p = float(row.get("development_bootstrap_pvalue"))
        except Exception:
            continue
        if math.isfinite(p):
            p_rows.append((p, idx))
    p_rows.sort()
    mtests = len(p_rows)
    running = 1.0
    for rank in range(mtests, 0, -1):
        p, idx = p_rows[rank - 1]
        q = min(running, p * mtests / rank)
        running = q
        rows[idx]["multiple_test_qvalue"] = round(min(1.0, q), 6)
    for row in rows:
        row.setdefault("multiple_test_qvalue", None)

    rows.sort(
        key=lambda x: (
            1 if x["hidden_validation_pass"] is True else 0,
            1 if x.get("development_guard_ok") else 0,
            float(x["selection_score"])
            if x.get("selection_score") is not None and math.isfinite(float(x["selection_score"]))
            else -1e99,
            float(x["development_score"])
            if x["development_score"] is not None and math.isfinite(float(x["development_score"]))
            else -1e99,
        ),
        reverse=True,
    )
    save_json(LEADERBOARD, {
        "updated_at": now(),
        "protocol": PROTOCOL,
        "universe_metadata": load_json(CONFIG).get("universe_metadata", {}),
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
    ap.add_argument(
        "--opportunity-fraction",
        type=float,
        default=0.30,
        help=(
            "fraction of search visits allocated to high expected-money "
            "development opportunities; remaining visits preserve persistent "
            "round-robin exploration"
        ),
    )
    ap.add_argument("--model", default="auto")
    ap.add_argument("--config", default="continuous_config.json")
    ap.add_argument("--state-dir", default="continuous_state")
    ap.add_argument(
        "--universe-plan",
        default="strategy_library/universe_plan.json",
    )
    args = ap.parse_args()
    configure_paths(args.config, args.state_dir, args.universe_plan)

    if not os.environ.get("NVIDIA_API_KEY"):
        raise SystemExit("NVIDIA_API_KEY missing")
    if not (0 < args.depth_fraction <= 1 and 0 < args.elite_fraction <= 1):
        raise SystemExit("selection fractions must be in (0,1]")
    if not (0.0 <= args.opportunity_fraction <= 0.50):
        raise SystemExit("opportunity fraction must be in [0,0.50]")
    if not (1 <= args.breadth_target <= args.depth_target <= args.elite_target):
        raise SystemExit("require breadth <= depth <= elite targets")

    STATE.mkdir(parents=True, exist_ok=True)
    TRACKS.mkdir(parents=True, exist_ok=True)
    tracks = build_tracks()
    if not tracks:
        raise SystemExit("no runnable tracks")
    reset_stale_protocol_state()

    started = time.monotonic()
    cursor = read_cursor(len(tracks))
    visits = 0
    opportunity_visits = opportunity_visit_indices(
        args.max_visits,
        args.opportunity_fraction,
    )

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

        if phase == "expansion_pending":
            print(
                "CURRENT DEVELOPMENT UNIVERSE FROZEN; hidden validation remains "
                "SEALED pending the configured prior-work/free-source expansion."
            )
            break

        if phase == "validation":
            nxt = next_validation_track(tracks, cursor)
            if nxt is None:
                print("ALL RUNNABLE TRACKS TERMINAL; hidden validation phase complete")
                break
            idx, track = nxt
            try:
                validate_track(track)
            except Exception as exc:
                meta_path = TRACKS / track["id"] / "state_meta.json"
                meta = load_json(meta_path) if meta_path.exists() else {}
                errors = int(meta.get("validation_runner_errors", 0) or 0) + 1
                meta["validation_runner_errors"] = errors
                meta["last_validation_runner_error"] = (
                    f"{type(exc).__name__}: {str(exc)[:500]}"
                )
                save_json(meta_path, meta)
                append_cycle({
                    "ts": now(), "track_id": track["id"],
                    "status": "validation_runner_error",
                    "attempt": errors,
                    "reason": meta["last_validation_runner_error"],
                })
                if errors >= 3:
                    save_json(TRACKS / track["id"] / "validation.json", {
                        "guard_ok": False,
                        "guard_reason": "validation runner failed 3 times",
                        "runner_error": meta["last_validation_runner_error"],
                        "protocol": PROTOCOL,
                        "oos_opened": False,
                    })
                    meta["status"] = "validation_fail"
                    save_json(meta_path, meta)
                print(f"[validation error] {track['id']}: {exc}", file=sys.stderr)
            cursor = (idx + 1) % len(tracks)
            visits += 1
            continue

        prefer_opportunity = visits in opportunity_visits
        scheduled = None
        scheduled_cursor_next = None
        opportunity_market = None
        if prefer_opportunity:
            scheduled = next_search_track(
                tracks,
                plan,
                cursor,
                prefer_opportunity=False,
            )
            if scheduled is not None:
                scheduled_idx, scheduled_track, _ = scheduled
                scheduled_cursor_next = (
                    scheduled_idx + 1
                ) % len(tracks)
                opportunity_market = (
                    scheduled_track.get("target", {}).get("market")
                )

        nxt = next_search_track(
            tracks,
            plan,
            cursor,
            prefer_opportunity=prefer_opportunity,
            opportunity_market=opportunity_market,
        )
        if nxt is None:
            # Recompute phase on the next loop; this occurs at a phase boundary.
            continue
        idx, track, target = nxt
        print(
            f"[track allocator] mode="
            f"{'opportunity' if prefer_opportunity else 'exploration'} "
            f"track={track['id']}"
        )
        current = track_counts(track)["valid"]
        visit_iters = min(args.iters_per_visit, max(1, target - current))
        try:
            chosen_model = select_research_model(track, args.model, current)
            print(f"[model router] {track['id']} -> {chosen_model}")
            process_track(track, visit_iters, chosen_model)
        except Exception as exc:
            append_cycle({
                "ts": now(), "track_id": track["id"],
                "status": "runner_error",
                "reason": f"{type(exc).__name__}: {str(exc)[:500]}",
            })
            print(f"[runner error] {track['id']}: {exc}", file=sys.stderr)
        cursor = next_cursor_after_visit(
            idx,
            len(tracks),
            prefer_opportunity=prefer_opportunity,
            scheduled_cursor_next=scheduled_cursor_next,
        )
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
        "pbo_ready_tracks": progress.get("pbo_ready_track_count", 0),
        "pbo_baselined_tracks": progress.get("pbo_baselined_track_count", 0),
        "terminal_tracks": progress["terminal_track_count"],
        "runnable_tracks": progress["runnable_track_count"],
        "validation_pass": progress["validation_pass_count"],
        "validation_fail": progress["validation_fail_count"],
        "breadth_eliminated": progress.get("breadth_eliminated_count", 0),
        "depth_eliminated": progress.get("depth_eliminated_count", 0),
        "validation_pending": progress.get("validation_pending_count", 0),
        "data_insufficient": progress["data_insufficient_count"],
        "seed_blocked": progress["seed_blocked_count"],
        "next_cursor": cursor,
    }, indent=2))


if __name__ == "__main__":
    main()
