#!/usr/bin/env python3
from pathlib import Path
import pandas as pd, numpy as np, yfinance as yf

OUT=Path("indicator_validation/output_sentinel_rsi2_bootstrap"); OUT.mkdir(parents=True,exist_ok=True)
END="2026-09-03"; START="2020-09-01"; COST=0.0007; NSIM=2000; BLOCK=20; SEED=20260905
C12=["BTC-USD","ETH-USD","BNB-USD","SOL-USD","XRP-USD","ADA-USD","DOGE-USD","LTC-USD","BCH-USD","LINK-USD","DOT-USD","AVAX-USD"]
E11=["SPY","QQQ","IWM","GLD","SLV","IEF","TLT","DBC","VNQ","EFA","EEM"]; ALL=C12+E11
def dl(sym):
    x=yf.download(sym,start="2020-01-01",end=END,interval="1d",auto_adjust=False,progress=False,threads=False)
    if isinstance(x.columns,pd.MultiIndex): x.columns=x.columns.get_level_values(0)
    x=x.rename(columns=str.lower).reset_index(); x=x.rename(columns={x.columns[0]:"datetime"})
    x["datetime"]=pd.to_datetime(x["datetime"],utc=True)
    return x[["datetime","open","close"]].dropna().drop_duplicates("datetime").set_index("datetime").sort_index()
raw={s:dl(s) for s in ALL}; idx=None
for s in ALL: idx=raw[s].index if idx is None else idx.intersection(raw[s].index)
idx=idx[idx>=pd.Timestamp(START,tz="UTC")]
O=pd.DataFrame({s:raw[s].loc[idx,"open"] for s in ALL},index=idx); C=pd.DataFrame({s:raw[s].loc[idx,"close"] for s in ALL},index=idx)
def sentinel(close):
    ema=close.ewm(span=63,adjust=False).mean(); sd=close.rolling(63).std(ddof=0); z=(close-ema)/sd
    b=(z>.5).fillna(False).to_numpy(); x=(z<-.5).fillna(False).to_numpy(); st=np.zeros(len(close),bool); on=False
    for i in range(len(close)):
        if b[i]: on=True
        elif x[i]: on=False
        st[i]=on
    return st
def rsi(close,n=2):
    d=close.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0)
    au=up.ewm(alpha=1/n,adjust=False,min_periods=n).mean(); ad=dn.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    rs=au/ad.replace(0,np.nan); r=100-100/(1+rs); r[(ad==0)&(au>0)]=100; r[(au==0)&(ad>0)]=0; return r
def sm(entry,exit_):
    e=np.asarray(entry.fillna(False),bool); x=np.asarray(exit_.fillna(False),bool); st=np.zeros(len(e),bool); on=False
    for i in range(len(e)):
        if on and x[i]: on=False
        elif (not on) and e[i]: on=True
        st[i]=on
    return st
Ts=np.zeros((len(idx),len(ALL)))
for j,s in enumerate(ALL):
    d=raw[s]; pos=d.index.get_indexer(idx); Ts[:,j]=sentinel(d.close)[pos].astype(float)/len(ALL)
Tr=np.zeros_like(Ts)
for s in E11:
    d=raw[s]; sma200=d.close.rolling(200).mean(); sma5=d.close.rolling(5).mean(); rr=rsi(d.close,2)
    st=sm((d.close>sma200)&(rr<5),d.close>sma5); pos=d.index.get_indexer(idx); Tr[:,ALL.index(s)]=st[pos].astype(float)/len(E11)
T=(Ts+Tr)/2
def equity(scalar):
    o=O.to_numpy(float); c=C.to_numpy(float); shares=np.zeros(o.shape[1]); cash=1.; curve=[]
    for i in range(len(O)):
        target=np.zeros(o.shape[1]) if i==0 else scalar*T[i-1]; mtm=cash+np.dot(shares,o[i]); desired=mtm*target
        trade=desired-shares*o[i]; cash-=trade.sum()+np.abs(trade).sum()*COST; shares+=trade/o[i]; curve.append(cash+np.dot(shares,c[i]))
    vals=shares*c[-1]; cash+=vals.sum()-np.abs(vals).sum()*COST; curve[-1]=cash
    e=np.asarray(curve); yrs=(idx[-1]-idx[0]).total_seconds()/(365.25*86400); r=np.ones(len(e)); r[1:]=e[1:]/e[:-1]
    return r,(e[-1]**(1/yrs)-1)*100,(e/np.maximum.accumulate(e)-1).min()*100
rng=np.random.default_rng(SEED); n=len(idx); nb=int(np.ceil(n/BLOCK)); starts=rng.integers(0,n,size=(NSIM,nb))
B=((starts[:,:,None]+np.arange(BLOCK)[None,None,:])%n).reshape(NSIM,-1)[:,:n]
rows=[]
for sp in range(1,101):
    ret,cagr,hdd=equity(sp/100); se=np.cumprod(ret[B],axis=1); loss=-(se/np.maximum.accumulate(se,axis=1)-1).min(axis=1)*100
    rows.append({"scalar_pct":sp,"cagr_pct":cagr,"historical_dd_pct":hdd,
                 "p50_dd_pct":float(np.percentile(loss,50)),"p90_dd_pct":float(np.percentile(loss,90)),
                 "p95_dd_pct":float(np.percentile(loss,95)),"p99_dd_pct":float(np.percentile(loss,99))})
g=pd.DataFrame(rows); g.to_csv(OUT/"sentinel_rsi2_bootstrap_grid.csv",index=False)
for pct in [95,99]:
    col=f"p{pct}_dd_pct"; out=[]
    for ce in range(3,31):
        ok=g[g[col]<=ce]
        if len(ok):
            w=ok.sort_values(["cagr_pct",col],ascending=[False,True]).iloc[0]; out.append({"dd_ceiling_pct":ce,**w.to_dict()})
    f=pd.DataFrame(out); f.to_csv(OUT/f"sentinel_rsi2_p{pct}_frontier.csv",index=False)
    print(f"P{pct} FRONTIER"); print(f.to_string(index=False))
