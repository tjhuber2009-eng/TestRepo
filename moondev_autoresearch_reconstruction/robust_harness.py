"""
Chronological robustness harness for continuous AUTORESEARCH.

Search data stops at 2022-12-31. 2023+ is deliberately outside this harness.
Each candidate is evaluated on multiple chronological folds plus the full
pre-OOS span. The agent receives only the conservative composite score and a
generic pass/fail reason; detailed fold metrics are retained for audit.
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
PROFILE = os.environ["AUTORESEARCH_PROFILE"]
MAX_DD_PCT = float(os.environ["AUTORESEARCH_MAX_DD_PCT"])
MIN_TRADES = int(os.environ.get("AUTORESEARCH_MIN_TRADES", "20"))
MIN_FOLD_TRADES = int(os.environ.get("AUTORESEARCH_MIN_FOLD_TRADES", "2"))
MIN_ACTIVE_FOLDS = int(os.environ.get("AUTORESEARCH_MIN_ACTIVE_FOLDS", "3"))
MIN_FOLD_BARS = int(os.environ.get("AUTORESEARCH_MIN_FOLD_BARS", "180"))
VOL_BAND = float(os.environ.get("AUTORESEARCH_VOL_BAND", "0.20"))
SEARCH_START = os.environ.get("AUTORESEARCH_IS_START", "2017-08-17")
SEARCH_END = os.environ.get("AUTORESEARCH_IS_END", "2022-12-31")

FOLDS = [
    ("F1_2017_2019", "2017-08-17", "2019-12-31"),
    ("F2_2020", "2020-01-01", "2020-12-31"),
    ("F3_2021", "2021-01-01", "2021-12-31"),
    ("F4_2022", "2022-01-01", "2022-12-31"),
]


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


def summarize_stats(stats):
    pf = stats["Profit Factor"]
    win = stats["Win Rate [%]"]
    return {
        "raw_k": round(k_metric(stats), 6),
        "return_pct": round(float(stats["Return [%]"]), 3),
        "sharpe": round(signed_sharpe(stats), 4),
        "ann_vol_pct": round(float(stats["Volatility (Ann.) [%]"]), 3),
        "max_dd_pct": round(float(stats["Max. Drawdown [%]"]), 3),
        "trades": int(stats["# Trades"]),
        "win_pct": round(float(win), 2) if not pd.isna(win) else 0.0,
        "pf": round(float(pf), 3) if not pd.isna(pf) else 0.0,
    }


def robust_score(folds):
    scores = np.array([float(x["raw_k"]) for x in folds], dtype=float)
    if len(scores) == 0 or not np.isfinite(scores).all():
        return float("-inf")
    med = float(np.median(scores))
    worst = float(np.min(scores))
    dispersion = float(np.std(scores))
    # Conservative on purpose: reward typical performance, heavily weight the
    # weakest chronological regime, and penalize instability.
    return 0.50 * med + 0.50 * worst - 0.10 * dispersion


def load_data():
    if not DATA_FILE.exists():
        raise FileNotFoundError(DATA_FILE)
    df = pd.read_csv(DATA_FILE)
    if df.empty:
        raise RuntimeError(f"empty dataset: {DATA_FILE}")

    time_col = next(
        (c for c in ["Date", "Datetime", "datetime", "timestamp", "Timestamp"] if c in df.columns),
        None,
    )
    if time_col is None:
        raise RuntimeError("timestamp column missing")
    idx = pd.to_datetime(df[time_col], utc=True)

    rename = {
        c: c.title()
        for c in df.columns
        if c.lower() in {"open", "high", "low", "close", "volume"}
    }
    df = df.rename(columns=rename)
    need = ["Open", "High", "Low", "Close"]
    missing = [c for c in need if c not in df.columns]
    if missing:
        raise RuntimeError(f"missing OHLC columns: {missing}")

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
        raise RuntimeError(
            f"data integrity failure: {int(bad.sum())} malformed OHLC rows"
        )
    return out


def run_one(df):
    from strategy import MoonStrategy
    return Backtest(
        df,
        MoonStrategy,
        cash=CASH,
        commission=COMMISSION,
        margin=MARGIN,
        trade_on_close=False,
    ).run()


def slice_dates(df, start, end):
    a = pd.Timestamp(start, tz="UTC")
    b = pd.Timestamp(end + " 23:59:59", tz="UTC")
    return df.loc[(df.index >= a) & (df.index <= b)]


def evaluate(df):
    full_df = slice_dates(df, SEARCH_START, SEARCH_END)
    if len(full_df) < 100:
        raise RuntimeError(f"only {len(full_df)} bars in pre-OOS search span")

    full = summarize_stats(run_one(full_df))
    fold_rows = []
    skipped = []

    for name, start, end in FOLDS:
        x = slice_dates(df, start, end)
        if len(x) < MIN_FOLD_BARS:
            skipped.append({"name": name, "bars": int(len(x)), "reason": "insufficient_bars"})
            continue
        s = summarize_stats(run_one(x))
        s.update({"name": name, "bars": int(len(x)), "start": start, "end": end})
        fold_rows.append(s)

    score = robust_score(fold_rows)
    full.update({
        "score": round(score, 6) if np.isfinite(score) else float("-inf"),
        "raw_full_k": full.pop("raw_k"),
        "folds": fold_rows,
        "skipped_folds": skipped,
        "active_folds": len(fold_rows),
        "worst_fold_k": round(min((x["raw_k"] for x in fold_rows), default=float("-inf")), 6),
        "median_fold_k": round(float(np.median([x["raw_k"] for x in fold_rows])), 6) if fold_rows else float("-inf"),
        "fold_score_std": round(float(np.std([x["raw_k"] for x in fold_rows])), 6) if fold_rows else float("inf"),
        "bars": int(len(full_df)),
        "start": str(full_df.index[0]),
        "end": str(full_df.index[-1]),
    })
    return full


def guard(summary):
    details = []

    if summary["trades"] < MIN_TRADES:
        details.append(f"total trades {summary['trades']} < {MIN_TRADES}")
    if summary["active_folds"] < MIN_ACTIVE_FOLDS:
        details.append(
            f"active folds {summary['active_folds']} < {MIN_ACTIVE_FOLDS}"
        )
    if not np.isfinite(summary["score"]):
        details.append("robust score not finite")
    if summary["max_dd_pct"] < -MAX_DD_PCT:
        details.append("full-span drawdown limit exceeded")

    for f in summary["folds"]:
        if f["trades"] < MIN_FOLD_TRADES:
            details.append(f"{f['name']} insufficient trades")
        if f["max_dd_pct"] < -MAX_DD_PCT:
            details.append(f"{f['name']} drawdown limit exceeded")
        if not np.isfinite(float(f["raw_k"])):
            details.append(f"{f['name']} score not finite")

    if BASELINE.exists():
        base = json.loads(BASELINE.read_text(encoding="utf-8"))
        bv = float(base["ann_vol_pct"])
        v = float(summary["ann_vol_pct"])
        if bv > 0 and abs(v - bv) / bv > VOL_BAND:
            details.append("portfolio volatility left frozen baseline band")

    ok = not details
    # Keep detailed diagnostics out of the agent-visible result ledger. This
    # reduces adaptive tuning to individual validation folds.
    public_reason = "ok" if ok else "chronological robustness gate failed"
    return ok, public_reason, details


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
    if mode == "check":
        x = slice_dates(df, SEARCH_START, SEARCH_END).tail(500)
        stats = summarize_stats(run_one(x))
        print(json.dumps(stats, indent=2, sort_keys=True))
        return

    summary = evaluate(df)
    ok, reason, details = guard(summary)
    summary.update({
        "guard_ok": bool(ok),
        "guard_reason": reason,
        "audit_guard_details": details,
        "symbol": SYMBOL,
        "market": MARKET,
        "profile": PROFILE,
        "commission": COMMISSION,
        "margin": MARGIN,
        "max_dd_limit_pct": MAX_DD_PCT,
        "protocol": "chronological_robust_v1",
        "oos_opened": False,
    })

    write_json(LAST_RUN, summary)
    if args.set_baseline:
        if not ok:
            raise RuntimeError(
                "refusing invalid robust baseline: " + "; ".join(details)
            )
        write_json(BASELINE, summary)

    print("SUMMARY")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
