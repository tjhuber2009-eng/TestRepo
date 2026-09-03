import json
import math
import os
import time
from dataclasses import dataclass
from datetime import datetime, timezone

import numpy as np
import pandas as pd
import requests

START_FETCH = "2013-01-01"
END_FETCH = "2026-09-03"  # exclusive-ish target; API chunks are timestamp bounded
PUBLISHED_START = "2014-05-20"
PUBLISHED_END = "2026-01-18"
PUBLICATION_DATE = "2026-01-15"
CURRENT_END = "2026-09-02"


def utc_ts(s):
    return int(pd.Timestamp(s, tz="UTC").timestamp())


def fetch_bitstamp():
    """Fetch BTC/USD daily OHLC in <=900-day chunks from Bitstamp's public API."""
    endpoint = "https://www.bitstamp.net/api/v2/ohlc/btcusd/"
    start = pd.Timestamp(START_FETCH, tz="UTC")
    stop = pd.Timestamp(END_FETCH, tz="UTC")
    rows = []
    cursor = start
    session = requests.Session()
    session.headers.update({"User-Agent": "dual-signal-independent-audit/1.0"})
    while cursor < stop:
        chunk_end = min(cursor + pd.Timedelta(days=899), stop)
        params = {
            "step": 86400,
            "limit": 1000,
            "start": int(cursor.timestamp()),
            "end": int(chunk_end.timestamp()),
            "exclude_current_candle": "true",
        }
        last_err = None
        for attempt in range(5):
            try:
                r = session.get(endpoint, params=params, timeout=30)
                r.raise_for_status()
                payload = r.json()
                block = payload.get("data", {}).get("ohlc", [])
                if not block:
                    raise RuntimeError(f"empty Bitstamp block {cursor}..{chunk_end}: {payload}")
                rows.extend(block)
                last_err = None
                break
            except Exception as e:
                last_err = e
                time.sleep(2 ** attempt)
        if last_err:
            raise last_err
        cursor = chunk_end + pd.Timedelta(days=1)
    df = pd.DataFrame(rows)
    for c in ["open", "high", "low", "close", "volume"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    df["Date"] = pd.to_datetime(pd.to_numeric(df["timestamp"]), unit="s", utc=True).dt.tz_localize(None)
    df = df.rename(columns={"open":"Open", "high":"High", "low":"Low", "close":"Close", "volume":"Volume"})
    df = df[["Date", "Open", "High", "Low", "Close", "Volume"]].dropna().drop_duplicates("Date").sort_values("Date")
    df = df.set_index("Date")
    # Filter to UTC calendar daily bars in requested range.
    return df.loc[pd.Timestamp(START_FETCH):pd.Timestamp(CURRENT_END)].copy()


def try_fetch_yahoo():
    try:
        import yfinance as yf
        d = yf.download("BTC-USD", start="2014-09-15", end="2026-09-03", interval="1d", auto_adjust=False, progress=False, threads=False)
        if isinstance(d.columns, pd.MultiIndex):
            d.columns = d.columns.get_level_values(0)
        keep = [c for c in ["Open", "High", "Low", "Close", "Volume"] if c in d.columns]
        d = d[keep].copy()
        if getattr(d.index, "tz", None) is not None:
            d.index = d.index.tz_localize(None)
        d = d.dropna(subset=["Open", "Close"])
        return d
    except Exception as e:
        print("YAHOO_FETCH_FAILED", repr(e))
        return None


def pine_ema(s, n):
    # Pine ta.ema recurrence: alpha = 2/(n+1). pandas adjust=False is the same recurrence.
    return s.ewm(span=n, adjust=False, min_periods=1).mean()


def make_signal(close, n, threshold=0.5):
    ema = pine_ema(close, n)
    # Pine ta.stdev(source, length) defaults biased=true => population std, ddof=0.
    sd = close.rolling(n, min_periods=n).std(ddof=0)
    z = (close - ema) / sd
    state = np.zeros(len(close), dtype=np.int8)
    prev = 0
    vals = z.to_numpy()
    for i, v in enumerate(vals):
        if np.isfinite(v):
            if v > threshold:
                prev = 1
            elif v < -threshold:
                prev = 0
        state[i] = prev
    return pd.Series(state, index=close.index, name=f"signal_{n}"), z, ema, sd


@dataclass
class EngineResult:
    equity: pd.Series
    position: pd.Series
    trades: pd.DataFrame


def run_engine(df, signal, cost_side=0.0, mode="next_open"):
    idx = df.index
    O = df["Open"].astype(float).to_numpy()
    C = df["Close"].astype(float).to_numpy()
    S = signal.astype(int).to_numpy()
    n = len(df)
    eq = np.ones(n, dtype=float)
    pos_arr = np.zeros(n, dtype=np.int8)
    pos = 0
    equity = 1.0
    trades = []
    trade = None

    def enter(i, px, when):
        nonlocal equity, trade
        baseline = equity
        equity *= (1.0 - cost_side)
        trade = {
            "entry_date": idx[i], "entry_price": float(px), "entry_equity_pre_fee": baseline,
            "entry_equity_post_fee": equity, "entry_when": when,
        }

    def exit_trade(i, px, when):
        nonlocal equity, trade
        equity *= (1.0 - cost_side)
        if trade is not None:
            t = dict(trade)
            t.update({
                "exit_date": idx[i], "exit_price": float(px), "exit_equity_post_fee": equity,
                "exit_when": when,
                "pnl_equity_units": equity - t["entry_equity_pre_fee"],
                "return_pct": equity / t["entry_equity_pre_fee"] - 1.0,
            })
            trades.append(t)
        trade = None

    for i in range(1, n):
        prev_close = C[i-1]
        if mode == "next_open":
            # Position from prior close participates until today's open.
            if pos:
                equity *= O[i] / prev_close
            desired = int(S[i-1])  # yesterday's completed-bar signal -> today's open
            if desired != pos:
                if pos == 1:
                    exit_trade(i, O[i], "open")
                else:
                    enter(i, O[i], "open")
                pos = desired
            if pos:
                equity *= C[i] / O[i]

        elif mode == "next_close":
            # Strict one-full-bar delay: yesterday's close signal can only fill today's close.
            if pos:
                equity *= C[i] / prev_close
            desired = int(S[i-1])
            if desired != pos:
                if pos == 1:
                    exit_trade(i, C[i], "close")
                else:
                    enter(i, C[i], "close")
                pos = desired

        elif mode == "same_close":
            # Upper-bound / execution-artifact comparison: signal from today's close fills that same close.
            if pos:
                equity *= C[i] / prev_close
            desired = int(S[i])
            if desired != pos:
                if pos == 1:
                    exit_trade(i, C[i], "same_close")
                else:
                    enter(i, C[i], "same_close")
                pos = desired
        else:
            raise ValueError(mode)

        eq[i] = equity
        pos_arr[i] = pos

    trades_df = pd.DataFrame(trades)
    return EngineResult(pd.Series(eq, index=idx, name="equity"), pd.Series(pos_arr, index=idx, name="position"), trades_df)


def annualized_metrics(equity, position, trades, start, end):
    start = pd.Timestamp(start)
    end = pd.Timestamp(end)
    e = equity.loc[(equity.index >= start) & (equity.index <= end)].dropna()
    p = position.reindex(e.index).fillna(0)
    if len(e) < 2:
        return None
    e = e / e.iloc[0]
    dr = e.pct_change().dropna()
    years = (e.index[-1] - e.index[0]).days / 365.2425
    total = e.iloc[-1] - 1.0
    cagr = e.iloc[-1] ** (1.0 / years) - 1.0 if years > 0 and e.iloc[-1] > 0 else np.nan
    peak = e.cummax()
    dd = e / peak - 1.0
    sharpe = math.sqrt(365.0) * dr.mean() / dr.std(ddof=1) if len(dr) > 2 and dr.std(ddof=1) > 0 else np.nan
    exposure = float(p.mean())
    pf = np.nan
    closed = 0
    wins = 0
    if trades is not None and len(trades):
        t = trades[(pd.to_datetime(trades["entry_date"]) >= start) & (pd.to_datetime(trades["exit_date"]) <= end)].copy()
        closed = len(t)
        if closed:
            pnls = t["pnl_equity_units"].astype(float)
            gp = pnls[pnls > 0].sum()
            gl = -pnls[pnls < 0].sum()
            pf = gp / gl if gl > 0 else np.inf
            wins = int((pnls > 0).sum())
    return {
        "start": e.index[0].date().isoformat(), "end": e.index[-1].date().isoformat(),
        "days": int((e.index[-1]-e.index[0]).days), "total_return_pct": total*100,
        "cagr_pct": cagr*100, "max_dd_pct": dd.min()*100, "sharpe": sharpe,
        "exposure_pct": exposure*100, "closed_trades": int(closed), "winning_trades": int(wins),
        "profit_factor": pf,
    }


def buy_hold_metrics(df, start, end):
    s = df.loc[(df.index >= pd.Timestamp(start)) & (df.index <= pd.Timestamp(end)), "Close"].dropna()
    if len(s) < 2:
        return None
    e = s / s.iloc[0]
    dr = e.pct_change().dropna()
    years = (e.index[-1] - e.index[0]).days / 365.2425
    total = e.iloc[-1]-1
    cagr = e.iloc[-1]**(1/years)-1 if years > 0 else np.nan
    dd = e/e.cummax()-1
    sharpe = math.sqrt(365)*dr.mean()/dr.std(ddof=1) if dr.std(ddof=1)>0 else np.nan
    return {"total_return_pct":total*100,"cagr_pct":cagr*100,"max_dd_pct":dd.min()*100,"sharpe":sharpe}


def fmt(x):
    if x is None: return "NA"
    if isinstance(x, float) and (np.isnan(x) or np.isinf(x)):
        return "inf" if np.isinf(x) else "NA"
    if isinstance(x, float): return f"{x:.4f}"
    return str(x)


def main():
    os.makedirs("audit_output", exist_ok=True)
    df = fetch_bitstamp()
    print("BITSTAMP_ROWS", len(df), "RANGE", df.index.min(), df.index.max())
    print("BITSTAMP_HEAD", df.head(2).to_dict("index"))
    print("BITSTAMP_TAIL", df.tail(2).to_dict("index"))
    if df.index.min() > pd.Timestamp("2013-02-01") or df.index.max() < pd.Timestamp(CURRENT_END):
        raise RuntimeError("Bitstamp range insufficient for requested audit")
    df.to_csv("audit_output/bitstamp_btcusd_daily.csv")

    windows = {
        "published_inferred": (PUBLISHED_START, PUBLISHED_END),
        "full_to_current": (PUBLISHED_START, CURRENT_END),
        "cycle_2014_2017": (PUBLISHED_START, "2017-12-31"),
        "cycle_2018_2021": ("2018-01-01", "2021-12-31"),
        "cycle_2022_2026": ("2022-01-01", CURRENT_END),
        "claimed_live_2023_current": ("2023-01-01", CURRENT_END),
        "public_forward": (PUBLICATION_DATE, CURRENT_END),
    }
    costs = {
        "zero": 0.0,
        "realistic_15bps_side": 0.0015,
        "half_pct_roundtrip_25bps_side": 0.0025,
        "author_0p5pct_side": 0.005,
    }
    rows = []
    signals = {}
    for length in (63,65):
        sig, z, ema, sd = make_signal(df["Close"], length, 0.5)
        signals[length] = sig
        pd.DataFrame({"Close":df["Close"],"EMA":ema,"Std":sd,"Z":z,"Signal":sig}).to_csv(f"audit_output/signal_{length}.csv")
        for mode in ("next_open", "next_close", "same_close"):
            for cost_name, cost in costs.items():
                eng = run_engine(df, sig, cost, mode)
                if mode == "next_open" and cost_name in ("zero","author_0p5pct_side"):
                    eng.trades.to_csv(f"audit_output/trades_{length}_{mode}_{cost_name}.csv", index=False)
                for wname,(ws,we) in windows.items():
                    m = annualized_metrics(eng.equity, eng.position, eng.trades, ws, we)
                    if m:
                        row = {"length":length,"threshold":0.5,"mode":mode,"cost_case":cost_name,"cost_side_bps":cost*10000,"window":wname,**m}
                        rows.append(row)
    out = pd.DataFrame(rows)
    out.to_csv("audit_output/results.csv", index=False)

    # Buy & hold comparators on the same windows.
    bh_rows=[]
    for wname,(ws,we) in windows.items():
        bm=buy_hold_metrics(df,ws,we)
        if bm: bh_rows.append({"window":wname,**bm})
    pd.DataFrame(bh_rows).to_csv("audit_output/buy_hold.csv",index=False)

    # Parameter plateau: lengths 45..85 at fixed ±0.5, causal next-open, zero costs.
    sens=[]
    for length in range(45,86):
        sig,_,_,_=make_signal(df["Close"],length,0.5)
        eng=run_engine(df,sig,0.0,"next_open")
        m=annualized_metrics(eng.equity,eng.position,eng.trades,PUBLISHED_START,PUBLISHED_END)
        sens.append({"length":length,**m})
    sens_df=pd.DataFrame(sens).sort_values("cagr_pct",ascending=False)
    sens_df.to_csv("audit_output/length_sensitivity.csv",index=False)

    # Threshold neighborhood for both provenance lengths.
    tsens=[]
    for length in (63,65):
        for threshold in np.arange(0.30,0.81,0.05):
            sig,_,_,_=make_signal(df["Close"],length,float(round(threshold,2)))
            eng=run_engine(df,sig,0.0,"next_open")
            m=annualized_metrics(eng.equity,eng.position,eng.trades,PUBLISHED_START,PUBLISHED_END)
            tsens.append({"length":length,"threshold":round(float(threshold),2),**m})
    tsens_df=pd.DataFrame(tsens).sort_values("cagr_pct",ascending=False)
    tsens_df.to_csv("audit_output/threshold_sensitivity.csv",index=False)

    # Independent Yahoo cross-feed where it overlaps. yfinance begins in Sep 2014.
    yahoo=try_fetch_yahoo()
    cross=[]
    if yahoo is not None and len(yahoo)>100:
        common_start=max(pd.Timestamp("2014-09-18"),yahoo.index.min(),df.index.min())
        common_end=min(pd.Timestamp(PUBLISHED_END),yahoo.index.max(),df.index.max())
        for source,d in (("bitstamp",df),("yahoo",yahoo)):
            for length in (63,65):
                sig,_,_,_=make_signal(d["Close"],length,0.5)
                eng=run_engine(d,sig,0.0,"next_open")
                m=annualized_metrics(eng.equity,eng.position,eng.trades,common_start,common_end)
                cross.append({"source":source,"length":length,"window_start":str(common_start.date()),"window_end":str(common_end.date()),**m})
        pd.DataFrame(cross).to_csv("audit_output/cross_feed.csv",index=False)
        yahoo.to_csv("audit_output/yahoo_btcusd_daily.csv")

    # Human-readable headline log, deliberately compact enough for Actions logs.
    focus=out[(out["mode"]=="next_open") & (out["cost_case"].isin(["zero","realistic_15bps_side","author_0p5pct_side"]))]
    print("\n=== HEADLINE RESULTS (causal next-open) ===")
    cols=["length","cost_case","window","total_return_pct","cagr_pct","max_dd_pct","sharpe","profit_factor","closed_trades","exposure_pct"]
    print(focus[cols].to_string(index=False,float_format=lambda x:f"{x:.3f}"))
    print("\n=== BUY HOLD ===")
    print(pd.DataFrame(bh_rows).to_string(index=False,float_format=lambda x:f"{x:.3f}"))
    print("\n=== LENGTH SENSITIVITY TOP 15 ===")
    print(sens_df[["length","cagr_pct","total_return_pct","max_dd_pct","closed_trades"]].head(15).to_string(index=False,float_format=lambda x:f"{x:.3f}"))
    print("\n=== LENGTH 63/65 RANKS ===")
    rank=sens_df.reset_index(drop=True)
    for L in (63,65):
        r=rank.index[rank["length"]==L][0]+1
        rr=rank[rank["length"]==L].iloc[0]
        print("LENGTH_RANK",L,r,"OF",len(rank),"CAGR",fmt(rr["cagr_pct"]),"TOTAL",fmt(rr["total_return_pct"]))
    print("\n=== THRESHOLD SENSITIVITY TOP 12 ===")
    print(tsens_df[["length","threshold","cagr_pct","total_return_pct","max_dd_pct","closed_trades"]].head(12).to_string(index=False,float_format=lambda x:f"{x:.3f}"))
    if cross:
        print("\n=== CROSS FEED ===")
        print(pd.DataFrame(cross)[["source","length","window_start","window_end","cagr_pct","total_return_pct","max_dd_pct","closed_trades"]].to_string(index=False,float_format=lambda x:f"{x:.3f}"))

    metadata={
        "generated_utc":datetime.now(timezone.utc).isoformat(),
        "primary_source":"Bitstamp public OHLC API /api/v2/ohlc/btcusd, step=86400",
        "fetch_range":[START_FETCH,CURRENT_END],
        "formula":"Z=(Close-EMA_n)/rolling_population_std_n; >+0.5 long; <-0.5 cash; hysteresis otherwise",
        "ema":"alpha=2/(n+1), recursive adjust=False",
        "stdev":"rolling population std ddof=0, matching Pine ta.stdev biased=true default",
        "causal_execution":"signal on completed close t; next_open fills at open t+1",
        "strict_execution":"next_close fills at close t+1",
        "same_close":"included only as optimistic/artifact comparison",
        "published_start_note":"2014-05-20 is inferred from public/sample-period evidence; exact original start date was not independently proven",
    }
    with open("audit_output/metadata.json","w") as f: json.dump(metadata,f,indent=2)


if __name__ == "__main__":
    main()
