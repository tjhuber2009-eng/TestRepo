from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .models import ResolvedTrade


@dataclass(frozen=True)
class ReplayResult:
    initial_equity: float
    final_equity: float
    net_return: float
    max_drawdown: float
    admitted_signal_ids: tuple[str, ...]
    skipped_concurrency_signal_ids: tuple[str, ...]
    peak_committed: float


def replay_resolved_trades(
    trades: list[ResolvedTrade],
    *,
    risk_fraction: float = 0.10,
    max_concurrent_positions: int = 5,
    initial_equity: float = 1.0,
) -> ReplayResult:
    if not 0 < risk_fraction <= 1:
        raise ValueError(
            "risk_fraction must be in (0,1]"
        )
    if max_concurrent_positions < 1:
        raise ValueError(
            "max_concurrent_positions must be >= 1"
        )
    if initial_equity <= 0:
        raise ValueError("initial_equity must be > 0")

    # No favorable mark-to-market is assumed. Taker fees consume cash
    # immediately at entry. At identical timestamps, capital is released
    # before a new entry is considered.
    events: list[
        tuple[datetime, int, str, ResolvedTrade]
    ] = []
    for trade in trades:
        if trade.resolved_at is None:
            continue
        events.append(
            (
                trade.signal.observed_at,
                1,
                trade.signal.signal_id,
                trade,
            )
        )
        events.append(
            (
                trade.resolved_at,
                0,
                trade.signal.signal_id,
                trade,
            )
        )
    events.sort(
        key=lambda x: (x[0], x[1], x[2])
    )

    cash = float(initial_equity)
    equity = float(initial_equity)
    peak_equity = equity
    max_dd = 0.0
    # signal_id -> (normalized stake, normalized entry fee)
    open_positions: dict[
        str, tuple[float, float]
    ] = {}
    admitted: list[str] = []
    skipped: list[str] = []
    peak_committed = 0.0

    for _, kind, signal_id, trade in events:
        if kind == 0:
            position = open_positions.pop(
                signal_id, None
            )
            if position is None:
                continue
            stake, _entry_fee = position
            original_stake = (
                trade.signal.size_usd
            )
            payout_ratio = (
                trade.payout_usd
                / original_stake
            )
            cash += stake * payout_ratio
            # Entry fee already reduced equity. Resolution changes equity
            # only by payout minus principal; together this equals the
            # fee-inclusive trade PnL.
            equity += stake * (
                payout_ratio - 1.0
            )
            peak_equity = max(
                peak_equity, equity
            )
            dd = (
                1.0 - equity / peak_equity
                if peak_equity > 0
                else 1.0
            )
            max_dd = max(max_dd, dd)
            continue

        if (
            len(open_positions)
            >= max_concurrent_positions
        ):
            skipped.append(signal_id)
            continue

        stake = risk_fraction * equity
        original_stake = trade.signal.size_usd
        fee_ratio = (
            trade.fee_usd / original_stake
        )
        entry_fee = stake * fee_ratio
        entry_cost = stake + entry_fee
        if (
            stake <= 0
            or entry_cost > cash + 1e-12
        ):
            skipped.append(signal_id)
            continue

        cash -= entry_cost
        equity -= entry_fee
        open_positions[signal_id] = (
            stake, entry_fee
        )
        admitted.append(signal_id)
        committed = sum(
            s + fee
            for s, fee
            in open_positions.values()
        )
        peak_committed = max(
            peak_committed, committed
        )

        dd = (
            1.0 - equity / peak_equity
            if peak_equity > 0
            else 1.0
        )
        max_dd = max(max_dd, dd)

    return ReplayResult(
        initial_equity=initial_equity,
        final_equity=equity,
        net_return=(
            equity / initial_equity - 1.0
        ),
        max_drawdown=max_dd,
        admitted_signal_ids=tuple(admitted),
        skipped_concurrency_signal_ids=tuple(
            skipped
        ),
        peak_committed=peak_committed,
    )
