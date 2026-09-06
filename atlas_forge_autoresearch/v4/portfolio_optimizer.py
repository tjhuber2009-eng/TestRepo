"""Robust strategy-portfolio optimizer for AUTORESEARCH v4."""
from __future__ import annotations

from dataclasses import dataclass, asdict

import numpy as np
import pandas as pd

from .alpha_objective import annualized_sharpe, max_drawdown_pct


@dataclass
class PortfolioCandidate:
    weights: dict[str, float]
    cagr_pct: float
    max_dd_pct: float
    sharpe: float
    bootstrap_median_cagr_pct: float
    bootstrap_dd_q95_pct: float
    effective_n: float
    score: float

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class PortfolioOptimizationResult:
    chosen: PortfolioCandidate | None
    candidates_evaluated: int
    feasible_count: int
    dd_cap_pct: float
    policy: str

    def to_dict(self) -> dict:
        return {
            "chosen": None if self.chosen is None else self.chosen.to_dict(),
            "candidates_evaluated": self.candidates_evaluated,
            "feasible_count": self.feasible_count,
            "dd_cap_pct": self.dd_cap_pct,
            "policy": self.policy,
        }


def _equity(r: np.ndarray) -> np.ndarray:
    return np.cumprod(1.0 + np.asarray(r, dtype=float))


def _cagr(r: np.ndarray, periods_per_year: float) -> float:
    arr = np.asarray(r, dtype=float)
    if arr.size == 0:
        return float("nan")
    eq = _equity(arr)
    years = arr.size / periods_per_year
    if years <= 0 or eq[-1] <= 0:
        return float("nan")
    return float((eq[-1] ** (1.0 / years) - 1.0) * 100.0)


def _block_bootstrap_indices(n: int, block: int, rng: np.random.Generator) -> np.ndarray:
    if n <= 0:
        return np.array([], dtype=int)
    out = []
    while len(out) < n:
        start = int(rng.integers(0, max(n - block + 1, 1)))
        out.extend(range(start, min(start + block, n)))
    return np.asarray(out[:n], dtype=int)


class RobustPortfolioOptimizer:
    """Maximize portfolio CAGR under a portfolio drawdown constraint."""

    def __init__(
        self,
        *,
        dd_cap_pct: float,
        periods_per_year: float = 252.0,
        n_candidates: int = 3000,
        bootstrap_reps: int = 200,
        block: int = 20,
        dd_quantile: float = 0.95,
        max_weight: float = 0.75,
        seed: int = 20260905,
    ):
        self.dd_cap_pct = float(dd_cap_pct)
        self.periods_per_year = float(periods_per_year)
        self.n_candidates = int(n_candidates)
        self.bootstrap_reps = int(bootstrap_reps)
        self.block = int(block)
        self.dd_quantile = float(dd_quantile)
        self.max_weight = float(max_weight)
        self.seed = int(seed)

    def _candidate_weights(self, n: int, rng: np.random.Generator) -> list[np.ndarray]:
        out = []
        for i in range(n):
            w = np.zeros(n)
            w[i] = 1.0
            out.append(w)
        out.append(np.full(n, 1.0 / n))
        while len(out) < self.n_candidates:
            w = rng.dirichlet(np.ones(n))
            if w.max() <= self.max_weight + 1e-12:
                out.append(w)
        return out

    def optimize(self, returns: pd.DataFrame) -> PortfolioOptimizationResult:
        x = returns.apply(pd.to_numeric, errors="coerce").dropna(how="any")
        if len(x) < 50 or x.shape[1] < 1:
            raise ValueError("portfolio optimizer requires >=50 aligned rows")
        names = list(x.columns)
        arr = x.to_numpy(dtype=float)
        rng = np.random.default_rng(self.seed)
        weight_set = self._candidate_weights(len(names), rng)

        boot_idx = [
            _block_bootstrap_indices(len(x), self.block, rng)
            for _ in range(self.bootstrap_reps)
        ]
        feasible: list[PortfolioCandidate] = []
        for w in weight_set:
            pr = arr @ w
            base_eq = _equity(pr)
            dd = max_drawdown_pct(base_eq)
            if not np.isfinite(dd) or abs(min(dd, 0.0)) > self.dd_cap_pct:
                continue
            cagr = _cagr(pr, self.periods_per_year)
            sharpe = annualized_sharpe(pr, self.periods_per_year)
            boot_cagr = []
            boot_dd = []
            for idx in boot_idx:
                br = pr[idx]
                boot_cagr.append(_cagr(br, self.periods_per_year))
                boot_dd.append(abs(min(max_drawdown_pct(_equity(br)), 0.0)))
            dd_q = float(np.quantile(boot_dd, self.dd_quantile))
            if dd_q > self.dd_cap_pct:
                continue
            med_cagr = float(np.median(boot_cagr))
            effective_n = float(1.0 / np.sum(np.square(w)))
            score = float(med_cagr + 0.10 * max(sharpe, 0.0) + 0.05 * effective_n)
            feasible.append(PortfolioCandidate(
                weights={name: float(v) for name, v in zip(names, w) if v > 1e-8},
                cagr_pct=float(cagr),
                max_dd_pct=float(dd),
                sharpe=float(sharpe),
                bootstrap_median_cagr_pct=med_cagr,
                bootstrap_dd_q95_pct=dd_q,
                effective_n=effective_n,
                score=score,
            ))
        feasible.sort(
            key=lambda c: (c.score, c.bootstrap_median_cagr_pct, c.cagr_pct, c.effective_n),
            reverse=True,
        )
        return PortfolioOptimizationResult(
            chosen=feasible[0] if feasible else None,
            candidates_evaluated=len(weight_set),
            feasible_count=len(feasible),
            dd_cap_pct=self.dd_cap_pct,
            policy=(
                "maximize paired moving-block-bootstrap median CAGR subject to original and "
                "bootstrap drawdown cap; small diversification/sharpe tie-breakers"
            ),
        )
