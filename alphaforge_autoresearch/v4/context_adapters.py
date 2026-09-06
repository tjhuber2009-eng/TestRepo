"""Free/public context-data adapters for AUTORESEARCH v4.

Adapters are source-specific and timestamp-preserving. The FeatureStoreBuilder
applies explicit backward-asof joins and lags.
"""
from __future__ import annotations

from io import StringIO
from pathlib import Path
from typing import Mapping
from urllib.parse import quote
from urllib.request import Request, urlopen
import json

import numpy as np
import pandas as pd

UA = "AUTORESEARCH-v4-context/1.0"


def _read_text(url: str, timeout: int = 30) -> str:
    req = Request(url, headers={"User-Agent": UA})
    with urlopen(req, timeout=timeout) as r:
        return r.read().decode("utf-8")


def fred_series(series_id: str, *, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    """Fetch a public FRED graph CSV without requiring an API key."""
    url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={quote(series_id)}"
    text = _read_text(url)
    x = pd.read_csv(StringIO(text))
    date_col = x.columns[0]
    x[date_col] = pd.to_datetime(x[date_col], utc=False)
    x = x.set_index(date_col).sort_index()
    col = x.columns[0]
    x[col] = pd.to_numeric(x[col], errors="coerce")
    x = x.rename(columns={col: series_id})
    if start is not None:
        x = x.loc[pd.Timestamp(start):]
    if end is not None:
        x = x.loc[:pd.Timestamp(end)]
    return x


def yahoo_daily_context(symbol: str, *, start: str, end: str) -> pd.DataFrame:
    """Fetch a public Yahoo daily context series such as ^VIX."""
    p1 = int(pd.Timestamp(start, tz="UTC").timestamp())
    p2 = int((pd.Timestamp(end, tz="UTC") + pd.Timedelta(days=1)).timestamp())
    enc = quote(symbol, safe="")
    url = (
        f"https://query1.finance.yahoo.com/v8/finance/chart/{enc}"
        f"?period1={p1}&period2={p2}&interval=1d&events=history&includeAdjustedClose=true"
    )
    payload = json.loads(_read_text(url))
    result = payload.get("chart", {}).get("result") or []
    if not result:
        raise RuntimeError(f"Yahoo returned no data for context {symbol}")
    r = result[0]
    stamps = pd.to_datetime(r["timestamp"], unit="s", utc=True).tz_convert(None)
    q = r["indicators"]["quote"][0]
    close = pd.to_numeric(pd.Series(q["close"], index=stamps), errors="coerce")
    return pd.DataFrame({"close": close}).dropna()


def yield_curve_context(*, start: str | None = None, end: str | None = None) -> pd.DataFrame:
    d2 = fred_series("DGS2", start=start, end=end)
    d10 = fred_series("DGS10", start=start, end=end)
    out = d2.join(d10, how="outer").sort_index()
    out["slope_10y_2y"] = out["DGS10"] - out["DGS2"]
    return out


def market_breadth(
    market_data: Mapping[str, pd.DataFrame], *, sma_window: int = 200
) -> pd.DataFrame:
    """Fraction of supplied assets above their own trailing SMA."""
    flags = {}
    for symbol, frame in market_data.items():
        close = pd.to_numeric(frame["Close"], errors="coerce")
        ma = close.rolling(sma_window, min_periods=sma_window).mean()
        flags[symbol] = (close > ma).astype(float).where(ma.notna())
    x = pd.DataFrame(flags)
    return pd.DataFrame({
        "fraction_above_sma": x.mean(axis=1, skipna=True),
        "breadth_count": x.notna().sum(axis=1),
    })


def load_earnings_events(path: str | Path) -> pd.DataFrame:
    """Load point-in-time earnings events: timestamp,symbol,surprise_z."""
    x = pd.read_csv(path)
    required = {"timestamp", "symbol", "surprise_z"}
    missing = required.difference(x.columns)
    if missing:
        raise ValueError(f"earnings events missing {sorted(missing)}")
    x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True).dt.tz_convert(None)
    x["surprise_z"] = pd.to_numeric(x["surprise_z"], errors="coerce")
    if x["surprise_z"].isna().any():
        raise ValueError("nonnumeric earnings surprise")
    return x.set_index("timestamp").sort_index()


def load_crypto_derivatives(path: str | Path) -> pd.DataFrame:
    """Load timestamped funding/basis/open-interest features from a frozen CSV."""
    x = pd.read_csv(path)
    if "timestamp" not in x:
        raise ValueError("crypto derivatives file requires timestamp")
    x["timestamp"] = pd.to_datetime(x["timestamp"], utc=True).dt.tz_convert(None)
    allowed = [c for c in ("funding_rate", "basis_pct", "open_interest", "mark_spot_spread_pct") if c in x]
    if not allowed:
        raise ValueError("no recognized crypto derivative fields")
    for c in allowed:
        x[c] = pd.to_numeric(x[c], errors="coerce")
    return x.set_index("timestamp")[allowed].sort_index()
