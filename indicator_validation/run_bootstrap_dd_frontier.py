#!/usr/bin/env python3
from pathlib import Path
import pandas as pd, numpy as np, yfinance as yf

OUT=Path("indicator_validation/output_bootstrap_dd_frontier"); OUT.mkdir(parents=True,exist_ok=True)
END="2026-09-03"; START="2020-09-01"; LOOKBACK=63; THRESH=0.5; COST=0.0007
C8=["BTC-USD","ETH-USD","BNB-USD","SOL-USD","ADA-USD","DOGE-USD","DOT-USD","AVAX-USD"]
PORTS={"MIX10":C8+["QQQ","GLD"],"CRYPTO8":C8}
NSIM=1000; BLOCK=20; SEED=20260904

def dl(sym):
    x=yf.download(sym,start="2020-01-01",end=END,interval="1d",auto_adjust=False,progress=False,threads=False)
    if isinstance(x.columns,pd.MultiIndex): x.columns=x.columns.get_level_values(0)
    x=x.rename(columns=str.lower).reset_index(); x=x.rename(columns={x.columns[0]:"datetime"})
    x["datetime"]=pd.to_datetime(x["datetime"],utc=True)
    return x[["datetime","open","close"]].dropna().drop_duplicates("datetime").set_index("datetime").sort_index()
raw={s:dl(s) for s in sorted(set(sum(PORTS.values(),[])))}

def signal(close):
    ema=close.ewm(span=LOOKBACK,adjust=False).mean(); sd=close.rolling(LOOKBACK).std(ddof=0); z=(close-ema)/sd
    bull=(z>THRESH).fillna(False).to_numpy(); bear=(z<-THRESH).fillna(False).to_numpy()
    st=np.zeros(len(close),bool); on=False
    for i in range(len(close)):
        if bull[i]: on=True
        elif bear[i]: on=False
        st[i]=on
    return pd.Series(st,index=close.index)

def panel(syms):
    idx=None
    for s in syms: idx=raw[s].index if idx is None else idx.intersection(raw[s].index)
    idx=idx[idx>=pd.Timestamp(START,tz="UTC")]
    O=pd.DataFrame({s:raw[s].loc[idx,"open"] for s in syms},index=idx)
    C=pd.DataFrame({s:raw[s].loc[idx,"close"] for s in syms},index=idx)
    S=pd.DataFrame({s:signal(raw[s]["close"]).reindex(idx).fillna(False) for s in syms},index=idx)
    W=pd.DataFrame(np.full(C.shape,1/len(syms)),index=idx,columns=syms)
    T=W*S.astype(float)
    return O,C,T

def equity(O,C,T,scalar):
    o=O.to_numpy(float); c=C.to_numpy(float); t=T.to_numpy(float)
    m=o.shape[1]; shares=np.zeros(m); cash=1.0; curve=[]
    for i in range(len(O)):
        target=np.zeros(m) if i==0 else scalar*t[i-1]
        mtm_open=cash+np.dot(shares,o[i]); desired=mtm_open*target; current=shares*o[i]; trade=desired-current
        cash-=trade.sum()+np.abs(trade).sum()*COST; shares+=trade/o[i]
        curve.append(cash+np.dot(shares,c[i]))
    vals=shares*c[-1]; cash+=vals.sum()-np.abs(vals).sum()*COST; curve[-1]=cash
    e=np.asarray(curve,float)
    years=(O.index[-1]-O.index[0]).total_seconds()/(365.25*86400)
    dd=np.min(e/np.maximum.accumulate(e)-1)*100
    cagr=(e[-1]**(1/years)-1)*100
    r=np.ones(len(e)); r[1:]=e[1:]/e[:-1]
    return e,r,cagr,dd

def bootstrap_indices(n):
    rng=np.random.default_rng(SEED); nb=int(np.ceil(n/BLOCK))
    starts=rng.integers(0,n,size=(NSIM,nb))
    offsets=np.arange(BLOCK)[None,None,:]
    idx=(starts[:,:,None]+offsets)%n
    return idx.reshape(NSIM,-1)[:,:n]

rows=[]
for pname,syms in PORTS.items():
    O,C,T=panel(syms); idx=bootstrap_indices(len(O))
    for scalar_pct in range(1,101):
        e,r,cagr,hdd=equity(O,C,T,scalar_pct/100)
        sampled=r[idx]
        sim_eq=np.cumprod(sampled,axis=1)
        peaks=np.maximum.accumulate(sim_eq,axis=1)
        mdd=(sim_eq/peaks-1).min(axis=1)*100
        rows.append({"portfolio":pname,"scalar_pct":scalar_pct,"cagr_pct":cagr,"historical_dd_pct":hdd,
                     "bootstrap_dd_p50_pct":-float(np.percentile(mdd,50)),
                     "bootstrap_dd_p90_pct":-float(np.percentile(mdd,90)),
                     "bootstrap_dd_p95_pct":-float(np.percentile(mdd,95)),
                     "bootstrap_dd_p99_pct":-float(np.percentile(mdd,99))})
g=pd.DataFrame(rows); g.to_csv(OUT/"bootstrap_candidate_grid.csv",index=False)
front=[]
for ceiling in range(3,61):
    ok=g[g.bootstrap_dd_p95_pct<=ceiling]
    if len(ok):
        w=ok.sort_values(["cagr_pct","bootstrap_dd_p95_pct"],ascending=[False,True]).iloc[0]
        front.append({"dd_ceiling_pct":ceiling,**w.to_dict()})
f=pd.DataFrame(front); f.to_csv(OUT/"bootstrap_p95_frontier.csv",index=False)
print("BOOTSTRAP P95 DD FRONTIER")
print(f.to_string(index=False))
