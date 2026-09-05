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
import platform
from pathlib import Path

import numpy as np
import pandas as pd
import backtesting
from backtesting import Backtest

from research_metrics import (
    annualized_k,
    deterministic_block_bootstrap_diagnostics,
    geometric_cagr,
    probabilistic_sharpe_ratio,
    tail_metrics,
)

HERE = Path(__file__).resolve().parent
BASELINE = HERE / "baseline.json"
LAST_RUN = HERE / "last_run.json"
VALIDATION_RUN = HERE / "validation_run.json"
LOOKAHEAD_AUDIT = HERE / "lookahead_audit.json"
STRATEGY_FILE = HERE / "strategy.py"
HARNESS_FILE = HERE / "robust_harness.py"
PROGRAM_FILE = HERE / os.environ.get("AUTORESEARCH_PROGRAM", "program_robust.md")

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
EXTREME_COST_STRESS_MULT = float(
    os.environ.get("AUTORESEARCH_EXTREME_COST_STRESS_MULT", "3.0")
)
BOOTSTRAP_REPS = int(os.environ.get("AUTORESEARCH_BOOTSTRAP_REPS", "500"))

SEARCH_START = os.environ.get("AUTORESEARCH_IS_START", "2017-08-17")
VALIDATION_START = os.environ.get("AUTORESEARCH_VALIDATION_START", "2021-01-01")
VALIDATION_END = os.environ.get("AUTORESEARCH_VALIDATION_END", "2022-12-31")
DEV_END = (pd.Timestamp(VALIDATION_START) - pd.Timedelta(days=1)).strftime("%Y-%m-%d")

PROTOCOL = "nested_chronological_v3"


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
    """OHLC adverse-equity proxy that supports overlapping open trades.

    Backtesting.py exposes bar-close equity. For every bar, this proxy adjusts
    close equity by marking each active long to that bar's Low and each active
    short to that bar's High. It therefore catches many intrabar risk-cap
    breaches that close-only equity can hide. Exit bars are excluded because
    orders execute at the next bar open with trade_on_close=False.

    It is still explicitly a proxy: intrabar path ordering and gaps within a
    daily bar cannot be reconstructed from OHLC alone.
    """
    if price_df is None:
        return 0.0
    trades = slice_trades(stats, start, end)
    if trades.empty:
        return 0.0

    eq_series = stats["_equity_curve"]["Equity"].astype(float)
    eq = eq_series.to_numpy(dtype=float)
    px = price_df.copy()
    px.index = pd.to_datetime(px.index, utc=True)
    px = px.reindex(pd.to_datetime(eq_series.index, utc=True))
    close = pd.to_numeric(px["Close"], errors="coerce").to_numpy(dtype=float)
    low = pd.to_numeric(px["Low"], errors="coerce").to_numpy(dtype=float)
    high = pd.to_numeric(px["High"], errors="coerce").to_numpy(dtype=float)
    if len(eq) != len(close):
        return 0.0

    adjustment = np.zeros(len(eq), dtype=float)
    for _, row in trades.iterrows():
        try:
            eb = int(row["EntryBar"])
            xb = int(row["ExitBar"])
            size = float(row["Size"])
        except Exception:
            continue
        if not np.isfinite(size) or size == 0 or eb < 0 or eb >= len(eq):
            continue
        # Exit orders execute at the exit bar open, so do not expose the
        # position to the remainder of that bar. A same-bar trade still gets
        # one bar of conservative adverse marking.
        last = min(len(eq) - 1, max(eb, xb - 1))
        for j in range(eb, last + 1):
            if not np.isfinite(close[j]):
                continue
            adverse = low[j] if size > 0 else high[j]
            if not np.isfinite(adverse):
                continue
            adjustment[j] += size * (adverse - close[j])

    adverse_eq = eq + adjustment
    peak = np.maximum.accumulate(eq)
    valid = np.isfinite(adverse_eq) & np.isfinite(peak) & (peak > 0)
    if not np.any(valid):
        return 0.0
    dd = np.where(valid, adverse_eq / peak - 1.0, 0.0)
    return round(float(np.min(dd) * 100.0), 3)


def benchmark_metrics(price_df, start, end):
    if price_df is None or "Close" not in price_df.columns:
        return {
            "benchmark_cagr_pct": 0.0,
            "benchmark_sharpe": 0.0,
            "benchmark_ann_vol_pct": 0.0,
            "benchmark_max_dd_pct": 0.0,
        }
    a = to_utc_timestamp(start)
    b = to_utc_timestamp(end, end=True)
    close = pd.to_numeric(
        price_df.loc[(price_df.index >= a) & (price_df.index <= b), "Close"],
        errors="coerce",
    ).dropna()
    if len(close) < 2:
        return {
            "benchmark_cagr_pct": 0.0,
            "benchmark_sharpe": 0.0,
            "benchmark_ann_vol_pct": 0.0,
            "benchmark_max_dd_pct": 0.0,
        }
    r = close.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    total = float(close.iloc[-1] / close.iloc[0] - 1.0)
    years = max(len(r) / float(BARS_PER_YEAR), 1.0 / float(BARS_PER_YEAR))
    cagr = geometric_cagr(total, years)
    vol = float(r.std(ddof=0) * math.sqrt(BARS_PER_YEAR)) if len(r) else 0.0
    sharpe = (
        float(r.mean() / r.std(ddof=0) * math.sqrt(BARS_PER_YEAR))
        if len(r) and float(r.std(ddof=0)) > 0 else 0.0
    )
    dd = close / close.cummax() - 1.0
    return {
        "benchmark_cagr_pct": round(float(cagr * 100.0), 3),
        "benchmark_sharpe": round(float(sharpe), 4),
        "benchmark_ann_vol_pct": round(float(vol * 100.0), 3),
        "benchmark_max_dd_pct": round(float(dd.min() * 100.0), 3),
    }


def metrics_from_stats(stats, start, end, price_df=None):
    eq = slice_equity(stats, start, end)
    if len(eq) < 2:
        return {
            "raw_k": float("-inf"), "return_pct": 0.0, "cagr_pct": 0.0,
            "development_years": 0.0, "sharpe": 0.0, "ann_vol_pct": 0.0,
            "max_dd_pct": 0.0, "trades": 0, "win_pct": 0.0, "pf": 0.0,
            "bars": int(len(eq)), "psr_zero": 0.0,
            "bootstrap_mean_positive_pvalue": 1.0,
        }

    rets = eq.pct_change().replace([np.inf, -np.inf], np.nan).dropna()
    total = float(eq.iloc[-1] / eq.iloc[0] - 1.0)
    years = max(len(rets) / float(BARS_PER_YEAR), 1.0 / float(BARS_PER_YEAR))
    cagr = geometric_cagr(total, years)
    vol = float(rets.std(ddof=0) * math.sqrt(BARS_PER_YEAR)) if len(rets) else 0.0
    sharpe = (
        float(rets.mean() / rets.std(ddof=0) * math.sqrt(BARS_PER_YEAR))
        if len(rets) and float(rets.std(ddof=0)) > 0 else 0.0
    )
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

    raw_k = annualized_k(total, years, sharpe)
    intrabar_proxy = intrabar_drawdown_proxy(stats, price_df, start, end)
    tail = tail_metrics(eq.to_numpy(dtype=float), rets.to_numpy(dtype=float), cagr)
    psr = probabilistic_sharpe_ratio(rets.to_numpy(dtype=float), 0.0)
    bench = benchmark_metrics(price_df, start, end)

    return {
        "raw_k": round(float(raw_k), 6) if np.isfinite(raw_k) else float("-inf"),
        "return_pct": round(total * 100.0, 3),
        "cagr_pct": round(float(cagr * 100.0), 3) if np.isfinite(cagr) else -100.0,
        "development_years": round(float(years), 4),
        "sharpe": round(sharpe, 4),
        "ann_vol_pct": round(vol * 100.0, 3),
        "max_dd_pct": round(max_dd, 3),
        "intrabar_dd_proxy_pct": intrabar_proxy,
        "intrabar_dd_proxy_method": "ohlc_active_positions_v2",
        "trades": int(len(pnl)),
        "trades_per_year": round(float(len(pnl) / years), 3) if years > 0 else 0.0,
        "avg_trade_pnl": round(float(pnl.mean()), 6) if len(pnl) else 0.0,
        "median_trade_pnl": round(float(pnl.median()), 6) if len(pnl) else 0.0,
        "win_loss_payoff": round(
            float(pnl[pnl > 0].mean() / abs(pnl[pnl < 0].mean())),
            4,
        ) if len(pnl[pnl > 0]) and len(pnl[pnl < 0]) and pnl[pnl < 0].mean() != 0 else 0.0,
        "win_pct": round(win_pct, 2),
        "pf": round(float(min(pf, 99.0)), 3),
        "top1_profit_concentration": round(top1_concentration, 4),
        "top3_profit_concentration": round(top3_concentration, 4),
        "psr_zero": round(float(psr), 6),
        "ulcer_index_pct": round(float(tail["ulcer_index_pct"]), 3),
        "daily_cvar_5_pct": round(float(tail["daily_cvar_5_pct"]), 4),
        "sortino_per_bar": round(float(tail["sortino_per_bar"]), 6),
        "calmar": round(float(tail["calmar"]), 4),
        "excess_cagr_vs_buyhold_pct": round(
            float(cagr * 100.0 - bench["benchmark_cagr_pct"]), 3
        ),
        "sharpe_minus_buyhold": round(
            float(sharpe - bench["benchmark_sharpe"]), 4
        ),
        **bench,
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


def deterministic_bootstrap(stats, start, end):
    eq = slice_equity(stats, start, end)
    r = eq.pct_change().replace([np.inf, -np.inf], np.nan).dropna().to_numpy(dtype=float)
    seed_material = (
        sha256_file(STRATEGY_FILE) + "|" + SYMBOL + "|" + PROFILE + "|" + start + "|" + end
    ).encode()
    seed = int(hashlib.sha256(seed_material).hexdigest()[:16], 16) % (2**32)
    rng = np.random.default_rng(seed)
    out = deterministic_block_bootstrap_diagnostics(
        r,
        bars_per_year=BARS_PER_YEAR,
        rng=rng,
        reps=BOOTSTRAP_REPS,
        block=10,
    )
    return {
        "sharpe_p10": round(float(out["sharpe_p10"]), 4)
        if np.isfinite(float(out["sharpe_p10"])) else float("-inf"),
        "mean_positive_pvalue": round(float(out["mean_positive_pvalue"]), 6),
        "reps": int(out["reps"]),
        "block": int(out["block"]),
    }


def robust_score(folds, stress, extreme_stress, bootstrap, psr_zero):
    scores = np.array([float(x["raw_k"]) for x in folds], dtype=float)
    scores = scores[np.isfinite(scores)]
    if len(scores) == 0:
        return float("-inf")
    med = float(np.median(scores))
    p25 = float(np.quantile(scores, 0.25))
    worst = float(np.min(scores))
    dispersion = float(np.std(scores))
    stress_k = float(stress["raw_k"]) if np.isfinite(float(stress["raw_k"])) else -1e6
    extreme_k = (
        float(extreme_stress["raw_k"])
        if np.isfinite(float(extreme_stress["raw_k"])) else -1e6
    )
    boot_p10 = float(bootstrap["sharpe_p10"])
    boot_penalty = max(0.0, -boot_p10) if np.isfinite(boot_p10) else 1.0
    psr_penalty = max(0.0, 0.80 - float(psr_zero))
    pvalue_penalty = max(0.0, float(bootstrap["mean_positive_pvalue"]) - 0.10)
    return (
        0.35 * med
        + 0.20 * p25
        + 0.15 * worst
        + 0.15 * stress_k
        + 0.10 * extreme_k
        - 0.10 * dispersion
        - 0.05 * boot_penalty
        - 0.05 * psr_penalty
        - 0.05 * pvalue_penalty
    )


def evaluate_search(df):
    a = to_utc_timestamp(SEARCH_START)
    d = to_utc_timestamp(DEV_END, end=True)
    work = df.loc[(df.index >= a) & (df.index <= d)]
    if len(work) < 200:
        raise RuntimeError(f"only {len(work)} bars in adaptive development span")

    base_stats = run_bt(work, COMMISSION)
    stress_stats = run_bt(work, COMMISSION * COST_STRESS_MULT)
    extreme_stats = run_bt(work, COMMISSION * EXTREME_COST_STRESS_MULT)
    full = metrics_from_stats(base_stats, SEARCH_START, DEV_END, work)
    stress = metrics_from_stats(stress_stats, SEARCH_START, DEV_END, work)
    extreme = metrics_from_stats(extreme_stats, SEARCH_START, DEV_END, work)

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
    bootstrap = deterministic_bootstrap(base_stats, SEARCH_START, DEV_END)
    score = robust_score(folds, stress, extreme, bootstrap, full["psr_zero"])
    worst_risk_dd = max(
        abs(float(full["max_dd_pct"])),
        abs(float(full["intrabar_dd_proxy_pct"])),
        abs(float(stress["max_dd_pct"])),
        abs(float(stress["intrabar_dd_proxy_pct"])),
        abs(float(extreme["max_dd_pct"])),
        abs(float(extreme["intrabar_dd_proxy_pct"])),
    )
    risk_utilization = worst_risk_dd / MAX_DD_PCT if MAX_DD_PCT > 0 else float("inf")
    dd_headroom_penalty = 0.10 * max(0.0, risk_utilization - 0.70)
    score -= dd_headroom_penalty

    evidence_grade = "A"
    if (
        full["development_years"] < 3.0
        or full["trades"] < max(MIN_TRADES, 20)
        or full.get("trades_per_year", 0.0) < 3.0
    ):
        evidence_grade = "B"
    if (
        full["development_years"] < 2.0
        or full["trades"] < MIN_TRADES
        or full.get("trades_per_year", 0.0) < 1.5
    ):
        evidence_grade = "C"
    if full["psr_zero"] < 0.80 or bootstrap["mean_positive_pvalue"] > 0.10:
        evidence_grade = chr(min(ord("D"), ord(evidence_grade) + 1))

    full.update({
        "score": round(score, 6) if np.isfinite(score) else float("-inf"),
        "raw_full_k": full.pop("raw_k"),
        "score_definition": "annualized_log_growth_x_sharpe_robust_v3",
        "stress": stress,
        "extreme_stress": extreme,
        "folds": folds,
        "active_folds": len(folds),
        "positive_fold_fraction": round(positive_fraction, 4),
        "worst_fold_k": round(min(finite_k), 6) if finite_k else float("-inf"),
        "median_fold_k": round(float(np.median(finite_k)), 6) if finite_k else float("-inf"),
        "fold_score_std": round(float(np.std(finite_k)), 6) if finite_k else float("inf"),
        "bootstrap_sharpe_p10": bootstrap["sharpe_p10"],
        "bootstrap_mean_positive_pvalue": bootstrap["mean_positive_pvalue"],
        "bootstrap_reps": bootstrap["reps"],
        "bootstrap_block": bootstrap["block"],
        "risk_cap_utilization": round(risk_utilization, 4),
        "dd_headroom_penalty": round(dd_headroom_penalty, 6),
        "evidence_grade": evidence_grade,
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
    extreme_stats = run_bt(work, COMMISSION * EXTREME_COST_STRESS_MULT)
    full = metrics_from_stats(base_stats, VALIDATION_START, VALIDATION_END, work)
    stress = metrics_from_stats(stress_stats, VALIDATION_START, VALIDATION_END, work)
    extreme = metrics_from_stats(extreme_stats, VALIDATION_START, VALIDATION_END, work)
    eq_idx = pd.to_datetime(base_stats["_equity_curve"].index, utc=True)
    windows = fold_windows(eq_idx, VALIDATION_START, VALIDATION_END)
    folds = []
    for name, start, end, n in windows:
        x = metrics_from_stats(base_stats, start, end, work)
        x.update({"name": name, "start": start, "end": end, "bars": n})
        folds.append(x)
    bootstrap = deterministic_bootstrap(base_stats, VALIDATION_START, VALIDATION_END)
    full.update({
        "stress": stress,
        "extreme_stress": extreme,
        "folds": folds,
        "active_folds": len(folds),
        "positive_fold_fraction": round(
            sum(1 for x in folds if float(x["return_pct"]) > 0) / len(folds)
            if folds else (1.0 if full["return_pct"] > 0 else 0.0),
            4,
        ),
        "bootstrap_sharpe_p10": bootstrap["sharpe_p10"],
        "bootstrap_mean_positive_pvalue": bootstrap["mean_positive_pvalue"],
        "bootstrap_reps": bootstrap["reps"],
        "bootstrap_block": bootstrap["block"],
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
    if summary["extreme_stress"]["return_pct"] <= 0:
        details.append("3x-cost stressed development return not positive")
    if summary["max_dd_pct"] < -MAX_DD_PCT:
        details.append("development drawdown limit exceeded")
    if summary["intrabar_dd_proxy_pct"] < -MAX_DD_PCT:
        details.append("development intrabar adverse-excursion DD proxy exceeded limit")
    if summary["stress"]["max_dd_pct"] < -MAX_DD_PCT:
        details.append("stressed development drawdown limit exceeded")
    if summary["stress"]["intrabar_dd_proxy_pct"] < -MAX_DD_PCT:
        details.append("stressed intrabar DD proxy exceeded limit")
    if summary["extreme_stress"]["max_dd_pct"] < -MAX_DD_PCT:
        details.append("3x-cost stressed development drawdown limit exceeded")
    if summary["extreme_stress"]["intrabar_dd_proxy_pct"] < -MAX_DD_PCT:
        details.append("3x-cost stressed intrabar DD proxy exceeded limit")
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
        if bv > 0 and v > bv * (1.0 + VOL_BAND):
            details.append("portfolio volatility rose above frozen development ceiling")

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
    if summary["extreme_stress"]["return_pct"] <= 0:
        details.append("hidden-validation 3x-cost stressed return not positive")
    if summary["max_dd_pct"] < -MAX_DD_PCT:
        details.append("hidden-validation drawdown limit exceeded")
    if summary["intrabar_dd_proxy_pct"] < -MAX_DD_PCT:
        details.append("hidden-validation intrabar DD proxy exceeded limit")
    if summary["stress"]["max_dd_pct"] < -MAX_DD_PCT:
        details.append("hidden-validation stressed drawdown limit exceeded")
    if summary["stress"]["intrabar_dd_proxy_pct"] < -MAX_DD_PCT:
        details.append("hidden-validation stressed intrabar DD proxy exceeded limit")
    if summary["extreme_stress"]["max_dd_pct"] < -MAX_DD_PCT:
        details.append("hidden-validation 3x-cost drawdown limit exceeded")
    if summary["extreme_stress"]["intrabar_dd_proxy_pct"] < -MAX_DD_PCT:
        details.append("hidden-validation 3x-cost intrabar DD proxy exceeded limit")
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
        "extreme_cost_stress_multiplier": EXTREME_COST_STRESS_MULT,
        "margin": MARGIN,
        "bars_per_year": BARS_PER_YEAR,
        "max_dd_limit_pct": MAX_DD_PCT,
        "protocol": PROTOCOL,
        "stage": stage,
        "strategy_sha256": sha256_file(STRATEGY_FILE),
        "data_sha256": sha256_file(DATA_FILE),
        "harness_sha256": sha256_file(HARNESS_FILE),
        "program_sha256": sha256_file(PROGRAM_FILE) if PROGRAM_FILE.exists() else None,
        "config_sha256": sha256_file(CONFIG_FILE) if CONFIG_FILE.exists() else None,
        "registry_sha256": sha256_file(REGISTRY_FILE) if REGISTRY_FILE.exists() else None,
        "seed_factory_sha256": sha256_file(SEED_FACTORY_FILE) if SEED_FACTORY_FILE.exists() else None,
        "python_version": platform.python_version(),
        "numpy_version": np.__version__,
        "pandas_version": pd.__version__,
        "backtesting_version": getattr(backtesting, "__version__", None),
        "adaptive_development_end": DEV_END,
        "hidden_validation_start": VALIDATION_START,
        "hidden_validation_end": VALIDATION_END,
        "oos_opened": False,
    })
    return summary


def _trade_entry_signature(stats, cutoff):
    trades = stats["_trades"]
    out = []
    if trades is None or len(trades) == 0:
        return out
    for _, row in trades.iterrows():
        try:
            entry_bar = int(row["EntryBar"])
            size = float(row["Size"])
            entry_price = float(row["EntryPrice"])
        except Exception:
            continue
        if entry_bar >= cutoff:
            continue
        out.append((
            entry_bar,
            1 if size > 0 else (-1 if size < 0 else 0),
            round(entry_price, 8),
        ))
    return out


def lookahead_prefix_audit(df):
    """Detect future-data leakage by replaying identical history prefixes.

    A causal strategy must produce the same equity path and entries through a
    past cutoff whether or not later bars are present in the input supplied to
    Strategy.init(). This specifically closes the common full-array indicator
    lookahead loophole while allowing normal self.I rolling indicators.
    """
    a = to_utc_timestamp(SEARCH_START)
    d = to_utc_timestamp(DEV_END, end=True)
    work = df.loc[(df.index >= a) & (df.index <= d)]
    if len(work) < 400:
        return {
            "passed": False,
            "reason": "insufficient development bars for prefix-invariance audit",
            "checks": [],
        }

    full_stats = run_bt(work, COMMISSION)
    full_eq = full_stats["_equity_curve"]["Equity"].astype(float).to_numpy()
    checks = []
    passed = True
    for fraction in (0.55, 0.70, 0.85):
        cut = int(len(work) * fraction)
        cut = max(250, min(cut, len(work) - 5))
        prefix = work.iloc[:cut]
        prefix_stats = run_bt(prefix, COMMISSION)
        prefix_eq = prefix_stats["_equity_curve"]["Equity"].astype(float).to_numpy()
        compare_n = min(len(prefix_eq), cut) - 2
        if compare_n <= 10:
            eq_equal = False
            max_abs_diff = float("inf")
        else:
            diff = np.abs(full_eq[:compare_n] - prefix_eq[:compare_n])
            max_abs_diff = float(np.nanmax(diff)) if len(diff) else 0.0
            scale = max(1.0, float(np.nanmax(np.abs(full_eq[:compare_n]))))
            eq_equal = bool(max_abs_diff <= max(1e-6, 1e-10 * scale))

        sig_cut = max(0, cut - 2)
        full_sig = _trade_entry_signature(full_stats, sig_cut)
        prefix_sig = _trade_entry_signature(prefix_stats, sig_cut)
        entries_equal = full_sig == prefix_sig
        check_ok = bool(eq_equal and entries_equal)
        passed = passed and check_ok
        checks.append({
            "fraction": fraction,
            "bars": cut,
            "equity_prefix_equal": eq_equal,
            "entry_signature_equal": entries_equal,
            "max_abs_equity_diff": round(max_abs_diff, 8)
            if np.isfinite(max_abs_diff) else None,
            "full_entries": len(full_sig),
            "prefix_entries": len(prefix_sig),
            "passed": check_ok,
        })
    return {
        "passed": bool(passed),
        "reason": "ok" if passed else "historical decisions changed when future bars were removed",
        "checks": checks,
        "protocol": PROTOCOL,
        "oos_opened": False,
    }


def main():
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--is", dest="mode", action="store_const", const="search")
    mode.add_argument("--validation", dest="mode", action="store_const", const="validation")
    mode.add_argument("--check", dest="mode", action="store_const", const="check")
    mode.add_argument(
        "--lookahead-audit",
        dest="mode",
        action="store_const",
        const="lookahead_audit",
    )
    ap.add_argument("--set-baseline", action="store_true")
    args = ap.parse_args()
    selected = args.mode or "search"

    df = load_data()
    if selected == "lookahead_audit":
        out = lookahead_prefix_audit(df)
        common_metadata(out, "development_lookahead_audit")
        write_json(LOOKAHEAD_AUDIT, out)
        print("LOOKAHEAD_AUDIT")
        print(json.dumps(out, indent=2, sort_keys=True))
        if not out.get("passed"):
            raise SystemExit(2)
        return

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
