#!/usr/bin/env python3
from pathlib import Path
import pandas as pd
import numpy as np
import yfinance as yf

OUT=Path("indicator_validation/output_sentinel_dd3"); OUT.mkdir(parents=True,exist_ok=True)
END="2026-09-03"; LOOKBACK=63; THRESH=0.5; COST=0.0007

def fetch():
    x=yf.download("BTC-USD",start="2014-09-17",end=END,interval="1d",auto_adjust=False,progress=False,threads=False)
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
    eq=1.0; shares=0.0; cash=1.0; target_prev=0.0
    curve=[]; turnover=0.0
    for i,row in df.iterrows():
        # desired allocation is based only on information through prior close
        target=0.0 if i==0 else (float(alloc_series.iloc[i-1]) if bool(sig.iloc[i-1]) else 0.0)
        px=float(row.open)
        # rebalance at open to target allocation
        mtm_open=cash+shares*px
        desired_value=mtm_open*target
        current_value=shares*px
        trade_value=desired_value-current_value
        if abs(trade_value)>1e-12:
            fee=abs(trade_value)*COST
            cash -= trade_value + fee
            shares += trade_value/px
            turnover += abs(trade_value)
        mtm_close=cash+shares*float(row.close)
        curve.append(mtm_close)
    # liquidate at final close with cost
    if shares!=0:
        val=shares*float(df.iloc[-1].close)
        cash += val - abs(val)*COST
        shares=0.0
        curve[-1]=cash
    eq=float(curve[-1])
    e=pd.Series(curve,index=df.datetime,dtype=float)
    years=(df.datetime.iloc[-1]-df.datetime.iloc[0]).total_seconds()/(365.25*86400)
    dd=float((e/e.cummax()-1).min()*100)
    cagr=(eq**(1/years)-1)*100
    return {"net_pct":(eq-1)*100,"cagr_pct":cagr,"maxdd_pct":dd,
            "mar":cagr/abs(dd) if dd<0 else np.nan,"turnover_x":turnover}

def fixed_grid(df):
    rows=[]
    for pct in np.arange(0.25,15.0001,0.25):
        a=pd.Series(pct/100,index=df.index)
        m=run(df,a); rows.append({"method":"fixed","allocation_pct":pct,**m})
    return pd.DataFrame(rows)

def vol_grid(df):
    # predeclared, causal vol-target family; cap prevents accidental leverage.
    r=np.log(df.close/df.close.shift(1))
    annvol=r.rolling(30).std(ddof=0)*np.sqrt(365)
    rows=[]
    for target in [0.01,0.015,0.02,0.025,0.03,0.04,0.05,0.06,0.08,0.10]:
        for cap in [0.05,0.10,0.15,0.25,0.50,1.0]:
            alloc=(target/annvol).clip(lower=0,upper=cap).fillna(0)
            m=run(df,alloc)
            rows.append({"method":"vol_target","target_ann_vol_pct":target*100,"cap_pct":cap*100,
                         "avg_alloc_pct":alloc.mean()*100,**m})
    return pd.DataFrame(rows)

df=fetch()
fixed=fixed_grid(df); vol=vol_grid(df)
fixed.to_csv(OUT/"fixed_allocation_grid.csv",index=False)
vol.to_csv(OUT/"vol_target_grid.csv",index=False)

front=pd.concat([fixed.assign(label=fixed["allocation_pct"].map(lambda x:f"fixed_{x:.2f}%")),
                 vol.assign(label=vol.apply(lambda r:f"vol_{r.target_ann_vol_pct:g}_cap{r.cap_pct:g}",axis=1))],
                ignore_index=True,sort=False)
eligible=front[front.maxdd_pct>=-3.0].sort_values(["cagr_pct","maxdd_pct"],ascending=[False,False])
eligible.to_csv(OUT/"eligible_dd3.csv",index=False)
print("TOP ELIGIBLE <=3% DD")
print(eligible.head(25).to_string(index=False))

# walk-forward sanity: choose best fixed allocation on 2014-2022 under 3% DD, evaluate 2023+ unchanged
cut=pd.Timestamp("2023-01-01",tz="UTC")
train=df[df.datetime<cut].reset_index(drop=True); test=df[df.datetime>=cut].reset_index(drop=True)
train_fixed=fixed_grid(train)
train_ok=train_fixed[train_fixed.maxdd_pct>=-3.0].sort_values("cagr_pct",ascending=False)
if len(train_ok):
    chosen=float(train_ok.iloc[0].allocation_pct)
    testm=run(test,pd.Series(chosen/100,index=test.index))
    print("\nTRAIN-SELECTED FIXED ALLOCATION")
    print({"chosen_allocation_pct":chosen,"train":train_ok.iloc[0].to_dict(),"test_2023_2026":testm})
    pd.DataFrame([{"chosen_allocation_pct":chosen,**{f"train_{k}":v for k,v in train_ok.iloc[0].to_dict().items()},**{f"test_{k}":v for k,v in testm.items()}}]).to_csv(OUT/"walkforward_fixed.csv",index=False)


# Walk-forward for predeclared volatility-target family.
train_vol=vol_grid(train)
train_vol_ok=train_vol[train_vol.maxdd_pct>=-3.0].sort_values(["cagr_pct","maxdd_pct"],ascending=[False,False])
if len(train_vol_ok):
    row=train_vol_ok.iloc[0]
    targ=float(row.target_ann_vol_pct)/100.0
    cap=float(row.cap_pct)/100.0
    rr=np.log(test.close/test.close.shift(1))
    annvol=rr.rolling(30).std(ddof=0)*np.sqrt(365)
    alloc=(targ/annvol).clip(lower=0,upper=cap).fillna(0)
    testm=run(test,alloc)
    print("\nTRAIN-SELECTED VOL TARGET")
    print({"target_ann_vol_pct":float(row.target_ann_vol_pct),"cap_pct":float(row.cap_pct),"train":row.to_dict(),"test_2023_2026":testm})
    pd.DataFrame([{"target_ann_vol_pct":float(row.target_ann_vol_pct),"cap_pct":float(row.cap_pct),**{f"train_{k}":v for k,v in row.to_dict().items()},**{f"test_{k}":v for k,v in testm.items()}}]).to_csv(OUT/"walkforward_vol.csv",index=False)
