#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import pandas as pd
import requests

DATASET_REPO = "vaquum/binance_btcusdt_30m_klines"
ONE_WAY_COSTS = [0.0007, 0.0010, 0.0015, 0.0020]
OUT = Path("indicator_validation/output")


def download_dataset() -> tuple[Path, dict]:
    OUT.mkdir(parents=True, exist_ok=True)
    meta_url = f"https://huggingface.co/datasets/{DATASET_REPO}/resolve/main/latest.json"
    meta = requests.get(meta_url, timeout=60).json()
    filename = meta["file_name"]
    url = f"https://huggingface.co/datasets/{DATASET_REPO}/resolve/main/{filename}"
    dst = OUT / filename
    with requests.get(url, timeout=180, stream=True) as r:
        r.raise_for_status()
        with dst.open("wb") as f:
            for chunk in r.iter_content(1024 * 1024):
                if chunk:
                    f.write(chunk)
    meta["download_url"] = url
    meta["sha256"] = hashlib.sha256(dst.read_bytes()).hexdigest()
    meta["bytes"] = dst.stat().st_size
    return dst, meta


def to_datetime(series: pd.Series) -> pd.Series:
    if pd.api.types.is_numeric_dtype(series):
        vals = pd.to_numeric(series, errors="coerce")
        med = float(vals.dropna().abs().median())
        if med > 1e17:
            unit = "ns"
        elif med > 1e14:
            unit = "us"
        elif med > 1e11:
            unit = "ms"
        else:
            unit = "s"
        return pd.to_datetime(vals, unit=unit, utc=True, errors="coerce")
    return pd.to_datetime(series, utc=True, errors="coerce")


def load_bars(path: Path) -> pd.DataFrame:
    df = pd.read_parquet(path)
    df.columns = [str(c).lower() for c in df.columns]
    dtcol = next((c for c in ["datetime", "timestamp", "open_time", "date", "ts"] if c in df.columns), None)
    if dtcol is None:
        raise ValueError(f"No datetime column; columns={list(df.columns)}")
    out = pd.DataFrame({"datetime": to_datetime(df[dtcol])})
    for c in ["open", "high", "low", "close"]:
        if c not in df.columns:
            raise ValueError(f"Missing {c}; columns={list(df.columns)}")
        out[c] = pd.to_numeric(df[c], errors="coerce")
    return out.dropna().sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)


def validate(df: pd.DataFrame) -> dict:
    bad = (
        (df.high < df[["open", "close", "low"]].max(axis=1))
        | (df.low > df[["open", "close", "high"]].min(axis=1))
        | (df.low <= 0)
    )
    delta = df.datetime.diff().dropna()
    expected = pd.Timedelta(minutes=30)
    gaps = delta[delta != expected]
    ret = df.close.pct_change().abs()
    return {
        "rows": int(len(df)),
        "start": str(df.datetime.iloc[0]),
        "end": str(df.datetime.iloc[-1]),
        "bad_ohlc_rows": int(bad.sum()),
        "non_30m_intervals": int(len(gaps)),
        "largest_gap_hours": float(gaps.max() / pd.Timedelta(hours=1)) if len(gaps) else 0.0,
        "max_abs_close_return_pct": float(ret.max() * 100),
        "price_min": float(df.low.min()),
        "price_max": float(df.high.max()),
    }


def make_signals(df: pd.DataFrame) -> tuple[pd.Series, pd.Series]:
    body = (df.close - df.open).abs()
    normal = body.rolling(100, min_periods=100).mean()
    pump = ((df.close > df.open) & (body > 3.0 * normal)).fillna(False)
    dump = ((df.close < df.open) & (body > 3.0 * normal)).fillna(False)
    return pump, dump


def backtest(df: pd.DataFrame, one_way_cost: float) -> tuple[dict, pd.DataFrame, pd.Series]:
    pump, dump = make_signals(df)
    eq = 1.0
    pos = False
    qty = 0.0
    entry_px = entry_eff = stop = np.nan
    entry_time = None
    entry_i = None
    start_eq = np.nan
    pending_entry = None
    pending_exit = False
    trades: list[dict] = []
    equity: list[float] = []
    exposed = 0

    for i, row in df.iterrows():
        # Frozen causal convention: completed-bar decisions fill next bar open.
        if pending_exit and pos:
            exit_px = float(row.open)
            exit_eff = exit_px * (1.0 - one_way_cost)
            eq += qty * (exit_eff - entry_eff)
            trades.append(
                {
                    "entry_time": entry_time,
                    "exit_time": row.datetime,
                    "entry": entry_px,
                    "exit": exit_px,
                    "return_pct": (eq / start_eq - 1.0) * 100.0,
                    "hold_bars": i - entry_i,
                    "stop_ref": stop,
                }
            )
            pos = False
            qty = 0.0
            pending_exit = False

        if pending_entry is not None and not pos:
            entry_px = float(row.open)
            entry_eff = entry_px * (1.0 + one_way_cost)
            start_eq = eq
            qty = eq / entry_eff
            stop = float(pending_entry["stop"])
            entry_time = row.datetime
            entry_i = i
            pos = True
            pending_entry = None

        if pos:
            exposed += 1
            mtm = eq + qty * (float(row.close) - entry_eff)
        else:
            mtm = eq
        equity.append(mtm)

        if pos:
            if float(row.low) <= stop or bool(dump.iloc[i]):
                pending_exit = True
        elif bool(pump.iloc[i]) and i < len(df) - 1:
            pending_entry = {"stop": float(row.low)}

    if pos:
        row = df.iloc[-1]
        exit_px = float(row.close)
        exit_eff = exit_px * (1.0 - one_way_cost)
        eq += qty * (exit_eff - entry_eff)
        trades.append(
            {
                "entry_time": entry_time,
                "exit_time": row.datetime,
                "entry": entry_px,
                "exit": exit_px,
                "return_pct": (eq / start_eq - 1.0) * 100.0,
                "hold_bars": len(df) - 1 - entry_i,
                "stop_ref": stop,
            }
        )
        equity[-1] = eq

    e = pd.Series(equity, index=df.datetime, dtype=float)
    maxdd = float((e / e.cummax() - 1.0).min() * 100.0)
    tr = pd.DataFrame(trades)
    if len(tr):
        gp = float(tr.loc[tr.return_pct > 0, "return_pct"].sum())
        gl = float(-tr.loc[tr.return_pct <= 0, "return_pct"].sum())
        pf = gp / gl if gl else (99.0 if gp else 0.0)
        win = float((tr.return_pct > 0).mean() * 100.0)
        hold = float(tr.hold_bars.mean())
    else:
        pf = win = hold = 0.0
    years = max((df.datetime.iloc[-1] - df.datetime.iloc[0]).total_seconds() / (365.25 * 86400), 1 / 365.25)
    cagr = ((eq ** (1 / years)) - 1) * 100 if eq > 0 else -100.0
    return (
        {
            "net_pct": float((eq - 1.0) * 100.0),
            "cagr_pct": float(cagr),
            "pf": float(pf),
            "win_pct": win,
            "trades": int(len(tr)),
            "maxdd_pct": maxdd,
            "exposure_pct": float(exposed / max(len(df) - 1, 1) * 100.0),
            "avg_hold_bars": hold,
            "bnh_pct": float((df.close.iloc[-1] / df.open.iloc[0] - 1.0) * 100.0),
        },
        tr,
        e,
    )


def isolated_window(full: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp, cost: float) -> dict | None:
    # Include 100-bar signal warmup before the requested window, but force flat at the
    # beginning by starting the actual simulator at the window boundary after signal prep.
    prior = full[full.datetime < start].tail(100)
    core = full[(full.datetime >= start) & (full.datetime < end)]
    if len(core) < 200:
        return None
    combo = pd.concat([prior, core], ignore_index=True)
    pump, dump = make_signals(combo)
    core2 = combo.iloc[len(prior):].reset_index(drop=True)
    # Recreate the precomputed signal values on the core, preserving warmup.
    core2["_pump"] = pump.iloc[len(prior):].to_numpy()
    core2["_dump"] = dump.iloc[len(prior):].to_numpy()

    # Small adapter keeps backtest logic identical while honoring pre-window warmup.
    body = (core2.close - core2.open).abs()
    # make_signals will be temporarily represented by equivalent synthetic rolling values
    # only for this isolated run via direct local implementation below.
    eq = 1.0; pos = False; qty = 0.0; pending_entry = None; pending_exit = False
    entry_px = entry_eff = stop = np.nan; entry_time = None; entry_i = None; start_eq = np.nan
    trades = []; equity = []; exposed = 0
    for i, row in core2.iterrows():
        if pending_exit and pos:
            exit_px = float(row.open); exit_eff = exit_px * (1-cost); eq += qty*(exit_eff-entry_eff)
            trades.append((eq/start_eq-1)*100); pos=False; qty=0; pending_exit=False
        if pending_entry is not None and not pos:
            entry_px=float(row.open); entry_eff=entry_px*(1+cost); start_eq=eq; qty=eq/entry_eff
            stop=float(pending_entry); entry_time=row.datetime; entry_i=i; pos=True; pending_entry=None
        equity.append(eq + qty*(float(row.close)-entry_eff) if pos else eq)
        if pos:
            exposed += 1
            if float(row.low)<=stop or bool(row._dump): pending_exit=True
        elif bool(row._pump) and i < len(core2)-1:
            pending_entry=float(row.low)
    if pos:
        exit_px=float(core2.iloc[-1].close); eq += qty*(exit_px*(1-cost)-entry_eff); trades.append((eq/start_eq-1)*100); equity[-1]=eq
    e=pd.Series(equity,dtype=float); dd=float((e/e.cummax()-1).min()*100)
    gp=sum(x for x in trades if x>0); gl=-sum(x for x in trades if x<=0)
    years=max((core2.datetime.iloc[-1]-core2.datetime.iloc[0]).total_seconds()/(365.25*86400),1/365.25)
    return {
        "net_pct":(eq-1)*100,
        "cagr_pct":((eq**(1/years))-1)*100 if eq>0 else -100,
        "pf":gp/gl if gl else (99.0 if gp else 0.0),
        "win_pct":100*sum(x>0 for x in trades)/len(trades) if trades else 0,
        "trades":len(trades),
        "maxdd_pct":dd,
        "exposure_pct":100*exposed/max(len(core2)-1,1),
        "bnh_pct":(core2.close.iloc[-1]/core2.open.iloc[0]-1)*100,
    }


def main() -> None:
    path, source = download_dataset()
    df = load_bars(path)
    quality = validate(df)
    if quality["bad_ohlc_rows"]:
        raise RuntimeError(f"Invalid OHLC rows: {quality}")

    rows: list[dict] = []
    for cost in ONE_WAY_COSTS:
        m, tr, e = backtest(df, cost)
        rows.append({"window": "FULL", "oneway_bps": cost*10000, **m})
        if cost == ONE_WAY_COSTS[0]:
            tr.to_csv(OUT / "boto_trades_primary.csv", index=False)
            e.rename("equity").to_csv(OUT / "boto_equity_primary.csv")

    primary = ONE_WAY_COSTS[0]
    y0, y1 = int(df.datetime.dt.year.min()), int(df.datetime.dt.year.max())
    for y in range(y0, y1 + 1):
        for label, a, b in [
            (str(y), f"{y}-01-01", f"{y+1}-01-01"),
            (f"{y}H1", f"{y}-01-01", f"{y}-07-01"),
            (f"{y}H2", f"{y}-07-01", f"{y+1}-01-01"),
        ]:
            m = isolated_window(df, pd.Timestamp(a, tz="UTC"), pd.Timestamp(b, tz="UTC"), primary)
            if m:
                rows.append({"window": label, "oneway_bps": primary*10000, **m})

    results = pd.DataFrame(rows)
    results.to_csv(OUT / "boto_long_results.csv", index=False)
    (OUT / "data_provenance.json").write_text(json.dumps({"dataset": source, "quality": quality}, indent=2))
    summary = {
        "source": source,
        "quality": quality,
        "full": results[results.window == "FULL"].to_dict("records"),
        "calendar_years": results[results.window.str.fullmatch(r"\\d{4}", na=False)].to_dict("records"),
    }
    (OUT / "summary.json").write_text(json.dumps(summary, indent=2))
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
