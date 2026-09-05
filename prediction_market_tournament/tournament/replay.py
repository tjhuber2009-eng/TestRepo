from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .fees import polymarket_taker_fee_usd, shares_for_stake
from .models import ResolvedTrade, Signal


@dataclass(frozen=True)
class ReplayResult:
    initial_equity: float
    final_equity: float
    net_pnl: float
    net_return: float
    max_drawdown: float
    admitted_signal_ids: tuple[str, ...]
    skipped_concurrency_signal_ids: tuple[str, ...]
    peak_committed: float
    open_signal_ids: tuple[str, ...]


def _signal_entry_economics(
    signal: Signal,
    trade: ResolvedTrade | None,
) -> tuple[float, float]:
    """Return (share notional, entry fee) using exact recorded data first."""
    if signal.entry_fee_usd is not None:
        return signal.size_usd, signal.entry_fee_usd

    if trade is not None:
        return signal.size_usd, trade.fee_usd

    # Compatibility fallback for old/shadow records that predate exact entry
    # fields. Active V1 lanes are required to persist exact entry economics.
    if signal.order_mode == "maker":
        return signal.size_usd, 0.0

    shares = shares_for_stake(signal.size_usd, signal.market_price)
    fee = polymarket_taker_fee_usd(
        shares,
        signal.market_price,
        signal.fee_rate,
        signal.fee_exponent,
    )
    return signal.size_usd, fee


def replay_forward_account(
    signals: list[Signal],
    trades: list[ResolvedTrade],
    *,
    risk_fraction: float = 0.10,
    max_concurrent_positions: int = 5,
    initial_equity: float = 50.0,
    as_of: datetime | None = None,
) -> ReplayResult:
    """Replay the exact observed forward account without hidden compounding.

    Every signal is admitted at its RECORDED executable cash size. The frozen
    risk fraction is a per-trade cap relative to initial paper capital, not a
    request to rescale later trades using hypothetical larger/smaller fills.

    Unresolved positions remain open, consume cash/concurrency, and are carried
    at share cost. This avoids favorable mark-to-market assumptions while still
    recognizing entry-fee drag immediately.
    """
    if not 0 < risk_fraction <= 1:
        raise ValueError("risk_fraction must be in (0,1]")
    if max_concurrent_positions < 1:
        raise ValueError("max_concurrent_positions must be >= 1")
    if initial_equity <= 0:
        raise ValueError("initial_equity must be > 0")
    if as_of is not None and as_of.tzinfo is None:
        raise ValueError("as_of must be timezone-aware")

    trade_by_signal = {
        trade.signal.signal_id: trade
        for trade in trades
        if trade.resolved_at is not None
    }

    # kind 0 = resolution (capital release), kind 1 = entry.
    events: list[
        tuple[datetime, int, str, Signal, ResolvedTrade | None]
    ] = []
    unique_signals: dict[str, Signal] = {}
    for signal in signals:
        unique_signals.setdefault(signal.signal_id, signal)

    for signal_id, signal in unique_signals.items():
        if as_of is not None and signal.observed_at > as_of:
            continue
        trade = trade_by_signal.get(signal_id)
        events.append((signal.observed_at, 1, signal_id, signal, trade))
        if (
            trade is not None
            and trade.resolved_at is not None
            and (as_of is None or trade.resolved_at <= as_of)
        ):
            events.append(
                (
                    trade.resolved_at,
                    0,
                    signal_id,
                    signal,
                    trade,
                )
            )

    events.sort(key=lambda row: (row[0], row[1], row[2]))

    cash = float(initial_equity)
    equity = float(initial_equity)
    peak_equity = equity
    max_dd = 0.0
    fixed_entry_cap = risk_fraction * initial_equity

    # signal_id -> (entry_cash, spent, entry_fee)
    open_positions: dict[str, tuple[float, float, float]] = {}
    admitted: list[str] = []
    skipped: list[str] = []
    peak_committed = 0.0

    for _, kind, signal_id, signal, trade in events:
        if kind == 0:
            position = open_positions.pop(signal_id, None)
            if position is None or trade is None:
                continue
            _entry_cash, spent, _entry_fee = position
            cash += trade.payout_usd
            # Entry fee already hit equity. Settlement replaces the share asset
            # carried at spent with its realized payout.
            equity += trade.payout_usd - spent
        else:
            if signal_id in open_positions or signal_id in admitted:
                continue
            if len(open_positions) >= max_concurrent_positions:
                skipped.append(signal_id)
                continue

            spent, entry_fee = _signal_entry_economics(signal, trade)
            entry_cash = spent + entry_fee
            if (
                entry_cash <= 0
                or entry_cash > fixed_entry_cap + 1e-9
                or entry_cash > cash + 1e-9
            ):
                skipped.append(signal_id)
                continue

            cash -= entry_cash
            equity -= entry_fee
            open_positions[signal_id] = (entry_cash, spent, entry_fee)
            admitted.append(signal_id)

            committed = sum(
                position[0]
                for position in open_positions.values()
            )
            peak_committed = max(peak_committed, committed)

        peak_equity = max(peak_equity, equity)
        dd = (
            1.0 - equity / peak_equity
            if peak_equity > 0
            else 1.0
        )
        max_dd = max(max_dd, dd)

    net_pnl = equity - initial_equity
    return ReplayResult(
        initial_equity=initial_equity,
        final_equity=equity,
        net_pnl=net_pnl,
        net_return=net_pnl / initial_equity,
        max_drawdown=max_dd,
        admitted_signal_ids=tuple(admitted),
        skipped_concurrency_signal_ids=tuple(skipped),
        peak_committed=peak_committed,
        open_signal_ids=tuple(sorted(open_positions)),
    )


def replay_resolved_trades(
    trades: list[ResolvedTrade],
    *,
    risk_fraction: float = 0.10,
    max_concurrent_positions: int = 5,
    initial_equity: float = 50.0,
) -> ReplayResult:
    """Compatibility wrapper for fully resolved historical/unit-test rows."""
    return replay_forward_account(
        [trade.signal for trade in trades],
        trades,
        risk_fraction=risk_fraction,
        max_concurrent_positions=max_concurrent_positions,
        initial_equity=initial_equity,
    )
