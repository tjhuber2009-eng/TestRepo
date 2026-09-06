"""Adaptive research-budget allocator for AUTORESEARCH v4.

After a mandatory breadth floor, Thompson sampling allocates experiments toward
family/market/profile/motif cells most likely to produce another robust keeper.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from math import sqrt
from typing import Sequence
import json
import random

import numpy as np


@dataclass(frozen=True)
class ResearchCell:
    family: str
    market: str
    profile: str
    motif: str = "base"
    timeframe: str = "1d"

    @property
    def id(self) -> str:
        return "__".join((self.family, self.market, self.profile, self.motif, self.timeframe))


@dataclass(frozen=True)
class ResearchObservation:
    cell_id: str
    keeper: bool
    utility: float
    model: str | None = None
    experiment_id: str | None = None


@dataclass
class CellPosterior:
    cell_id: str
    visits: int
    keepers: int
    alpha: float
    beta: float
    keeper_mean: float
    utility_mean: float
    utility_std: float
    pooled_visits: float

    def to_dict(self) -> dict:
        return asdict(self)


class ResearchAllocator:
    def __init__(
        self,
        *,
        min_visits_per_cell: int = 2,
        alpha0: float = 1.0,
        beta0: float = 3.0,
        pool_strength: float = 0.25,
        seed_salt: str = "autoresearch-v4",
    ):
        self.min_visits_per_cell = int(min_visits_per_cell)
        self.alpha0 = float(alpha0)
        self.beta0 = float(beta0)
        self.pool_strength = float(pool_strength)
        self.seed_salt = str(seed_salt)

    @staticmethod
    def _by_cell(observations: Sequence[ResearchObservation]) -> dict[str, list[ResearchObservation]]:
        out: dict[str, list[ResearchObservation]] = {}
        for obs in observations:
            out.setdefault(obs.cell_id, []).append(obs)
        return out

    @staticmethod
    def _cell_map(cells: Sequence[ResearchCell]) -> dict[str, ResearchCell]:
        return {c.id: c for c in cells}

    def posterior(
        self,
        cell: ResearchCell,
        cells: Sequence[ResearchCell],
        observations: Sequence[ResearchObservation],
    ) -> CellPosterior:
        by = self._by_cell(observations)
        cmap = self._cell_map(cells)
        own = by.get(cell.id, [])
        visits = len(own)
        keepers = sum(int(o.keeper) for o in own)

        pooled_success = 0.0
        pooled_failure = 0.0
        pooled_visits = 0.0
        for cid, rows in by.items():
            if cid == cell.id or cid not in cmap:
                continue
            other = cmap[cid]
            shared = sum([
                other.family == cell.family,
                other.market == cell.market,
                other.motif == cell.motif,
                other.timeframe == cell.timeframe,
            ])
            if shared < 2:
                continue
            weight = self.pool_strength * (shared / 4.0)
            ks = sum(int(o.keeper) for o in rows)
            pooled_success += weight * ks
            pooled_failure += weight * (len(rows) - ks)
            pooled_visits += weight * len(rows)

        alpha = self.alpha0 + keepers + pooled_success
        beta = self.beta0 + (visits - keepers) + pooled_failure
        positive = np.asarray([o.utility for o in own if o.keeper and np.isfinite(o.utility)], dtype=float)
        if positive.size:
            utility_mean = float(np.mean(positive))
            utility_std = float(np.std(positive, ddof=1)) if positive.size > 1 else max(abs(utility_mean) * 0.5, 1e-6)
        else:
            utility_mean = 0.01
            utility_std = 0.02
        return CellPosterior(
            cell_id=cell.id,
            visits=visits,
            keepers=keepers,
            alpha=float(alpha),
            beta=float(beta),
            keeper_mean=float(alpha / (alpha + beta)),
            utility_mean=utility_mean,
            utility_std=utility_std,
            pooled_visits=float(pooled_visits),
        )

    def snapshot(
        self, cells: Sequence[ResearchCell], observations: Sequence[ResearchObservation]
    ) -> list[CellPosterior]:
        rows = [self.posterior(c, cells, observations) for c in cells]
        rows.sort(key=lambda x: (x.keeper_mean, x.utility_mean, -x.visits), reverse=True)
        return rows

    def select(
        self,
        cells: Sequence[ResearchCell],
        observations: Sequence[ResearchObservation],
        *,
        decision_counter: int = 0,
    ) -> tuple[ResearchCell, dict]:
        if not cells:
            raise ValueError("no research cells")
        post = {p.cell_id: p for p in self.snapshot(cells, observations)}
        under = [c for c in cells if post[c.id].visits < self.min_visits_per_cell]
        if under:
            under.sort(key=lambda c: (post[c.id].visits, c.id))
            chosen = under[decision_counter % len(under)]
            return chosen, {
                "method": "mandatory_breadth",
                "posterior": post[chosen.id].to_dict(),
            }

        fingerprint = "|".join(
            f"{p.cell_id}:{p.alpha:.6f}:{p.beta:.6f}:{p.utility_mean:.6f}:{p.utility_std:.6f}"
            for p in sorted(post.values(), key=lambda x: x.cell_id)
        )
        seed = int(sha256(f"{self.seed_salt}|{decision_counter}|{fingerprint}".encode()).hexdigest()[:16], 16)
        rng = random.Random(seed)
        scored = []
        for cell in cells:
            p = post[cell.id]
            keeper_draw = rng.betavariate(p.alpha, p.beta)
            utility_draw = max(0.0, rng.gauss(p.utility_mean, p.utility_std / sqrt(max(p.keepers, 1))))
            value = keeper_draw * utility_draw
            scored.append((value, keeper_draw, utility_draw, cell))
        scored.sort(key=lambda x: (x[0], x[1], x[2], x[3].id), reverse=True)
        value, kdraw, udraw, chosen = scored[0]
        return chosen, {
            "method": "contextual_thompson",
            "sampled_value": value,
            "sampled_keeper_probability": kdraw,
            "sampled_positive_utility": udraw,
            "posterior": post[chosen.id].to_dict(),
        }


def load_observations(path) -> list[ResearchObservation]:
    rows = []
    try:
        with open(path, encoding="utf-8") as fh:
            for line in fh:
                try:
                    x = json.loads(line)
                    rows.append(ResearchObservation(**x))
                except Exception:
                    continue
    except FileNotFoundError:
        pass
    return rows


def append_observation(path, obs: ResearchObservation) -> None:
    with open(path, "a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(obs), sort_keys=True) + "\n")
