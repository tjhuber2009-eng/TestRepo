"""Reusable mutation motifs and transfer planner for AUTORESEARCH v4."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from hashlib import sha256
from typing import Mapping, Sequence
import json

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class MotifSpec:
    id: str
    description: str
    compatible_markets: tuple[str, ...]
    parameter_grid: Mapping[str, tuple]
    tags: tuple[str, ...] = ()

    def to_dict(self) -> dict:
        x = asdict(self)
        x["parameter_grid"] = {k: list(v) for k, v in self.parameter_grid.items()}
        return x


DEFAULT_MOTIFS: tuple[MotifSpec, ...] = (
    MotifSpec(
        "long_term_trend_gate",
        "Require price above a long moving average before long exposure.",
        ("crypto", "etf", "stock", "futures", "futures_proxy", "forex"),
        {"window": (150, 175, 200, 225)},
        ("regime", "trend"),
    ),
    MotifSpec(
        "atr_trailing_exit",
        "ATR trailing exit to reduce adverse excursion while preserving winners.",
        ("crypto", "etf", "stock", "futures", "futures_proxy", "forex"),
        {"atr_window": (10, 14, 20), "atr_multiple": (2.0, 2.5, 3.0, 3.5)},
        ("exit", "volatility"),
    ),
    MotifSpec(
        "volume_expansion_gate",
        "Require recent volume to exceed its slower baseline.",
        ("crypto", "etf", "stock", "futures", "futures_proxy"),
        {"fast": (3, 5, 10), "slow": (20, 40), "ratio": (1.0, 1.2, 1.5)},
        ("volume", "confirmation"),
    ),
    MotifSpec(
        "volatility_contraction_gate",
        "Favor entries after short-term realized volatility contracts.",
        ("crypto", "etf", "stock", "futures", "futures_proxy", "forex"),
        {"fast": (5, 10), "slow": (20, 60), "ratio": (0.6, 0.8, 1.0)},
        ("volatility", "setup"),
    ),
    MotifSpec(
        "relative_strength_gate",
        "Require positive relative momentum versus a benchmark.",
        ("crypto", "etf", "stock", "futures", "futures_proxy"),
        {"lookback": (20, 60, 126)},
        ("cross_asset", "momentum"),
    ),
    MotifSpec(
        "breadth_confirmation",
        "Require broad participation before taking risk-on signals.",
        ("etf", "stock", "futures", "futures_proxy"),
        {"threshold": (0.45, 0.50, 0.55, 0.60)},
        ("breadth", "regime"),
    ),
    MotifSpec(
        "time_stop",
        "Exit a position after a fixed maximum holding period.",
        ("crypto", "etf", "stock", "futures", "futures_proxy", "forex"),
        {"max_bars": (3, 5, 10, 20)},
        ("exit", "risk"),
    ),
    MotifSpec(
        "drawdown_recovery_gate",
        "Reduce new risk while the strategy/portfolio is in deep drawdown.",
        ("crypto", "etf", "stock", "futures", "futures_proxy", "forex"),
        {"max_drawdown": (0.05, 0.10, 0.15, 0.20)},
        ("risk", "regime"),
    ),
    MotifSpec(
        "volatility_regime_gate",
        "Condition entries on causal volatility-regime state.",
        ("crypto", "etf", "stock", "futures", "futures_proxy", "forex"),
        {"allowed": (("low", "normal"), ("normal",), ("normal", "high"))},
        ("regime", "volatility"),
    ),
)


def motif_registry() -> dict[str, MotifSpec]:
    return {m.id: m for m in DEFAULT_MOTIFS}


def trend_gate(signal: pd.Series, close: pd.Series, window: int = 200) -> pd.Series:
    ma = close.rolling(window, min_periods=window).mean()
    return signal.where(close > ma, 0.0)


def volume_expansion_gate(
    signal: pd.Series, volume: pd.Series, fast: int = 5, slow: int = 20, ratio: float = 1.2
) -> pd.Series:
    f = volume.rolling(fast, min_periods=fast).mean()
    s = volume.rolling(slow, min_periods=slow).mean()
    return signal.where(f >= ratio * s, 0.0)


def volatility_contraction_gate(
    signal: pd.Series, close: pd.Series, fast: int = 5, slow: int = 20, ratio: float = 0.8
) -> pd.Series:
    r = close.pct_change()
    f = r.rolling(fast, min_periods=fast).std(ddof=1)
    s = r.rolling(slow, min_periods=slow).std(ddof=1)
    return signal.where(f <= ratio * s, 0.0)


def relative_strength_gate(
    signal: pd.Series, close: pd.Series, benchmark_close: pd.Series, lookback: int = 60
) -> pd.Series:
    rel = (close / close.shift(lookback)) / (
        benchmark_close / benchmark_close.shift(lookback)
    ) - 1.0
    return signal.where(rel > 0.0, 0.0)


def breadth_gate(signal: pd.Series, breadth: pd.Series, threshold: float = 0.5) -> pd.Series:
    return signal.where(breadth >= threshold, 0.0)


def time_stop(binary_signal: pd.Series, max_bars: int = 10) -> pd.Series:
    s = (binary_signal.fillna(0.0) > 0).astype(int)
    out = pd.Series(0.0, index=s.index)
    held = 0
    for i, on in enumerate(s.to_numpy()):
        if on and held < max_bars:
            out.iloc[i] = 1.0
            held += 1
        else:
            if not on:
                held = 0
            elif held >= max_bars:
                out.iloc[i] = 0.0
    return out


def drawdown_recovery_gate(signal: pd.Series, equity: pd.Series, max_drawdown: float = 0.10) -> pd.Series:
    peak = equity.cummax()
    dd = equity / peak.replace(0.0, np.nan) - 1.0
    return signal.where(dd >= -abs(max_drawdown), 0.0)


def atr_trailing_position(
    entry_signal: pd.Series,
    market: pd.DataFrame,
    *,
    atr_window: int = 14,
    atr_multiple: float = 3.0,
) -> pd.Series:
    prev = market["Close"].shift(1)
    tr = pd.concat([
        market["High"] - market["Low"],
        (market["High"] - prev).abs(),
        (market["Low"] - prev).abs(),
    ], axis=1).max(axis=1)
    atr = tr.rolling(atr_window, min_periods=atr_window).mean()
    desired = (entry_signal.fillna(0.0) > 0).to_numpy()
    close = market["Close"].to_numpy(dtype=float)
    atrv = atr.to_numpy(dtype=float)
    out = np.zeros(len(market), dtype=float)
    active = False
    peak = np.nan
    for i in range(len(market)):
        if not active and desired[i] and np.isfinite(atrv[i]):
            active = True
            peak = close[i]
        if active:
            peak = max(float(peak), close[i])
            stop = peak - atr_multiple * atrv[i] if np.isfinite(atrv[i]) else -np.inf
            if close[i] < stop or not desired[i]:
                active = False
                peak = np.nan
            else:
                out[i] = 1.0
    return pd.Series(out, index=market.index)


@dataclass
class MotifEvidence:
    motif_id: str
    family: str
    market: str
    profile: str
    keeper: bool
    delta_score: float


class MotifTransferPlanner:
    """Ranks motif/family/market transfer experiments from accumulated evidence."""
    def __init__(self, evidence: Sequence[MotifEvidence] = ()):
        self.evidence = list(evidence)

    def motif_stats(self) -> dict[str, dict]:
        stats: dict[str, dict] = {}
        for e in self.evidence:
            x = stats.setdefault(e.motif_id, {"attempts": 0, "keepers": 0, "delta": []})
            x["attempts"] += 1
            x["keepers"] += int(e.keeper)
            x["delta"].append(float(e.delta_score))
        for motif_id, x in stats.items():
            x["keeper_rate"] = x["keepers"] / max(x["attempts"], 1)
            x["mean_delta"] = float(np.mean(x.pop("delta"))) if x["attempts"] else 0.0
        return stats

    def plan(
        self,
        families: Sequence[Mapping],
        markets: Sequence[str],
        profiles: Sequence[str] = ("prop", "private"),
    ) -> list[dict]:
        stats = self.motif_stats()
        registry = motif_registry()
        rows = []
        for motif_id, motif in registry.items():
            prior = stats.get(motif_id, {"attempts": 0, "keepers": 0, "keeper_rate": 0.0, "mean_delta": 0.0})
            for family in families:
                fid = str(family.get("id", family))
                allowed = set(family.get("markets", markets)) if isinstance(family, Mapping) else set(markets)
                for market in markets:
                    if market not in allowed or market not in motif.compatible_markets:
                        continue
                    for profile in profiles:
                        material = f"{motif_id}|{fid}|{market}|{profile}".encode()
                        rows.append({
                            "motif_id": motif_id,
                            "family": fid,
                            "market": market,
                            "profile": profile,
                            "prior_attempts": prior["attempts"],
                            "prior_keeper_rate": prior["keeper_rate"],
                            "prior_mean_delta": prior["mean_delta"],
                            "experiment_id": sha256(material).hexdigest()[:16],
                            "priority": (
                                2.0 * prior["keeper_rate"]
                                + max(prior["mean_delta"], 0.0)
                                + 1.0 / np.sqrt(1.0 + prior["attempts"])
                            ),
                        })
        rows.sort(key=lambda x: (x["priority"], -x["prior_attempts"], x["experiment_id"]), reverse=True)
        return rows


def write_default_registry(path) -> None:
    payload = {"protocol": "alpha_generation_v4", "motifs": [m.to_dict() for m in DEFAULT_MOTIFS]}
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(payload, fh, indent=2, sort_keys=True)
        fh.write("\n")
