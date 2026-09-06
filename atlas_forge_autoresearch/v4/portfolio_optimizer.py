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
    bootstrap_cagr_q25_pct: float
    bootstrap_cagr_q10_pct: float
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

    def _normalize_capped(self, raw: np.ndarray) -> np.ndarray:
        """Project nonnegative preferences onto the simplex with max_weight."""
        pref = np.maximum(np.asarray(raw, dtype=float), 0.0)
        n = pref.size
        if n < 1:
            raise ValueError("empty portfolio preference vector")
        if self.max_weight * n < 1.0 - 1e-12:
            raise ValueError("max_weight is infeasible for the number of strategies")
        if not np.isfinite(pref).all() or pref.sum() <= 0.0:
            pref = np.ones(n, dtype=float)

        out = np.zeros(n, dtype=float)
        active = np.ones(n, dtype=bool)
        remaining = 1.0
        while active.any() and remaining > 1e-12:
            base = pref[active]
            if base.sum() <= 0.0:
                base = np.ones(base.size, dtype=float)
            proposal = remaining * base / base.sum()
            active_idx = np.flatnonzero(active)
            over = proposal > self.max_weight + 1e-12
            if not over.any():
                out[active_idx] = proposal
                remaining = 0.0
                break
            capped_idx = active_idx[over]
            out[capped_idx] = self.max_weight
            remaining -= self.max_weight * len(capped_idx)
            active[capped_idx] = False
        if remaining > 1e-9:
            raise ValueError("unable to satisfy capped simplex allocation")
        return out

    def _guided_compositions(self, arr: np.ndarray) -> list[np.ndarray]:
        """Deterministic money/correlation seeds before random portfolio search.

        These are development-only composition hypotheses. They do not relax
        bootstrap drawdown gates; they simply ensure economically sensible
        high-growth and diversification mixes are explicitly evaluated rather
        than left to chance in the Dirichlet search.
        """
        n = arr.shape[1]
        if n == 1:
            return [np.ones(1, dtype=float)]

        safe = np.clip(arr, -0.999999, None)
        log_growth = np.nanmean(np.log1p(safe), axis=0) * self.periods_per_year
        vol = np.nanstd(arr, axis=0, ddof=1) * np.sqrt(self.periods_per_year)
        vol = np.where(np.isfinite(vol) & (vol > 1e-9), vol, np.nan)

        corr = np.corrcoef(np.nan_to_num(arr, nan=0.0), rowvar=False)
        corr = np.nan_to_num(corr, nan=0.0, posinf=0.0, neginf=0.0)
        if n > 1:
            avg_abs_corr = (
                np.sum(np.abs(corr), axis=1) - 1.0
            ) / float(n - 1)
        else:
            avg_abs_corr = np.zeros(n, dtype=float)

        positive_growth = np.maximum(log_growth, 0.0)
        inverse_vol = np.divide(
            1.0,
            vol,
            out=np.zeros_like(vol, dtype=float),
            where=np.isfinite(vol),
        )
        growth_risk = np.divide(
            positive_growth,
            vol,
            out=np.zeros_like(positive_growth, dtype=float),
            where=np.isfinite(vol),
        )
        growth_corr = np.divide(
            growth_risk,
            1.0 + avg_abs_corr,
            out=np.zeros_like(growth_risk, dtype=float),
            where=np.isfinite(avg_abs_corr),
        )

        preferences = [
            growth_corr,
            growth_risk,
            positive_growth,
            inverse_vol,
            np.ones(n, dtype=float),
        ]

        order = np.argsort(growth_corr)[::-1]
        for k in range(2, min(n, 6) + 1):
            raw = np.zeros(n, dtype=float)
            raw[order[:k]] = np.maximum(growth_corr[order[:k]], 1e-12)
            preferences.append(raw)

        out: list[np.ndarray] = []
        seen = set()
        for raw in preferences:
            w = self._normalize_capped(raw)
            key = tuple(np.round(w, 12))
            if key not in seen:
                seen.add(key)
                out.append(w)
        return out

    def _candidate_compositions(
        self, n: int, rng: np.random.Generator
    ) -> list[np.ndarray]:
        if n < 1:
            raise ValueError("portfolio requires at least one strategy")
        if self.max_weight * n < 1.0 - 1e-12:
            raise ValueError(
                "max_weight is infeasible for the number of strategies"
            )

        out = []
        # Deterministic seeds must obey the same concentration constraint as
        # randomized candidates. Previously one-hot seeds bypassed max_weight.
        if self.max_weight >= 1.0 - 1e-12:
            for i in range(n):
                w = np.zeros(n)
                w[i] = 1.0
                out.append(w)

        equal = np.full(n, 1.0 / n)
        if equal.max() <= self.max_weight + 1e-12:
            out.append(equal)

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
        # Composition search and bootstrap resampling must have independent
        # deterministic RNG streams. Otherwise a different concentration cap
        # changes rejection-sampling consumption and silently changes the
        # bootstrap paths, defeating paired sensitivity comparisons.
        composition_rng = np.random.default_rng(self.seed)
        bootstrap_rng = np.random.default_rng(self.seed + 104729)
        random_compositions = self._candidate_compositions(
            len(names), composition_rng
        )
        compositions: list[np.ndarray] = []
        seen = set()
        for w in self._guided_compositions(arr) + random_compositions:
            key = tuple(np.round(np.asarray(w, dtype=float), 12))
            if key in seen:
                continue
            seen.add(key)
            compositions.append(np.asarray(w, dtype=float))
            if len(compositions) >= self.n_candidates:
                break

        boot_idx = np.vstack([
            _block_bootstrap_indices(
                len(x), self.block, bootstrap_rng
            )
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
            q25_cagr = float(np.nanquantile(boot_cagr, 0.25))
            q10_cagr = float(np.nanquantile(boot_cagr, 0.10))
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
                    bootstrap_cagr_q25_pct=q25_cagr,
                    bootstrap_cagr_q10_pct=q10_cagr,
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
