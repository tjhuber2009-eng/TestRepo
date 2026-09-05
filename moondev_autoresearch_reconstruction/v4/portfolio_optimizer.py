"""Robust strategy-portfolio optimizer for AUTORESEARCH v4."""
from __future__ import annotations

from dataclasses import asdict, dataclass

import numpy as np
import pandas as pd

from .alpha_objective import annualized_sharpe, max_drawdown_pct


@dataclass
class PortfolioCandidate:
    weights: dict[str, float]
    gross_exposure: float
    cash_weight: float
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
    max_gross: float
    policy: str

    def to_dict(self) -> dict:
        return {
            "chosen": None if self.chosen is None else self.chosen.to_dict(),
            "candidates_evaluated": self.candidates_evaluated,
            "feasible_count": self.feasible_count,
            "dd_cap_pct": self.dd_cap_pct,
            "max_gross": self.max_gross,
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


def _block_bootstrap_indices(
    n: int, block: int, rng: np.random.Generator
) -> np.ndarray:
    if n <= 0:
        return np.array([], dtype=int)
    out = []
    while len(out) < n:
        start = int(rng.integers(0, max(n - block + 1, 1)))
        out.extend(range(start, min(start + block, n)))
    return np.asarray(out[:n], dtype=int)


def _bootstrap_dd_q95(
    boot_base_returns: np.ndarray,
    scale: float,
    quantile: float,
) -> float:
    """Vectorized drawdown quantile for a scaled portfolio return stream."""
    r = boot_base_returns * float(scale)
    eq = np.cumprod(1.0 + r, axis=1)
    peaks = np.maximum.accumulate(eq, axis=1)
    dd = eq / np.where(peaks == 0.0, np.nan, peaks) - 1.0
    worst = np.abs(np.nanmin(dd, axis=1) * 100.0)
    return float(np.nanquantile(worst, quantile))


class RobustPortfolioOptimizer:
    """Maximize portfolio CAGR under original and bootstrap DD constraints.

    Composition weights describe the relative allocation among strategies.
    A separate gross scale determines total capital deployment.  This allows
    cash when tail risk is too high and controlled leverage when diversification
    creates unused risk budget.
    """

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
        max_gross: float = 1.5,
        min_gross: float = 0.10,
        scale_search_steps: int = 12,
        seed: int = 20260905,
    ):
        self.dd_cap_pct = float(dd_cap_pct)
        self.periods_per_year = float(periods_per_year)
        self.n_candidates = int(n_candidates)
        self.bootstrap_reps = int(bootstrap_reps)
        self.block = int(block)
        self.dd_quantile = float(dd_quantile)
        self.max_weight = float(max_weight)
        self.max_gross = float(max_gross)
        self.min_gross = float(min_gross)
        self.scale_search_steps = int(scale_search_steps)
        self.seed = int(seed)
        if not (0.0 < self.min_gross <= self.max_gross):
            raise ValueError("require 0 < min_gross <= max_gross")

    def _candidate_compositions(
        self, n: int, rng: np.random.Generator
    ) -> list[np.ndarray]:
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

    def _max_feasible_scale(
        self,
        base_returns: np.ndarray,
        boot_base_returns: np.ndarray,
    ) -> tuple[float | None, float | None]:
        """Find the largest gross scale satisfying both DD caps efficiently.

        Drawdown is not exactly linear in exposure because returns compound, but
        it is close enough to use a one-pass risk estimate as a starting point.
        We then bracket and refine locally, preserving the exact final gate
        while avoiding a full binary search for every portfolio composition.
        """
        cache: dict[float, tuple[bool, float, float]] = {}

        def feasible(scale: float) -> tuple[bool, float, float]:
            key = round(float(scale), 10)
            if key in cache:
                return cache[key]
            pr = base_returns * float(scale)
            dd = abs(min(max_drawdown_pct(_equity(pr)), 0.0))
            q = _bootstrap_dd_q95(
                boot_base_returns, float(scale), self.dd_quantile
            )
            ok = (
                np.isfinite(dd)
                and np.isfinite(q)
                and dd <= self.dd_cap_pct
                and q <= self.dd_cap_pct
                and np.all(1.0 + pr > 0.0)
            )
            cache[key] = (bool(ok), float(dd), float(q))
            return cache[key]

        ok_min, dd_min, q_min = feasible(self.min_gross)
        if not ok_min:
            return None, None

        # One reference pass at unit gross provides a strong scale estimate.
        ref = min(max(1.0, self.min_gross), self.max_gross)
        ok_ref, dd_ref, q_ref = feasible(ref)
        if ok_ref and ref >= self.max_gross - 1e-12:
            return ref, q_ref

        risk_ref = max(dd_ref, q_ref, 1e-9)
        estimate = ref * self.dd_cap_pct / risk_ref
        estimate = min(max(estimate, self.min_gross), self.max_gross)

        ok_est, _, q_est = feasible(estimate)
        if ok_est:
            lo = estimate
            q_lo = q_est
            hi = self.max_gross
            ok_hi, _, q_hi = feasible(hi)
            if ok_hi:
                return hi, q_hi
        else:
            lo = self.min_gross
            q_lo = q_min
            hi = estimate

        # Only a short local refinement is needed after the risk-based estimate.
        for _ in range(max(3, min(self.scale_search_steps, 6))):
            mid = 0.5 * (lo + hi)
            ok, _, q = feasible(mid)
            if ok:
                lo = mid
                q_lo = q
            else:
                hi = mid
        return float(lo), float(q_lo)

    def optimize(self, returns: pd.DataFrame) -> PortfolioOptimizationResult:
        x = returns.apply(pd.to_numeric, errors="coerce").dropna(how="any")
        if len(x) < 50 or x.shape[1] < 1:
            raise ValueError("portfolio optimizer requires >=50 aligned rows")
        names = list(x.columns)
        arr = x.to_numpy(dtype=float)
        rng = np.random.default_rng(self.seed)
        compositions = self._candidate_compositions(len(names), rng)

        boot_idx = np.vstack([
            _block_bootstrap_indices(len(x), self.block, rng)
            for _ in range(self.bootstrap_reps)
        ])
        boot_assets = arr[boot_idx]

        feasible: list[PortfolioCandidate] = []
        evaluated = 0
        for composition in compositions:
            evaluated += 1
            base = arr @ composition
            boot_base = np.einsum("rts,s->rt", boot_assets, composition)
            scale, dd_q = self._max_feasible_scale(base, boot_base)
            if scale is None:
                continue

            actual_w = composition * scale
            pr = base * scale
            base_eq = _equity(pr)
            dd = max_drawdown_pct(base_eq)
            cagr = _cagr(pr, self.periods_per_year)
            sharpe = annualized_sharpe(pr, self.periods_per_year)

            boot_cagr = np.asarray(
                [_cagr(row * scale, self.periods_per_year) for row in boot_base],
                dtype=float,
            )
            med_cagr = float(np.nanmedian(boot_cagr))
            gross = float(np.sum(np.abs(actual_w)))
            norm = actual_w / gross if gross > 0 else actual_w
            effective_n = float(1.0 / np.sum(np.square(norm))) if gross > 0 else 0.0

            # Bootstrap CAGR is the primary objective. Diversification and
            # Sharpe are intentionally tiny tie-breakers.
            score = float(
                med_cagr
                + 0.02 * max(sharpe, 0.0)
                + 0.01 * effective_n
            )
            feasible.append(
                PortfolioCandidate(
                    weights={
                        name: float(v)
                        for name, v in zip(names, actual_w)
                        if v > 1e-8
                    },
                    gross_exposure=gross,
                    cash_weight=float(max(0.0, 1.0 - gross)),
                    cagr_pct=float(cagr),
                    max_dd_pct=float(dd),
                    sharpe=float(sharpe),
                    bootstrap_median_cagr_pct=med_cagr,
                    bootstrap_dd_q95_pct=float(dd_q),
                    effective_n=effective_n,
                    score=score,
                )
            )

        feasible.sort(
            key=lambda c: (
                c.bootstrap_median_cagr_pct,
                c.cagr_pct,
                c.score,
                c.effective_n,
            ),
            reverse=True,
        )
        return PortfolioOptimizationResult(
            chosen=feasible[0] if feasible else None,
            candidates_evaluated=evaluated,
            feasible_count=len(feasible),
            dd_cap_pct=self.dd_cap_pct,
            max_gross=self.max_gross,
            policy=(
                "choose strategy composition and maximum feasible total gross exposure; "
                "maximize paired moving-block-bootstrap median CAGR subject to original "
                "and bootstrap drawdown caps; cash is allowed and leverage is capped"
            ),
        )
