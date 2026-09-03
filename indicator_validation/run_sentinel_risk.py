#!/usr/bin/env python3
from pathlib import Path
import pandas as pd
import numpy as np
import yfinance as yf

OUT=Path("indicator_validation/output_sentinel_risk"); OUT.mkdir(parents=True,exist_ok=True)
END="2026-09-03"; LOOKBACK=63; THRESH=0.5; COST=0.0007
ALLOCATIONS=[1.0,0.75,0.50,0.33,0.25]

def fetch():
    x=yf.download("BTC-USD",start="2014-09-17",end=END,interval="1d",auto_adjust=False,progress=False,threads=False)
    if isinstance(x.columns,pd.MultiIndex): x.columns=x.columns.get_level_values(0)
    x=x.rename(columns=str.lower).reset_index(); x=x.rename(columns={x.columns[0]:"datetime"})
    x["datetime"]=pd.to_datetime(x["datetime"],utc=True)
    return x[["datetime","open","high","low","close"]].dropna().sort_values("datetime").reset_index(drop=True)

def signals(df):
    ema=df.close.ewm(span=LOOKBACK,adjust=False).mean()
    sd=df.close.rolling(LOOKBACK).std(ddof=0)
    z=(df.close-ema)/sd
    return (z>THRESH).fillna(False),(z<-THRESH).fillna(False)

def bt(df,alloc):
    bull,bear=signals(df)
    eq=1.0; cash=1.0; qty=0.0; pos=False; pending=None
    entry_eq=0.0; entry_time=None; trades=[]; curve=[]; exposed=0
    for i,row in df.iterrows():
        if pending=="enter" and not pos:
            entry_eq=eq
            invest=eq*alloc
            px=float(row.open)*(1+COST)
            qty=invest/px
            cash=eq-invest
            pos=True; entry_time=row.datetime; pending=None
        elif pending=="exit" and pos:
            px=float(row.open)*(1-COST)
            neweq=cash+qty*px
            trades.append((entry_time,row.datetime,neweq-entry_eq))
            eq=neweq; cash=eq; qty=0.0; pos=False; pending=None
        mtm=cash+qty*float(row.close) if pos else eq
        eq=mtm if not pos else eq
        curve.append(mtm)
        if pos: exposed+=1
        if i<len(df)-1:
            if (not pos) and bool(bull.iloc[i]): pending="enter"
            elif pos and bool(bear.iloc[i]): pending="exit"
    if pos:
        px=float(df.iloc[-1].close)*(1-COST)
        neweq=cash+qty*px
        trades.append((entry_time,df.iloc[-1].datetime,neweq-entry_eq))
        eq=neweq; curve[-1]=eq
    else:
        eq=curve[-1]
    e=pd.Series(curve,index=df.datetime,dtype=float)
    years=(df.datetime.iloc[-1]-df.datetime.iloc[0]).total_seconds()/(365.25*86400)
    tr=pd.DataFrame(trades,columns=["entry","exit","pnl"])
    gp=tr.loc[tr.pnl>0,"pnl"].sum(); gl=-tr.loc[tr.pnl<=0,"pnl"].sum()
    dd=float((e/e.cummax()-1).min()*100)
    cagr=(eq**(1/years)-1)*100
    # Same initial BTC allocation, held throughout, rest stays as zero-yield cash.
    ratio=df.close/float(df.open.iloc[0])
    bheq=(1-alloc)+alloc*ratio
    bh_final=float(bheq.iloc[-1])
    bh_cagr=(bh_final**(1/years)-1)*100
    bh_dd=float((bheq/bheq.cummax()-1).min()*100)
    return {
        "allocation_pct":alloc*100,
        "net_pct":(eq-1)*100,
        "cagr_pct":cagr,
        "maxdd_pct":dd,
        "mar":cagr/abs(dd) if dd<0 else np.nan,
        "pf":float(gp/gl) if gl>0 else np.inf,
        "trades":len(tr),
        "exposure_pct":exposed/max(len(df)-1,1)*100*alloc,
        "fractional_bh_net_pct":(bh_final-1)*100,
        "fractional_bh_cagr_pct":bh_cagr,
        "fractional_bh_maxdd_pct":bh_dd,
        "fractional_bh_mar":bh_cagr/abs(bh_dd) if bh_dd<0 else np.nan,
    }

df=fetch()
rows=[bt(df,a) for a in ALLOCATIONS]
out=pd.DataFrame(rows)
out.to_csv(OUT/"btc_sentinel63_fractional_risk.csv",index=False)
print(out.to_string(index=False))
