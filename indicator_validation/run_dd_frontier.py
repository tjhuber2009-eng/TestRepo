#!/usr/bin/env python3
from pathlib import Path
import pandas as pd
import numpy as np
import yfinance as yf

OUT=Path("indicator_validation/output_dd_frontier"); OUT.mkdir(parents=True,exist_ok=True)
END="2026-09-03"; LOOKBACK=63; THRESH=0.5; COST=0.0007
CEILINGS=list(range(3,61))

def fetch(start="2014-09-17"):
    x=yf.download("BTC-USD",start=start,end=END,interval="1d",auto_adjust=False,progress=False,threads=False)
    if isinstance(x.columns,pd.MultiIndex): x.columns=x.columns.get_level_values(0)
    x=x.rename(columns=str.lower).reset_index(); x=x.rename(columns={x.columns[0]:"datetime"})
    x["datetime"]=pd.to_datetime(x["datetime"],utc=True)
    return x[["datetime","open","high","low","close"]].dropna().sort_values("datetime").reset_index(drop=True)

def state_signal(df):
    ema=df.close.ewm(span=LOOKBACK,adjust=False).mean()
    sd=df.close.rolling(LOOKBACK).std(ddof=0)
    z=(df.close-ema)/sd
    bull=(z>THRESH).fillna(False); bear=(z<-THRESH).fillna(False)
    state=[]; on=False
    for i in range(len(df)):
        if bull.iloc[i]: on=True
        elif bear.iloc[i]: on=False
        state.append(on)
    return pd.Series(state,index=df.index,dtype=bool)

def run(df, alloc_series):
    sig=state_signal(df)
    shares=0.0; cash=1.0; curve=[]; turnover=0.0
    for i,row in df.iterrows():
        target=0.0 if i==0 else (float(alloc_series.iloc[i-1]) if bool(sig.iloc[i-1]) else 0.0)
        px=float(row.open)
        mtm_open=cash+shares*px
        desired=mtm_open*target
        current=shares*px
        trade=desired-current
        if abs(trade)>1e-12:
            cash -= trade + abs(trade)*COST
            shares += trade/px
            turnover += abs(trade)
        curve.append(cash+shares*float(row.close))
    if shares:
        val=shares*float(df.iloc[-1].close)
        cash += val-abs(val)*COST
        curve[-1]=cash
    eq=float(curve[-1])
    e=pd.Series(curve,index=df.datetime,dtype=float)
    years=max((df.datetime.iloc[-1]-df.datetime.iloc[0]).total_seconds()/(365.25*86400),1/365.25)
    dd=float((e/e.cummax()-1).min()*100)
    cagr=(eq**(1/years)-1)*100 if eq>0 else -100
    return {"net_pct":(eq-1)*100,"cagr_pct":cagr,"maxdd_pct":dd,
            "mar":cagr/abs(dd) if dd<0 else np.nan,"turnover_x":turnover}

def candidate_grid(df):
    rows=[]
    # Fixed sizing: dense enough to trace the frontier without optimizing the signal.
    for pct in np.arange(0.25,100.0001,0.25):
        a=pd.Series(pct/100,index=df.index)
        rows.append({"method":"fixed","name":f"fixed_{pct:.2f}%","allocation_pct":pct,**run(df,a)})
    # Causal 30d volatility targeting, broad predeclared family.
    r=np.log(df.close/df.close.shift(1))
    annvol=r.rolling(30).std(ddof=0)*np.sqrt(365)
    for target_pct in np.arange(0.5,40.0001,0.5):
        for cap_pct in [5,10,15,20,25,33,50,75,100]:
            alloc=(target_pct/100/annvol).clip(lower=0,upper=cap_pct/100).fillna(0)
            rows.append({"method":"vol_target","name":f"vol{target_pct:.1f}_cap{cap_pct}",
                         "target_ann_vol_pct":target_pct,"cap_pct":cap_pct,
                         "avg_alloc_pct":float(alloc.mean()*100),**run(df,alloc)})
    return pd.DataFrame(rows)

def frontier(grid):
    out=[]
    for ceiling in CEILINGS:
        ok=grid[grid.maxdd_pct>=-ceiling].copy()
        if len(ok)==0: continue
        w=ok.sort_values(["cagr_pct","maxdd_pct"],ascending=[False,False]).iloc[0]
        out.append({"dd_ceiling_pct":ceiling,**w.to_dict()})
    return pd.DataFrame(out)

df=fetch()
full_grid=candidate_grid(df)
full_front=frontier(full_grid)
full_grid.to_csv(OUT/"full_candidate_grid.csv",index=False)
full_front.to_csv(OUT/"full_history_frontier.csv",index=False)

# Walk-forward: select from 2014-2022 only, then run frozen choice on 2023+.
cut=pd.Timestamp("2023-01-01",tz="UTC")
train=df[df.datetime<cut].reset_index(drop=True)
test=df[df.datetime>=cut].reset_index(drop=True)
train_grid=candidate_grid(train)
train_front=frontier(train_grid)
wf=[]
for _,w in train_front.iterrows():
    if w.method=="fixed":
        alloc=pd.Series(float(w.allocation_pct)/100,index=test.index)
    else:
        r=np.log(test.close/test.close.shift(1))
        annvol=r.rolling(30).std(ddof=0)*np.sqrt(365)
        alloc=(float(w.target_ann_vol_pct)/100/annvol).clip(lower=0,upper=float(w.cap_pct)/100).fillna(0)
    tm=run(test,alloc)
    wf.append({
        "dd_ceiling_pct":int(w.dd_ceiling_pct),
        "selected_method":w.method,"selected_name":w["name"],
        "train_cagr_pct":w.cagr_pct,"train_maxdd_pct":w.maxdd_pct,
        "test_cagr_pct":tm["cagr_pct"],"test_maxdd_pct":tm["maxdd_pct"],
        "test_net_pct":tm["net_pct"],
        "test_pass_ceiling":tm["maxdd_pct"]>=-float(w.dd_ceiling_pct)
    })
wf=pd.DataFrame(wf)
wf.to_csv(OUT/"walkforward_frontier.csv",index=False)

print("FULL HISTORY FRONTIER")
print(full_front[["dd_ceiling_pct","name","method","cagr_pct","maxdd_pct","net_pct","mar"]].to_string(index=False))
print("\nWALK-FORWARD FRONTIER")
print(wf.to_string(index=False))
