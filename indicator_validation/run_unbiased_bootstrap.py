#!/usr/bin/env python3
from pathlib import Path
import pandas as pd, numpy as np, yfinance as yf

OUT=Path("indicator_validation/output_unbiased_bootstrap"); OUT.mkdir(parents=True,exist_ok=True)
END="2026-09-03"; START="2020-09-01"; LOOKBACK=63; THRESH=0.5; COST=0.0007
NSIM=1000; BLOCK=20; SEED=20260904
C12=["BTC-USD","ETH-USD","BNB-USD","SOL-USD","XRP-USD","ADA-USD","DOGE-USD","LTC-USD","BCH-USD","LINK-USD","DOT-USD","AVAX-USD"]
CASES={
 "ALL12_QQQ_GLD_equal":(C12+["QQQ","GLD"],"equal"),
 "ALL12_QQQ_GLD_invvol":(C12+["QQQ","GLD"],"invvol"),
 "ALL12_equal":(C12,"equal"),
}
def dl(sym):
    x=yf.download(sym,start="2020-01-01",end=END,interval="1d",auto_adjust=False,progress=False,threads=False)
    if isinstance(x.columns,pd.MultiIndex): x.columns=x.columns.get_level_values(0)
    x=x.rename(columns=str.lower).reset_index(); x=x.rename(columns={x.columns[0]:"datetime"})
    x["datetime"]=pd.to_datetime(x["datetime"],utc=True)
    return x[["datetime","open","close"]].dropna().drop_duplicates("datetime").set_index("datetime").sort_index()
raw={s:dl(s) for s in sorted({x for v,_ in CASES.values() for x in v})}
def signal(close):
    ema=close.ewm(span=LOOKBACK,adjust=False).mean(); sd=close.rolling(LOOKBACK).std(ddof=0); z=(close-ema)/sd
    bull=(z>THRESH).fillna(False).to_numpy(); bear=(z<-THRESH).fillna(False).to_numpy()
    st=np.zeros(len(close),bool); on=False
    for i in range(len(close)):
        if bull[i]: on=True
        elif bear[i]: on=False
        st[i]=on
    return pd.Series(st,index=close.index)
def panel(syms,kind):
    idx=None
    for s in syms: idx=raw[s].index if idx is None else idx.intersection(raw[s].index)
    idx=idx[idx>=pd.Timestamp(START,tz="UTC")]
    O=pd.DataFrame({s:raw[s].loc[idx,"open"] for s in syms},index=idx)
    C=pd.DataFrame({s:raw[s].loc[idx,"close"] for s in syms},index=idx)
    S=pd.DataFrame({s:signal(raw[s]["close"]).reindex(idx).fillna(False) for s in syms},index=idx)
    if kind=="equal":
        W=pd.DataFrame(np.full(C.shape,1/len(syms)),index=idx,columns=syms)
    else:
        rv=np.log(C/C.shift(1)).rolling(60).std(ddof=0)*np.sqrt(365); inv=1/rv.replace(0,np.nan)
        W=inv.div(inv.sum(axis=1),axis=0).fillna(0)
    return O,C,W*S.astype(float)
def equity(O,C,T,scalar):
    o=O.to_numpy(float); c=C.to_numpy(float); t=T.to_numpy(float); shares=np.zeros(o.shape[1]); cash=1.; curve=[]
    for i in range(len(O)):
        target=np.zeros(o.shape[1]) if i==0 else scalar*t[i-1]
        mtm=cash+np.dot(shares,o[i]); desired=mtm*target; current=shares*o[i]; trade=desired-current
        cash-=trade.sum()+np.abs(trade).sum()*COST; shares+=trade/o[i]; curve.append(cash+np.dot(shares,c[i]))
    vals=shares*c[-1]; cash+=vals.sum()-np.abs(vals).sum()*COST; curve[-1]=cash
    e=np.asarray(curve); yrs=(O.index[-1]-O.index[0]).total_seconds()/(365.25*86400)
    cagr=(e[-1]**(1/yrs)-1)*100; hdd=(e/np.maximum.accumulate(e)-1).min()*100
    r=np.ones(len(e)); r[1:]=e[1:]/e[:-1]
    return r,cagr,hdd
def bidx(n):
    rng=np.random.default_rng(SEED); nb=int(np.ceil(n/BLOCK)); starts=rng.integers(0,n,size=(NSIM,nb))
    return ((starts[:,:,None]+np.arange(BLOCK)[None,None,:])%n).reshape(NSIM,-1)[:,:n]
rows=[]
for name,(syms,kind) in CASES.items():
    O,C,T=panel(syms,kind); idx=bidx(len(O))
    for sp in range(1,101):
        r,cagr,hdd=equity(O,C,T,sp/100); sr=r[idx]; se=np.cumprod(sr,axis=1); peaks=np.maximum.accumulate(se,axis=1)
        loss=-(se/peaks-1).min(axis=1)*100
        rows.append({"case":name,"scalar_pct":sp,"cagr_pct":cagr,"historical_dd_pct":hdd,
                     "p50_dd_pct":float(np.percentile(loss,50)),"p90_dd_pct":float(np.percentile(loss,90)),
                     "p95_dd_pct":float(np.percentile(loss,95)),"p99_dd_pct":float(np.percentile(loss,99))})
g=pd.DataFrame(rows); g.to_csv(OUT/"unbiased_bootstrap_grid.csv",index=False)
out=[]
for ce in range(3,61):
    ok=g[g.p95_dd_pct<=ce]
    if len(ok):
        w=ok.sort_values(["cagr_pct","p95_dd_pct"],ascending=[False,True]).iloc[0]
        out.append({"dd_ceiling_pct":ce,**w.to_dict()})
f=pd.DataFrame(out); f.to_csv(OUT/"unbiased_bootstrap_p95_frontier.csv",index=False)
print("UNBIASED BOOTSTRAP P95 FRONTIER")
print(f.to_string(index=False))
