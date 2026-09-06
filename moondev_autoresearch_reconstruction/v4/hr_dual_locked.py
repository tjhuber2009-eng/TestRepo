"""Development-only replay of frozen HR-DUAL-ALPHA-001.

Source lock:
- repo: tjhuber2009-eng/hr_mech
- branch: research/hr-dual-alpha-001
- commit: abfe2babadd20ca4c6c1b36af0545691e3bb6dde
- implementation blob: 27a24f0bc1883c497af23ff3a27918e35f3f4c11

The mechanics below are a provider-decoupled port of that frozen implementation.
No parameter is optimized here. The caller must supply already-adjusted daily
OHLC data and must keep the development boundary sealed.
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import pandas as pd

from .alpha_objective import metrics_from_equity

SOURCE_LOCK = {
    "repository": "tjhuber2009-eng/hr_mech",
    "branch": "research/hr-dual-alpha-001",
    "commit": "abfe2babadd20ca4c6c1b36af0545691e3bb6dde",
    "implementation_blob_sha": "27a24f0bc1883c497af23ff3a27918e35f3f4c11",
    "result_blob_sha": "66b68a96dc6dd31c5f629d067bf176e99b3917a3",
    "prior_classification": "SUPERIOR_PASS",
}
TICKERS = ["QQQ", "TQQQ", "TECL", "IEF", "GLD", "SHY"]
RISK = ["TQQQ", "TECL"]
DEF = ["IEF", "GLD", "SHY"]


def read_adjusted_csv(path):
    x = pd.read_csv(path)
    idx = pd.DatetimeIndex(pd.to_datetime(x.pop("Date"), utc=True))
    x.index = idx.normalize().tz_localize(None)
    x = x.sort_index()
    return x[["Open", "High", "Low", "Close"]].astype(float)


def load_adjusted_data(root):
    root = Path(root)
    mapping = {
        "QQQ": "qqq_1d.csv",
        "TQQQ": "tqqq_1d.csv",
        "TECL": "tecl_1d.csv",
        "IEF": "ief_1d.csv",
        "GLD": "gld_1d.csv",
        "SHY": "shy_1d.csv",
    }
    for symbol, name in mapping.items():
        csv_path = root / name
        manifest_path = csv_path.with_suffix(".manifest.json")
        if not manifest_path.exists():
            raise RuntimeError(f"HR-DUAL missing source manifest: {manifest_path}")
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest.get("source") != "yahoo":
            raise RuntimeError(f"HR-DUAL requires Yahoo source parity: {symbol}")
        if manifest.get("adjustment_method") != "legacy_adjusted_close":
            raise RuntimeError(
                f"HR-DUAL requires Yahoo adjusted-close-equivalent OHLC: {symbol}"
            )
        if manifest.get("oos_included") is not False:
            raise RuntimeError(f"HR-DUAL source manifest is not development-only: {symbol}")
    data = {s: read_adjusted_csv(root / name) for s, name in mapping.items()}
    idx = data[TICKERS[0]].index
    for symbol in TICKERS[1:]:
        idx = idx.intersection(data[symbol].index)
    idx = idx.sort_values()
    if len(idx) < 300:
        raise RuntimeError("HR-DUAL requires at least 300 common daily bars")
    for symbol in TICKERS:
        data[symbol] = data[symbol].loc[idx].copy()
        if data[symbol].isna().any().any():
            raise RuntimeError(f"HR-DUAL missing adjusted OHLC: {symbol}")
    return data


def choose_highest(series, universe):
    s = series[universe]
    if s.isna().any():
        raise RuntimeError("HR-DUAL missing score")
    return sorted(universe, key=lambda t: (-float(s[t]), t))[0]


def build_targets(data):
    idx = data["QQQ"].index
    close = pd.DataFrame({t: data[t]["Close"] for t in TICKERS}, index=idx)
    qqq = close["QQQ"]
    sma200 = qqq.rolling(200, min_periods=200).mean()
    risk_on = qqq > sma200
    mom = (
        close[RISK + DEF].pct_change(63, fill_method=None)
        - close[RISK + DEF].pct_change(21, fill_method=None)
    )
    rev = -close[RISK].pct_change(3, fill_method=None)
    month_ends = (
        pd.Series(idx, index=idx).groupby(idx.to_period("M")).max().tolist()
    )
    targets = {}
    decisions = []
    for signal_date in month_ends:
        signal_date = pd.Timestamp(signal_date)
        i = idx.get_loc(signal_date)
        if i + 1 >= len(idx) or pd.isna(sma200.loc[signal_date]):
            continue
        execution_date = pd.Timestamp(idx[i + 1])
        ro = bool(risk_on.loc[signal_date])
        if ro:
            mom_pick = choose_highest(mom.loc[signal_date], RISK)
            rev_pick = choose_highest(rev.loc[signal_date], RISK)
            rec = {"mode": "RISK_ON", "mom": mom_pick, "rev": rev_pick, "def": None}
        else:
            def_pick = choose_highest(mom.loc[signal_date], DEF)
            rec = {"mode": "RISK_OFF", "mom": None, "rev": None, "def": def_pick}
        targets[execution_date] = rec
        decisions.append({
            "signal_date": signal_date.strftime("%Y-%m-%d"),
            "execution_date": execution_date.strftime("%Y-%m-%d"),
            **rec,
        })
    if not targets:
        raise RuntimeError("HR-DUAL produced no monthly targets")
    return targets, decisions


def exposure_map(state):
    out = {t: 0.0 for t in RISK + DEF}
    if state["mode"] == "RISK_ON":
        for key in ("mom", "rev"):
            sleeve = state[key]
            if sleeve["held"] is not None:
                out[sleeve["held"]] += sleeve["eq"]
    elif state["mode"] == "RISK_OFF":
        sleeve = state["def"]
        if sleeve["held"] is not None:
            out[sleeve["held"]] += sleeve["eq"]
    return out


def total_equity(state):
    if state["mode"] == "RISK_ON":
        return float(state["mom"]["eq"] + state["rev"]["eq"])
    if state["mode"] == "RISK_OFF":
        return float(state["def"]["eq"])
    return float(state["cash"])


def mark_day(data, state, date, cost):
    stops = 0
    if state["mode"] == "RISK_ON":
        for key, stop_pct in (("mom", 0.10), ("rev", 0.08)):
            sleeve = state[key]
            if sleeve["held"] is None:
                continue
            bar = data[sleeve["held"]].loc[date]
            prev = float(sleeve["prev"])
            op, lo, close = float(bar.Open), float(bar.Low), float(bar.Close)
            stop = float(sleeve["basis"]) * (1.0 - stop_pct)
            if op <= stop:
                sleeve["eq"] *= op / prev
                sleeve["eq"] *= 1.0 - cost
                sleeve["held"] = sleeve["basis"] = sleeve["prev"] = None
                stops += 1
            elif lo <= stop:
                sleeve["eq"] *= stop / prev
                sleeve["eq"] *= 1.0 - cost
                sleeve["held"] = sleeve["basis"] = sleeve["prev"] = None
                stops += 1
            else:
                sleeve["eq"] *= close / prev
                sleeve["prev"] = close
    elif state["mode"] == "RISK_OFF":
        sleeve = state["def"]
        if sleeve["held"] is not None:
            close = float(data[sleeve["held"]].loc[date, "Close"])
            sleeve["eq"] *= close / float(sleeve["prev"])
            sleeve["prev"] = close
    return stops


def rebalance(data, state, date, rec, cost):
    before = total_equity(state)
    current = exposure_map(state)
    desired_w = {t: 0.0 for t in RISK + DEF}
    if rec["mode"] == "RISK_ON":
        desired_w[rec["mom"]] += 0.60
        desired_w[rec["rev"]] += 0.40
    else:
        desired_w[rec["def"]] = 1.0
    desired_pre = {t: before * desired_w[t] for t in desired_w}
    turnover = sum(abs(desired_pre[t] - current[t]) for t in desired_pre)
    after = before - turnover * cost
    if after <= 0:
        raise RuntimeError("HR-DUAL nonpositive equity after rebalance")
    if rec["mode"] == "RISK_ON":
        mc = float(data[rec["mom"]].loc[date, "Close"])
        rc = float(data[rec["rev"]].loc[date, "Close"])
        return {
            "mode": "RISK_ON",
            "mom": {"eq": after * 0.60, "held": rec["mom"], "basis": mc, "prev": mc},
            "rev": {"eq": after * 0.40, "held": rec["rev"], "basis": rc, "prev": rc},
            "def": None,
            "cash": 0.0,
        }, turnover
    dc = float(data[rec["def"]].loc[date, "Close"])
    return {
        "mode": "RISK_OFF",
        "mom": None,
        "rev": None,
        "def": {"eq": after, "held": rec["def"], "basis": dc, "prev": dc},
        "cash": 0.0,
    }, turnover


def simulate(data, bp):
    idx = data["QQQ"].index
    targets, decisions = build_targets(data)
    cost = float(bp) / 10000.0
    state = {"mode": "CASH", "cash": 1.0, "mom": None, "rev": None, "def": None}
    equity = []
    stop_count = 0
    turnover_total = 0.0
    rebalances = 0
    for date in idx:
        date = pd.Timestamp(date)
        stop_count += mark_day(data, state, date, cost)
        if date in targets:
            state, turnover = rebalance(data, state, date, targets[date], cost)
            turnover_total += turnover
            rebalances += 1
        eq = total_equity(state)
        if not np.isfinite(eq) or eq <= 0:
            raise RuntimeError(f"HR-DUAL invalid equity on {date}")
        equity.append((date, eq))
    eq = pd.Series(dict(equity), dtype=float).sort_index()
    returns = eq.pct_change().fillna(0.0)
    years = max((eq.index[-1] - eq.index[0]).days / 365.2425, 1e-12)
    return {
        "equity": eq,
        "returns": returns,
        "stop_count": stop_count,
        "rebalances": rebalances,
        "turnover_total": turnover_total,
        "decisions": decisions,
        "years": years,
    }


def replay(data_dir, output=None):
    data = load_adjusted_data(data_dir)
    base = simulate(data, 5)
    stress = simulate(data, 15)
    metrics = metrics_from_equity(
        base["equity"].to_numpy(),
        base["returns"].to_numpy(),
        252.0,
        base["years"],
        base["stop_count"] + base["rebalances"],
        num_trials=1,
        pbo=None,
        turnover_per_year=base["turnover_total"] / base["years"],
        gross_exposure=1.0,
        cost_stress_cagr_pct=metrics_from_equity(
            stress["equity"].to_numpy(),
            stress["returns"].to_numpy(),
            252.0,
            stress["years"],
            stress["stop_count"] + stress["rebalances"],
            num_trials=1,
        ).cagr_pct,
    )
    payload = {
        "protocol": "alpha_generation_v4",
        "stage": "development_only",
        "source_lock": SOURCE_LOCK,
        "provider_role": "Yahoo legacy_adjusted_close source-parity diagnostic",
        "data_basis": "Yahoo chart OHLC scaled by AdjClose/Close to match yfinance auto_adjust=True semantics",
        "authoritative_portfolio_eligible": False,
        "eligibility_reason": (
            "frozen prior strategy is replayed source-faithfully, but V4 "
            "authoritative portfolio remains Tiingo evidence-bearing"
        ),
        "data_start": data["QQQ"].index.min().strftime("%Y-%m-%d"),
        "data_end": data["QQQ"].index.max().strftime("%Y-%m-%d"),
        "metrics_5bp": metrics.to_dict(),
        "stop_count": base["stop_count"],
        "scheduled_rebalances": base["rebalances"],
        "hidden_validation_opened": False,
        "final_oos_opened": False,
    }
    if output:
        p = Path(output)
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return payload


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", required=True)
    ap.add_argument("--output", required=True)
    args = ap.parse_args()
    print(json.dumps(replay(args.data_dir, args.output), indent=2, sort_keys=True))
