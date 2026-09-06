"""Causal feature-store builder for AUTORESEARCH v4."""
from __future__ import annotations

from dataclasses import dataclass
from hashlib import sha256
from pathlib import Path
from typing import Mapping
import json

import numpy as np
import pandas as pd


def _hash_frame(df: pd.DataFrame) -> str:
    payload = pd.util.hash_pandas_object(df, index=True).to_numpy().tobytes()
    return sha256(payload).hexdigest()


def _rsi(close: pd.Series, window: int = 14) -> pd.Series:
    delta = close.diff()
    up = delta.clip(lower=0.0)
    down = -delta.clip(upper=0.0)
    avg_up = up.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    avg_dn = down.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()
    rs = avg_up / avg_dn.replace(0.0, np.nan)
    return 100.0 - 100.0 / (1.0 + rs)


def _atr(df: pd.DataFrame, window: int = 14) -> pd.Series:
    prev = df["Close"].shift(1)
    tr = pd.concat([
        df["High"] - df["Low"],
        (df["High"] - prev).abs(),
        (df["Low"] - prev).abs(),
    ], axis=1).max(axis=1)
    return tr.rolling(window, min_periods=window).mean()


def _expanding_zscore(series: pd.Series, min_periods: int = 60) -> pd.Series:
    mean = series.expanding(min_periods=min_periods).mean()
    std = series.expanding(min_periods=min_periods).std(ddof=1)
    return (series - mean) / std.replace(0.0, np.nan)


def _validate(frame: pd.DataFrame, symbol: str) -> None:
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError(f"{symbol}: DatetimeIndex required")
    if not frame.index.is_monotonic_increasing or frame.index.has_duplicates:
        raise ValueError(f"{symbol}: non-monotonic/duplicate index")
    for col in ("Open", "High", "Low", "Close"):
        if col not in frame:
            raise ValueError(f"{symbol}: missing {col}")


@dataclass
class FeatureStore:
    by_asset: dict[str, pd.DataFrame]
    cross_sectional: pd.DataFrame
    manifest: dict

    def write(self, root: str | Path) -> None:
        root = Path(root)
        root.mkdir(parents=True, exist_ok=True)
        for symbol, frame in self.by_asset.items():
            frame.to_csv(root / f"{symbol}_features.csv")
        self.cross_sectional.to_csv(root / "cross_sectional_features.csv")
        (root / "manifest.json").write_text(
            json.dumps(self.manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )


class FeatureStoreBuilder:
    """Build features using only information available at each timestamp."""

    def __init__(self, periods_per_year: Mapping[str, float] | None = None):
        self.periods_per_year = dict(periods_per_year or {})

    def asset_features(self, symbol: str, frame: pd.DataFrame) -> pd.DataFrame:
        _validate(frame, symbol)
        x = frame.copy()
        out = pd.DataFrame(index=x.index)
        close = x["Close"].astype(float)
        open_ = x["Open"].astype(float)
        high = x["High"].astype(float)
        low = x["Low"].astype(float)
        ret1 = close.pct_change()
        out["ret_1"] = ret1
        for w in (5, 20, 60, 126, 252):
            out[f"ret_{w}"] = close.pct_change(w)
        for w in (5, 20, 60):
            out[f"rv_{w}"] = ret1.rolling(w, min_periods=w).std(ddof=1) * np.sqrt(
                self.periods_per_year.get(symbol, 252.0)
            )
        out["atr_14"] = _atr(x, 14)
        out["atr_14_pct"] = out["atr_14"] / close
        out["gap_1"] = open_ / close.shift(1) - 1.0
        out["intraday_1"] = close / open_ - 1.0
        out["range_pct"] = (high - low) / close.replace(0.0, np.nan)
        out["close_location"] = (close - low) / (high - low).replace(0.0, np.nan)
        for w in (20, 50, 200):
            sma = close.rolling(w, min_periods=w).mean()
            out[f"dist_sma_{w}"] = close / sma - 1.0
        for w in (20, 252):
            rolling_high = close.rolling(w, min_periods=w).max()
            rolling_low = close.rolling(w, min_periods=w).min()
            out[f"dist_high_{w}"] = close / rolling_high - 1.0
            out[f"dist_low_{w}"] = close / rolling_low - 1.0
        out["rsi_2"] = _rsi(close, 2)
        out["rsi_14"] = _rsi(close, 14)
        out["ret_20_z_expanding"] = _expanding_zscore(out["ret_20"])
        out["rv_20_z_expanding"] = _expanding_zscore(out["rv_20"])

        if "Volume" in x:
            vol = pd.to_numeric(x["Volume"], errors="coerce")
            dollar = vol * close
            out["volume"] = vol
            out["dollar_volume"] = dollar
            out["volume_ratio_5_20"] = (
                vol.rolling(5, min_periods=5).mean()
                / vol.rolling(20, min_periods=20).mean().replace(0.0, np.nan)
            )
            out["dollar_volume_ratio_5_20"] = (
                dollar.rolling(5, min_periods=5).mean()
                / dollar.rolling(20, min_periods=20).mean().replace(0.0, np.nan)
            )
            out["liquidity_z_expanding"] = _expanding_zscore(np.log1p(dollar))
        return out.replace([np.inf, -np.inf], np.nan)

    @staticmethod
    def join_context(
        features: pd.DataFrame,
        context: pd.DataFrame,
        *,
        prefix: str,
        lag_rows: int = 1,
    ) -> pd.DataFrame:
        if not isinstance(context.index, pd.DatetimeIndex):
            raise ValueError("context requires DatetimeIndex")
        ctx = context.sort_index().copy()
        if lag_rows:
            ctx = ctx.shift(int(lag_rows))
        ctx = ctx.add_prefix(prefix)
        left = features.sort_index().reset_index().rename(columns={features.index.name or "index": "ts"})
        right = ctx.reset_index().rename(columns={ctx.index.name or "index": "ts"})
        merged = pd.merge_asof(left, right, on="ts", direction="backward")
        return merged.set_index("ts")

    def build(
        self,
        market_data: Mapping[str, pd.DataFrame],
        *,
        contexts: Mapping[str, pd.DataFrame] | None = None,
        context_lags: Mapping[str, int] | None = None,
    ) -> FeatureStore:
        by_asset: dict[str, pd.DataFrame] = {}
        input_hashes = {}
        for symbol in sorted(market_data):
            frame = market_data[symbol]
            input_hashes[symbol] = _hash_frame(frame)
            feat = self.asset_features(symbol, frame)
            for name, context in (contexts or {}).items():
                feat = self.join_context(
                    feat,
                    context,
                    prefix=f"ctx_{name}__",
                    lag_rows=(context_lags or {}).get(name, 1),
                )
            by_asset[symbol] = feat

        long_parts = []
        for symbol, feat in by_asset.items():
            cols = [c for c in ("ret_20", "ret_126", "rv_20", "dist_sma_200") if c in feat]
            part = feat[cols].copy()
            part["symbol"] = symbol
            long_parts.append(part)
        if long_parts:
            long = pd.concat(long_parts).reset_index().rename(columns={"index": "ts"})
            rank_cols = [c for c in ("ret_20", "ret_126", "rv_20", "dist_sma_200") if c in long]
            for col in rank_cols:
                long[f"xrank_{col}"] = long.groupby("ts")[col].rank(pct=True, method="average")
            cross = long.set_index(["ts", "symbol"]).sort_index()
        else:
            cross = pd.DataFrame()

        manifest = {
            "protocol": "alpha_generation_v4",
            "causal": True,
            "execution_convention": "features through close[t], execute open[t+1]",
            "context_join": "backward_asof_with_explicit_lag",
            "assets": sorted(market_data),
            "input_hashes": input_hashes,
            "contexts": sorted((contexts or {}).keys()),
            "context_lags": dict(context_lags or {}),
        }
        return FeatureStore(by_asset=by_asset, cross_sectional=cross, manifest=manifest)
