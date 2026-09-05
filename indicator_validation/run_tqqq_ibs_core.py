#!/usr/bin/env python3
from pathlib import Path
import pandas as pd, numpy as np, yfinance as yf

OUT=Path("indicator_validation/output_tqqq_ibs_core"); OUT.mkdir(parents=True,exist_ok=True)
END="2026-09-03"; COSTS=[.0007,.0015,.0025]; NSIM=3000; BLOCKS=[5,20,60]; SEED=20260905
d=yf.download("TQQQ",start="2010-02-01",end=END,interval="1d",auto_adjust=True,progress=False,threads=False)
if isinstance(d.columns,pd.MultiIndex): d.columns=d.columns.get_level_values(0)
d=d.rename(columns=str.lower).reset_index(); d=d.rename(columns={d.columns[0]:"datetime"}); d["datetime"]=pd.to_datetime(d["datetime"],utc=True)
d=d[["datetime","open","high","low","close"]].dropna().drop_duplicates("datetime").set_index("datetime").sort_index()
ibs=(d.close-d.low)/(d.high-d.low).replace(0,np.nan)

def desired_state(skip_sep=False):
    st=np.zeros(len(d),bool); on=False
    for i,dt in enumerate(d.index):
        if on and ibs.iloc[i]>.95: on=False
        elif (not on) and ibs.iloc[i]<.14 and (not skip_sep or dt.month!=9): on=True
        st[i]=on
    return pd.Series(st,index=d.index)

def run(skip_sep,cost,start,end,scalar=1.0):
    x=d[(d.index>=start)&(d.index<end)].copy()
    fullstate=desired_state(skip_sep)
    # Next-session-open execution: today's target is yesterday's close state.
    tgt=fullstate.shift(1).reindex(x.index).fillna(False).astype(float)*scalar
    shares=0.0; cash=1.0; curve=[]; trades=0; entries=0; exits=0; prev_t=0.0
    for dt,row in x.iterrows():
        t=float(tgt.loc[dt]); eqo=cash+shares*row.open
        if not np.isclose(t,prev_t):
            desired=eqo*t; cur=shares*row.open; tr=desired-cur
            cash-=tr+abs(tr)*cost; shares+=tr/row.open; trades+=1
            if t>prev_t: entries+=1
            else: exits+=1
            prev_t=t
        curve.append(cash+shares*row.close)
    if len(x) and shares!=0:
        cash+=shares*x.close.iloc[-1]*(1-cost); shares=0; curve[-1]=cash; exits+=1; trades+=1
    e=pd.Series(curve,index=x.index)
    yrs=max((x.index[-1]-x.index[0]).total_seconds()/(365.25*86400),1/365.25)
    final=float(e.iloc[-1]); dd=float((e/e.cummax()-1).min()*100); cagr=(final**(1/yrs)-1)*100 if final>0 else -100
    return {"cagr_pct":cagr,"maxdd_pct":dd,"net_pct":(final-1)*100,"trades":trades,"entries":entries,"exits":exits},e

periods=[
 ("full",pd.Timestamp("2010-02-11",tz="UTC"),pd.Timestamp("2100-01-01",tz="UTC")),
 ("pre2020",pd.Timestamp("2010-02-11",tz="UTC"),pd.Timestamp("2020-01-01",tz="UTC")),
 ("claim_2020_jun2025",pd.Timestamp("2020-01-01",tz="UTC"),pd.Timestamp("2025-07-01",tz="UTC")),
 ("postclaim",pd.Timestamp("2025-07-01",tz="UTC"),pd.Timestamp("2100-01-01",tz="UTC")),
 ("holdout_2023",pd.Timestamp("2023-01-01",tz="UTC"),pd.Timestamp("2100-01-01",tz="UTC")),
]
rows=[]
for skip in [False,True]:
    for cost in COSTS:
        for name,a,b in periods:
            m,_=run(skip,cost,a,b,1.0); rows.append({"variant":"core_skip_sep" if skip else "core","cost_bps":cost*10000,"period":name,**m})
pd.DataFrame(rows).to_csv(OUT/"period_metrics.csv",index=False)

# Historical 3% DD scale, using full period and each cost.
front=[]
for skip in [False,True]:
  for cost in COSTS:
    cand=[]
    for sp in np.arange(.01,1.0001,.005):
      m,_=run(skip,cost,periods[0][1],periods[0][2],sp); cand.append({"scalar_pct":sp*100,**m})
    g=pd.DataFrame(cand); ok=g[g.maxdd_pct>=-3]
    if len(ok):
      w=ok.sort_values(["cagr_pct","maxdd_pct"],ascending=[False,False]).iloc[0]
      front.append({"variant":"core_skip_sep" if skip else "core","cost_bps":cost*10000,**w.to_dict()})
pd.DataFrame(front).to_csv(OUT/"historical_3pct_frontier.csv",index=False)

# Bootstrap 3% frontier on 7bp full-period equity, separately by variant.
boot=[]
for skip in [False,True]:
    # Generate equity curves over a scale grid; derive daily returns.
    scales=np.arange(.01,.501,.005)
    curves=[]; metrics=[]
    for sp in scales:
        m,e=run(skip,.0007,periods[0][1],periods[0][2],sp); curves.append(e.to_numpy()); metrics.append(m)
    arr=np.column_stack(curves); ret=np.ones_like(arr); ret[1:]=arr[1:]/arr[:-1]; n=len(arr)
    for block in BLOCKS:
        rng=np.random.default_rng(SEED+block+(100 if skip else 0)); nb=int(np.ceil(n/block)); starts=rng.integers(0,n,size=(NSIM,nb))
        B=((starts[:,:,None]+np.arange(block)[None,None,:])%n).reshape(NSIM,-1)[:,:n]
        for qi,sp in enumerate(scales):
            se=np.cumprod(ret[B,qi],axis=1); loss=-(se/np.maximum.accumulate(se,axis=1)-1).min(axis=1)*100
            boot.append({"variant":"core_skip_sep" if skip else "core","block_days":block,"scalar_pct":sp*100,
                         "cagr_pct":metrics[qi]["cagr_pct"],"historical_dd_pct":metrics[qi]["maxdd_pct"],
                         "p95_dd_pct":np.percentile(loss,95),"p99_dd_pct":np.percentile(loss,99)})
bg=pd.DataFrame(boot); bg.to_csv(OUT/"bootstrap_grid.csv",index=False)
bf=[]
for variant in bg.variant.unique():
  for block in BLOCKS:
    z=bg[(bg.variant==variant)&(bg.block_days==block)]
    for p in [95,99]:
      col=f"p{p}_dd_pct"; ok=z[z[col]<=3]
      if len(ok):
        w=ok.sort_values(["cagr_pct",col],ascending=[False,True]).iloc[0]
        bf.append({"variant":variant,"block_days":block,"confidence_pct":p,**w.to_dict()})
pd.DataFrame(bf).to_csv(OUT/"bootstrap_3pct_frontier.csv",index=False)

print("PERIOD METRICS"); print(pd.DataFrame(rows).to_string(index=False))
print("\nHISTORICAL <=3% FRONTIER"); print(pd.DataFrame(front).to_string(index=False))
print("\nBOOTSTRAP <=3% FRONTIER"); print(pd.DataFrame(bf).to_string(index=False))
