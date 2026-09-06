"""Separate intraday research protocol for AUTORESEARCH v4.

Intraday research is intentionally isolated from the daily protocol because
spread, slippage, funding, session handling and annualization differ materially.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import time
from typing import Mapping

import numpy as np
import pandas as pd

from .multi_asset_engine import AssetCost, MultiAssetBacktester, PortfolioLimits


@dataclass(frozen=True)
class IntradayProtocol:
    id: str = "intraday_v1_sealed"
    bar_minutes: int = 60
    development_end: str = "2020-12-31"
    hidden_validation_start: str = "2021-01-01"
    hidden_validation_end: str = "2022-12-31"
    final_oos_start: str = "2023-01-01"
    timezone: str = "UTC"
    session_start: str | None = None
    session_end: str | None = None
    bars_per_day: float = 24.0
    days_per_year: float = 365.0

    @property
    def periods_per_year(self) -> float:
        return float(self.bars_per_day * self.days_per_year)

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass(frozen=True)
class IntradayExecutionCost:
    commission_bps: float
    half_spread_bps: float
    slippage_bps: float
    funding_bps_per_year: float = 0.0

    def to_asset_cost(self) -> AssetCost:
        return AssetCost(
            commission_bps=self.commission_bps,
            slippage_bps=self.half_spread_bps + self.slippage_bps,
            borrow_bps_per_year=self.funding_bps_per_year,
        )


def _parse_hhmm(value: str) -> time:
    hh, mm = value.split(":", 1)
    return time(int(hh), int(mm))


def assert_intraday_data(frame: pd.DataFrame, protocol: IntradayProtocol, *, stage: str = "development") -> None:
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("intraday frame requires DatetimeIndex")
    idx = frame.index
    if idx.tz is None:
        raise ValueError("timezone-aware intraday index required")
    if not idx.is_monotonic_increasing or idx.has_duplicates:
        raise ValueError("intraday index must be strictly increasing")
    if len(idx) >= 3:
        median_minutes = np.median(np.diff(idx.asi8) / 60_000_000_000)
        if abs(median_minutes - protocol.bar_minutes) > max(1.0, protocol.bar_minutes * 0.25):
            raise ValueError(f"unexpected bar interval median={median_minutes}m")
    max_ts = idx.max().tz_convert("UTC").tz_localize(None)
    if stage in {"development", "search", "fit"}:
        boundary = pd.Timestamp(protocol.development_end) + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    elif stage == "validation":
        boundary = pd.Timestamp(protocol.hidden_validation_end) + pd.Timedelta(days=1) - pd.Timedelta(nanoseconds=1)
    else:
        raise ValueError(f"unknown intraday stage {stage}")
    if max_ts > boundary:
        raise RuntimeError(f"intraday {stage} data crosses sealed boundary {boundary.date()}")
    if max_ts >= pd.Timestamp(protocol.final_oos_start):
        raise RuntimeError("final OOS contamination")


def apply_session(frame: pd.DataFrame, protocol: IntradayProtocol) -> pd.DataFrame:
    if protocol.session_start is None or protocol.session_end is None:
        return frame
    local = frame.tz_convert(protocol.timezone)
    start = _parse_hhmm(protocol.session_start)
    end = _parse_hhmm(protocol.session_end)
    t = local.index.time
    if start <= end:
        mask = np.array([(x >= start and x <= end) for x in t])
    else:
        mask = np.array([(x >= start or x <= end) for x in t])
    return frame.loc[mask]


class IntradayBacktester(MultiAssetBacktester):
    def __init__(
        self,
        market_data: Mapping[str, pd.DataFrame],
        *,
        protocol: IntradayProtocol,
        execution_costs: Mapping[str, IntradayExecutionCost],
        limits: PortfolioLimits | None = None,
        stage: str = "development",
    ):
        checked = {}
        for symbol, frame in market_data.items():
            assert_intraday_data(frame, protocol, stage=stage)
            checked[symbol] = apply_session(frame, protocol)
        super().__init__(
            checked,
            costs={s: execution_costs[s].to_asset_cost() for s in checked},
            limits=limits,
            periods_per_year=protocol.periods_per_year,
        )
        self.protocol = protocol
        self.stage = stage
