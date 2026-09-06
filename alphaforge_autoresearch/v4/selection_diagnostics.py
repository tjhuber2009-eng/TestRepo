"""Selection-bias diagnostics for AUTORESEARCH v4 development searches."""
from __future__ import annotations

import itertools
import math
from typing import Sequence

import numpy as np


def cscv_pbo(matrix: Sequence[Sequence[float]], max_splits: int = 252) -> dict | None:
    """Probability of Backtest Overfitting from fixed even chronological slices."""
    x = np.asarray(matrix, dtype=float)
    if x.ndim != 2:
        return None
    n_strat, n_folds = x.shape
    if n_strat < 5 or n_folds < 4 or n_folds % 2:
        return None
    if not np.all(np.isfinite(x)):
        return None
    half = n_folds // 2
    combos = list(itertools.combinations(range(n_folds), half))
    if len(combos) > max_splits:
        idx = np.linspace(0, len(combos) - 1, max_splits, dtype=int)
        combos = [combos[i] for i in idx]

    all_idx = set(range(n_folds))
    logits = []
    below = 0
    valid = 0
    for train_tuple in combos:
        train = np.asarray(train_tuple, dtype=int)
        test = np.asarray(sorted(all_idx - set(train_tuple)), dtype=int)
        train_perf = np.mean(x[:, train], axis=1)
        best = int(np.argmax(train_perf))
        test_perf = np.mean(x[:, test], axis=1)
        order = np.argsort(test_perf)
        rank = int(np.where(order == best)[0][0]) + 1
        percentile = (rank - 0.5) / n_strat
        percentile = min(max(percentile, 1e-9), 1 - 1e-9)
        logits.append(math.log(percentile / (1 - percentile)))
        below += percentile < 0.5
        valid += 1
    if valid == 0:
        return None
    return {
        "pbo": float(below / valid),
        "cscv_splits": int(valid),
        "median_oos_logit": float(np.median(np.asarray(logits))),
        "candidate_count": int(n_strat),
        "fold_count": int(n_folds),
        "partition": "fixed_even_development_slices",
    }


def optimizer_pbo(optimization_result, *, gate_only: bool = True) -> dict | None:
    rows = []
    for trial in optimization_result.trials:
        if gate_only and not trial.gate_ok:
            continue
        vals = list(trial.fold_scores)
        if len(vals) < 4 or len(vals) % 2 or not np.all(np.isfinite(vals)):
            continue
        rows.append(vals)
    if not rows:
        return None
    widths = {}
    for row in rows:
        widths[len(row)] = widths.get(len(row), 0) + 1
    width = max(widths, key=lambda k: (widths[k], k))
    matrix = [row for row in rows if len(row) == width]
    return cscv_pbo(matrix)
