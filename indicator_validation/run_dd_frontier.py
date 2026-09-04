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
    bull=(z>THRESH).fillna(False).to_numpy(); bear=(z<-THRESH).fillna(False).to_numpy()
    state=np.zeros(len(df),dtype=bool); on=False
    for i in range(len(df)):
        if bull[i]: on=True
        elif bear[i]: on=False
        state[i]=on
    return state

def engine(df):
    return {
      "o":df.open.to_numpy(float),"c":df.close.to_numpy(float),
      "sig":state_signal(df),
      "years":max((df.datetime.iloc[-1]-df.datetime.iloc[0]).total_seconds()/(365.25*86400),1/365.25)
    }

def run_fast(E, alloc):
    o=E["o"]; c=E["c"]; sig=E["sig"]; n=len(o)
    # target at today's open is prior close's state and prior-close-known allocation.
    target=np.zeros(n,float)
    target[1:]=alloc[:-1]*sig[:-1]
    shares=0.0; cash=1.0; peak=1.0; maxdd=0.0; turnover=0.0; last=1.0
    for i in range(n):
        px=o[i]
        mtm_open=cash+shares*px
        desired=mtm_open*target[i]
        trade=desired-shares*px
        if trade!=0.0:
            cash -= trade + abs(trade)*COST
            shares += trade/px
            turnover += abs(trade)
        last=cash+shares*c[i]
        if last>peak: peak=last
        dd=last/peak-1.0
        if dd<maxdd: maxdd=dd
    if shares!=0.0:
        val=shares*c[-1]
        cash += val-abs(val)*COST
        last=cash
        if last>peak: peak=last
        dd=last/peak-1.0
        if dd<maxdd: maxdd=dd
    cagr=(last**(1/E["years"])-1)*100 if last>0 else -100.0
    ddpct=maxdd*100
    return {"net_pct":(last-1)*100,"cagr_pct":cagr,"maxdd_pct":ddpct,
            "mar":cagr/abs(ddpct) if ddpct<0 else np.nan,"turnover_x":turnover}

def candidate_grid(df):
    E=engine(df); rows=[]; n=len(df)
    for pct in np.arange(0.25,100.0001,0.25):
        alloc=np.full(n,pct/100,float)
        rows.append({"method":"fixed","name":f"fixed_{pct:.2f}%","allocation_pct":pct,**run_fast(E,alloc)})
    r=np.log(df.close/df.close.shift(1))
    annvol=(r.rolling(30).std(ddof=0)*np.sqrt(365)).to_numpy(float)
    for target_pct in np.arange(0.5,40.0001,0.5):
        for cap_pct in [5,10,15,20,25,33,50,75,100]:
            alloc=np.nan_to_num(target_pct/100/annvol,nan=0.0,posinf=0.0,neginf=0.0)
            alloc=np.clip(alloc,0,cap_pct/100)
            rows.append({"method":"vol_target","name":f"vol{target_pct:.1f}_cap{cap_pct}",
                         "target_ann_vol_pct":target_pct,"cap_pct":cap_pct,
                         "avg_alloc_pct":float(np.mean(alloc)),**run_fast(E,alloc)})
    return pd.DataFrame(rows)

def frontier(grid):
    out=[]
    for ceiling in CEILINGS:
        ok=grid[grid.maxdd_pct>=-ceiling]
        if len(ok)==0: continue
        w=ok.sort_values(["cagr_pct","maxdd_pct"],ascending=[False,False]).iloc[0]
        out.append({"dd_ceiling_pct":ceiling,**w.to_dict()})
    return pd.DataFrame(out)

def alloc_for_choice(df,w):
    if w["method"]=="fixed":
        return np.full(len(df),float(w["allocation_pct"])/100,float)
    r=np.log(df.close/df.close.shift(1))
    annvol=(r.rolling(30).std(ddof=0)*np.sqrt(365)).to_numpy(float)
    alloc=np.nan_to_num(float(w["target_ann_vol_pct"])/100/annvol,nan=0.0,posinf=0.0,neginf=0.0)
    return np.clip(alloc,0,float(w["cap_pct"])/100)

df=fetch()
full_grid=candidate_grid(df); full_front=frontier(full_grid)
full_grid.to_csv(OUT/"full_candidate_grid.csv",index=False)
full_front.to_csv(OUT/"full_history_frontier.csv",index=False)

cut=pd.Timestamp("2023-01-01",tz="UTC")
train=df[df.datetime<cut].reset_index(drop=True); test=df[df.datetime>=cut].reset_index(drop=True)
train_grid=candidate_grid(train); train_front=frontier(train_grid); Et=engine(test)
wf=[]
for _,w in train_front.iterrows():
    tm=run_fast(Et,alloc_for_choice(test,w))
    wf.append({"dd_ceiling_pct":int(w.dd_ceiling_pct),"selected_method":w.method,"selected_name":w["name"],
               "train_cagr_pct":w.cagr_pct,"train_maxdd_pct":w.maxdd_pct,
               "test_cagr_pct":tm["cagr_pct"],"test_maxdd_pct":tm["maxdd_pct"],"test_net_pct":tm["net_pct"],
               "test_pass_ceiling":tm["maxdd_pct"]>=-float(w.dd_ceiling_pct)})
wf=pd.DataFrame(wf); wf.to_csv(OUT/"walkforward_frontier.csv",index=False)

print("FULL HISTORY FRONTIER")
print(full_front[["dd_ceiling_pct","name","method","cagr_pct","maxdd_pct","net_pct","mar"]].to_string(index=False))
print("\nWALK-FORWARD FRONTIER")
print(wf.to_string(index=False))
