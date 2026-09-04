"""
Moon Dev AUTORESEARCH — reconstructed FROZEN harness.
"""

import argparse
import json
import os
import sys
from pathlib import Path

import numpy as np
import pandas as pd
from backtesting import Backtest

HERE = Path(__file__).resolve().parent
DATA_DIR = HERE / "data"
BASELINE = HERE / "baseline.json"
LAST_RUN = HERE / "last_run.json"

ASSET = "ETH"
CASH = 10_000_000
COMMISSION = 0.002
MARGIN = 0.25
IS_START = "2017-08-17"
IS_END = "2022-12-31"
OOS_START = "2023-01-01"
MIN_TRADES = 50
VOL_BAND = 0.10


def signed_sharpe(stats):
    vol = float(stats["Volatility (Ann.) [%]"])
    if not np.isfinite(vol) or vol == 0:
        return 0.0
    return float(stats["Return (Ann.) [%]"]) / vol


def k_metric(stats):
    ret = float(stats["Return [%]"]) / 100
    if ret <= -1:
        return float("-inf")
    return float(np.log1p(ret) * signed_sharpe(stats))


def run(df):
    from strategy import MoonStrategy
    bt = Backtest(
        df,
        MoonStrategy,
        cash=CASH,
        commission=COMMISSION,
        margin=MARGIN,
    )
    return bt.run()


def summarize(stats):
    pf = stats["Profit Factor"]
    win = stats["Win Rate [%]"]
    return {
        "score": round(k_metric(stats), 4),
        "return_pct": round(float(stats["Return [%]"]), 2),
        "sharpe": round(signed_sharpe(stats), 3),
        "ann_vol_pct": round(float(stats["Volatility (Ann.) [%]"]), 2),
        "max_dd_pct": round(float(stats["Max. Drawdown [%]"]), 2),
        "trades": int(stats["# Trades"]),
        "win_pct": round(float(win), 2) if not pd.isna(win) else 0.0,
        "pf": round(float(pf), 3) if not pd.isna(pf) else 0.0,
    }


def guard(summary):
    if summary["trades"] < MIN_TRADES:
        return False, f"trades {summary['trades']} < {MIN_TRADES}"
    if not np.isfinite(summary["score"]):
        return False, "score not finite"
    if BASELINE.exists():
        with open(BASELINE, encoding="utf-8") as f:
            base = json.load(f)
        bv = base["ann_vol_pct"]
        v = summary["ann_vol_pct"]
        if bv > 0 and abs(v - bv) / bv > VOL_BAND:
            return False, (
                f"ann vol {v:.1f}% outside "
                f"+-{int(VOL_BAND * 100)}% of baseline {bv:.1f}%"
            )
    return True, "ok"


def candidate_paths(asset):
    a = asset.upper()
    return [
        DATA_DIR / f"{a}_6h.csv",
        DATA_DIR / f"{a}USDT_6h.csv",
        DATA_DIR / f"{a}-6h.csv",
    ]


def load_data(asset):
    path = next((p for p in candidate_paths(asset) if p.exists()), None)
    if path is None:
        names = ", ".join(p.name for p in candidate_paths(asset))
        raise FileNotFoundError(
            f"No cached 6h data for {asset}. Expected one of: {names}. "
            f"Run: {sys.executable} prepare_data.py --asset {asset}"
        )

    df = pd.read_csv(path)
    if df.empty:
        raise ValueError(f"Data file is empty: {path}")

    time_col = next(
        (
            c for c in [
                "Date", "Datetime", "datetime", "timestamp",
                "Timestamp", "Open time"
            ] if c in df.columns
        ),
        None,
    )
    if time_col is None:
        unnamed = [c for c in df.columns if c.lower().startswith("unnamed")]
        if unnamed:
            time_col = unnamed[0]
        else:
            raise ValueError(f"No timestamp column found in {path}")

    ts = df[time_col]
    if np.issubdtype(ts.dtype, np.number):
        unit = "ms" if float(ts.dropna().iloc[0]) > 10_000_000_000 else "s"
        idx = pd.to_datetime(ts, unit=unit, utc=True)
    else:
        idx = pd.to_datetime(ts, utc=True)

    rename = {
        c: c.title()
        for c in df.columns
        if c.lower() in {"open", "high", "low", "close", "volume"}
    }
    df = df.rename(columns=rename)
    need = ["Open", "High", "Low", "Close"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise ValueError(f"Missing OHLC columns in {path}: {missing}")

    cols = need + (["Volume"] if "Volume" in df.columns else [])
    out = df[cols].copy()
    out.index = idx
    out.index.name = "Date"
    out = out[~out.index.duplicated(keep="first")].sort_index()
    for c in cols:
        out[c] = pd.to_numeric(out[c], errors="coerce")
    out = out.dropna(subset=need)
    if "Volume" not in out.columns:
        out["Volume"] = 0.0

    bad = (
        out["High"] < out[["Open", "Close", "Low"]].max(axis=1)
    ) | (
        out["Low"] > out[["Open", "Close", "High"]].min(axis=1)
    )
    if bad.any():
        raise ValueError(
            f"Found {int(bad.sum())} malformed OHLC bars in {path}"
        )
    return out


def slice_mode(df, mode):
    is_start = pd.Timestamp(IS_START, tz="UTC")
    is_end = pd.Timestamp(IS_END + " 23:59:59", tz="UTC")
    if mode == "is":
        return df.loc[(df.index >= is_start) & (df.index <= is_end)]
    if mode == "oos":
        return df.loc[df.index >= pd.Timestamp(OOS_START, tz="UTC")]
    if mode == "check":
        return df.loc[(df.index >= is_start) & (df.index <= is_end)].tail(1500)
    if mode == "full":
        return df
    raise ValueError(mode)


def write_json(path, obj):
    tmp = Path(str(path) + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(obj, f, indent=2, sort_keys=True)
        f.write("\n")
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    modes = ap.add_mutually_exclusive_group()
    modes.add_argument("--is", dest="mode", action="store_const", const="is")
    modes.add_argument("--oos", dest="mode", action="store_const", const="oos")
    modes.add_argument("--check", dest="mode", action="store_const", const="check")
    modes.add_argument("--full", dest="mode", action="store_const", const="full")
    ap.add_argument("--asset", default=None)
    ap.add_argument("--set-baseline", action="store_true")
    args = ap.parse_args()

    if args.asset and args.mode:
        ap.error("--asset cannot be combined with a mode flag")
    asset = (args.asset or ASSET).upper()
    mode = "full" if args.asset else (args.mode or "is")

    df = slice_mode(load_data(asset), mode)
    if len(df) < 100:
        raise RuntimeError(f"Only {len(df)} bars available for {asset} mode={mode}")

    stats = run(df)
    print(stats.to_string())
    summary = summarize(stats)
    ok, reason = guard(summary)
    summary.update(
        {
            "guard_ok": bool(ok),
            "guard_reason": reason,
            "asset": asset,
            "mode": mode,
            "bars": int(len(df)),
            "start": str(df.index[0]),
            "end": str(df.index[-1]),
        }
    )

    if mode != "check":
        write_json(LAST_RUN, summary)
    if args.set_baseline:
        if mode != "is" or asset != ASSET:
            raise RuntimeError(
                "--set-baseline is only valid with default ETH --is"
            )
        write_json(BASELINE, summary)

    print("\nSUMMARY")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
