"""
Generic daily-market AUTORESEARCH harness for cross-market tournament jobs.

All market assumptions are supplied through environment variables by the
workflow. OOS is intentionally absent from tournament jobs.
"""

import argparse
import json
import os
from pathlib import Path

import numpy as np
import pandas as pd
from backtesting import Backtest

HERE = Path(__file__).resolve().parent
BASELINE = HERE / "baseline.json"
LAST_RUN = HERE / "last_run.json"

SYMBOL = os.environ["AUTORESEARCH_SYMBOL"]
MARKET = os.environ["AUTORESEARCH_MARKET"]
DATA_FILE = HERE / os.environ["AUTORESEARCH_DATA_FILE"]
CASH = float(os.environ.get("AUTORESEARCH_CASH", "10000000"))
COMMISSION = float(os.environ["AUTORESEARCH_COMMISSION"])
MARGIN = float(os.environ["AUTORESEARCH_MARGIN"])
IS_START = os.environ.get("AUTORESEARCH_IS_START", "2017-08-17")
IS_END = os.environ.get("AUTORESEARCH_IS_END", "2022-12-31")
MIN_TRADES = int(os.environ.get("AUTORESEARCH_MIN_TRADES", "50"))
VOL_BAND = float(os.environ.get("AUTORESEARCH_VOL_BAND", "0.10"))
MAX_DD_PCT = float(os.environ["AUTORESEARCH_MAX_DD_PCT"])
PROFILE = os.environ["AUTORESEARCH_PROFILE"]


def signed_sharpe(stats):
    vol = float(stats["Volatility (Ann.) [%]"])
    if not np.isfinite(vol) or vol == 0:
        return 0.0
    return float(stats["Return (Ann.) [%]"]) / vol


def k_metric(stats):
    ret = float(stats["Return [%]"]) / 100.0
    if ret <= -1:
        return float("-inf")
    return float(np.log1p(ret) * signed_sharpe(stats))


def load_data():
    if not DATA_FILE.exists():
        raise FileNotFoundError(DATA_FILE)
    df = pd.read_csv(DATA_FILE)
    if df.empty:
        raise RuntimeError(f"empty dataset: {DATA_FILE}")
    idx = pd.to_datetime(df["Date"], utc=True)
    need = ["Open", "High", "Low", "Close"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise RuntimeError(f"missing OHLC columns: {missing}")
    out = df[need + (["Volume"] if "Volume" in df.columns else [])].copy()
    out.index = idx
    out.index.name = "Date"
    out = out[~out.index.duplicated(keep="first")].sort_index()
    for c in out.columns:
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
        raise RuntimeError(f"{int(bad.sum())} malformed OHLC rows")
    return out


def run(df):
    from strategy import AtlasStrategy
    return Backtest(
        df,
        AtlasStrategy,
        cash=CASH,
        commission=COMMISSION,
        margin=MARGIN,
        trade_on_close=False,
    ).run()


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
    if summary["max_dd_pct"] < -MAX_DD_PCT:
        return False, (
            f"max drawdown {summary['max_dd_pct']:.2f}% exceeds "
            f"{MAX_DD_PCT:.1f}% limit"
        )
    if BASELINE.exists():
        base = json.loads(BASELINE.read_text(encoding="utf-8"))
        bv = float(base["ann_vol_pct"])
        v = float(summary["ann_vol_pct"])
        if bv > 0 and abs(v - bv) / bv > VOL_BAND:
            return False, (
                f"ann vol {v:.1f}% outside "
                f"+-{int(VOL_BAND * 100)}% of baseline {bv:.1f}%"
            )
    return True, "ok"


def write_json(path, obj):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def main():
    ap = argparse.ArgumentParser()
    modes = ap.add_mutually_exclusive_group()
    modes.add_argument("--is", dest="mode", action="store_const", const="is")
    modes.add_argument("--check", dest="mode", action="store_const", const="check")
    ap.add_argument("--set-baseline", action="store_true")
    args = ap.parse_args()
    mode = args.mode or "is"

    df = load_data()
    start = pd.Timestamp(IS_START, tz="UTC")
    end = pd.Timestamp(IS_END + " 23:59:59", tz="UTC")
    df = df.loc[(df.index >= start) & (df.index <= end)]
    if mode == "check":
        df = df.tail(500)
    if len(df) < 100:
        raise RuntimeError(f"only {len(df)} bars available")

    stats = run(df)
    summary = summarize(stats)
    ok, reason = guard(summary)
    summary.update({
        "guard_ok": bool(ok),
        "guard_reason": reason,
        "symbol": SYMBOL,
        "market": MARKET,
        "profile": PROFILE,
        "commission": COMMISSION,
        "margin": MARGIN,
        "max_dd_limit_pct": MAX_DD_PCT,
        "bars": int(len(df)),
        "start": str(df.index[0]),
        "end": str(df.index[-1]),
        "mode": mode,
    })

    print(stats.to_string())
    print("\nSUMMARY")
    print(json.dumps(summary, indent=2, sort_keys=True))

    if mode != "check":
        write_json(LAST_RUN, summary)
    if args.set_baseline:
        if mode != "is":
            raise RuntimeError("--set-baseline requires --is")
        if not ok:
            raise RuntimeError(f"refusing invalid baseline: {reason}")
        write_json(BASELINE, summary)


if __name__ == "__main__":
    main()
