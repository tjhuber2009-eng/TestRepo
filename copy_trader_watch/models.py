from __future__ import annotations

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
    research_score: float | None = None
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
    if record.return_pct is None:
        return None

    if record.max_drawdown_pct is None:
        dd = 0.0
        ratio_component = 0.0
    else:
        dd = abs(record.max_drawdown_pct)
        ratio = record.return_pct / max(dd, 5.0)
        ratio_component = clamp(ratio, -10.0, 20.0) * 3.0

    age_component = clamp((record.age_days or 0.0) / 365.0, 0.0, 3.0) * 5.0
    trade_component = 0.0
    if record.trades:
        import math
        trade_component = clamp(math.log10(max(record.trades, 1)), 0.0, 4.0) * 2.0

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

    return round(ratio_component + age_component + trade_component + pf_component + source_component + copy_component - penalty, 4)
