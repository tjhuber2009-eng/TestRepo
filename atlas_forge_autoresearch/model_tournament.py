"""Matched-model tournament for hardened AUTORESEARCH.

This benchmark does NOT mutate continuous research state and never opens hidden
validation. Every model receives the same frozen strategy champion, baseline,
program, market/profile context, and one-change task for each benchmark case.

The tournament measures generation quality on development data only:
- API/output success
- safety/local-change admission
- robust-harness survival
- development guard pass
- material improvement over the frozen baseline
- matched-case delta K
"""

import argparse
import hashlib
import json
import math
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

from openai import OpenAI

import continuous_runner as cr
import loop

HERE = Path(__file__).resolve().parent
PROGRAM = HERE / "program_robust.md"
RESULT = HERE / "model_tournament_result.json"

CASES = [
    "sentinel63__btc__private",
    "sentinel63__btc__prop",
    "sentinel63__eth__private",
    "sentinel63__aapl__private",
    "ibs_deep_pullback__tqqq__private",
    "ibs_deep_pullback__tqqq__prop",
    "ibs_deep_pullback__aapl__prop",
    "connors_rsi2__nvda__private",
    "connors_rsi2_65_nextopen__aapl__private",
    "cumulative_rsi3_45_nextopen__aapl__private",
]

PROVIDERS = {
    "nvidia": {
        "base_url": "https://integrate.api.nvidia.com/v1",
        "key_env": "NVIDIA_API_KEY",
    },
    "groq": {
        "base_url": "https://api.groq.com/openai/v1",
        "key_env": "GROQ_API_KEY",
    },
    "gemini": {
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "key_env": "GEMINI_API_KEY",
    },
}


def now():
    return cr.now()


def json_dump(path, obj):
    Path(path).write_text(
        json.dumps(obj, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def finite_or_none(value):
    try:
        x = float(value)
    except Exception:
        return None
    return x if math.isfinite(x) else None


def material_delta(base_score):
    return max(0.005, min(0.02, 0.01 * max(abs(float(base_score)), 0.10)))


def provider_client(provider):
    cfg = PROVIDERS[provider]
    key = os.environ.get(cfg["key_env"], "").strip()
    if not key:
        return None, f"{cfg['key_env']} missing"
    return OpenAI(base_url=cfg["base_url"], api_key=key, timeout=210.0), None


def normalize_content(message):
    content = getattr(message, "content", None)
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        chunks = []
        for item in content:
            if isinstance(item, str):
                chunks.append(item)
            elif isinstance(item, dict):
                text = item.get("text") or item.get("content")
                if text:
                    chunks.append(str(text))
            else:
                text = getattr(item, "text", None)
                if text:
                    chunks.append(str(text))
        return "\n".join(chunks)
    return "" if content is None else str(content)


def model_call(client, model, prompt):
    last_error = None
    started = time.time()
    for attempt in range(1, 5):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "You are a precise quantitative strategy code editor. "
                            "Follow the output protocol exactly and make one localized "
                            "conceptual change only."
                        ),
                    },
                    {"role": "user", "content": prompt},
                ],
                temperature=0.15,
                max_tokens=12000,
            )
            raw = normalize_content(response.choices[0].message)
            if not raw.strip():
                raise RuntimeError("empty model response")
            return raw, attempt, round(time.time() - started, 3), None
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {str(exc)[:1000]}"
            if attempt < 4 and loop.retryable(exc):
                time.sleep(min(2 ** (attempt - 1), 15))
                continue
            break
    return "", 4, round(time.time() - started, 3), last_error


def preflight(client, model):
    try:
        response = client.chat.completions.create(
            model=model,
            messages=[{"role": "user", "content": "Reply with exactly OK."}],
            temperature=0,
            max_tokens=16,
        )
        raw = normalize_content(response.choices[0].message).strip()
        return bool(raw), raw[:100]
    except Exception as exc:
        return False, f"{type(exc).__name__}: {str(exc)[:800]}"


def build_prompt(track, baseline, strategy, trial=1):
    program = PROGRAM.read_text(encoding="utf-8")
    return "\n".join([
        program,
        "",
        "# MATCHED MODEL TOURNAMENT",
        "This is a frozen matched benchmark. You do not see any other model's output.",
        "The hidden validation period and 2023+ final OOS are unavailable.",
        f"Benchmark trial: {trial}",
        f"Strategy family: {track['family']['id']}",
        f"Market: {track['target']['market']}",
        f"Instrument: {track['target']['symbol']}",
        f"Profile: {track['profile_name']}",
        f"Hard max drawdown: {track['profile']['max_dd_pct']}%",
        f"Frozen baseline robust K: {baseline.get('score')}",
        f"Frozen baseline return: {baseline.get('return_pct')}%",
        f"Frozen baseline Sharpe: {baseline.get('sharpe')}",
        f"Frozen baseline DD: {baseline.get('max_dd_pct')}%",
        f"Frozen baseline trades: {baseline.get('trades')}",
        "",
        "## Frozen current strategy.py",
        strategy,
        "",
        "## Task",
        "Propose exactly ONE causal conceptual strategy improvement.",
        "Return the COMPLETE replacement strategy.py.",
        "COPY the current file verbatim except for the smallest region needed.",
        "Do not change vol_target, f_max, vol_lookback, or _units.",
        "Do not perform numeric-only parameter tuning.",
        "Do not use dates, hidden validation, future data, filesystem/network access,",
        "environment access, dynamic code execution, or a backtest inside strategy.py.",
        "",
        "OUTPUT FORMAT — EXACTLY THESE MARKERS, RAW PYTHON, NO JSON:",
        "<<<PROPOSAL>>>",
        "one-line description of the single conceptual change",
        "<<<STRATEGY_PY>>>",
        "<complete raw Python source for strategy.py>",
        "<<<END_STRATEGY_PY>>>",
        "No markdown fences and no text outside the markers.",
    ])


def find_track(track_id):
    tracks = {x["id"]: x for x in cr.build_tracks()}
    if track_id not in tracks:
        raise RuntimeError(f"benchmark track not found in current universe: {track_id}")
    return tracks[track_id]


def score_strategy_under_current_protocol(track, strategy_source):
    """Re-score a frozen strategy under the current harness without state mutation."""
    cr.prepare_data(track)
    env = cr.safe_harness_env(cr.target_env(track))
    (HERE / "strategy.py").write_text(strategy_source, encoding="utf-8")
    p = HERE / "last_run.json"
    if p.exists():
        p.unlink()
    proc = subprocess.run(
        [sys.executable, "robust_harness.py", "--is"],
        cwd=HERE,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=300,
    )
    if proc.returncode != 0 or not p.exists():
        raise RuntimeError(
            f"baseline re-score failed for {track['id']}: "
            + (proc.stdout[-1200:] if proc.stdout else f"exit {proc.returncode}")
        )
    baseline = cr.load_json(p)
    if baseline.get("protocol") != cr.PROTOCOL:
        raise RuntimeError("baseline re-score returned wrong protocol")
    return baseline


def load_frozen_case(track):
    track_dir = cr.TRACKS / track["id"]
    meta_path = track_dir / "state_meta.json"
    strategy_path = track_dir / "strategy_best.py"

    meta = cr.load_json(meta_path) if meta_path.exists() else {}
    if strategy_path.exists():
        strategy = strategy_path.read_text(encoding="utf-8")
        source_kind = "frozen_continuous_champion"
    else:
        # Tournament cases remain runnable even immediately after a protocol
        # migration: generate the documented family seed, then freeze it for
        # this matched benchmark only.
        cr.generate_seed(track)
        strategy = (HERE / "strategy.py").read_text(encoding="utf-8")
        source_kind = "generated_family_seed"

    baseline = meta.get("baseline") or {}
    score = finite_or_none(baseline.get("score"))
    if meta.get("protocol") != cr.PROTOCOL or score is None:
        baseline = score_strategy_under_current_protocol(track, strategy)
        meta = {
            "protocol": cr.PROTOCOL,
            "baseline_source_protocol": meta.get("protocol"),
            "tournament_rescored": True,
        }
    meta["tournament_strategy_source"] = source_kind
    score = finite_or_none(baseline.get("score"))
    if score is None:
        raise RuntimeError(f"non-finite frozen baseline score for {track['id']}")
    return meta, baseline, strategy


def run_harness(track, candidate_source):
    cr.prepare_data(track)
    env = cr.safe_harness_env(cr.target_env(track))
    (HERE / "strategy.py").write_text(candidate_source, encoding="utf-8")
    p = HERE / "last_run.json"
    if p.exists():
        p.unlink()
    started = time.time()
    proc = subprocess.run(
        [sys.executable, "robust_harness.py", "--is"],
        cwd=HERE,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        timeout=240,
    )
    elapsed = round(time.time() - started, 3)
    if proc.returncode != 0 or not p.exists():
        return None, elapsed, (
            f"harness exit {proc.returncode}: {proc.stdout[-1200:]}"
            if proc.stdout else f"harness exit {proc.returncode}"
        )
    return cr.load_json(p), elapsed, None


def evaluate_case(client, provider, model, track_id, trial=1):
    track = find_track(track_id)
    meta, baseline, strategy = load_frozen_case(track)
    cr.prepare_data(track)

    prompt = build_prompt(track, baseline, strategy, trial=trial)
    started = time.time()
    raw, api_attempts, api_seconds, api_error = model_call(client, model, prompt)
    record = {
        "track_id": track_id,
        "family": track["family"]["id"],
        "target": track["target"]["id"],
        "market": track["target"]["market"],
        "profile": track["profile_name"],
        "baseline_score": finite_or_none(baseline.get("score")),
        "baseline_return_pct": finite_or_none(baseline.get("return_pct")),
        "baseline_cagr_pct": finite_or_none(baseline.get("cagr_pct")),
        "baseline_sharpe": finite_or_none(baseline.get("sharpe")),
        "baseline_max_dd_pct": finite_or_none(baseline.get("max_dd_pct")),
        "provider": provider,
        "model": model,
        "trial": trial,
        "api_attempts": api_attempts,
        "api_seconds": api_seconds,
        "api_success": api_error is None,
        "api_error": api_error,
        "proposal": None,
        "admitted": False,
        "admission_reason": None,
        "backtested": False,
        "guard_ok": False,
        "candidate_score": None,
        "delta_k": None,
        "would_keep": False,
        "candidate_return_pct": None,
        "candidate_cagr_pct": None,
        "candidate_sharpe": None,
        "candidate_psr_zero": None,
        "candidate_evidence_grade": None,
        "candidate_max_dd_pct": None,
        "candidate_pf": None,
        "harness_seconds": None,
        "elapsed_seconds": None,
    }
    if api_error is not None:
        record["admission_reason"] = "api_failure"
        record["elapsed_seconds"] = round(time.time() - started, 3)
        return record

    try:
        proposal, source = loop.parse_and_validate(raw)
        record["proposal"] = proposal
    except Exception as exc:
        record["admission_reason"] = f"parse_or_safety: {type(exc).__name__}: {str(exc)[:800]}"
        record["elapsed_seconds"] = round(time.time() - started, 3)
        return record

    try:
        if loop.risk_control_fingerprint(source) != loop.risk_control_fingerprint(strategy):
            record["admission_reason"] = "risk_control_change"
            record["elapsed_seconds"] = round(time.time() - started, 3)
            return record
        if loop.canonical_ast_hash(source) == loop.canonical_ast_hash(strategy):
            record["admission_reason"] = "duplicate"
            record["elapsed_seconds"] = round(time.time() - started, 3)
            return record
        if loop.structural_ast_hash(source) == loop.structural_ast_hash(strategy):
            record["admission_reason"] = "parameter_only"
            record["elapsed_seconds"] = round(time.time() - started, 3)
            return record
        local_ok, detail = loop.local_change_guard(source, strategy)
        if not local_ok:
            record["admission_reason"] = f"too_broad: {detail}"
            record["elapsed_seconds"] = round(time.time() - started, 3)
            return record
        record["admitted"] = True
        record["admission_reason"] = f"ok: {detail}"
    except Exception as exc:
        record["admission_reason"] = f"admission_exception: {type(exc).__name__}: {str(exc)[:800]}"
        record["elapsed_seconds"] = round(time.time() - started, 3)
        return record

    summary, harness_seconds, harness_error = run_harness(track, source)
    record["harness_seconds"] = harness_seconds
    if harness_error is not None:
        record["admission_reason"] = f"harness_failure: {harness_error}"
        record["elapsed_seconds"] = round(time.time() - started, 3)
        return record

    record["backtested"] = True
    record["guard_ok"] = bool(summary.get("guard_ok"))
    score = finite_or_none(summary.get("score"))
    record["candidate_score"] = score
    record["candidate_return_pct"] = finite_or_none(summary.get("return_pct"))
    record["candidate_cagr_pct"] = finite_or_none(summary.get("cagr_pct"))
    record["candidate_sharpe"] = finite_or_none(summary.get("sharpe"))
    record["candidate_psr_zero"] = finite_or_none(summary.get("psr_zero"))
    record["candidate_evidence_grade"] = summary.get("evidence_grade")
    record["candidate_max_dd_pct"] = finite_or_none(summary.get("max_dd_pct"))
    record["candidate_pf"] = finite_or_none(summary.get("pf"))
    if score is not None:
        delta = score - float(baseline["score"])
        record["delta_k"] = round(delta, 6)
        record["would_keep"] = bool(
            record["guard_ok"] and delta > material_delta(float(baseline["score"]))
        )
    record["elapsed_seconds"] = round(time.time() - started, 3)
    return record


def aggregate_single_model(provider, model, cases, trials=2):
    client, missing = provider_client(provider)
    out = {
        "created_at": now(),
        "protocol": cr.PROTOCOL,
        "provider": provider,
        "model": model,
        "available": False,
        "availability_detail": missing,
        "cases_requested": len(cases),
        "trials_per_case": int(trials),
        "cases": [],
        "summary": {},
        "hidden_validation_opened": False,
        "final_oos_opened": False,
    }
    if client is None:
        json_dump(RESULT, out)
        return out

    ok, detail = preflight(client, model)
    out["available"] = ok
    out["availability_detail"] = detail
    if not ok:
        json_dump(RESULT, out)
        return out

    total_runs = len(cases) * int(trials)
    nrun = 0
    for case in cases:
        for trial in range(1, int(trials) + 1):
            nrun += 1
            print(
                f"[{nrun}/{total_runs}] {provider} {model} -> {case} trial={trial}",
                flush=True,
            )
            try:
                rec = evaluate_case(client, provider, model, case, trial=trial)
            except Exception as exc:
                rec = {
                    "track_id": case,
                    "provider": provider,
                    "model": model,
                    "trial": trial,
                    "api_success": False,
                    "admitted": False,
                    "backtested": False,
                    "guard_ok": False,
                    "would_keep": False,
                    "delta_k": None,
                    "admission_reason": f"runner_exception: {type(exc).__name__}: {str(exc)[:1000]}",
                }
            out["cases"].append(rec)
            json_dump(RESULT, out)

    rows = out["cases"]
    deltas = [r["delta_k"] for r in rows if r.get("delta_k") is not None]
    guard_deltas = [
        r["delta_k"] for r in rows
        if r.get("guard_ok") and r.get("delta_k") is not None
    ]
    s = {
        "attempts": len(rows),
        "api_success": sum(bool(r.get("api_success")) for r in rows),
        "admitted": sum(bool(r.get("admitted")) for r in rows),
        "backtested": sum(bool(r.get("backtested")) for r in rows),
        "guard_pass": sum(bool(r.get("guard_ok")) for r in rows),
        "would_keep": sum(bool(r.get("would_keep")) for r in rows),
        "positive_delta": sum(
            r.get("delta_k") is not None and r["delta_k"] > 0 for r in rows
        ),
        "mean_delta_k": round(sum(deltas) / len(deltas), 6) if deltas else None,
        "median_delta_k": (
            round(float(statistics.median(deltas)), 6) if deltas else None
        ),
        "mean_guard_delta_k": (
            round(sum(guard_deltas) / len(guard_deltas), 6)
            if guard_deltas else None
        ),
        "unique_proposals": len({
            hashlib.sha256(
                " ".join(str(r.get("proposal") or "").lower().split()).encode()
            ).hexdigest()
            for r in rows if r.get("proposal")
        }),
        "total_seconds": round(
            sum(float(r.get("elapsed_seconds") or 0.0) for r in rows), 3
        ),
    }
    out["summary"] = s
    json_dump(RESULT, out)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--provider", choices=sorted(PROVIDERS), required=True)
    ap.add_argument("--model", required=True)
    ap.add_argument("--cases", default=",".join(CASES))
    ap.add_argument("--trials", type=int, default=2)
    args = ap.parse_args()
    cases = [x.strip() for x in args.cases.split(",") if x.strip()]
    if args.trials < 1 or args.trials > 5:
        raise SystemExit("--trials must be between 1 and 5")
    result = aggregate_single_model(args.provider, args.model, cases, trials=args.trials)
    print(json.dumps(result["summary"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
