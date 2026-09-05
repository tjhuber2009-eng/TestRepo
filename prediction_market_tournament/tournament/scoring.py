from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Iterable

from .fees import polymarket_taker_fee_usd, shares_for_stake
from .models import ResolvedTrade, Signal


def settle_binary_signal(
    signal: Signal,
    won: bool,
    fill_price: float | None = None,
    resolved_at=None,
) -> ResolvedTrade:
    price = signal.market_price if fill_price is None else fill_price
    if not 0 < price <= 1:
        raise ValueError("fill_price must be in (0,1]")

    if (
        fill_price is None
        and signal.executed_shares is not None
        and signal.entry_fee_usd is not None
    ):
        shares = signal.executed_shares
        fee = signal.entry_fee_usd
    else:
        shares = shares_for_stake(signal.size_usd, price)
        fee = (
            0.0
            if signal.order_mode == "maker"
            else polymarket_taker_fee_usd(
                shares,
                price,
                signal.fee_rate,
                signal.fee_exponent,
            )
        )

    payout = shares if won else 0.0
    pnl = payout - signal.size_usd - fee
    return ResolvedTrade(
        signal=signal,
        won=won,
        fill_price=price,
        fee_usd=fee,
        payout_usd=payout,
        pnl_usd=pnl,
        return_on_stake=pnl / signal.size_usd,
        resolved_at=resolved_at,
    )


@dataclass(frozen=True)
class TournamentMetrics:
    trades: int
    wins: int
    win_rate: float
    net_pnl_usd: float
    return_on_staked_capital: float
    profit_factor: float
    mean_trade_return: float
    median_trade_return: float
    max_drawdown: float
    brier_score: float
    capital_efficiency: float

    def as_dict(self) -> dict:
        return self.__dict__.copy()


def _max_drawdown(
    returns: list[float],
    risk_fraction: float,
) -> float:
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for trade_return in returns:
        equity *= max(
            0.0,
            1.0 + risk_fraction * trade_return,
        )
        peak = max(peak, equity)
        dd = 1.0 - equity / peak if peak > 0 else 1.0
        max_dd = max(max_dd, dd)
    return max_dd


def _peak_committed_capital(rows: list[ResolvedTrade]) -> float:
    """Peak overlapping cash committed, including entry fees."""
    if not rows:
        return 0.0

    if any(trade.resolved_at is None for trade in rows):
        return sum(
            trade.signal.size_usd + trade.fee_usd
            for trade in rows
        )

    events: list[tuple[object, int, float]] = []
    for trade in rows:
        entry_cost = trade.signal.size_usd + trade.fee_usd
        events.append((trade.signal.observed_at, 1, entry_cost))
        events.append((trade.resolved_at, 0, entry_cost))
    events.sort(key=lambda item: (item[0], item[1]))

    committed = 0.0
    peak = 0.0
    for _, kind, entry_cost in events:
        if kind == 0:
            committed = max(0.0, committed - entry_cost)
        else:
            committed += entry_cost
            peak = max(peak, committed)
    return peak


def summarize(
    trades: Iterable[ResolvedTrade],
    *,
    risk_fraction: float = 0.10,
) -> TournamentMetrics:
    rows = list(trades)
    if not rows:
        return TournamentMetrics(
            0, 0, 0, 0, 0, 0,
            0, 0, 0, 0, 0,
        )

    rows.sort(
        key=lambda trade: (
            trade.resolved_at or trade.signal.observed_at,
            trade.signal.signal_id,
        )
    )
    returns = [trade.return_on_stake for trade in rows]
    pnls = [trade.pnl_usd for trade in rows]
    gross_profit = sum(value for value in pnls if value > 0)
    gross_loss = -sum(value for value in pnls if value < 0)
    profit_factor = (
        float("inf")
        if gross_loss == 0 and gross_profit > 0
        else (gross_profit / gross_loss if gross_loss else 0.0)
    )
    total_staked = sum(trade.signal.size_usd for trade in rows)
    net = sum(pnls)
    brier = (
        sum(
            (
                trade.signal.fair_probability
                - (1.0 if trade.won else 0.0)
            ) ** 2
            for trade in rows
        )
        / len(rows)
    )

    peak_capital = _peak_committed_capital(rows)
    capital_efficiency = net / peak_capital if peak_capital else 0.0

    return TournamentMetrics(
        trades=len(rows),
        wins=sum(trade.won for trade in rows),
        win_rate=sum(trade.won for trade in rows) / len(rows),
        net_pnl_usd=net,
        return_on_staked_capital=(
            net / total_staked if total_staked else 0.0
        ),
        profit_factor=profit_factor,
        mean_trade_return=sum(returns) / len(returns),
        median_trade_return=median(returns),
        max_drawdown=_max_drawdown(returns, risk_fraction),
        brier_score=brier,
        capital_efficiency=capital_efficiency,
    )


def fixed_window_score(metrics: TournamentMetrics) -> float:
    """Ranking score only; never a parameter-optimization objective."""
    if metrics.trades == 0:
        return float("-inf")

    sample_conf = min(1.0, (metrics.trades / 100.0) ** 0.5)
    pf_term = min(metrics.profit_factor, 5.0) / 5.0
    return (
        0.35 * metrics.return_on_staked_capital
        + 0.25 * metrics.capital_efficiency
        + 0.20 * pf_term
        - 0.15 * metrics.max_drawdown
        - 0.05 * metrics.brier_score
    ) * (0.35 + 0.65 * sample_conf)
