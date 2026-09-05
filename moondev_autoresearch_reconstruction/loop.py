"""
Moon Dev AUTORESEARCH — NVIDIA NIM variant.

The research harness and keep/revert rules stay local and frozen. NVIDIA only
proposes one complete replacement for strategy.py at a time.
"""

import argparse
import ast
import hashlib
import json
import math
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
os.chdir(HERE)
PY = sys.executable
HARNESS = os.environ.get("AUTORESEARCH_HARNESS", "harness.py")
PROGRAM = os.environ.get("AUTORESEARCH_PROGRAM", "program.md")

STRATEGY = "strategy.py"
BEST = "strategy_best.py"
BASELINE = "baseline.json"
LAST_RUN = "last_run.json"
RESULTS = "results.tsv"
PROPOSAL = "proposal.txt"
STOP = "STOP"
KEEPERS = "keepers"
LOGS = "logs"
SEEN_HASHES = "seen_hashes.json"
EXPERIMENTS = "experiments.jsonl"
LOOKAHEAD_AUDIT = "lookahead_audit.json"

NVIDIA_BASE_URL = "https://integrate.api.nvidia.com/v1"
DEFAULT_MODEL = "nvidia/nemotron-3-super-120b-a12b"
MAX_API_RETRIES = 6


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path):
    with open(path, encoding="utf-8") as f:
        return json.load(f)


def canonical_ast_hash(source):
    tree = ast.parse(source, filename=STRATEGY)
    canonical = ast.dump(tree, annotate_fields=True, include_attributes=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


class _NumericShape(ast.NodeTransformer):
    def visit_Constant(self, node):
        if isinstance(node.value, (int, float)) and not isinstance(node.value, bool):
            return ast.copy_location(ast.Constant(value=0), node)
        return node


def structural_ast_dump(source):
    tree = ast.parse(source, filename=STRATEGY)
    tree = _NumericShape().visit(tree)
    ast.fix_missing_locations(tree)
    return ast.dump(tree, annotate_fields=True, include_attributes=False)


def structural_ast_hash(source):
    canonical = structural_ast_dump(source)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def ast_complexity(source):
    tree = ast.parse(source, filename=STRATEGY)
    nodes = list(ast.walk(tree))
    return {
        "nodes": len(nodes),
        "ifs": sum(isinstance(x, ast.If) for x in nodes),
        "calls": sum(isinstance(x, ast.Call) for x in nodes),
        "numeric_literals": sum(
            isinstance(x, ast.Constant)
            and isinstance(x.value, (int, float))
            and not isinstance(x.value, bool)
            for x in nodes
        ),
    }


def _node_hash(node):
    return hashlib.sha256(
        ast.dump(node, annotate_fields=True, include_attributes=False).encode("utf-8")
    ).hexdigest()


def semantic_units(source):
    """Return stable hashes for meaningful top-level/class units.

    This intentionally ignores formatting/comments and compares regenerated
    complete files by function/method/assignment identity rather than one giant
    whole-file similarity ratio.
    """
    tree = ast.parse(source, filename=STRATEGY)
    units = {}
    for node in tree.body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            units[f"func:{node.name}"] = _node_hash(node)
        elif isinstance(node, ast.ClassDef) and node.name == "MoonStrategy":
            for item in node.body:
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    units[f"MoonStrategy.{item.name}"] = _node_hash(item)
                elif isinstance(item, (ast.Assign, ast.AnnAssign)):
                    targets = item.targets if isinstance(item, ast.Assign) else [item.target]
                    for target in targets:
                        if isinstance(target, ast.Name):
                            units[f"MoonStrategy.assign:{target.id}"] = _node_hash(item)
    return units


def changed_semantic_units(candidate, baseline):
    a = semantic_units(candidate)
    b = semantic_units(baseline)
    keys = sorted(set(a) | set(b))
    return [k for k in keys if a.get(k) != b.get(k)]


def local_change_guard(candidate, baseline):
    changed = changed_semantic_units(candidate, baseline)
    # A normal conceptual change may add one indicator helper and modify init/next.
    # More than four semantic units is almost always a wholesale rewrite.
    if len(changed) > 4:
        return False, f"too many semantic units changed ({len(changed)}): {changed[:8]}"

    changed_methods = [x for x in changed if x.startswith("MoonStrategy.")]
    if len(changed_methods) > 3:
        return False, f"too many MoonStrategy units changed ({len(changed_methods)}): {changed_methods}"

    base_c = ast_complexity(baseline)
    cand_c = ast_complexity(candidate)
    limits = {
        "nodes": 650,
        "ifs": 18,
        "calls": 60,
        "numeric_literals": 40,
    }
    for key, allowance in limits.items():
        if cand_c[key] > base_c[key] + allowance:
            return (
                False,
                f"complexity jump too large for {key}: "
                f"{base_c[key]} -> {cand_c[key]} (allow +{allowance})",
            )
    return True, changed


def risk_control_fingerprint(source):
    tree = ast.parse(source, filename=STRATEGY)
    cls = next(
        (x for x in tree.body if isinstance(x, ast.ClassDef) and x.name == "MoonStrategy"),
        None,
    )
    if cls is None:
        return None
    frozen_assignments = {}
    units_dump = None
    for node in cls.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for target in targets:
                if isinstance(target, ast.Name) and target.id in {
                    "vol_target", "f_max", "vol_lookback",
                }:
                    value = node.value
                    frozen_assignments[target.id] = ast.dump(
                        value, annotate_fields=True, include_attributes=False
                    )
        elif isinstance(node, ast.FunctionDef) and node.name == "_units":
            units_dump = ast.dump(
                node, annotate_fields=True, include_attributes=False
            )
    payload = json.dumps(
        {"assignments": frozen_assignments, "units": units_dump},
        sort_keys=True,
    )
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def load_seen_hashes():
    if not os.path.exists(SEEN_HASHES):
        return set()
    try:
        payload = load_json(SEEN_HASHES)
        return set(payload.get("hashes", []))
    except Exception:
        return set()


def save_seen_hashes(values):
    tmp = SEEN_HASHES + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump({"version": 1, "hashes": sorted(values)}, f, indent=2)
        f.write("\n")
    os.replace(tmp, SEEN_HASHES)


def json_safe(value):
    if isinstance(value, float):
        return value if math.isfinite(value) else None
    if isinstance(value, dict):
        return {k: json_safe(v) for k, v in value.items()}
    if isinstance(value, list):
        return [json_safe(v) for v in value]
    if isinstance(value, tuple):
        return [json_safe(v) for v in value]
    return value


def file_sha256(path):
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def append_experiment_record(
    ts, iteration, model, verdict, reason, desc, base_score,
    candidate_score, candidate_ast_sha, candidate_source_sha,
    best_before_ast_sha, result=None,
):
    prompt_path = os.path.join(LOGS, f"prompt_{iteration}.txt")
    record = {
        "ts": ts,
        "iteration": iteration,
        "model": model,
        "family": os.environ.get("AUTORESEARCH_FAMILY", "unspecified"),
        "market": os.environ.get("AUTORESEARCH_MARKET", "unspecified"),
        "symbol": os.environ.get("AUTORESEARCH_SYMBOL", "unspecified"),
        "profile": os.environ.get("AUTORESEARCH_PROFILE", "unspecified"),
        "verdict": verdict,
        "reason": reason,
        "description": desc,
        "base_score": str(base_score),
        "candidate_score": str(candidate_score),
        "candidate_ast_sha256": candidate_ast_sha,
        "candidate_source_sha256": candidate_source_sha,
        "best_before_ast_sha256": best_before_ast_sha,
        "prompt_sha256": file_sha256(prompt_path) if os.path.exists(prompt_path) else None,
        "program_sha256": file_sha256(PROGRAM) if os.path.exists(PROGRAM) else None,
        "harness_sha256": file_sha256(HARNESS) if os.path.exists(HARNESS) else None,
        "idea_sha256": hashlib.sha256(
            " ".join(str(desc).lower().split()).encode("utf-8")
        ).hexdigest() if desc else None,
    }
    if isinstance(result, dict):
        for key in [
            "return_pct", "cagr_pct", "sharpe", "ann_vol_pct",
            "max_dd_pct", "intrabar_dd_proxy_pct", "trades", "pf",
            "psr_zero", "bootstrap_sharpe_p10",
            "bootstrap_mean_positive_pvalue", "evidence_grade",
            "calmar", "sortino_annualized", "ulcer_index_pct", "benchmark_cagr_pct",
            "excess_cagr_vs_buyhold_pct", "sharpe_minus_buyhold",
            "risk_cap_utilization", "positive_fold_fraction",
        ]:
            record[key] = result.get(key)
        record["extreme_stress_return_pct"] = (
            (result.get("extreme_stress") or {}).get("return_pct")
        )
        record["guard_ok"] = bool(result.get("guard_ok"))
        record["cscv_slice_k"] = [
            x.get("raw_k") for x in (result.get("cscv_slices") or [])
        ]
        record["fold_raw_k"] = [
            x.get("raw_k") for x in (result.get("folds") or [])
        ]
        record["fold_return_pct"] = [
            x.get("return_pct") for x in (result.get("folds") or [])
        ]
        paired = result.get("paired_vs_baseline") or {}
        audit = result.get("lookahead_audit") or {}
        record["lookahead_audit_pass"] = audit.get("passed")
        record["lookahead_audit_reason"] = audit.get("reason")
        record["selection_eligible"] = bool(result.get("guard_ok")) and (
            audit.get("passed") is not False
        )
        record["comparable_folds"] = paired.get("comparable_folds")
        record["median_fold_delta_k"] = paired.get("median_fold_delta_k")
        record["improved_fold_fraction"] = paired.get("improved_fold_fraction")
    with open(EXPERIMENTS, "a", encoding="utf-8") as f:
        f.write(
            json.dumps(json_safe(record), sort_keys=True, allow_nan=False) + "\n"
        )


def ensure_seed():
    os.makedirs(KEEPERS, exist_ok=True)
    os.makedirs(LOGS, exist_ok=True)
    if not (os.path.exists(BASELINE) and os.path.exists(BEST)):
        print("[setup] freezing seed baseline")
        with open(os.path.join(LOGS, "baseline.txt"), "w", encoding="utf-8") as f:
            r = subprocess.run(
                [PY, HARNESS, "--is", "--set-baseline"],
                stdout=f,
                stderr=subprocess.STDOUT,
                text=True,
                env=safe_backtest_env(),
            )
        if r.returncode != 0:
            raise RuntimeError("baseline failed; see logs/baseline.txt")
        shutil.copy(STRATEGY, BEST)
        shutil.copy(STRATEGY, os.path.join(KEEPERS, "000_seed.py"))
    if not os.path.exists(RESULTS):
        with open(RESULTS, "w", encoding="utf-8", newline="") as f:
            f.write(
                "ts\titer\tverdict\tscore\tbase_score\tret_pct\tcagr_pct\t"
                "sharpe\tann_vol\ttrades\tmax_dd\tpsr_zero\tboot_p\t"
                "evidence\textreme_ret_pct\tguard\tdesc\n"
            )
    seen = load_seen_hashes()
    if os.path.exists(BEST):
        with open(BEST, encoding="utf-8") as f:
            seen.add(canonical_ast_hash(f.read()))
    save_seen_hashes(seen)


def results_rows():
    with open(RESULTS, encoding="utf-8") as f:
        lines = f.read().rstrip("\n").split("\n")
    return lines[0], lines[1:]


def build_prompt(iteration, base):
    header, rows = results_rows()
    with open(PROGRAM, encoding="utf-8") as f:
        program = f.read()
    with open(STRATEGY, encoding="utf-8") as f:
        strategy = f.read()
    prompt = "\n".join(
        [
            program,
            "",
            f"## Current strategy family: {os.environ.get('AUTORESEARCH_FAMILY', 'unspecified')}",
            f"## Current market: {os.environ.get('AUTORESEARCH_MARKET', 'crypto')}",
            f"## Current instrument: {os.environ.get('AUTORESEARCH_SYMBOL', 'ETHUSDT')}",
            f"## Current research profile: {os.environ.get('AUTORESEARCH_PROFILE', 'prop')}",
            f"## HARD MAX DRAWDOWN: {os.environ.get('AUTORESEARCH_MAX_DD_PCT', '10.0')}%",
            "Any candidate exceeding that historical max-drawdown limit is invalid,",
            "regardless of return, Sharpe, or K. Do not propose a sizing increase",
            "or structural change that violates the profile's drawdown ceiling.",
            "",
            f"## Current baseline (must beat): score K={base['score']}, "
            f"CAGR {base.get('cagr_pct', 'n/a')}%, Sharpe {base.get('sharpe', 'n/a')}, "
            f"ann vol {base['ann_vol_pct']}%, trades {base['trades']}, "
            f"PSR {base.get('psr_zero', 'n/a')}, evidence {base.get('evidence_grade', 'n/a')}",
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
            "Prefer a causal market hypothesis over another generic indicator filter.",
            "Read the recent results and avoid repeating a concept that already failed on this track.",
            "Return the COMPLETE replacement strategy.py, not a patch.",
            "COPY the current file verbatim except for the smallest code region needed",
            "for that one conceptual change. Do not rewrite unchanged helpers/imports.",
            "A normal accepted edit changes at most one helper plus init/next.",
            "Preserve class MoonStrategy and obey every law in program.md.",
            "Do not change position size merely to increase returns.",
            "Do not use future data or OOS information.",
            "Do not run a backtest yourself.",
            "",
            "For safety, strategy.py may import ordinary numeric/trading libraries",
            "such as numpy, pandas, math, statistics, and backtesting only.",
            "Do not use filesystem, shell, subprocess, network, environment,",
            "dynamic-import, eval, exec, or file I/O APIs.",
            "",
            "OUTPUT FORMAT — EXACTLY THESE MARKERS, RAW PYTHON, NO JSON:",
            "<<<PROPOSAL>>>",
            "one-line description of the single conceptual change",
            "<<<STRATEGY_PY>>>",
            "<complete raw Python source for strategy.py>",
            "<<<END_STRATEGY_PY>>>",
            "Do not use markdown code fences. Do not add text outside the markers.",
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


def parse_delimited_response(raw):
    text = (raw or "").strip()
    p = text.find("<<<PROPOSAL>>>")
    s = text.find("<<<STRATEGY_PY>>>")
    e = text.rfind("<<<END_STRATEGY_PY>>>")
    if p < 0 or s < 0 or e < 0 or not (p < s < e):
        raise ValueError("required output markers missing or out of order")
    if text[:p].strip() or text[e + len("<<<END_STRATEGY_PY>>>"):].strip():
        raise ValueError("unexpected text outside output markers")
    proposal = text[p + len("<<<PROPOSAL>>>"):s].strip()
    source = text[s + len("<<<STRATEGY_PY>>>"):e].strip()
    return proposal, source


def parse_json_fallback(raw):
    text = (raw or "").strip()
    try:
        payload = json.loads(text)
    except json.JSONDecodeError:
        first = text.find("{")
        last = text.rfind("}")
        if first < 0 or last <= first:
            raise
        payload = json.loads(text[first:last + 1])
    if not isinstance(payload, dict):
        raise ValueError("JSON fallback response must be an object")
    return payload.get("proposal"), payload.get("strategy_py")


ALLOWED_IMPORT_ROOTS = {
    "numpy", "pandas", "math", "statistics", "backtesting",
}
FORBIDDEN_CALL_NAMES = {
    "open", "exec", "eval", "compile", "__import__", "input", "breakpoint",
    "getattr", "setattr", "delattr", "globals", "locals", "vars", "dir",
}
FORBIDDEN_ATTR_CALLS = {
    "read_csv", "read_json", "read_pickle", "read_parquet", "read_excel",
    "read_html", "read_xml", "read_sql", "read_fwf", "read_sas",
    "read_stata", "read_feather", "read_clipboard",
    "to_csv", "to_json", "to_pickle", "to_parquet", "to_excel", "to_sql",
    "load", "save", "loadtxt", "savetxt", "genfromtxt", "fromfile", "tofile",
    "memmap", "open_memmap", "DataSource", "HDFStore", "ExcelFile",
    "TextFileReader", "urlopen", "urlretrieve", "get_handle",
}


def validate_source_safety(tree):
    nodes = list(ast.walk(tree))
    if len(nodes) > 4000:
        raise ValueError(f"strategy AST too large: {len(nodes)} nodes > 4000")
    if sum(isinstance(x, ast.If) for x in nodes) > 140:
        raise ValueError("strategy has pathologically many conditional branches")
    if sum(isinstance(x, ast.Call) for x in nodes) > 360:
        raise ValueError("strategy has pathologically many function calls")
    numeric_literals = sum(
        isinstance(x, ast.Constant)
        and isinstance(x.value, (int, float))
        and not isinstance(x.value, bool)
        for x in nodes
    )
    if numeric_literals > 280:
        raise ValueError("strategy has pathologically many numeric literals / degrees of freedom")

    allow_calendar = os.environ.get("AUTORESEARCH_ALLOW_CALENDAR") == "1"
    for node in nodes:
        if isinstance(node, ast.Name) and node.id.startswith("__"):
            raise ValueError(
                f"dunder/introspection name access is forbidden: {node.id}"
            )
        if isinstance(node, ast.Attribute):
            if node.attr.startswith("__"):
                raise ValueError("dunder/introspection attribute access is forbidden")
            if (
                not allow_calendar
                and node.attr in {
                    "index", "year", "month", "day", "date",
                    "datetime", "dayofweek", "weekday",
                }
            ):
                raise ValueError(
                    f"calendar/index access forbidden in this non-calendar family: {node.attr}"
                )
        if isinstance(node, ast.Constant) and not allow_calendar:
            if isinstance(node.value, int) and 1990 <= node.value <= 2030:
                raise ValueError("year-like integer literal forbidden in non-calendar family")
            if isinstance(node.value, str) and re.search(
                r"\\b(?:19|20)\\d{2}[-/]", node.value
            ):
                raise ValueError("date-like string literal forbidden in non-calendar family")

        if isinstance(node, ast.Import):
            names = [alias.name for alias in node.names]
        elif isinstance(node, ast.ImportFrom):
            names = [node.module] if node.module else []
        else:
            names = []

        for name in names:
            root = name.split(".", 1)[0]
            if root not in ALLOWED_IMPORT_ROOTS:
                raise ValueError(
                    f"import not allowed in strategy.py: {name}; "
                    f"allowed roots are {sorted(ALLOWED_IMPORT_ROOTS)}"
                )

        if isinstance(node, ast.Call):
            # Calls resolved through subscriptions/lambdas can bypass simple
            # name-based safety checks (e.g. __builtins__["open"](...)).
            # Strategy code has no legitimate need for dynamic call targets.
            if not isinstance(node.func, (ast.Name, ast.Attribute)):
                raise ValueError("dynamic call targets are forbidden in strategy.py")
            if isinstance(node.func, ast.Name) and node.func.id in FORBIDDEN_CALL_NAMES:
                raise ValueError(f"forbidden call in strategy.py: {node.func.id}")
            if isinstance(node.func, ast.Attribute):
                if node.func.attr in FORBIDDEN_ATTR_CALLS or node.func.attr.startswith("read_"):
                    raise ValueError(
                        f"forbidden I/O-style call in strategy.py: {node.func.attr}"
                    )


def validate_payload(proposal, source):
    if not isinstance(proposal, str) or not proposal.strip():
        raise ValueError("missing non-empty proposal")
    if not isinstance(source, str) or not source.strip():
        raise ValueError("missing non-empty strategy source")
    if len(source) > 30000:
        raise ValueError("strategy source exceeds 30,000 characters")
    if chr(96) * 3 in source:
        raise ValueError("strategy source contains markdown fences")
    tree = ast.parse(source, filename=STRATEGY)
    if not any(
        isinstance(node, ast.ClassDef) and node.name == "MoonStrategy"
        for node in ast.walk(tree)
    ):
        raise ValueError("strategy.py does not define class MoonStrategy")
    validate_source_safety(tree)
    proposal = proposal.strip().splitlines()[0].replace("\t", " ")[:200]
    return proposal, source.rstrip() + "\n"


def parse_and_validate(raw):
    first_error = None
    try:
        proposal, source = parse_delimited_response(raw)
        return validate_payload(proposal, source)
    except Exception as exc:
        first_error = exc
    try:
        proposal, source = parse_json_fallback(raw)
        return validate_payload(proposal, source)
    except Exception as exc:
        raise ValueError(
            f"delimiter parse failed ({first_error}); JSON fallback failed ({exc})"
        ) from exc


def log_agent_attempt(iteration, attempt, raw, error=None):
    path = os.path.join(LOGS, f"agent_{iteration}_attempt_{attempt}.txt")
    with open(path, "w", encoding="utf-8") as f:
        f.write(f"timestamp={now()}\n")
        f.write(f"attempt={attempt}\n")
        if error:
            f.write(f"error={type(error).__name__}: {str(error)[:1000]}\n")
        f.write(f"response_characters={len(raw or '')}\n")
        f.write("\n--- RAW MODEL RESPONSE ---\n")
        f.write((raw or "")[:50000])


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
    last_raw = ""
    last_exc = None
    repair_note = ""

    for attempt in range(1, MAX_API_RETRIES + 1):
        messages = [
            {
                "role": "system",
                "content": (
                    "You are a precise quantitative strategy code editor. "
                    "Follow the requested output protocol exactly."
                ),
            },
            {"role": "user", "content": prompt},
        ]
        if repair_note:
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Your previous response was rejected before backtesting. "
                        f"Validation error: {repair_note}\n"
                        "Return one valid candidate again using the exact markers. "
                        "Copy the current strategy verbatim and make only one localized "
                        "conceptual edit; do not regenerate or refactor unrelated code."
                    ),
                }
            )

        try:
            response = client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=0.15,
                max_tokens=12000,
            )
            raw = response.choices[0].message.content or ""
            last_raw = raw
            try:
                proposal, source = parse_and_validate(raw)
            except Exception as exc:
                last_exc = exc
                repair_note = str(exc)[:800]
                log_agent_attempt(iteration, attempt, raw, exc)
                if attempt < MAX_API_RETRIES:
                    continue
                break

            log_agent_attempt(iteration, attempt, raw)
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
            log_agent_attempt(iteration, attempt, last_raw, exc)
            if retryable(exc) and attempt < MAX_API_RETRIES:
                time.sleep(min(2 ** (attempt - 1), 20))
                continue
            break

    elapsed = time.time() - t0
    with open(log_path, "w", encoding="utf-8") as f:
        f.write(f"timestamp={now()}\n")
        f.write(f"model={model}\n")
        f.write(f"endpoint={NVIDIA_BASE_URL}\n")
        f.write("api_success=false\n")
        f.write(f"duration_seconds={elapsed:.3f}\n")
        f.write(f"response_characters={len(last_raw)}\n")
        f.write(f"error_type={type(last_exc).__name__ if last_exc else 'unknown'}\n")
        f.write(f"error={str(last_exc)[:1000] if last_exc else 'unknown'}\n")
    raise RuntimeError(f"NVIDIA research-agent call failed: {last_exc}")


def safe_backtest_env():
    env = dict(os.environ)
    for key in list(env):
        upper = key.upper()
        if (
            upper == "NVIDIA_API_KEY"
            or upper == "GH_TOKEN"
            or upper == "GITHUB_TOKEN"
            or upper.endswith("_TOKEN")
            or upper.endswith("_API_KEY")
            or upper.endswith("_SECRET")
        ):
            env.pop(key, None)
    env["PYTHONNOUSERSITE"] = "1"
    return env

def run_backtest(iteration):
    path = os.path.join(LOGS, f"run_{iteration}.txt")
    t0 = time.time()
    with open(path, "w", encoding="utf-8") as f:
        r = subprocess.run(
            [PY, HARNESS, "--is"],
            stdout=f,
            stderr=subprocess.STDOUT,
            text=True,
            env=safe_backtest_env(),
        )
    if r.returncode != 0:
        return None, f"harness exited {r.returncode}; see {path}", time.time() - t0
    return load_json(LAST_RUN), "", time.time() - t0


def paired_fold_improvement(base, candidate):
    base_folds = {
        x.get("name"): x for x in (base.get("folds") or [])
        if x.get("name") is not None
    }
    candidate_folds = {
        x.get("name"): x for x in (candidate.get("folds") or [])
        if x.get("name") is not None
    }
    deltas = []
    for name in sorted(set(base_folds) & set(candidate_folds)):
        try:
            a = float(base_folds[name].get("raw_k"))
            b = float(candidate_folds[name].get("raw_k"))
        except Exception:
            continue
        if math.isfinite(a) and math.isfinite(b):
            deltas.append(b - a)
    if not deltas:
        return {
            "comparable_folds": 0,
            "median_fold_delta_k": None,
            "improved_fold_fraction": None,
        }
    ordered = sorted(deltas)
    n = len(ordered)
    median = (
        ordered[n // 2] if n % 2
        else 0.5 * (ordered[n // 2 - 1] + ordered[n // 2])
    )
    return {
        "comparable_folds": n,
        "median_fold_delta_k": round(float(median), 6),
        "improved_fold_fraction": round(
            sum(x > 0 for x in deltas) / n, 4
        ),
    }


def run_lookahead_audit(iteration):
    if os.path.exists(LOOKAHEAD_AUDIT):
        os.remove(LOOKAHEAD_AUDIT)
    log_path = os.path.join(LOGS, f"lookahead_{iteration}.txt")
    with open(log_path, "w", encoding="utf-8") as f:
        proc = subprocess.run(
            [PY, HARNESS, "--lookahead-audit"],
            stdout=f,
            stderr=subprocess.STDOUT,
            text=True,
            env=safe_backtest_env(),
        )
    if not os.path.exists(LOOKAHEAD_AUDIT):
        return {
            "passed": False,
            "reason": f"lookahead audit exited {proc.returncode} without result",
        }
    try:
        out = load_json(LOOKAHEAD_AUDIT)
    except Exception as exc:
        return {"passed": False, "reason": f"lookahead audit JSON error: {exc}"}
    if proc.returncode != 0:
        out["passed"] = False
        out.setdefault("reason", f"lookahead audit exited {proc.returncode}")
    return out


def append_result(
    ts, iteration, verdict, score, base, ret, sharpe, vol,
    trades, dd, reason, desc, result=None,
):
    result = result if isinstance(result, dict) else {}
    with open(RESULTS, "a", encoding="utf-8", newline="") as f:
        f.write(
            "\t".join(
                map(
                    str,
                    [
                        ts, iteration, verdict, score, base["score"], ret,
                        result.get("cagr_pct", "nan"),
                        sharpe, vol, trades, dd,
                        result.get("psr_zero", "nan"),
                        result.get("bootstrap_mean_positive_pvalue", "nan"),
                        result.get("evidence_grade", ""),
                        (result.get("extreme_stress") or {}).get("return_pct", "nan"),
                        reason.replace("\t", " "),
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
    duplicate = sum(1 for r in rows if len(r.split("\t")) > 2 and r.split("\t")[2] == "DUPLICATE")
    parameter_only = sum(
        1 for r in rows
        if len(r.split("\t")) > 2 and r.split("\t")[2] == "PARAMETER_ONLY"
    )
    too_broad = sum(
        1 for r in rows
        if len(r.split("\t")) > 2 and r.split("\t")[2] == "TOO_BROAD"
    )
    risk_change = sum(
        1 for r in rows
        if len(r.split("\t")) > 2 and r.split("\t")[2] == "RISK_CONTROL_CHANGE"
    )
    base = load_json(BASELINE)
    print(
        f"[4/4] SCOREBOARD tries={len(rows)} kept={kept} "
        f"rejected={rejected} crash={crashed} duplicate={duplicate} "
        f"parameter_only={parameter_only} too_broad={too_broad} "
        f"risk_change={risk_change} current_K={base['score']}"
    )


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--iters", type=int, default=0, help="0 = run until STOP")
    ap.add_argument("--model", default=DEFAULT_MODEL)
    ap.add_argument("--fallback-model", default=DEFAULT_MODEL)
    args = ap.parse_args()

    print("MOON DEV AUTORESEARCH — NVIDIA NIM")
    print(f"model={args.model}")
    print(f"endpoint={NVIDIA_BASE_URL}")
    print(f"harness={HARNESS}")
    print(f"program={PROGRAM}")
    print(f"family={os.environ.get('AUTORESEARCH_FAMILY', 'unspecified')}")
    print(f"market={os.environ.get('AUTORESEARCH_MARKET', 'crypto')}")
    print(f"instrument={os.environ.get('AUTORESEARCH_SYMBOL', 'ETHUSDT')}")
    print("key=NVIDIA_API_KEY environment variable")
    print(f"profile={os.environ.get('AUTORESEARCH_PROFILE', 'prop')}")
    print(f"max_dd_limit={os.environ.get('AUTORESEARCH_MAX_DD_PCT', '10.0')}%")

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
        with open(BEST, encoding="utf-8") as f:
            best_before_source = f.read()
        best_before_ast_sha = canonical_ast_hash(best_before_source)
        candidate_ast_sha = None
        candidate_source_sha = None
        result = None
        shutil.copy(BEST, STRATEGY)
        if os.path.exists(PROPOSAL):
            os.remove(PROPOSAL)

        print(f"\nITERATION {iteration} baseline K={base['score']}")
        print("[1/4] AGENT")

        used_model = args.model
        prompt = build_prompt(iteration, base)
        try:
            desc = run_agent(iteration, prompt, used_model)
        except Exception as primary_exc:
            if args.fallback_model and args.fallback_model != used_model:
                print(
                    f"[agent fallback] {used_model} failed; trying "
                    f"{args.fallback_model}"
                )
                used_model = args.fallback_model
                try:
                    desc = run_agent(iteration, prompt, used_model)
                except Exception as fallback_exc:
                    exc = fallback_exc
                else:
                    exc = None
            else:
                exc = primary_exc
            if exc is not None:
                verdict = "CRASH"
                reason = str(exc)[:200]
                score = ret = sharpe = vol = dd = "nan"
                trades = 0
                desc = "(agent failed)"
                shutil.copy(BEST, STRATEGY)
        else:
            exc = None

        if exc is None:
            print(f"proposal: {desc}")
            with open(STRATEGY, encoding="utf-8") as f:
                candidate_source = f.read()
            with open(BEST, encoding="utf-8") as f:
                best_source = f.read()
            fingerprint = canonical_ast_hash(candidate_source)
            candidate_ast_sha = fingerprint
            candidate_source_sha = hashlib.sha256(
                candidate_source.encode("utf-8")
            ).hexdigest()
            seen = load_seen_hashes()
            if fingerprint in seen:
                verdict = "DUPLICATE"
                reason = "semantic AST duplicate of a previously generated candidate"
                score = ret = sharpe = vol = dd = "nan"
                trades = 0
                shutil.copy(BEST, STRATEGY)
            elif risk_control_fingerprint(candidate_source) != risk_control_fingerprint(best_source):
                verdict = "RISK_CONTROL_CHANGE"
                reason = "host-owned sizing controls (_units/vol_target/f_max/vol_lookback) changed"
                score = ret = sharpe = vol = dd = "nan"
                trades = 0
                seen.add(fingerprint)
                save_seen_hashes(seen)
                shutil.copy(BEST, STRATEGY)
            elif structural_ast_hash(candidate_source) == structural_ast_hash(best_source):
                verdict = "PARAMETER_ONLY"
                reason = "numeric-only parameter mutation rejected by hardened anti-curve-fit policy"
                score = ret = sharpe = vol = dd = "nan"
                trades = 0
                seen.add(fingerprint)
                save_seen_hashes(seen)
                shutil.copy(BEST, STRATEGY)
            else:
                local_ok, local_detail = local_change_guard(
                    candidate_source, best_source
                )
                if not local_ok:
                    verdict = "TOO_BROAD"
                    reason = f"localized-change guard: {local_detail}"
                    score = ret = sharpe = vol = dd = "nan"
                    trades = 0
                    seen.add(fingerprint)
                    save_seen_hashes(seen)
                    shutil.copy(BEST, STRATEGY)
                else:
                    seen.add(fingerprint)
                    save_seen_hashes(seen)
                    print(f"localized semantic change: {local_detail}")
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
                        paired = paired_fold_improvement(base, result)
                        result["paired_vs_baseline"] = paired
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
                        else:
                            base_score = float(base["score"])
                            base_delta = max(
                                0.005,
                                min(0.02, 0.01 * max(abs(base_score), 0.10)),
                            )
                            # Repeated adaptive search creates a multiple-testing
                            # burden. Raise the required material improvement
                            # modestly as this track consumes more experiments.
                            search_penalty = 1.0 + 0.12 * math.log1p(iteration)
                            min_delta = min(0.04, base_delta * search_penalty)
                            paired = result.get("paired_vs_baseline") or {}
                            fold_ok = True
                            if int(paired.get("comparable_folds") or 0) >= 3:
                                fold_ok = (
                                    float(paired.get("improved_fold_fraction") or 0.0) >= 0.50
                                    and float(paired.get("median_fold_delta_k") or -1e9) >= 0.0
                                )
                            if float(score) > base_score + min_delta and fold_ok:
                                lookahead = run_lookahead_audit(iteration)
                                result["lookahead_audit"] = lookahead
                                if lookahead.get("passed"):
                                    verdict = "KEPT"
                                    reason = (
                                        f"K {base_score} -> {score}; paired folds "
                                        f"{paired.get('improved_fold_fraction')} improved; "
                                        f"prefix lookahead audit PASS "
                                        f"(required delta {min_delta:.4f})"
                                    )
                                else:
                                    verdict = "REJECTED"
                                    reason = (
                                        "prefix-invariance lookahead audit failed: "
                                        f"{lookahead.get('reason')}"
                                    )
                            elif not fold_ok:
                                verdict = "REJECTED"
                                reason = (
                                    "paired chronological folds did not show a "
                                    "median/majority improvement over baseline"
                                )
                            else:
                                verdict = "REJECTED"
                                reason = (
                                    f"K {score} did not exceed {base_score} "
                                    f"by adaptive required delta {min_delta:.4f}"
                                )

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
            trades, dd, reason, desc, result=result
        )
        append_experiment_record(
            ts=ts,
            iteration=iteration,
            model=used_model,
            verdict=verdict,
            reason=reason,
            desc=desc,
            base_score=base["score"],
            candidate_score=score,
            candidate_ast_sha=candidate_ast_sha,
            candidate_source_sha=candidate_source_sha,
            best_before_ast_sha=best_before_ast_sha,
            result=result,
        )
        scoreboard()


if __name__ == "__main__":
    main()
