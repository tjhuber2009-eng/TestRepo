"""Controlled, structure-frozen parameter optimizer for AUTORESEARCH v4."""
from __future__ import annotations

from dataclasses import dataclass, asdict
from hashlib import sha256
from itertools import product
from math import log, sqrt
from typing import Callable, Mapping, Sequence
import json

import numpy as np


@dataclass(frozen=True)
class ParameterSpec:
    name: str
    values: tuple[float | int, ...]


@dataclass
class ParameterTrial:
    params: dict[str, float | int]
    fold_scores: list[float]
    gate_ok: bool
    structural_fingerprint: str
    primary_score: float
    median_score: float
    worst_score: float
    score_std: float
    plateau_score: float | None = None
    adjusted_score: float | None = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class ParameterOptimizationResult:
    chosen: ParameterTrial | None
    trials: list[ParameterTrial]
    frozen_structure: str
    search_hash: str
    selection_policy: str

    def to_dict(self) -> dict:
        return {
            "chosen": None if self.chosen is None else self.chosen.to_dict(),
            "trials": [x.to_dict() for x in self.trials],
            "frozen_structure": self.frozen_structure,
            "search_hash": self.search_hash,
            "selection_policy": self.selection_policy,
        }


Evaluator = Callable[[Mapping[str, float | int]], Mapping]


class StableParameterOptimizer:
    """Selects broad, stable parameter plateaus rather than isolated peaks."""

    def __init__(
        self,
        specs: Sequence[ParameterSpec],
        *,
        max_trials: int = 256,
        plateau_neighbors: int = 4,
        dispersion_penalty: float = 0.35,
        multiple_test_penalty: float = 0.10,
    ):
        self.specs = list(specs)
        self.max_trials = int(max_trials)
        self.plateau_neighbors = int(plateau_neighbors)
        self.dispersion_penalty = float(dispersion_penalty)
        self.multiple_test_penalty = float(multiple_test_penalty)
        if not self.specs:
            raise ValueError("at least one parameter spec required")
        total = int(np.prod([len(x.values) for x in self.specs]))
        if total > self.max_trials:
            raise ValueError(f"parameter grid {total} exceeds max_trials={self.max_trials}")

    def grid(self) -> list[dict[str, float | int]]:
        names = [x.name for x in self.specs]
        return [dict(zip(names, values)) for values in product(*(x.values for x in self.specs))]

    def _param_matrix(self, trials: list[ParameterTrial]) -> np.ndarray:
        rows = []
        for t in trials:
            rows.append([float(t.params[s.name]) for s in self.specs])
        arr = np.asarray(rows, dtype=float)
        if arr.size == 0:
            return arr
        lo = arr.min(axis=0)
        hi = arr.max(axis=0)
        span = np.where(hi > lo, hi - lo, 1.0)
        return (arr - lo) / span

    def optimize(self, evaluator: Evaluator, *, frozen_structure: str) -> ParameterOptimizationResult:
        grid = self.grid()
        trials: list[ParameterTrial] = []
        for params in grid:
            result = dict(evaluator(params))
            fingerprint = str(result.get("structural_fingerprint", frozen_structure))
            if fingerprint != frozen_structure:
                raise RuntimeError("parameter optimizer detected a structural strategy change")
            scores = [float(x) for x in result.get("fold_scores", []) if np.isfinite(float(x))]
            if not scores:
                median = worst = std = float("-inf")
            else:
                median = float(np.median(scores))
                worst = float(np.min(scores))
                std = float(np.std(scores, ddof=1)) if len(scores) > 1 else 0.0
            primary = result.get("primary_score", median)
            try:
                primary = float(primary)
            except Exception:
                primary = float("-inf")
            if not np.isfinite(primary):
                primary = float("-inf")
            trials.append(ParameterTrial(
                params=dict(params),
                fold_scores=scores,
                gate_ok=bool(result.get("gate_ok", False)),
                structural_fingerprint=fingerprint,
                primary_score=primary,
                median_score=median,
                worst_score=worst,
                score_std=std,
            ))

        coords = self._param_matrix(trials)
        valid_medians = [t.median_score for t in trials if t.gate_ok and np.isfinite(t.median_score)]
        fallback = min(valid_medians) if valid_medians else -1e99
        for i, trial in enumerate(trials):
            if not trial.gate_ok or not np.isfinite(trial.median_score):
                trial.plateau_score = float("-inf")
                trial.adjusted_score = float("-inf")
                continue
            dist = np.sqrt(np.sum((coords - coords[i]) ** 2, axis=1))
            order = np.argsort(dist)
            neighbors = [
                trials[j].median_score
                for j in order[1 : 1 + self.plateau_neighbors]
                if trials[j].gate_ok and np.isfinite(trials[j].median_score)
            ]
            local = [trial.median_score] + neighbors
            trial.plateau_score = float(np.median(local)) if local else fallback
            nfold = max(len(trial.fold_scores), 1)
            search_penalty = self.multiple_test_penalty * sqrt(log(1.0 + len(trials)) / nfold)
            trial.adjusted_score = float(
                0.55 * trial.median_score
                + 0.30 * trial.plateau_score
                + 0.15 * trial.worst_score
                - self.dispersion_penalty * trial.score_std
                - search_penalty
            )

        eligible = [
            t for t in trials
            if t.gate_ok
            and np.isfinite(t.primary_score)
            and np.isfinite(t.adjusted_score)
        ]
        eligible.sort(
            key=lambda t: (
                t.primary_score,
                t.adjusted_score,
                t.plateau_score,
                t.worst_score,
                -t.score_std,
            ),
            reverse=True,
        )
        chosen = eligible[0] if eligible else None
        search_payload = {
            "frozen_structure": frozen_structure,
            "specs": [{"name": s.name, "values": list(s.values)} for s in self.specs],
            "policy": "hard_gate_then_primary_score_then_stability_tiebreakers",
        }
        search_hash = sha256(json.dumps(search_payload, sort_keys=True).encode()).hexdigest()
        return ParameterOptimizationResult(
            chosen=chosen,
            trials=trials,
            frozen_structure=frozen_structure,
            search_hash=search_hash,
            selection_policy=(
                "hard-gate first; maximize evaluator primary_score; then prefer broad local "
                "plateaus and strong worst-fold performance while penalizing fold dispersion "
                "and search multiplicity"
            ),
        )
