"""Small, auditable boosted-stump meta-filter for trade selection.

Designed for noisy financial samples: deliberately shallow, deterministic and
walk-forward compatible. It filters an existing strategy's signals rather than
replacing the underlying strategy with a black box.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class DecisionStump:
    feature: str
    threshold: float
    polarity: int
    alpha: float

    def predict(self, x: pd.DataFrame) -> np.ndarray:
        v = x[self.feature].to_numpy(dtype=float)
        pred = np.where(v >= self.threshold, 1.0, -1.0)
        return pred * float(self.polarity)


class BoostedStumpMetaFilter:
    def __init__(self, n_estimators: int = 12, learning_rate: float = 0.5, quantiles: int = 12):
        self.n_estimators = int(n_estimators)
        self.learning_rate = float(learning_rate)
        self.quantiles = int(quantiles)
        self.stumps: list[DecisionStump] = []
        self.medians: dict[str, float] = {}
        self.features: list[str] = []

    def _prepare(self, x: pd.DataFrame, *, fit: bool) -> pd.DataFrame:
        if fit:
            self.features = list(x.columns)
            self.medians = {
                c: float(pd.to_numeric(x[c], errors="coerce").median())
                for c in self.features
            }
        missing = set(self.features).difference(x.columns)
        if missing:
            raise ValueError(f"missing meta-filter features: {sorted(missing)}")
        out = pd.DataFrame(index=x.index)
        for c in self.features:
            out[c] = pd.to_numeric(x[c], errors="coerce").fillna(self.medians[c])
        return out

    def fit(self, x: pd.DataFrame, y: Sequence[int | bool]) -> "BoostedStumpMetaFilter":
        if len(x) != len(y) or len(x) < 8:
            raise ValueError("meta-filter needs at least 8 aligned examples")
        xx = self._prepare(x, fit=True)
        yy = np.asarray(y, dtype=int)
        if not set(np.unique(yy)).issubset({0, 1}) or len(np.unique(yy)) < 2:
            raise ValueError("binary labels with both classes required")
        target = np.where(yy == 1, 1.0, -1.0)
        weights = np.full(len(xx), 1.0 / len(xx), dtype=float)
        self.stumps = []

        for _ in range(self.n_estimators):
            best = None
            best_err = float("inf")
            for feature in self.features:
                values = xx[feature].to_numpy(dtype=float)
                qs = np.linspace(0.05, 0.95, self.quantiles)
                thresholds = np.unique(np.quantile(values, qs))
                for threshold in thresholds:
                    raw = np.where(values >= threshold, 1.0, -1.0)
                    for polarity in (1, -1):
                        pred = raw * polarity
                        err = float(np.sum(weights[pred != target]))
                        if err < best_err:
                            best_err = err
                            best = (feature, float(threshold), int(polarity), pred)
            if best is None or best_err >= 0.5 - 1e-12:
                break
            best_err = max(best_err, 1e-12)
            alpha = self.learning_rate * 0.5 * np.log((1.0 - best_err) / best_err)
            feature, threshold, polarity, pred = best
            self.stumps.append(DecisionStump(feature, threshold, polarity, float(alpha)))
            weights *= np.exp(-alpha * target * pred)
            weights /= weights.sum()
        return self

    def decision_function(self, x: pd.DataFrame) -> np.ndarray:
        if not self.stumps:
            return np.zeros(len(x), dtype=float)
        xx = self._prepare(x, fit=False)
        score = np.zeros(len(xx), dtype=float)
        for stump in self.stumps:
            score += stump.alpha * stump.predict(xx)
        return score

    def predict_proba(self, x: pd.DataFrame) -> np.ndarray:
        score = np.clip(self.decision_function(x), -20.0, 20.0)
        p = 1.0 / (1.0 + np.exp(-2.0 * score))
        return np.column_stack([1.0 - p, p])

    def filter_mask(self, x: pd.DataFrame, threshold: float = 0.55) -> np.ndarray:
        return self.predict_proba(x)[:, 1] >= float(threshold)

    def feature_importance(self) -> dict[str, float]:
        imp = {f: 0.0 for f in self.features}
        for stump in self.stumps:
            imp[stump.feature] += abs(stump.alpha)
        total = sum(imp.values())
        if total > 0:
            imp = {k: v / total for k, v in imp.items()}
        return dict(sorted(imp.items(), key=lambda kv: kv[1], reverse=True))


def walk_forward_probabilities(
    x: pd.DataFrame,
    y: pd.Series,
    *,
    min_train: int = 100,
    retrain_every: int = 25,
    n_estimators: int = 12,
) -> pd.Series:
    """Out-of-sample probabilities using only prior observations at every row."""
    if not x.index.equals(y.index):
        raise ValueError("x/y indexes must match")
    out = pd.Series(np.nan, index=x.index, dtype=float)
    model: BoostedStumpMetaFilter | None = None
    last_fit = -10**9
    for i in range(len(x)):
        if i < min_train:
            continue
        if model is None or i - last_fit >= retrain_every:
            train_x = x.iloc[:i]
            train_y = y.iloc[:i]
            if train_y.nunique(dropna=True) < 2:
                continue
            model = BoostedStumpMetaFilter(n_estimators=n_estimators).fit(train_x, train_y)
            last_fit = i
        out.iloc[i] = float(model.predict_proba(x.iloc[[i]])[0, 1])
    return out
