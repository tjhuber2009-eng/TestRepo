"""
Nested chronological robustness harness for continuous AUTORESEARCH.

Protocol v2 deliberately separates adaptive development from hidden pre-OOS
validation:

* NVIDIA/search sees development data only.
* Development ends before AUTORESEARCH_VALIDATION_START.
* Hidden validation is opened only after every research track is frozen.
* 2023+ is never downloaded by this project and remains final one-look OOS.

Candidates are scored from continuous equity-path folds (no artificial strategy
reset at year boundaries), stressed at higher transaction costs, and audited
with a deterministic block bootstrap diagnostic.
"""

import argparse
import hashlib
import json
import math
import os
from pathlib import Path

import numpy as np
import pandas as pd
from backtesting import Backtest

HERE = Path(__file__).resolve().parent
BASELINE = HERE / "baseline.json"
LAST_RUN = HERE / "last_run.json"
VALIDATION_RUN = HERE / "validation_run.json"
STRATEGY_FILE = HERE / "strategy.py"

SYMBOL = os.environ["AUTORESEARCH_SYMBOL"]
MARKET = os.environ["AUTORESEARCH_MARKET"]
DATA_FILE = HERE / os.environ["AUTORESEARCH_DATA_FILE"]
CASH = float(os.environ.get("AUTORESEARCH_CASH", "10000000"))
COMMISSION = float(os.environ["AUTORESEARCH_COMMISSION"])
MARGIN = float(os.environ["AUTORESEARCH_MARGIN"])
BARS_PER_YEAR = int(os.environ.get("AUTORESEARCH_BARS_PER_YEAR", "252"))
PROFILE = os.environ["AUTORESEARCH_PROFILE"]
MAX_DD_PCT = float(os.environ["AUTORESEARCH_MAX_DD_PCT"])

MIN_TRADES = int(os.environ.get("AUTORESEARCH_MIN_TRADES", "12"))
MIN_VALIDATION_TRADES = int(os.environ.get("AUTORESEARCH_MIN_VALIDATION_TRADES", "2"))
MIN_ACTIVE_FOLDS = int(os.environ.get("AUTORESEARCH_MIN_ACTIVE_FOLDS", "3"))
MIN_FOLD_BARS = int(os.environ.get("AUTORESEARCH_MIN_FOLD_BARS", "100"))
VOL_BAND = float(os.environ.get("AUTORESEARCH_VOL_BAND", "0.25"))
COST_STRESS_MULT = float(os.environ.get("AUTORESEARCH_COST_STRESS_MULT", "2.0"))

SEARCH_START = os.environ.get("AUTORESEARCH_IS_START", "2017-08-17")
VALIDATION_START = os.environ.get("AUTORESEARCH_VALIDATION_START", "2021-01-01")
VALIDATION_END = os.environ.get("AUTORESEARCH_VALIDATION_END", "2022-12-31")
DEV_END = (pd.Timestamp(VALIDATION_START) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")

PROTOCOL = "nested_chronological_v2"


def sha256_file(path):
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def to_utc_timestamp(value, end=False):
    text = str(value)
    if end and len(text) == 10:
        text += " 23:59:59"
    return pd.Timestamp(text, tz="UTC")


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
    if not out.index.is_monotonic_increasing:
        raise RuntimeError("data index is not monotonic")
    return out


def run_bt(df, commission):
    from strategy import MoonStrategy
    return Backtest(
        df,
        MoonStrategy,
        cash=CASH,
        commission=commission,
        margin=MARGIN,
        trade_on_close=False,
    ).run()


def slice_equity(stats, start, end):
    eq = stats["_equity_curve"]["Equity"].astype(float).copy()
    idx = pd.to_datetime(eq.index, utc=True)
    eq.index = idx
    a = to_utc_timestamp(start)
    b = to_utc_timestamp(end, end=True)
    return eq.loc[(eq.index >= a) & (eq.index <= b)]


def slice_trades(stats, start, end):
    tr = stats["_trades"].copy()
    if tr.empty or "ExitTime" not in tr.columns:
        return tr.iloc[0:0]
    exits = pd.to_datetime(tr["ExitTime"], utc=True)
    a = to_utc_timestamp(start)
    b = to_utc_timestamp(end, end=True)
    return tr.loc[(exits >= a) & (exits <= b)].copy()


def intrabar_drawdown_proxy(stats, price_df, start, end):
    """Conservative one-trade-at-a-time adverse-excursion equity proxy.

    Daily close equity can hide an intraday breach. For each realized trade we
    mark its worst daily low/high while open against pre-entry equity and the
    prior equity peak. This is still a proxy (especially if a strategy
    pyramids), so it is reported explicitly rather than called exact.
    """
    if price_df is None:
        return 0.0
    tr = slice_trades(stats, start, end)
    if tr.empty:
        return 0.0
    eq = stats["_equity_curve"]["Equity"].astype(float).reset_index(drop=True)
    lows = pd.to_numeric(price_df["Low"], errors="coerce").reset_index(drop=True)
    highs = pd.to_numeric(price_df["High"], errors="coerce").reset_index(drop=True)
    worst = 0.0
    for _, row in tr.iterrows():
        try:
            eb = int(row["EntryBar"])
            xb = int(row["ExitBar"])
            size = float(row["Size"])
            entry = float(row["EntryPrice"])
        except Exception:
            continue
        if eb < 0 or eb >= len(eq) or not np.isfinite([size, entry]).all():
            continue
        xb = min(max(xb, eb), len(eq) - 1)
        base_i = max(0, eb - 1)
        base_equity = float(eq.iloc[base_i])
        peak_equity = float(eq.iloc[: eb + 1].max())
        if base_equity <= 0 or peak_equity <= 0:
            continue
        if size > 0:
            adverse_px = float(lows.iloc[eb:xb + 1].min())
            adverse_pnl = size * (adverse_px - entry)
        elif size < 0:
            adverse_px = float(highs.iloc[eb:xb + 1].max())
            adverse_pnl = abs(size) * (entry - adverse_px)
        else:
            continue
        adverse_equity = base_equity + adverse_pnl
        dd = 100.0 * (adverse_equity / peak_equity - 1.0)
        worst = min(worst, dd)
    return round(float(worst), 3)


def metrics_from_stats(stats, start, end, price_df=None):
    eq = slice_equity(stats, start, end)
    if len(eq) < 2:
        return {
            "raw_k": float("-inf"), "return_pct": 0.0, "sharpe": 0.0,
            "ann_vol_pct": 0.0, "max_dd_pct": 0.0, "trades": 0,
            "win_pct": 0.0, "pf": 0.0, "bars": int(len(eq)),
        }

    rets = eq.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    total = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    years = max(len(rets) / float(BARS_PER_YEAR), 1.0 / float(BARS_PER_YEAR))
    if total <= -1.0:
        ann_ret = -1.0
    else:
        ann_ret = (1.0 + total) ** (1.0 / years) - 1.0
    vol = float(rets.std(ddof=0) * math.sqrt(BARS_PER_YEAR)) if len(rets) else 0.0
    sharpe = float(ann_ret / vol) if vol > 0 and np.isfinite(vol) else 0.0
    peak = eq.cummax()
    dd = eq / peak - 1.0
    max_dd = float(dd.min() * 100.0)

    trades = slice_trades(stats, start, end)
    pnl = pd.to_numeric(trades.get("PnL", pd.Series(dtype=float)), errors="coerce").dropna()
    gains = float(pnl[pnl > 0].sum()) if len(pnl) else 0.0
    losses = float(-pnl[pnl < 0].sum()) if len(pnl) else 0.0
    if losses > 0:
        pf = gains / losses
    elif gains > 0:
        pf = 99.0
    else:
        pf = 0.0
    wins = int((pnl > 0).sum()) if len(pnl) else 0
    win_pct = 100.0 * wins / len(pnl) if len(pnl) else 0.0
    positive = pnl[pnl > 0].sort_values(ascending=False)
    gross_profit = float(positive.sum()) if len(positive) else 0.0
    top1_concentration = (
        float(positive.iloc[0]) / gross_profit
        if gross_profit > 0 and len(positive) else 0.0
    )
    top3_concentration = (
        float(positive.iloc[:3].sum()) / gross_profit
        if gross_profit > 0 and len(positive) else 0.0
    )
    raw_k = math.log1p(total) * sharpe if total > -1 and np.isfinite(sharpe) else float("-inf")
    intrabar_proxy = intrabar_drawdown_proxy(stats, price_df, start, end)

    return {
        "raw_k": round(float(raw_k), 6) if np.isfinite(raw_k) else float("-inf"),
        "return_pct": round(total * 100.0, 3),
        "sharpe": round(sharpe, 4),
        "ann_vol_pct": round(vol * 100.0, 3),
        "max_dd_pct": round(max_dd, 3),
        "intrabar_dd_proxy_pct": intrabar_proxy,
        "trades": int(len(pnl)),
        "win_pct": round(win_pct, 2),
        "pf": round(float(min(pf, 99.0)), 3),
        "top1_profit_concentration": round(top1_concentration, 4),
        "top3_profit_concentration": round(top3_concentration, 4),
        "bars": int(len(eq)),
    }


def fold_windows(eq_index, start, end):
    idx = pd.DatetimeIndex(pd.to_datetime(eq_index, utc=True))
    a = to_utc_timestamp(start)
    b = to_utc_timestamp(end, end=True)
    idx = idx[(idx >= a) & (idx <= b)]
    if len(idx) == 0:
        return []

    annual = []
    for year in range(idx[0].year, idx[-1].year + 1):
        s = pd.Timestamp(f"{year}-01-01", tz="UTC")
        e = pd.Timestamp(f"{year}-12-31 23:59:59", tz="UTC")
        n = int(((idx >= s) & (idx <= e)).sum())
        if n >= MIN_FOLD_BARS:
            annual.append((f"Y{year}", s.strftime("%Y-%m-%d"), e.strftime("%Y-%m-%d"), n))
    if len(annual) >= MIN_ACTIVE_FOLDS:
        return annual

    half = []
    for year in range(idx[0].year, idx[-1].year + 1):
        for half_no, (m1, m2) in enumerate(((1, 6), (7, 12)), 1):
            s = pd.Timestamp(year=year, month=m1, day=1, tz="UTC")
            e = (pd.Timestamp(year=year, month=m2, day=1, tz="UTC") + pd.offsets.MonthEnd(1))
            e = pd.Timestamp(e).tz_convert("UTC") + pd.Timedelta(hours=23, minutes=59, seconds=59)
            n = int(((idx >= s) & (idx <= e)).sum())
            if n >= MIN_FOLD_BARS:
                half.append((f"H{half_no}_{year}", s.strftime("%Y-%m-%d"), e.strftime("%Y-%m-%d"), n))
    return half


def deterministic_bootstrap_p10(stats, start, end, reps=200, block=10):
    eq = slice_equity(stats, start, end)
    r = eq.pct_change().replace([np.inf, -np.inf], np.nan).dropna().to_numpy(dtype=float)
    n = len(r)
    if n < 30:
        return float("-inf")
    seed_material = (
        sha256_file(STRATEGY_FILE) + "|" + SYMBOL + "|" + PROFILE + "|" + start + "|" + end
    ).encode()
    seed = int(hashlib.sha256(seed_material).hexdigest()[:16], 16) % (2**32)
    rng = np.random.default_rng(seed)
    vals = []
    for _ in range(reps):
        pieces = []
        while sum(len(x) for x in pieces) < n:
            j = int(rng.integers(0, max(1, n - block + 1)))
            pieces.append(r[j:j + block])
        sample = np.concatenate(pieces)[:n]
        sd = float(np.std(sample, ddof=0))
        vals.append(float(np.mean(sample) / sd * math.sqrt(BARS_PER_YEAR)) if sd > 0 else 0.0)
    return round(float(np.quantile(vals, 0.10)), 4)


def robust_score(folds, stress, bootstrap_p10):
    scores = np.array([float(x["raw_k"]) for x in folds], dtype=float)
    scores = scores[np.isfinite(scores)]
    if len(scores) == 0:
        return float("-inf")
    med = float(np.median(scores))
    p25 = float(np.quantile(scores, 0.25))
    worst = float(np.min(scores))
    dispersion = float(np.std(scores))
    stress_k = float(stress["raw_k"]) if np.isfinite(float(stress["raw_k"])) else -1e6
    boot_penalty = max(0.0, -float(bootstrap_p10)) if np.isfinite(bootstrap_p10) else 1.0
    return (
        0.40 * med
        + 0.25 * p25
        + 0.20 * worst
        + 0.15 * stress_k
        - 0.10 * dispersion
        - 0.05 * boot_penalty
    )


def evaluate_search(df):
    a = to_utc_timestamp(SEARCH_START)
    d = to_utc_timestamp(DEV_END, end=True)
    work = df.loc[(df.index >= a) & (df.index <= d)]
    if len(work) < 200:
        raise RuntimeError(f"only {len(work)} bars in adaptive development span")

    base_stats = run_bt(work, COMMISSION)
    stress_stats = run_bt(work, COMMISSION * COST_STRESS_MULT)
    full = metrics_from_stats(base_stats, SEARCH_START, DEV_END, work)
    stress = metrics_from_stats(stress_stats, SEARCH_START, DEV_END, work)

    eq_idx = pd.to_datetime(base_stats["_equity_curve"].index, utc=True)
    windows = fold_windows(eq_idx, SEARCH_START, DEV_END)
    folds = []
    for name, start, end, n in windows:
        x = metrics_from_stats(base_stats, start, end, work)
        x.update({"name": name, "start": start, "end": end, "bars": n})
        folds.append(x)

    finite_k = [float(x["raw_k"]) for x in folds if np.isfinite(float(x["raw_k"]))]
    positive_fraction = (
        sum(1 for x in folds if float(x["return_pct"]) > 0) / len(folds)
        if folds else 0.0
    )
    boot = deterministic_bootstrap_p10(base_stats, SEARCH_START, DEV_END)
    score = robust_score(folds, stress, boot)
    worst_risk_dd = max(
        abs(float(full["max_dd_pct"])),
        abs(float(full["intrabar_dd_proxy_pct"])),
        abs(float(stress["max_dd_pct"])),
        abs(float(stress["intrabar_dd_proxy_pct"])),
    )
    risk_utilization = worst_risk_dd / MAX_DD_PCT if MAX_DD_PCT > 0 else float("inf")
    dd_headroom_penalty = 0.10 * max(0.0, risk_utilization - 0.70)
    score -= dd_headroom_penalty

    full.update({
        "score": round(score, 6) if np.isfinite(score) else float("-inf"),
        "raw_full_k": full.pop("raw_k"),
        "stress": stress,
        "folds": folds,
        "active_folds": len(folds),
        "positive_fold_fraction": round(positive_fraction, 4),
        "worst_fold_k": round(min(finite_k), 6) if finite_k else float("-inf"),
        "median_fold_k": round(float(np.median(finite_k)), 6) if finite_k else float("-inf"),
        "fold_score_std": round(float(np.std(finite_k)), 6) if finite_k else float("inf"),
        "bootstrap_sharpe_p10": boot,
        "risk_cap_utilization": round(risk_utilization, 4),
        "dd_headroom_penalty": round(dd_headroom_penalty, 6),
        "start": SEARCH_START,
        "end": DEV_END,
    })
    return full


def evaluate_validation(df):
    a = to_utc_timestamp(SEARCH_START)
    end = to_utc_timestamp(VALIDATION_END, end=True)
    work = df.loc[(df.index >= a) & (df.index <= end)]
    if len(work) < 200:
        raise RuntimeError(f"only {len(work)} bars available through hidden validation")

    base_stats = run_bt(work, COMMISSION)
    stress_stats = run_bt(work, COMMISSION * COST_STRESS_MULT)
    full = metrics_from_stats(base_stats, VALIDATION_START, VALIDATION_END, work)
    stress = metrics_from_stats(stress_stats, VALIDATION_START, VALIDATION_END, work)
    eq_idx = pd.to_datetime(base_stats["_equity_curve"].index, utc=True)
    windows = fold_windows(eq_idx, VALIDATION_START, VALIDATION_END)
    folds = []
    for name, start, end, n in windows:
        x = metrics_from_stats(base_stats, start, end)
        x.update({"name": name, "start": start, "end": end, "bars": n})
        folds.append(x)
    full.update({
        "stress": stress,
        "folds": folds,
        "active_folds": len(folds),
        "positive_fold_fraction": round(
            sum(1 for x in folds if float(x["return_pct"]) > 0) / len(folds)
            if folds else (1.0 if full["return_pct"] > 0 else 0.0),
            4,
        ),
        "bootstrap_sharpe_p10": deterministic_bootstrap_p10(
            base_stats, VALIDATION_START, VALIDATION_END
        ),
        "start": VALIDATION_START,
        "end": VALIDATION_END,
    })
    return full


def search_guard(summary):
    details = []
    if summary["trades"] < MIN_TRADES:
        details.append(f"development trades {summary['trades']} < {MIN_TRADES}")
    if summary["active_folds"] < MIN_ACTIVE_FOLDS:
        details.append(
            f"active development folds {summary['active_folds']} < {MIN_ACTIVE_FOLDS}"
        )
    if not np.isfinite(float(summary["score"])):
        details.append("robust score not finite")
    if summary["return_pct"] <= 0:
        details.append("development return not positive")
    if summary["stress"]["return_pct"] <= 0:
        details.append("2x-cost stressed development return not positive")
    if summary["max_dd_pct"] < -MAX_DD_PCT:
        details.append("development drawdown limit exceeded")
    if summary["intrabar_dd_proxy_pct"] < -MAX_DD_PCT:
        details.append("development intrabar adverse-excursion DD proxy exceeded limit")
    if summary["stress"]["max_dd_pct"] < -MAX_DD_PCT:
        details.append("stressed development drawdown limit exceeded")
    if summary["stress"]["intrabar_dd_proxy_pct"] < -MAX_DD_PCT:
        details.append("stressed intrabar DD proxy exceeded limit")
    if summary["trades"] >= 10 and summary["top1_profit_concentration"] > 0.70:
        details.append("single winning trade supplies >70% of gross profit")
    if summary["positive_fold_fraction"] < 0.40:
        details.append("fewer than 40% of development folds are profitable")
    if np.isfinite(float(summary["median_fold_k"])) and summary["median_fold_k"] < -0.10:
        details.append("median development-fold K below -0.10")
    if np.isfinite(float(summary["bootstrap_sharpe_p10"])) and summary["bootstrap_sharpe_p10"] < -0.50:
        details.append("block-bootstrap Sharpe p10 below -0.50")

    if BASELINE.exists():
        base = json.loads(BASELINE.read_text(encoding="utf-8"))
        bv = float(base["ann_vol_pct"])
        v = float(summary["ann_vol_pct"])
        if bv > 0 and abs(v - bv) / bv > VOL_BAND:
            details.append("portfolio volatility left frozen development baseline band")

    ok = not details
    return ok, ("ok" if ok else "development robustness gate failed"), details


def validation_guard(summary):
    details = []
    if summary["trades"] < MIN_VALIDATION_TRADES:
        details.append(
            f"hidden-validation trades {summary['trades']} < {MIN_VALIDATION_TRADES}"
        )
    if summary["return_pct"] <= 0:
        details.append("hidden-validation return not positive")
    if summary["stress"]["return_pct"] <= 0:
        details.append("hidden-validation stressed return not positive")
    if summary["max_dd_pct"] < -MAX_DD_PCT:
        details.append("hidden-validation drawdown limit exceeded")
    if summary["intrabar_dd_proxy_pct"] < -MAX_DD_PCT:
        details.append("hidden-validation intrabar DD proxy exceeded limit")
    if summary["stress"]["max_dd_pct"] < -MAX_DD_PCT:
        details.append("hidden-validation stressed drawdown limit exceeded")
    if summary["stress"]["intrabar_dd_proxy_pct"] < -MAX_DD_PCT:
        details.append("hidden-validation stressed intrabar DD proxy exceeded limit")
    if summary["positive_fold_fraction"] < 0.50:
        details.append("less than half of hidden-validation folds are profitable")
    ok = not details
    return ok, ("ok" if ok else "hidden chronological validation failed"), details


def write_json(path, obj):
    tmp = Path(str(path) + ".tmp")
    tmp.write_text(json.dumps(obj, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def common_metadata(summary, stage):
    summary.update({
        "symbol": SYMBOL,
        "market": MARKET,
        "profile": PROFILE,
        "commission": COMMISSION,
        "cost_stress_multiplier": COST_STRESS_MULT,
        "margin": MARGIN,
        "bars_per_year": BARS_PER_YEAR,
        "max_dd_limit_pct": MAX_DD_PCT,
        "protocol": PROTOCOL,
        "stage": stage,
        "strategy_sha256": sha256_file(STRATEGY_FILE),
        "data_sha256": sha256_file(DATA_FILE),
        "adaptive_development_end": DEV_END,
        "hidden_validation_start": VALIDATION_START,
        "hidden_validation_end": VALIDATION_END,
        "oos_opened": False,
    })
    return summary


def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--is", dest="mode", action="store_const", const="search")
    mode.add_argument("--validation", dest="mode", action="store_const", const="validation")
    mode.add_argument("--check", dest="mode", action="store_const", const="check")
    ap.add_argument("--set-baseline", action="store_true")
    args = ap.parse_args()
    selected = args.mode or "search"

    df = load_data()
    if selected == "check":
        x = df.loc[df.index <= to_utc_timestamp(DEV_END, end=True)].tail(500)
        stats = run_bt(x, COMMISSION)
        out = metrics_from_stats(
            stats,
            x.index[0].strftime("%Y-%m-%d"),
            x.index[-1].strftime("%Y-%m-%d"),
            x,
        )
        print(json.dumps(out, indent=2, sort_keys=True))
        return

    if selected == "validation":
        summary = evaluate_validation(df)
        ok, reason, details = validation_guard(summary)
        summary.update({
            "guard_ok": bool(ok),
            "guard_reason": reason,
            "audit_guard_details": details,
        })
        common_metadata(summary, "hidden_validation")
        write_json(VALIDATION_RUN, summary)
        print("HIDDEN_VALIDATION_SUMMARY")
        print(json.dumps(summary, indent=2, sort_keys=True))
        return

    summary = evaluate_search(df)
    ok, reason, details = search_guard(summary)
    summary.update({
        "guard_ok": bool(ok),
        "guard_reason": reason,
        "audit_guard_details": details,
    })
    common_metadata(summary, "adaptive_development")
    write_json(LAST_RUN, summary)

    if args.set_baseline:
        if not ok:
            raise RuntimeError(
                "refusing invalid development baseline: " + "; ".join(details)
            )
        write_json(BASELINE, summary)

    print("DEVELOPMENT_SUMMARY")
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
