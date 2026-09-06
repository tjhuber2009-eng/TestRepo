"""Staggered-inception satellite sleeve for qualified shorter-history alphas.

The long-history core remains authoritative evidence. Supplemental strategies are
allowed only from their first available development observation; earlier sleeve
capital is explicitly cash. This avoids truncating the core history.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from itertools import combinations

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SatelliteCandidateSpec:
    name: str
    sleeve_weight: float
    satellite_weights: dict[str, float]
    inception_dates: dict[str, str]

    def to_dict(self) -> dict:
        return asdict(self)


def _pad_from_inception(series: pd.Series, index: pd.Index) -> tuple[pd.Series, str]:
    x = pd.to_numeric(series, errors="coerce").replace([np.inf, -np.inf], np.nan)
    valid = x.dropna()
    if valid.empty:
        raise ValueError("supplemental strategy has no finite returns")
    first = valid.index.min()
    aligned = x.reindex(index)
    before = index < first
    aligned.loc[before] = 0.0
    after = index >= first
    if aligned.loc[after].isna().any():
        missing = int(aligned.loc[after].isna().sum())
        raise ValueError(
            f"supplemental strategy has {missing} missing rows after inception"
        )
    return aligned.astype(float), pd.Timestamp(first).strftime("%Y-%m-%d")


def _normalized_core_stream(
    core_returns: pd.DataFrame,
    core_weights: dict[str, float],
) -> pd.Series:
    missing = set(core_weights).difference(core_returns.columns)
    if missing:
        raise ValueError(f"core weights missing return columns: {sorted(missing)}")
    w = pd.Series(core_weights, dtype=float).reindex(core_returns.columns).fillna(0.0)
    gross = float(w.abs().sum())
    if gross <= 0.0:
        raise ValueError("core portfolio has zero gross weight")
    w = w / gross
    return core_returns.mul(w, axis=1).sum(axis=1).rename("long_history_core")


def satellite_gross_profile(
    index: pd.Index,
    spec: SatelliteCandidateSpec,
) -> pd.Series:
    """Unit-scale realized gross: core plus only satellites whose data exist."""
    sleeve = float(spec.sleeve_weight)
    gross = pd.Series(1.0 - sleeve, index=index, dtype=float)
    for name, weight in spec.satellite_weights.items():
        first = pd.Timestamp(spec.inception_dates[name])
        active = pd.Series(
            (pd.DatetimeIndex(index) >= first).astype(float),
            index=index,
            dtype=float,
        )
        gross = gross + sleeve * float(weight) * active
    return gross.rename(spec.name)


def build_staggered_satellite_candidates(
    core_returns: pd.DataFrame,
    core_weights: dict[str, float],
    supplemental_returns: dict[str, pd.Series],
    *,
    max_satellite_weight: float = 0.25,
    sleeve_steps: tuple[float, ...] = (0.05, 0.10, 0.15, 0.20, 0.25, 0.30, 0.35),
) -> tuple[dict[str, pd.Series], dict[str, SatelliteCandidateSpec]]:
    """Create deterministic core+satellite return streams on the full core index."""
    core = core_returns.apply(pd.to_numeric, errors="coerce").dropna(how="any")
    if core.empty:
        return {}, {}
    if not (0.0 < max_satellite_weight < 1.0):
        raise ValueError("max_satellite_weight must be in (0,1)")

    base = _normalized_core_stream(core, core_weights)
    padded: dict[str, pd.Series] = {}
    inception: dict[str, str] = {}
    for name, series in sorted(supplemental_returns.items()):
        try:
            p, first = _pad_from_inception(series, core.index)
        except ValueError:
            continue
        padded[name] = p
        inception[name] = first
    if not padded:
        return {}, {}

    mixes: list[tuple[str, dict[str, float]]] = []
    names = sorted(padded)
    for name in names:
        mixes.append((name, {name: 1.0}))
    for a, b in combinations(names, 2):
        mixes.append((f"{a}+{b}", {a: 0.5, b: 0.5}))
    if len(names) >= 2:
        eq = 1.0 / len(names)
        mixes.append(("equal_all", {name: eq for name in names}))

    candidates: dict[str, pd.Series] = {}
    specs: dict[str, SatelliteCandidateSpec] = {}
    seen = set()
    for mix_name, weights in mixes:
        key_weights = tuple(sorted((k, round(float(v), 12)) for k, v in weights.items()))
        if key_weights in seen:
            continue
        seen.add(key_weights)
        satellite = sum(
            padded[name] * float(weight)
            for name, weight in weights.items()
        )
        for sleeve in sleeve_steps:
            sleeve = float(sleeve)
            if sleeve <= 0.0 or sleeve > max_satellite_weight + 1e-12:
                continue
            candidate_name = f"satellite_{sleeve:.2f}__{mix_name}"
            candidates[candidate_name] = (
                (1.0 - sleeve) * base + sleeve * satellite
            ).rename(candidate_name)
            specs[candidate_name] = SatelliteCandidateSpec(
                name=candidate_name,
                sleeve_weight=sleeve,
                satellite_weights={
                    name: float(weight) for name, weight in weights.items()
                },
                inception_dates={
                    name: inception[name] for name in weights
                },
            )
    return candidates, specs
