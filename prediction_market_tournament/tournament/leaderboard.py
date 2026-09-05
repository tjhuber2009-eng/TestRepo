from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Iterable

from .models import ResolvedTrade, Signal
from .replay import replay_resolved_trades
from .scoring import summarize


@dataclass(frozen=True)
class LaneLeaderboardRow:
    lane: str
    calendar_days: float
    provisional: bool
    signals: int
    resolved_trades: int
    unresolved_signals: int
    admitted_trades: int
    skipped_concurrency: int
    net_return: float
    profit_factor: float
    capital_efficiency: float
    max_drawdown: float
    brier_score: float

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def build_equal_window_leaderboard(
    signals: Iterable[Signal],
    trades: Iterable[ResolvedTrade],
    *,
    window_start: datetime,
    as_of: datetime | None = None,
    window_days: int = 30,
    risk_fraction: float = 0.10,
    max_concurrent_positions: int = 5,
) -> list[LaneLeaderboardRow]:
    if window_start.tzinfo is None:
        raise ValueError(
            "window_start must be timezone-aware"
        )
    as_of = as_of or datetime.now(timezone.utc)
    if as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")

    hard_end = window_start + timedelta(
        days=window_days
    )
    decision_end = min(as_of, hard_end)
    calendar_days = max(
        0.0,
        (
            decision_end - window_start
        ).total_seconds()
        / 86400.0,
    )

    sigs = [
        s
        for s in signals
        if window_start
        <= s.observed_at
        < decision_end
    ]
    rows = [
        t
        for t in trades
        if window_start
        <= t.signal.observed_at
        < decision_end
    ]
    by_lane = sorted(
        {s.lane for s in sigs}
        | {t.signal.lane for t in rows}
    )
    result: list[LaneLeaderboardRow] = []

    for lane in by_lane:
        lane_sigs = [
            s for s in sigs if s.lane == lane
        ]
        lane_trades = [
            t
            for t in rows
            if (
                t.signal.lane == lane
                and t.resolved_at is not None
                and t.resolved_at <= as_of
            )
        ]
        resolved_ids = {
            t.signal.signal_id
            for t in lane_trades
        }
        replay = replay_resolved_trades(
            lane_trades,
            risk_fraction=risk_fraction,
            max_concurrent_positions=(
                max_concurrent_positions
            ),
        )
        admitted = set(
            replay.admitted_signal_ids
        )
        admitted_trades = [
            t
            for t in lane_trades
            if t.signal.signal_id in admitted
        ]
        metrics = summarize(
            admitted_trades,
            risk_fraction=risk_fraction,
        )
        cap_eff = (
            replay.net_return
            / replay.peak_committed
            if replay.peak_committed
            else 0.0
        )
        result.append(
            LaneLeaderboardRow(
                lane=lane,
                calendar_days=calendar_days,
                provisional=(
                    calendar_days < window_days
                    or len(lane_sigs) == 0
                    or len(resolved_ids)
                    < len(lane_sigs)
                ),
                signals=len(lane_sigs),
                resolved_trades=len(
                    lane_trades
                ),
                unresolved_signals=max(
                    0,
                    len(lane_sigs)
                    - len(resolved_ids),
                ),
                admitted_trades=len(
                    admitted_trades
                ),
                skipped_concurrency=len(
                    replay
                    .skipped_concurrency_signal_ids
                ),
                net_return=replay.net_return,
                profit_factor=(
                    metrics.profit_factor
                ),
                capital_efficiency=cap_eff,
                max_drawdown=(
                    replay.max_drawdown
                ),
                brier_score=metrics.brier_score,
            )
        )

    return sorted(
        result,
        key=lambda row: (
            row.net_return,
            row.profit_factor,
        ),
        reverse=True,
    )
