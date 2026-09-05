"""Causal multi-asset portfolio backtester for AUTORESEARCH v4.

Signals are formed from bar t information and executed at the next bar's open.
Portfolio P&L is measured open-to-next-open, which makes the timing convention
explicit and prevents close-to-same-close lookahead.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable, Mapping

import numpy as np
import pandas as pd

from .alpha_objective import AlphaMetrics, RiskPolicy, cagr_pct_from_equity, hard_gate, metrics_from_equity


@dataclass(frozen=True)
class AssetCost:
    commission_bps: float = 2.0
    slippage_bps: float = 1.0
    borrow_bps_per_year: float = 0.0

    @property
    def one_way_fraction(self) -> float:
        return (self.commission_bps + self.slippage_bps) / 10_000.0


@dataclass(frozen=True)
class PortfolioLimits:
    gross_leverage: float = 2.0
    net_min: float = -1.0
    net_max: float = 1.0
    per_asset_abs_weight: float = 1.0


@dataclass
class MultiAssetResult:
    equity: pd.Series
    returns: pd.Series
    target_weights: pd.DataFrame
    execution_weights: pd.DataFrame
    turnover: pd.Series
    costs: pd.Series
    metrics: AlphaMetrics
    gate_ok: bool
    gate_reasons: list[str]

    def summary(self) -> dict:
        return {
            "metrics": self.metrics.to_dict(),
            "gate_ok": self.gate_ok,
            "gate_reasons": list(self.gate_reasons),
            "final_equity": float(self.equity.iloc[-1]) if len(self.equity) else None,
            "mean_gross_exposure": float(self.execution_weights.abs().sum(axis=1).mean()),
            "max_gross_exposure": float(self.execution_weights.abs().sum(axis=1).max()),
        }


StrategyFn = Callable[[Mapping[str, pd.DataFrame], Mapping[str, pd.DataFrame] | None], pd.DataFrame]


def _validate_ohlcv(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError(f"{symbol}: DatetimeIndex required")
    if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise ValueError(f"{symbol}: index must be strictly increasing")
    required = {"Open", "High", "Low", "Close"}
    missing = required.difference(frame.columns)
    if missing:
        raise ValueError(f"{symbol}: missing OHLC columns {sorted(missing)}")
    x = frame.copy()
    for col in required:
        x[col] = pd.to_numeric(x[col], errors="coerce")
    if x[list(required)].isna().any().any():
        raise ValueError(f"{symbol}: OHLC contains nonnumeric/missing values")
    if (x["High"] < x[["Open", "Close", "Low"]].max(axis=1)).any():
        raise ValueError(f"{symbol}: invalid High")
    if (x["Low"] > x[["Open", "Close", "High"]].min(axis=1)).any():
        raise ValueError(f"{symbol}: invalid Low")
    return x


def align_market_data(data: Mapping[str, pd.DataFrame]) -> tuple[pd.DatetimeIndex, dict[str, pd.DataFrame]]:
    if not data:
        raise ValueError("at least one asset is required")
    checked = {symbol: _validate_ohlcv(frame, symbol) for symbol, frame in data.items()}
    common = None
    for frame in checked.values():
        common = frame.index if common is None else common.intersection(frame.index)
    if common is None or len(common) < 3:
        raise ValueError("insufficient common bars across assets")
    common = common.sort_values()
    return common, {symbol: frame.loc[common].copy() for symbol, frame in checked.items()}


def project_weights(weights: pd.DataFrame, limits: PortfolioLimits) -> pd.DataFrame:
    """Project raw desired weights onto simple leverage/net/per-asset constraints."""
    w = weights.astype(float).replace([np.inf, -np.inf], np.nan).fillna(0.0).copy()
    w = w.clip(-limits.per_asset_abs_weight, limits.per_asset_abs_weight)
    for idx in w.index:
        row = w.loc[idx].to_numpy(dtype=float)
        gross = float(np.abs(row).sum())
        if gross > limits.gross_leverage and gross > 0:
            row *= limits.gross_leverage / gross
        net = float(row.sum())
        if net > limits.net_max and net != 0:
            pos = row > 0
            excess = net - limits.net_max
            pos_sum = row[pos].sum()
            if pos_sum > 0:
                row[pos] *= max(0.0, (pos_sum - excess) / pos_sum)
        elif net < limits.net_min and net != 0:
            neg = row < 0
            neg_abs = -row[neg].sum()
            excess = limits.net_min - net
            if neg_abs > 0:
                row[neg] *= max(0.0, (neg_abs - excess) / neg_abs)
        w.loc[idx] = row
    return w


class MultiAssetBacktester:
    def __init__(
        self,
        market_data: Mapping[str, pd.DataFrame],
        *,
        costs: Mapping[str, AssetCost] | None = None,
        limits: PortfolioLimits | None = None,
        periods_per_year: float = 252.0,
        starting_equity: float = 1.0,
    ):
        self.index, self.data = align_market_data(market_data)
        self.symbols = sorted(self.data)
        self.costs = {s: (costs or {}).get(s, AssetCost()) for s in self.symbols}
        self.limits = limits or PortfolioLimits()
        self.periods_per_year = float(periods_per_year)
        self.starting_equity = float(starting_equity)

    def open_to_next_open_returns(self) -> pd.DataFrame:
        out = {}
        for symbol, frame in self.data.items():
            out[symbol] = frame["Open"].shift(-1) / frame["Open"] - 1.0
        return pd.DataFrame(out, index=self.index)

    def run(
        self,
        strategy: StrategyFn,
        *,
        features: Mapping[str, pd.DataFrame] | None = None,
        risk_policy: RiskPolicy | None = None,
        num_trials: int = 1,
        pbo: float | None = None,
        cost_multiplier: float = 1.0,
        cost_stress_multiplier: float | None = None,
        trade_event_count: int | None = None,
    ) -> MultiAssetResult:
        raw = strategy(self.data, features)
        if not isinstance(raw, pd.DataFrame):
            raise TypeError("strategy must return a DataFrame of target weights")
        raw = raw.reindex(index=self.index, columns=self.symbols).fillna(0.0)
        targets = project_weights(raw, self.limits)

        exec_w = targets.shift(1).fillna(0.0)
        asset_ret = self.open_to_next_open_returns()

        delta = exec_w.diff().fillna(exec_w)
        turnover = delta.abs().sum(axis=1)
        transaction_cost = pd.Series(0.0, index=self.index)
        for symbol in self.symbols:
            transaction_cost += (
                delta[symbol].abs()
                * self.costs[symbol].one_way_fraction
            )
        gross = exec_w.abs().sum(axis=1)
        borrowed = (gross - 1.0).clip(lower=0.0)
        borrow_cost = pd.Series(0.0, index=self.index)
        if any(self.costs[s].borrow_bps_per_year for s in self.symbols):
            bps = max(self.costs[s].borrow_bps_per_year for s in self.symbols)
            borrow_cost += borrowed * (bps / 10_000.0) / self.periods_per_year

        cost_series = transaction_cost * float(cost_multiplier) + borrow_cost
        gross_pnl = (exec_w * asset_ret).sum(axis=1)
        portfolio_ret = gross_pnl - cost_series
        portfolio_ret = portfolio_ret.iloc[:-1]
        exec_w = exec_w.loc[portfolio_ret.index]
        targets = targets.loc[portfolio_ret.index]
        turnover = turnover.loc[portfolio_ret.index]
        cost_series = cost_series.loc[portfolio_ret.index]
        gross = gross.loc[portfolio_ret.index]

        equity = self.starting_equity * (1.0 + portfolio_ret).cumprod()
        if len(equity) < 2:
            years = 0.0
        else:
            elapsed_days = max((equity.index[-1] - equity.index[0]).total_seconds() / 86400.0, 1.0)
            years = elapsed_days / 365.2425

        trades = int(trade_event_count if trade_event_count is not None else (delta.abs().sum(axis=1) > 1e-12).sum())
        turn_per_year = float(turnover.sum() / max(years, 1e-12)) if years > 0 else float("nan")
        mean_gross = float(gross.mean()) if len(gross) else 0.0
        stress_cagr = None
        if cost_stress_multiplier is not None:
            stress_cost = (
                transaction_cost.loc[portfolio_ret.index] * float(cost_stress_multiplier)
                + borrow_cost.loc[portfolio_ret.index]
            )
            stress_ret = gross_pnl.loc[portfolio_ret.index] - stress_cost
            stress_equity = self.starting_equity * (1.0 + stress_ret).cumprod()
            stress_cagr = cagr_pct_from_equity(stress_equity.to_numpy(), years)

        metrics = metrics_from_equity(
            equity.to_numpy(),
            portfolio_ret.to_numpy(),
            self.periods_per_year,
            years,
            trades,
            num_trials=num_trials,
            pbo=pbo,
            turnover_per_year=turn_per_year,
            gross_exposure=mean_gross,
            cost_stress_cagr_pct=stress_cagr,
        )
        if risk_policy is None:
            gate_ok, reasons = True, []
        else:
            gate_ok, reasons = hard_gate(metrics, risk_policy)
        return MultiAssetResult(
            equity=equity,
            returns=portfolio_ret,
            target_weights=targets,
            execution_weights=exec_w,
            turnover=turnover,
            costs=cost_series,
            metrics=metrics,
            gate_ok=gate_ok,
            gate_reasons=reasons,
        )


def leveraged_regime_rotation(
    *,
    signal_symbol: str,
    risk_symbol: str,
    defensive_symbol: str | None = None,
    sma_window: int = 175,
    momentum_window: int = 126,
    risk_weight: float = 1.0,
    defensive_weight: float = 1.0,
) -> StrategyFn:
    """Factory for signal-asset -> traded-asset rotation."""
    def strategy(data: Mapping[str, pd.DataFrame], features=None) -> pd.DataFrame:
        if signal_symbol not in data or risk_symbol not in data:
            raise KeyError("signal/risk symbol missing from market data")
        close = data[signal_symbol]["Close"]
        sma = close.rolling(sma_window, min_periods=sma_window).mean()
        momentum = close / close.shift(momentum_window) - 1.0
        risk_on = (close > sma) & (momentum > 0.0)
        out = pd.DataFrame(0.0, index=close.index, columns=sorted(data))
        out.loc[risk_on, risk_symbol] = risk_weight
        if defensive_symbol is not None:
            if defensive_symbol not in data:
                raise KeyError("defensive symbol missing from market data")
            out.loc[~risk_on, defensive_symbol] = defensive_weight
        return out
    return strategy

def leveraged_hysteresis_rotation(
    *,
    signal_symbol: str,
    risk_symbol: str,
    defensive_symbol: str | None = None,
    sma_window: int = 175,
    entry_band: float = 0.02,
    exit_band: float = 0.02,
    risk_weight: float = 1.0,
    defensive_weight: float = 1.0,
) -> StrategyFn:
    """Stateful causal SMA-band regime intended to reduce threshold whipsaw.

    At close[t], risk-on is entered only above SMA*(1+entry_band) and is
    retained until close[t] falls below SMA*(1-exit_band). The target produced
    at t is shifted by the engine and cannot execute before open[t+1].
    """
    if sma_window < 2:
        raise ValueError("sma_window must be >=2")
    if entry_band < 0.0 or exit_band < 0.0:
        raise ValueError("hysteresis bands must be non-negative")

    def strategy(data: Mapping[str, pd.DataFrame], features=None) -> pd.DataFrame:
        if signal_symbol not in data or risk_symbol not in data:
            raise KeyError("signal/risk symbol missing from market data")
        if defensive_symbol is not None and defensive_symbol not in data:
            raise KeyError("defensive symbol missing from market data")
        close = data[signal_symbol]["Close"]
        sma = close.rolling(sma_window, min_periods=sma_window).mean()
        out = pd.DataFrame(0.0, index=close.index, columns=sorted(data))
        active = False
        for i, ts in enumerate(close.index):
            c = float(close.iloc[i])
            s = float(sma.iloc[i]) if pd.notna(sma.iloc[i]) else float("nan")
            if np.isfinite(s):
                if not active and c > s * (1.0 + float(entry_band)):
                    active = True
                elif active and c < s * (1.0 - float(exit_band)):
                    active = False
            else:
                active = False
            if active:
                out.loc[ts, risk_symbol] = float(risk_weight)
            elif defensive_symbol is not None:
                out.loc[ts, defensive_symbol] = float(defensive_weight)
        return out

    return strategy

