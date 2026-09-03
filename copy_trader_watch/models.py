from __future__ import annotations

import math
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class TraderSnapshot:
    platform: str
    trader_id: str
    name: str
    observed_at: str
    source: str
    source_url: str | None = None
    source_quality: float = 0.0
    free: bool | None = None
    us_access: str = "unknown"
    live_evidence: str = "unknown"
    return_pct: float | None = None
    return_window: str | None = None
    max_drawdown_pct: float | None = None
    profit_factor: float | None = None
    trades: int | None = None
    win_rate_pct: float | None = None
    age_days: float | None = None
    leverage: float | None = None
    activity_per_day: float | None = None
    profit_concentration_pct: float | None = None
    copyability_score: float | None = None
    actionable: bool = False
    actionable_reason: str = ""

    # Historical/seed ranking. Sample size does not enter this score.
    research_score: float | None = None
    # Evidence confidence is reported separately and never used as an exclusion gate.
    evidence_score: float | None = None

    # Forward-test fields are populated by forward.py after each scheduled observation.
    forward_test_eligible: bool = True
    forward_test_reason: str = ""
    forward_observations: int = 0
    forward_return_pct: float | None = None
    forward_max_drawdown_pct: float | None = None
    forward_profit_factor: float | None = None
    forward_win_rate_pct: float | None = None
    forward_score: float | None = None
    rank_score: float | None = None

    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class AdapterResult:
    platform: str
    observed_at: str
    records: list[TraderSnapshot] = field(default_factory=list)
    status: str = "ok"
    message: str = ""
    metadata: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "platform": self.platform,
            "observed_at": self.observed_at,
            "records": [r.to_dict() for r in self.records],
            "status": self.status,
            "message": self.message,
            "metadata": self.metadata,
        }


def clamp(value: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, value))


def score_snapshot(record: TraderSnapshot) -> float | None:
    """Historical seed score with no sample-size or track-record-age penalty.

    Age and trade count are intentionally excluded. A brand-new signal is allowed to
    compete on observed economics immediately; sample depth is reported separately
    through ``evidence_score`` and the forward test ultimately controls ranking.
    """
    if record.return_pct is None:
        return None

    if record.max_drawdown_pct is None:
        dd = 0.0
        ratio_component = 0.0
    else:
        dd = abs(record.max_drawdown_pct)
        ratio = record.return_pct / max(dd, 5.0)
        ratio_component = clamp(ratio, -10.0, 20.0) * 3.0

    pf_component = 0.0
    if record.profit_factor is not None:
        pf_component = clamp(record.profit_factor - 1.0, -1.0, 2.0) * 5.0

    source_component = clamp(record.source_quality, 0.0, 100.0) * 0.08
    copy_component = clamp(record.copyability_score or 0.0, 0.0, 100.0) * 0.08

    penalty = 0.0
    if record.max_drawdown_pct is None:
        penalty += 10.0
    if record.leverage and record.leverage > 10:
        penalty += min(20.0, (record.leverage - 10.0) * 0.8)
    if dd > 30:
        penalty += min(25.0, (dd - 30.0) * 0.8)
    if record.profit_concentration_pct and record.profit_concentration_pct > 25:
        penalty += min(15.0, (record.profit_concentration_pct - 25.0) * 0.4)
    if record.live_evidence == "demo":
        penalty += 15.0
    if record.us_access == "no":
        penalty += 3.0

    return round(ratio_component + pf_component + source_component + copy_component - penalty, 4)


def score_evidence(record: TraderSnapshot) -> float:
    """Confidence/context score only. It never blocks or lowers forward rank."""
    source = clamp(record.source_quality, 0.0, 100.0) * 0.35

    age = max(0.0, record.age_days or 0.0)
    # Smooth saturation: a small sample still gets non-zero evidence rather than fail.
    age_component = clamp(math.log10(1.0 + age) / math.log10(1.0 + 1095.0), 0.0, 1.0) * 25.0

    trades = max(0, record.trades or 0)
    trade_component = clamp(math.log10(1.0 + trades) / math.log10(1.0 + 5000.0), 0.0, 1.0) * 25.0

    completeness = 0.0
    if record.max_drawdown_pct is not None:
        completeness += 5.0
    if record.profit_factor is not None:
        completeness += 4.0
    if record.win_rate_pct is not None:
        completeness += 3.0
    if record.live_evidence in {"real", "onchain", "public-ledger-api"}:
        completeness += 3.0

    return round(clamp(source + age_component + trade_component + completeness, 0.0, 100.0), 4)


def score_forward(
    forward_return_pct: float | None,
    forward_max_drawdown_pct: float | None,
) -> float | None:
    """Forward rank score. No observation-count/sample-size adjustment is applied."""
    if forward_return_pct is None:
        return None
    dd = abs(forward_max_drawdown_pct or 0.0)
    ratio = forward_return_pct / max(dd, 2.0)
    return round(
        clamp(forward_return_pct, -100.0, 500.0) * 0.25
        + clamp(ratio, -25.0, 40.0) * 5.0,
        4,
    )
