"""
Moon Dev AUTORESEARCH — NVIDIA NIM variant.

The research harness and keep/revert rules stay local and frozen. NVIDIA only
proposes one complete replacement for strategy.py at a time.
"""

import argparse
import ast
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
PY = sys.executable

STRATEGY = "strategy.py"
BEST = "strategy_best.py"
BASELINE = "baseline.json"
LAST_RUN = "last_run.json"
RESULTS = "results.tsv"
PROPOSAL = "proposal.txt"
STOP = "STOP"
KEEPERS = "keepers"
LOGS = "logs"

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b"
MAX_API_RETRIES = 4


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def ensure_seed():
    os.makedirs(KEEPERS, exist_ok=True)
    os.makedirs(LOGS, exist_ok=True)
    if not (os.path.exists(BASELINE) and os.path.exists(BEST)):
        print("[setup] freezing seed baseline")
        with open(os.path.join(LOGS, "baseline.txt"), "w", encoding="utf-8") as f:
            r = subprocess.run(
                [PY, "harness.py", "--is", "--set-baseline"],
                stdout=f,
                stderr=subprocess.STDOUT,
                text=True,
            )
        if r.returncode != 0:
            raise RuntimeError("baseline failed; see logs/baseline.txt")
        shutil.copy(STRATEGY, BEST)
        shutil.copy(STRATEGY, os.path.join(KEEPERS, "000_seed.py"))
    if not os.path.exists(RESULTS):
        with open(RESULTS, "w", encoding="utf-8", newline="") as f:
            f.write(
                "ts\titer\tverdict\tscore\tbase_score\tret_pct\tsharpe\t"
                "ann_vol\ttrades\tmax_dd\tguard\tdesc\n"
            )


def results_rows():
    with open(RESULTS, encoding="utf-8") as f:
        lines = f.read().rstrip("\n").split("\n")
    return lines[0], lines[1:]


def build_prompt(iteration, base):
    header, rows = results_rows()
    with open("program.md", encoding="utf-8") as f:
        program = f.read()
    with open(STRATEGY, encoding="utf-8") as f:
        strategy = f.read()
    prompt = "\n".join(
        [
            program,
            "",
            f"## Current baseline (must beat): score K={base['score']}, "
            f"ann vol {base['ann_vol_pct']}%, trades {base['trades']}",
            "",
            "## Last 30 results (newest last)",
            header,
            *rows[-30:],
            "",
            "## Current strategy.py",
            strategy,
            "",
            "## Your task now",
            "You are the strategy research agent.",
            "You may change strategy.py only.",
            "Make exactly ONE conceptual strategy change.",
            "Return the COMPLETE replacement strategy.py, not a patch.",
            "Preserve class MoonStrategy and obey every law in program.md.",
            "Do not change position size merely to increase returns.",
            "Do not use future data or OOS information.",
            "Do not run a backtest yourself.",
            "Return valid JSON only with exactly these fields:",
            '{"proposal":"one-line description","strategy_py":"complete Python source"}',
            "No markdown fences and no commentary outside the JSON object.",
        ]
    )
    with open(os.path.join(LOGS, f"prompt_{iteration}.txt"), "w", encoding="utf-8") as f:
        f.write(prompt)
    return prompt


def status_code(exc):
    code = getattr(exc, "status_code", None)
    if code is not None:
        return code
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None) if response is not None else None


def retryable(exc):
    code = status_code(exc)
    if code == 429 or (isinstance(code, int) and code >= 500):
        return True
    name = type(exc).__name__.lower()
    return "timeout" in name or "connection" in name


def extract_json(text):
    text = (text or "").strip()
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        first = text.find("{")
        last = text.rfind("}")
        if first >= 0 and last > first:
            return json.loads(text[first:last + 1])
        raise


def validate_payload(payload):
    if not isinstance(payload, dict):
        raise ValueError("response JSON must be an object")
    proposal = payload.get("proposal")
    source = payload.get("strategy_py")
    if not isinstance(proposal, str) or not proposal.strip():
        raise ValueError("missing non-empty proposal")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("missing non-empty strategy_py")
    if chr(96) * 3 in source:
        raise ValueError("strategy_py contains markdown fences")
    tree = ast.parse(source, filename=STRATEGY)
    if not any(
        isinstance(node, ast.ClassDef) and node.name == "MoonStrategy"
        for node in ast.walk(tree)
    ):
        raise ValueError("strategy_py does not define class MoonStrategy")
    proposal = proposal.strip().splitlines()[0].replace("\t", " ")[:200]
    return proposal, source.rstrip() + "\n"


def run_agent(iteration, prompt, model):
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        raise RuntimeError("NVIDIA_API_KEY is not set")
    try:
        from openai import OpenAI
    except ImportError as exc:
        raise RuntimeError(
            "openai package missing; run pip install -r requirements.txt"
        ) from exc

    client = OpenAI(base_url=NVIDIA_BASE_URL, api_key=key, timeout=180.0)
    log_path = os.path.join(LOGS, f"agent_{iteration}.txt")
    t0 = time.time()
    raw = ""
    last_exc = None

    for attempt in range(1, MAX_API_RETRIES + 1):
        try:
            response = client.chat.completions.create(
                model=model,
                messages=[{"role": "user", "content": prompt}],
                temperature=0.25,
                max_tokens=12000,
            )
            raw = response.choices[0].message.content or ""
            proposal, source = validate_payload(extract_json(raw))
            with open(PROPOSAL, "w", encoding="utf-8", newline="\n") as f:
                f.write(proposal + "\n")
            with open(STRATEGY, "w", encoding="utf-8", newline="\n") as f:
                f.write(source)

            elapsed = time.time() - t0
            with open(log_path, "w", encoding="utf-8") as f:
                f.write(f"timestamp={now()}\n")
                f.write(f"model={model}\n")
                f.write(f"endpoint={NVIDIA_BASE_URL}\n")
                f.write("api_success=true\n")
                f.write(f"attempts={attempt}\n")
                f.write(f"duration_seconds={elapsed:.3f}\n")
                f.write(f"response_characters={len(raw)}\n")
                f.write(f"proposal={proposal}\n")
            return proposal
        except Exception as exc:
            last_exc = exc
            if not (retryable(exc) and attempt < MAX_API_RETRIES):
                break
            time.sleep(min(2 ** (attempt - 1), 8))

    elapsed = time.time() - t0
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"timestamp={now()}\n")
        f.write(f"model={model}\n")
        f.write(f"endpoint={NVIDIA_BASE_URL}\n")
        f.write("api_success=false\n")
        f.write(f"duration_seconds={elapsed:.3f}\n")
        f.write(f"response_characters={len(raw)}\n")
        f.write(f"error_type={type(last_exc).__name__ if last_exc else 'unknown'}\n")
        f.write(f"error={str(last_exc)[:1000] if last_exc else 'unknown'}\n")
    raise RuntimeError(f"NVIDIA research-agent call failed: {last_exc}")


def run_backtest(iteration):
    path = os.path.join(LOGS, f"run_{iteration}.txt")
    t0 = time.time()
    with open(path, "w", encoding="utf-8") as f:
        r = subprocess.run(
            [PY, "harness.py", "--is"],
            stdout=f,
            stderr=subprocess.STDOUT,
            text=True,
        )
    if r.returncode != 0:
        return None, f"harness exited {r.returncode}; see {path}", time.time() - t0
    return load_json(LAST_RUN), "", time.time() - t0


def append_result(ts, iteration, verdict, score, base, ret, sharpe, vol, trades, dd, reason, desc):
    with open(RESULTS, "a", encoding="utf-8", newline="") as f:
        f.write(
            "\t".join(
                map(
                    str,
                    [
                        ts, iteration, verdict, score, base["score"], ret,
                        sharpe, vol, trades, dd, reason.replace("\t", " "),
                        desc.replace("\t", " "),
                    ],
                )
            ) + "\n"
        )


def scoreboard():
    _, rows = results_rows()
    kept = sum(1 for r in rows if len(r.split("\t")) > 2 and r.split("\t")[2] == "KEPT")
    rejected = sum(1 for r in rows if len(r.split("\t")) > 2 and r.split("\t")[2] == "REJECTED")
    crashed = sum(1 for r in rows if len(r.split("\t")) > 2 and r.split("\t")[2] == "CRASH")
    base = load_json(BASELINE)
    print(
        f"[4/4] SCOREBOARD tries={len(rows)} kept={kept} "
        f"rejected={rejected} crash={crashed} current_K={base['score']}"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=0, help="0 = run until STOP")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    args = ap.parse_args()

    print("MOON DEV AUTORESEARCH — NVIDIA NIM")
    print(f"model={args.model}")
    print(f"endpoint={NVIDIA_BASE_URL}")
    print("key=NVIDIA_API_KEY environment variable")

    ensure_seed()
    _, rows = results_rows()
    iteration = len(rows)
    start_iteration = iteration

    if os.path.exists(STOP):
        raise SystemExit("STOP file already exists; remove it before starting")

    while True:
        if os.path.exists(STOP):
            print("STOP file found; ending cleanly")
            break
        if args.iters and iteration - start_iteration >= args.iters:
            break

        iteration += 1
        ts = now()
        base = load_json(BASELINE)
        shutil.copy(BEST, STRATEGY)
        if os.path.exists(PROPOSAL):
            os.remove(PROPOSAL)

        print(f"\nITERATION {iteration} baseline K={base['score']}")
        print("[1/4] AGENT")

        try:
            desc = run_agent(iteration, build_prompt(iteration, base), args.model)
        except Exception as exc:
            verdict = "CRASH"
            reason = str(exc)[:200]
            score = ret = sharpe = vol = dd = "nan"
            trades = 0
            desc = "(agent failed)"
            shutil.copy(BEST, STRATEGY)
        else:
            print(f"proposal: {desc}")
            print("[2/4] BACKTEST")
            result, err, secs = run_backtest(iteration)
            if result is None:
                verdict = "CRASH"
                reason = err
                score = ret = sharpe = vol = dd = "nan"
                trades = 0
                shutil.copy(BEST, STRATEGY)
            else:
                score = result["score"]
                ret = result["return_pct"]
                sharpe = result["sharpe"]
                vol = result["ann_vol_pct"]
                dd = result["max_dd_pct"]
                trades = result["trades"]
                print(
                    f"K={score} return={ret}% sharpe={sharpe} vol={vol}% "
                    f"dd={dd}% trades={trades} runtime={secs:.1f}s"
                )
                if not result["guard_ok"]:
                    verdict = "REJECTED"
                    reason = f"guard: {result['guard_reason']}"
                elif score > base["score"]:
                    verdict = "KEPT"
                    reason = f"K {base['score']} -> {score}"
                else:
                    verdict = "REJECTED"
                    reason = f"K {score} did not beat {base['score']}"

                if verdict == "KEPT":
                    shutil.copy(STRATEGY, BEST)
                    shutil.copy(
                        STRATEGY,
                        os.path.join(KEEPERS, f"{iteration:03d}_K{score}.py"),
                    )
                    shutil.copy(LAST_RUN, BASELINE)
                else:
                    shutil.copy(BEST, STRATEGY)

        print(f"[3/4] VERDICT {verdict}: {reason}")
        append_result(
            ts, iteration, verdict, score, base, ret, sharpe, vol,
            trades, dd, reason, desc
        )
        scoreboard()


if __name__ == "__main__":
    main()
