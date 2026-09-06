"""Risk-constrained alpha objective for AUTORESEARCH v4.

The objective is deliberately multi-objective: maximize sustainable CAGR only
among candidates that pass hard risk/evidence constraints.  Sharpe/DSR/PBO are
evidence, not substitutes for return.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from math import exp, isfinite, sqrt
from statistics import NormalDist
from typing import Iterable, Mapping, Sequence

import numpy as np

_EULER_GAMMA = 0.5772156649015329
_NORM = NormalDist()


@dataclass(frozen=True)
class RiskPolicy:
    name: str
    max_dd_pct: float
    min_trades: int = 20
    min_psr: float = 0.80
    min_dsr: float = 0.60
    max_pbo: float = 0.50
    min_cost_stress_cagr_pct: float = 0.0
    max_turnover_per_year: float | None = None
    max_gross_exposure: float | None = None


@dataclass
class AlphaMetrics:
    cagr_pct: float
    max_dd_pct: float
    sharpe: float
    sortino: float
    psr_zero: float
    dsr: float
    pbo: float | None
    trades: int
    turnover_per_year: float | None = None
    gross_exposure: float | None = None
    cost_stress_cagr_pct: float | None = None
    years: float | None = None
    cumulative_return_pct: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def _finite_array(values: Iterable[float]) -> np.ndarray:
    arr = np.asarray(list(values), dtype=float)
    return arr[np.isfinite(arr)]


def max_drawdown_pct(equity: Sequence[float]) -> float:
    arr = _finite_array(equity)
    if arr.size == 0:
        return float("nan")
    peaks = np.maximum.accumulate(arr)
    dd = arr / np.where(peaks == 0, np.nan, peaks) - 1.0
    return float(np.nanmin(dd) * 100.0)


def annualized_sharpe(returns: Sequence[float], periods_per_year: float) -> float:
    r = _finite_array(returns)
    if r.size < 2:
        return float("nan")
    sd = float(np.std(r, ddof=1))
    if sd <= 0:
        return float("nan")
    return float(np.mean(r) / sd * sqrt(periods_per_year))


def annualized_sortino(returns: Sequence[float], periods_per_year: float) -> float:
    r = _finite_array(returns)
    if r.size < 2:
        return float("nan")
    downside = np.minimum(r, 0.0)
    dd = float(np.sqrt(np.mean(np.square(downside))))
    if dd <= 0:
        return float("nan")
    return float(np.mean(r) / dd * sqrt(periods_per_year))


def probabilistic_sharpe_ratio(
    returns: Sequence[float], periods_per_year: float, benchmark_sharpe: float = 0.0
) -> float:
    """Bailey/Lopez de Prado style PSR approximation from sample moments."""
    r = _finite_array(returns)
    n = r.size
    if n < 3:
        return float("nan")
    sr = annualized_sharpe(r, periods_per_year)
    if not isfinite(sr):
        return float("nan")
    centered = r - np.mean(r)
    sd = np.std(r, ddof=1)
    if sd <= 0:
        return float("nan")
    skew = float(np.mean((centered / sd) ** 3))
    kurt = float(np.mean((centered / sd) ** 4))
    denom_sq = max(1e-12, 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr)
    z = (sr - benchmark_sharpe) * sqrt(max(n - 1, 1)) / sqrt(denom_sq)
    return float(_NORM.cdf(z))


def expected_max_sharpe(num_trials: int, sharpe_std: float) -> float:
    """Expected maximum Sharpe under multiple independent trials."""
    n = max(int(num_trials), 1)
    if n <= 1 or sharpe_std <= 0 or not isfinite(sharpe_std):
        return 0.0
    q1 = _NORM.inv_cdf(max(1e-12, 1.0 - 1.0 / n))
    q2 = _NORM.inv_cdf(max(1e-12, 1.0 - 1.0 / (n * exp(1.0))))
    return float(sharpe_std * ((1.0 - _EULER_GAMMA) * q1 + _EULER_GAMMA * q2))


def deflated_sharpe_ratio(
    returns: Sequence[float],
    periods_per_year: float,
    num_trials: int,
    trial_sharpes: Sequence[float] | None = None,
) -> float:
    """Approximate DSR probability after correcting for search multiplicity."""
    r = _finite_array(returns)
    if r.size < 3:
        return float("nan")
    sr = annualized_sharpe(r, periods_per_year)
    if not isfinite(sr):
        return float("nan")
    if trial_sharpes is not None:
        ts = _finite_array(trial_sharpes)
        sigma_sr = float(np.std(ts, ddof=1)) if ts.size >= 2 else 0.0
    else:
        sigma_sr = sqrt(max(1.0 + 0.5 * sr * sr, 1e-12) / max(r.size - 1, 1))
    benchmark = expected_max_sharpe(num_trials, sigma_sr)
    return probabilistic_sharpe_ratio(r, periods_per_year, benchmark_sharpe=benchmark)


def cagr_pct_from_equity(equity: Sequence[float], years: float) -> float:
    arr = _finite_array(equity)
    if arr.size < 2 or years <= 0 or arr[0] <= 0 or arr[-1] <= 0:
        return float("nan")
    return float(((arr[-1] / arr[0]) ** (1.0 / years) - 1.0) * 100.0)


def metrics_from_equity(
    equity: Sequence[float],
    period_returns: Sequence[float],
    periods_per_year: float,
    years: float,
    trades: int,
    *,
    num_trials: int = 1,
    trial_sharpes: Sequence[float] | None = None,
    pbo: float | None = None,
    turnover_per_year: float | None = None,
    gross_exposure: float | None = None,
    cost_stress_cagr_pct: float | None = None,
) -> AlphaMetrics:
    arr = _finite_array(equity)
    cumulative = None
    if arr.size >= 2 and arr[0] != 0:
        cumulative = float((arr[-1] / arr[0] - 1.0) * 100.0)
    return AlphaMetrics(
        cagr_pct=cagr_pct_from_equity(arr, years),
        max_dd_pct=max_drawdown_pct(arr),
        sharpe=annualized_sharpe(period_returns, periods_per_year),
        sortino=annualized_sortino(period_returns, periods_per_year),
        psr_zero=probabilistic_sharpe_ratio(period_returns, periods_per_year, 0.0),
        dsr=deflated_sharpe_ratio(
            period_returns, periods_per_year, num_trials, trial_sharpes=trial_sharpes
        ),
        pbo=pbo,
        trades=int(trades),
        turnover_per_year=turnover_per_year,
        gross_exposure=gross_exposure,
        cost_stress_cagr_pct=cost_stress_cagr_pct,
        years=float(years),
        cumulative_return_pct=cumulative,
    )


def hard_gate(metrics: AlphaMetrics, policy: RiskPolicy) -> tuple[bool, list[str]]:
    reasons: list[str] = []
    if not isfinite(metrics.cagr_pct):
        reasons.append("nonfinite_cagr")
    if not isfinite(metrics.max_dd_pct) or abs(min(metrics.max_dd_pct, 0.0)) > policy.max_dd_pct:
        reasons.append("drawdown_cap")
    if metrics.trades < policy.min_trades:
        reasons.append("insufficient_trades")
    if not isfinite(metrics.psr_zero) or metrics.psr_zero < policy.min_psr:
        reasons.append("psr")
    if not isfinite(metrics.dsr) or metrics.dsr < policy.min_dsr:
        reasons.append("dsr")
    if metrics.pbo is not None and isfinite(metrics.pbo) and metrics.pbo > policy.max_pbo:
        reasons.append("pbo")
    if metrics.cost_stress_cagr_pct is not None:
        if (
            not isfinite(metrics.cost_stress_cagr_pct)
            or metrics.cost_stress_cagr_pct < policy.min_cost_stress_cagr_pct
        ):
            reasons.append("cost_stress")
    if policy.max_turnover_per_year is not None and metrics.turnover_per_year is not None:
        if metrics.turnover_per_year > policy.max_turnover_per_year:
            reasons.append("turnover")
    if policy.max_gross_exposure is not None and metrics.gross_exposure is not None:
        if metrics.gross_exposure > policy.max_gross_exposure:
            reasons.append("gross_exposure")
    return (not reasons), reasons


def sustainable_cagr_score(metrics: AlphaMetrics, policy: RiskPolicy) -> float:
    """CAGR-first utility among hard-gate-passing candidates."""
    ok, _ = hard_gate(metrics, policy)
    if not ok:
        return float("-inf")
    dd_headroom = max(policy.max_dd_pct - abs(min(metrics.max_dd_pct, 0.0)), 0.0)
    evidence = max(min(metrics.dsr, 1.0), 0.0) + max(min(metrics.psr_zero, 1.0), 0.0)
    return float(metrics.cagr_pct + 0.02 * dd_headroom + 0.05 * evidence)


def dominates(a: AlphaMetrics, b: AlphaMetrics) -> bool:
    """Pareto dominance for CAGR/evidence vs drawdown/turnover/PBO."""
    a_pbo = a.pbo if a.pbo is not None and isfinite(a.pbo) else 1.0
    b_pbo = b.pbo if b.pbo is not None and isfinite(b.pbo) else 1.0
    a_turn = a.turnover_per_year if a.turnover_per_year is not None else 0.0
    b_turn = b.turnover_per_year if b.turnover_per_year is not None else 0.0
    at_least = (
        a.cagr_pct >= b.cagr_pct
        and a.dsr >= b.dsr
        and a.psr_zero >= b.psr_zero
        and abs(min(a.max_dd_pct, 0.0)) <= abs(min(b.max_dd_pct, 0.0))
        and a_pbo <= b_pbo
        and a_turn <= b_turn
    )
    strict = (
        a.cagr_pct > b.cagr_pct
        or a.dsr > b.dsr
        or a.psr_zero > b.psr_zero
        or abs(min(a.max_dd_pct, 0.0)) < abs(min(b.max_dd_pct, 0.0))
        or a_pbo < b_pbo
        or a_turn < b_turn
    )
    return bool(at_least and strict)


def pareto_frontier(items: Mapping[str, AlphaMetrics]) -> list[str]:
    keys = list(items)
    out: list[str] = []
    for key in keys:
        if not any(dominates(items[other], items[key]) for other in keys if other != key):
            out.append(key)
    return out
