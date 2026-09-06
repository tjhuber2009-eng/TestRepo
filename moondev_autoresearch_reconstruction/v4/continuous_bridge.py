"""Bridge continuous AUTORESEARCH champions into AUTORESEARCH v4.

The continuous engine is an adaptive idea generator.  This module deliberately
keeps promotion stricter than discovery:

* development-only leaderboard rows are pre-screened for guard/cost evidence;
* private candidates are replayed on the v4 development data and require an
  actual PBO diagnostic before they can become portfolio-eligible;
* prop candidates are only handed to the intraday engine when an explicit
  causal adapter exists for their signal family.

No hidden-validation or final-OOS data is read here.
"""
from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
import ast
import json
import math
import re
import subprocess
from typing import Callable, Iterable

import numpy as np
import pandas as pd


STATE_BRANCH = "continuous-autoresearch-state"
LEADERBOARD_PATH = (
    "moondev_autoresearch_reconstruction/continuous_state/"
    "leaderboard_latest.json"
)
TRACK_ROOT = (
    "moondev_autoresearch_reconstruction/continuous_state/tracks"
)

TARGET_SYMBOL = {
    "btc": "BTCUSDT",
    "eth": "ETHUSDT",
    "sol": "SOLUSDT",
    "spy": "SPY",
    "qqq": "QQQ",
    "tqqq": "TQQQ",
    "aapl": "AAPL",
    "nvda": "NVDA",
    "es": "ES=F",
    "nq": "NQ=F",
    "gold": "GC=F",
    "oil": "CL=F",
}

# These adapters replay the continuous engine's daily *signal* causally on the
# v4 hourly prop path.  V4 intentionally replaces source sizing with its own
# prop exposure optimizer.
PROP_SIGNAL_ADAPTERS = {
    "btc_rsi_adx": "daily_rsi_adx",
    "sentinel63": "daily_sentinel",
    "sentinel65": "daily_sentinel",
}

GRADE_RANK = {"A": 3, "B": 2, "C": 1, "D": 0}


@dataclass(frozen=True)
class PromotionCandidate:
    track_id: str
    profile: str
    family: str
    target: str
    symbol: str
    market: str
    exactness: str | None
    evidence_grade: str | None
    development_cagr_pct: float
    development_max_dd_pct: float
    development_sharpe: float
    development_pf: float
    development_years: float
    development_trades: int
    selection_score: float
    multiple_test_qvalue: float | None
    pbo: float | None
    extreme_stress_return_pct: float
    adapter: str | None

    def to_dict(self) -> dict:
        return asdict(self)


def _git_show(ref: str, path: str) -> str | None:
    """Read one file from a local git ref, fetching the state branch if needed."""
    refs = (f"refs/remotes/origin/{ref}", ref)
    for git_ref in refs:
        try:
            return subprocess.check_output(
                ["git", "show", f"{git_ref}:{path}"],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except (OSError, subprocess.SubprocessError):
            pass
    try:
        subprocess.run(
            [
                "git", "fetch", "origin",
                f"{ref}:refs/remotes/origin/{ref}",
            ],
            check=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=45,
        )
        return subprocess.check_output(
            ["git", "show", f"refs/remotes/origin/{ref}:{path}"],
            text=True,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.SubprocessError):
        return None


def load_continuous_leaderboard() -> dict | None:
    raw = _git_show(STATE_BRANCH, LEADERBOARD_PATH)
    if not raw:
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if payload.get("protocol") != "nested_chronological_v3":
        return None
    return payload


def load_candidate_source(track_id: str) -> str | None:
    if not re.fullmatch(r"[A-Za-z0-9_.-]+", str(track_id)):
        return None
    return _git_show(
        STATE_BRANCH,
        f"{TRACK_ROOT}/{track_id}/strategy_best.py",
    )


def _finite(x, default: float = float("nan")) -> float:
    try:
        y = float(x)
    except (TypeError, ValueError):
        return default
    return y if math.isfinite(y) else default


def _eligible_row(row: dict, profile: str) -> bool:
    if row.get("profile") != profile:
        return False
    if not bool(row.get("development_guard_ok")):
        return False
    if str(row.get("evidence_grade") or "D") not in {"A", "B"}:
        return False
    if _finite(row.get("development_cagr_pct"), -1.0) <= 0.0:
        return False
    if _finite(row.get("extreme_stress_return_pct"), -1.0) <= 0.0:
        return False
    q = row.get("multiple_test_qvalue")
    if q is not None and _finite(q, 1.0) > 0.10:
        return False
    pbo = row.get("pbo")
    if pbo is not None and _finite(pbo, 1.0) > 0.55:
        return False
    if int(row.get("development_trades") or 0) < 8:
        return False
    return row.get("target") in TARGET_SYMBOL


def select_candidates(
    profile: str,
    *,
    available_symbols: Iterable[str] | None = None,
    per_target: int = 2,
    max_total: int = 12,
    leaderboard: dict | None = None,
) -> list[PromotionCandidate]:
    """Select a diversified promotion queue from the continuous leaderboard."""
    payload = leaderboard if leaderboard is not None else load_continuous_leaderboard()
    if not payload:
        return []
    allowed = None if available_symbols is None else set(available_symbols)
    rows = []
    for row in payload.get("rows", []):
        if not _eligible_row(row, profile):
            continue
        symbol = TARGET_SYMBOL[str(row["target"])]
        if allowed is not None and symbol not in allowed:
            continue
        rows.append(row)

    rows.sort(
        key=lambda r: (
            _finite(r.get("selection_score"), -1e99),
            _finite(r.get("development_cagr_pct"), -1e99),
            _finite(r.get("development_sharpe"), -1e99),
            GRADE_RANK.get(str(r.get("evidence_grade")), -1),
        ),
        reverse=True,
    )
    counts: dict[str, int] = {}
    out = []
    for row in rows:
        target = str(row["target"])
        if counts.get(target, 0) >= int(per_target):
            continue
        counts[target] = counts.get(target, 0) + 1
        family = str(row["family"])
        out.append(
            PromotionCandidate(
                track_id=str(row["track_id"]),
                profile=profile,
                family=family,
                target=target,
                symbol=TARGET_SYMBOL[target],
                market=str(row.get("market") or ""),
                exactness=(
                    None if row.get("exactness") is None
                    else str(row.get("exactness"))
                ),
                evidence_grade=(
                    None if row.get("evidence_grade") is None
                    else str(row.get("evidence_grade"))
                ),
                development_cagr_pct=_finite(row.get("development_cagr_pct"), 0.0),
                development_max_dd_pct=_finite(row.get("development_max_dd_pct"), 0.0),
                development_sharpe=_finite(row.get("development_sharpe"), 0.0),
                development_pf=_finite(row.get("development_pf"), 0.0),
                development_years=_finite(row.get("development_years"), 0.0),
                development_trades=int(row.get("development_trades") or 0),
                selection_score=_finite(row.get("selection_score"), -1e99),
                multiple_test_qvalue=(
                    None if row.get("multiple_test_qvalue") is None
                    else _finite(row.get("multiple_test_qvalue"))
                ),
                pbo=(
                    None if row.get("pbo") is None
                    else _finite(row.get("pbo"))
                ),
                extreme_stress_return_pct=_finite(
                    row.get("extreme_stress_return_pct"), 0.0
                ),
                adapter=PROP_SIGNAL_ADAPTERS.get(family),
            )
        )
        if len(out) >= int(max_total):
            break
    return out


def _parse_int(source: str, pattern: str, default: int) -> int:
    m = re.search(pattern, source, re.M)
    return int(m.group(1)) if m else int(default)


def _parse_float(source: str, pattern: str, default: float) -> float:
    m = re.search(pattern, source, re.M)
    return float(m.group(1)) if m else float(default)


def _parse_int_required(source: str, pattern: str, name: str) -> int:
    m = re.search(pattern, source, re.M)
    if not m:
        raise ValueError(f"required promoted parameter not found: {name}")
    return int(m.group(1))


def _parse_float_required(source: str, pattern: str, name: str) -> float:
    m = re.search(pattern, source, re.M)
    if not m:
        raise ValueError(f"required promoted parameter not found: {name}")
    return float(m.group(1))


def source_execution_adapter_blocker(source: str) -> str | None:
    """Return why a daily signal adapter cannot preserve source execution."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return "source_parse_failed"

    strategy = next(
        (
            node for node in tree.body
            if isinstance(node, ast.ClassDef) and node.name == "MoonStrategy"
        ),
        None,
    )
    if strategy is None:
        return "strategy_class_missing"
    methods = {
        node.name: node
        for node in strategy.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    next_fn = methods.get("next")
    if next_fn is None:
        return "next_method_missing"

    pending = [next_fn]
    visited = set()
    while pending:
        fn = pending.pop()
        if fn.name in visited:
            continue
        visited.add(fn.name)
        for node in ast.walk(fn):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                attr = node.func.attr
                if attr == "sell":
                    return "short_entry_not_transferred"
                if attr == "buy":
                    for kw in node.keywords:
                        if kw.arg in {"sl", "tp"} and not (
                            isinstance(kw.value, ast.Constant)
                            and kw.value.value is None
                        ):
                            return "source_bracket_not_transferred"
                if (
                    isinstance(node.func.value, ast.Name)
                    and node.func.value.id == "self"
                    and attr in methods
                    and attr not in visited
                ):
                    pending.append(methods[attr])
            if (
                isinstance(node, ast.Attribute)
                and isinstance(node.ctx, ast.Store)
                and node.attr in {"sl", "tp"}
            ):
                return "dynamic_stop_or_target_not_transferred"
    return None


def prop_transfer_candidates(
    available_symbols: Iterable[str],
    *,
    leaderboard: dict | None = None,
) -> tuple[list[dict], dict]:
    """Return supported prop-transfer parameter seeds plus a full audit manifest."""
    candidates = select_candidates(
        "prop",
        available_symbols=available_symbols,
        per_target=2,
        max_total=12,
        leaderboard=leaderboard,
    )
    supported = []
    audit = []
    for c in candidates:
        row = c.to_dict()
        source = load_candidate_source(c.track_id)
        if c.adapter is None:
            row["transfer_status"] = "adapter_required"
            audit.append(row)
            continue
        if not source:
            row["transfer_status"] = "source_unavailable"
            audit.append(row)
            continue

        blocker = source_execution_adapter_blocker(source)
        if blocker is not None:
            row["transfer_status"] = "adapter_required"
            row["transfer_reason"] = blocker
            audit.append(row)
            continue

        try:
            params = {
                "family": "continuous_daily_signal",
                "source_family": c.family,
                "source_target": c.symbol,
                "continuous_track_id": c.track_id,
                "adapter": c.adapter,
                "source_vol_target": _parse_float_required(
                    source,
                    r"^\s*vol_target\s*=\s*([0-9.]+)",
                    "source_vol_target",
                ),
                "source_vol_lookback": _parse_int_required(
                    source,
                    r"^\s*vol_lookback\s*=\s*(\d+)",
                    "source_vol_lookback",
                ),
                "source_min_bars": _parse_int_required(
                    source,
                    r"if\s+len\(self\.data\.Close\)\s*<\s*(\d+)",
                    "source_min_bars",
                ),
                "transfer_exactness": "signal_logic_exact_v4_risk_resized",
                "source_stop_required": False,
                "source_stop_transferred": True,
            }
        except ValueError as exc:
            row["transfer_status"] = "adapter_required"
            row["transfer_reason"] = str(exc)
            audit.append(row)
            continue
        if c.family == "btc_rsi_adx":
            try:
                params.update({
                    "sma_window": _parse_int_required(
                        source,
                        r"self\.sma50\s*=\s*self\.I\(_sma_now,\s*self\.data\.Close,\s*(\d+)\)",
                        "sma_window",
                    ),
                    "ema_window": _parse_int_required(
                        source,
                        r"self\.ema7\s*=\s*self\.I\(_ema_now,\s*self\.data\.Close,\s*(\d+)\)",
                        "ema_window",
                    ),
                    "rsi_window": _parse_int_required(
                        source,
                        r"self\.rsi2\s*=\s*self\.I\(_rsi_now,\s*self\.data\.Close,\s*(\d+)\)",
                        "rsi_window",
                    ),
                    "adx_window": _parse_int_required(
                        source,
                        r"self\.adx2\s*=\s*self\.I\(_adx_now,.*?,\s*(\d+)\)",
                        "adx_window",
                    ),
                })
            except ValueError as exc:
                row["transfer_status"] = "adapter_required"
                row["transfer_reason"] = str(exc)
                audit.append(row)
                continue
        elif c.family in {"sentinel63", "sentinel65"}:
            try:
                params.update({
                    "signal_window": _parse_int_required(
                        source,
                        r"self\.ema\d+\s*=\s*self\.I\(_ema_now,\s*self\.data\.Close,\s*(\d+)\)",
                        "signal_window",
                    ),
                    "entry_z": _parse_float_required(
                        source,
                        r"if\s+not\s+self\.position\s+and\s+z\s*>\s*([-+]?[0-9]*\.?[0-9]+)",
                        "entry_z",
                    ),
                    "exit_z": _parse_float_required(
                        source,
                        r"elif\s+self\.position\s+and\s+z\s*<\s*([-+]?[0-9]*\.?[0-9]+)",
                        "exit_z",
                    ),
                })
            except ValueError as exc:
                row["transfer_status"] = "adapter_required"
                row["transfer_reason"] = str(exc)
                audit.append(row)
                continue
        elif c.family in {"donchian_20_10", "donchian_sma50"}:
            params.update({
                "entry_lookback": _parse_int(
                    source,
                    r"entry_lookback\s*=\s*(\d+)",
                    _parse_int(
                        source,
                        r"self\.hh\s*=\s*self\.I\(_rolling_high,.*?,\s*(\d+)\)",
                        20,
                    ),
                ),
                "exit_lookback": _parse_int(
                    source,
                    r"exit_lookback\s*=\s*(\d+)",
                    _parse_int(
                        source,
                        r"self\.ll\s*=\s*self\.I\(_rolling_low,.*?,\s*(\d+)\)",
                        10,
                    ),
                ),
                "sma_window": (
                    None
                    if c.family == "donchian_20_10"
                    else _parse_int(
                        source,
                        r"self\.sma\d+\s*=\s*self\.I\(_sma,.*?,\s*(\d+)\)",
                        50,
                    )
                ),
                "source_stop_transferred": False,
                "transfer_exactness": "signal_only_proxy",
            })
        elif c.family == "swing_terminal_pullback_proxy":
            params.update({
                "ema_fast": _parse_int(
                    source,
                    r"self\.ema20\s*=\s*self\.I\(_ema,.*?,\s*(\d+)\)",
                    20,
                ),
                "ema_slow": _parse_int(
                    source,
                    r"self\.ema50\s*=\s*self\.I\(_ema,.*?,\s*(\d+)\)",
                    50,
                ),
                "atr_window": _parse_int(
                    source,
                    r"self\.atr\s*=\s*self\.I\(_atr,.*?,\s*(\d+)\)",
                    20,
                ),
                "adx_window": _parse_int(
                    source,
                    r"self\.adx\s*=\s*self\.I\(_adx,.*?,\s*(\d+)\)",
                    14,
                ),
                "pullback_atr_mult": 0.40,
                "adx_min": 20.0,
                "source_stop_transferred": False,
                "transfer_exactness": "signal_only_proxy",
            })
        row["transfer_status"] = "supported"
        row["transfer_params"] = dict(params)
        audit.append(row)
        supported.append(params)

    return supported, {
        "available": bool(candidates),
        "policy": (
            "continuous prop champions are pre-screened development hypotheses; "
            "only exact long-only daily signal/exit adapters enter the Prague-aligned "
            "v4 simulator; required parameters are parsed from source, active source "
            "stops/brackets/short entries fail closed, and v4 exposure sizing replaces "
            "source sizing by design under 3x cost stress"
        ),
        "candidate_count": len(candidates),
        "supported_count": len(supported),
        "candidates": audit,
        "hidden_validation_opened": False,
        "final_oos_opened": False,
    }


_ALLOWED_IMPORT_ROOTS = {"numpy", "pandas", "backtesting"}
_BANNED_CALLS = {"eval", "exec", "open", "compile", "__import__", "input"}


def _validate_replay_source(source: str) -> ast.AST:
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                if alias.name.split(".", 1)[0] not in _ALLOWED_IMPORT_ROOTS:
                    raise ValueError(f"unsupported import in promoted strategy: {alias.name}")
        elif isinstance(node, ast.ImportFrom):
            root = (node.module or "").split(".", 1)[0]
            if root not in _ALLOWED_IMPORT_ROOTS:
                raise ValueError(f"unsupported import in promoted strategy: {node.module}")
        elif isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
            if node.func.id in _BANNED_CALLS:
                raise ValueError(f"unsafe call in promoted strategy: {node.func.id}")
    return tree


def _replay_metrics(stats, periods_per_year: float) -> tuple[pd.Series, dict]:
    eq = stats["_equity_curve"]["Equity"].astype(float)
    returns = eq.pct_change().replace([np.inf, -np.inf], np.nan).fillna(0.0)
    arr = returns.to_numpy(dtype=float)
    years = max(len(arr) / float(periods_per_year), 1.0 / float(periods_per_year))
    cagr = (
        float((eq.iloc[-1] / eq.iloc[0]) ** (1.0 / years) - 1.0) * 100.0
        if len(eq) > 1 and eq.iloc[0] > 0 and eq.iloc[-1] > 0
        else float("nan")
    )
    peaks = eq.cummax()
    dd = float((eq / peaks - 1.0).min() * 100.0)
    sd = float(np.std(arr, ddof=1)) if len(arr) > 1 else 0.0
    sharpe = (
        float(np.mean(arr) / sd * math.sqrt(float(periods_per_year)))
        if sd > 0
        else 0.0
    )
    return returns, {
        "cagr_pct": cagr,
        "max_dd_pct": dd,
        "sharpe": sharpe,
        "trades": int(len(stats["_trades"])),
    }


def private_portfolio_eligible(
    replay_ok: bool,
    pbo: float | None,
    *,
    max_pbo: float = 0.55,
) -> bool:
    """Fail closed when a promoted private family lacks PBO evidence."""
    return bool(
        replay_ok
        and pbo is not None
        and math.isfinite(float(pbo))
        and float(pbo) <= float(max_pbo)
    )


def replay_private_candidate(
    candidate: PromotionCandidate,
    source: str,
    frame: pd.DataFrame,
    *,
    commission: float,
    margin: float,
    max_dd_pct: float,
    cost_stress_multiplier: float = 3.0,
) -> tuple[pd.Series, dict]:
    """Replay one continuous champion on v4 development data.

    Portfolio eligibility is fail-closed on PBO: a strong replay with missing
    PBO remains a research candidate, never an authoritative allocation input.
    """
    from backtesting import Backtest

    tree = _validate_replay_source(source)
    namespace: dict = {}
    exec(compile(tree, f"<continuous:{candidate.track_id}>", "exec"), namespace)
    strategy = namespace.get("MoonStrategy")
    if strategy is None:
        raise ValueError("MoonStrategy not found in promoted strategy source")

    base = Backtest(
        frame.copy(),
        strategy,
        cash=10_000_000,
        commission=float(commission),
        margin=float(margin),
        trade_on_close=False,
    ).run()
    stress = Backtest(
        frame.copy(),
        strategy,
        cash=10_000_000,
        commission=float(commission) * float(cost_stress_multiplier),
        margin=float(margin),
        trade_on_close=False,
    ).run()
    returns, base_metrics = _replay_metrics(
        base, 365.0 if candidate.market == "crypto" else 252.0
    )
    _, stress_metrics = _replay_metrics(
        stress, 365.0 if candidate.market == "crypto" else 252.0
    )
    replay_ok = (
        math.isfinite(base_metrics["cagr_pct"])
        and math.isfinite(stress_metrics["cagr_pct"])
        and base_metrics["cagr_pct"] > 0.0
        and stress_metrics["cagr_pct"] > 0.0
        and abs(min(base_metrics["max_dd_pct"], 0.0)) <= float(max_dd_pct)
        and base_metrics["trades"] >= 8
    )
    portfolio_eligible = private_portfolio_eligible(
        replay_ok,
        candidate.pbo,
        max_pbo=0.55,
    )
    return returns, {
        "candidate": candidate.to_dict(),
        "base": base_metrics,
        "cost_stress": stress_metrics,
        "replay_gate_ok": bool(replay_ok),
        "portfolio_eligible": bool(portfolio_eligible),
        "portfolio_gate_reason": (
            "eligible"
            if portfolio_eligible
            else (
                "replay_failed"
                if not replay_ok
                else "pbo_missing_or_above_limit"
            )
        ),
        "hidden_validation_opened": False,
        "final_oos_opened": False,
    }


def replay_private_promotions(
    data: dict[str, pd.DataFrame],
    *,
    max_dd_pct: float,
    cost_stress_multiplier: float = 3.0,
) -> tuple[dict[str, pd.Series], dict]:
    candidates = select_candidates(
        "private",
        available_symbols=data.keys(),
        per_target=1,
        max_total=8,
    )
    commission = {
        "BTCUSDT": 0.001,
        "ETHUSDT": 0.001,
        "SPY": 0.0002,
        "QQQ": 0.0002,
        "TQQQ": 0.0002,
    }
    margin = {
        "BTCUSDT": 0.25,
        "ETHUSDT": 0.25,
        "SPY": 0.5,
        "QQQ": 0.5,
        "TQQQ": 0.5,
    }
    eligible = {}
    audit = []
    for c in candidates:
        source = load_candidate_source(c.track_id)
        if not source:
            row = c.to_dict()
            row["replay_status"] = "source_unavailable"
            audit.append(row)
            continue
        try:
            returns, row = replay_private_candidate(
                c,
                source,
                data[c.symbol],
                commission=commission.get(c.symbol, 0.0002),
                margin=margin.get(c.symbol, 0.5),
                max_dd_pct=max_dd_pct,
                cost_stress_multiplier=cost_stress_multiplier,
            )
        except Exception as exc:
            row = c.to_dict()
            row["replay_status"] = "error"
            row["error"] = f"{type(exc).__name__}: {exc}"
            audit.append(row)
            continue
        row["replay_status"] = "completed"
        audit.append(row)
        if row["portfolio_eligible"]:
            eligible[f"continuous__{c.track_id}"] = returns

    return eligible, {
        "available": bool(candidates),
        "policy": (
            "replay top diversified continuous private champions on v4 development "
            "data; require positive 3x-cost replay plus actual PBO <= 0.55 before "
            "portfolio eligibility"
        ),
        "candidate_count": len(candidates),
        "portfolio_eligible_count": len(eligible),
        "candidates": audit,
        "hidden_validation_opened": False,
        "final_oos_opened": False,
    }
