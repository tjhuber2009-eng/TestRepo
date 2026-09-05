from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Iterable

from .fees import polymarket_taker_fee_usd, shares_for_stake
from .models import Signal, ResolvedTrade


def settle_binary_signal(signal: Signal, won: bool, fill_price: float | None = None, resolved_at=None) -> ResolvedTrade:
    p = signal.market_price if fill_price is None else fill_price
    if not 0 < p <= 1:
        raise ValueError("fill_price must be in (0,1]")
    shares = shares_for_stake(signal.size_usd, p)
    fee = 0.0 if signal.order_mode == "maker" else polymarket_taker_fee_usd(
        shares, p, signal.fee_rate, signal.fee_exponent
    )
    payout = shares if won else 0.0
    pnl = payout - signal.size_usd - fee
    return ResolvedTrade(
        signal=signal,
        won=won,
        fill_price=p,
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


def _max_drawdown(returns: list[float], risk_fraction: float) -> float:
    equity = 1.0
    peak = 1.0
    max_dd = 0.0
    for r in returns:
        equity *= max(0.0, 1.0 + risk_fraction * r)
        peak = max(peak, equity)
        dd = 1.0 - equity / peak if peak > 0 else 1.0
        max_dd = max(max_dd, dd)
    return max_dd


def summarize(
    trades: Iterable[ResolvedTrade],
    *,
    risk_fraction: float = 0.10,
) -> TournamentMetrics:
    rows = list(trades)
    if not rows:
        return TournamentMetrics(0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0)

    returns = [t.return_on_stake for t in rows]
    pnls = [t.pnl_usd for t in rows]
    gross_profit = sum(x for x in pnls if x > 0)
    gross_loss = -sum(x for x in pnls if x < 0)
    pf = float("inf") if gross_loss == 0 and gross_profit > 0 else (
        gross_profit / gross_loss if gross_loss else 0.0
    )
    total_staked = sum(t.signal.size_usd for t in rows)
    net = sum(pnls)
    brier = sum(
        (t.signal.fair_probability - (1.0 if t.won else 0.0)) ** 2 for t in rows
    ) / len(rows)

    # Approximate capital efficiency: net PnL / max single-event capital committed.
    peak_capital = max(t.signal.size_usd for t in rows)
    cap_eff = net / peak_capital if peak_capital else 0.0

    return TournamentMetrics(
        trades=len(rows),
        wins=sum(t.won for t in rows),
        win_rate=sum(t.won for t in rows) / len(rows),
        net_pnl_usd=net,
        return_on_staked_capital=net / total_staked if total_staked else 0.0,
        profit_factor=pf,
        mean_trade_return=sum(returns) / len(returns),
        median_trade_return=median(returns),
        max_drawdown=_max_drawdown(returns, risk_fraction),
        brier_score=brier,
        capital_efficiency=cap_eff,
    )


def fixed_window_score(metrics: TournamentMetrics) -> float:
    """
    Ranking score, not an optimizer objective.

    Small samples are never discarded. Profit factor and capital efficiency are
    rewarded, while drawdown and forecast error are penalized. The sample-size
    term saturates instead of imposing a hard minimum.
    """
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
