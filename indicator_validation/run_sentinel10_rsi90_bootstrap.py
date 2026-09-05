#!/usr/bin/env python3
from pathlib import Path
import pandas as pd, numpy as np, yfinance as yf
OUT=Path("indicator_validation/output_sentinel10_rsi90_bootstrap"); OUT.mkdir(parents=True,exist_ok=True)
END="2026-09-03"; START="2020-09-01"; COST=.0007; NSIM=3000; BLOCK=20; SEED=20260905
C12=["BTC-USD","ETH-USD","BNB-USD","SOL-USD","XRP-USD","ADA-USD","DOGE-USD","LTC-USD","BCH-USD","LINK-USD","DOT-USD","AVAX-USD"]
E11=["SPY","QQQ","IWM","GLD","SLV","IEF","TLT","DBC","VNQ","EFA","EEM"]; ALL=C12+E11
def dl(s):
 x=yf.download(s,start="2020-01-01",end=END,interval="1d",auto_adjust=False,progress=False,threads=False)
 if isinstance(x.columns,pd.MultiIndex):x.columns=x.columns.get_level_values(0)
 x=x.rename(columns=str.lower).reset_index();x=x.rename(columns={x.columns[0]:"datetime"});x["datetime"]=pd.to_datetime(x["datetime"],utc=True)
 return x[["datetime","open","close"]].dropna().drop_duplicates("datetime").set_index("datetime").sort_index()
raw={s:dl(s) for s in ALL};idx=None
for s in ALL:idx=raw[s].index if idx is None else idx.intersection(raw[s].index)
idx=idx[idx>=pd.Timestamp(START,tz="UTC")];O=pd.DataFrame({s:raw[s].loc[idx,"open"] for s in ALL},index=idx);C=pd.DataFrame({s:raw[s].loc[idx,"close"] for s in ALL},index=idx)
def sent(c):
 ema=c.ewm(span=63,adjust=False).mean();sd=c.rolling(63).std(ddof=0);z=(c-ema)/sd;b=(z>.5).fillna(False).to_numpy();x=(z<-.5).fillna(False).to_numpy();st=np.zeros(len(c),bool);on=False
 for i in range(len(c)):
  if b[i]:on=True
  elif x[i]:on=False
  st[i]=on
 return st
def rsi(c):
 d=c.diff();up=d.clip(lower=0);dn=-d.clip(upper=0);au=up.ewm(alpha=.5,adjust=False,min_periods=2).mean();ad=dn.ewm(alpha=.5,adjust=False,min_periods=2).mean();rs=au/ad.replace(0,np.nan);r=100-100/(1+rs);r[(ad==0)&(au>0)]=100;r[(au==0)&(ad>0)]=0;return r
def sm(en,ex):
 e=np.asarray(en.fillna(False),bool);x=np.asarray(ex.fillna(False),bool);st=np.zeros(len(e),bool);on=False
 for i in range(len(e)):
  if on and x[i]:on=False
  elif (not on) and e[i]:on=True
  st[i]=on
 return st
Ts=np.zeros((len(idx),len(ALL)));Tr=np.zeros_like(Ts)
for j,s in enumerate(ALL):
 d=raw[s];p=d.index.get_indexer(idx);Ts[:,j]=sent(d.close)[p]/len(ALL)
for s in E11:
 d=raw[s];st=sm((d.close>d.close.rolling(200).mean())&(rsi(d.close)<5),d.close>d.close.rolling(5).mean());p=d.index.get_indexer(idx);Tr[:,ALL.index(s)]=st[p]/len(E11)
T=.10*Ts+.90*Tr
def equity(sc):
 o=O.to_numpy(float);c=C.to_numpy(float);shares=np.zeros(o.shape[1]);cash=1.;curve=[]
 for i in range(len(o)):
  target=np.zeros(o.shape[1]) if i==0 else sc*T[i-1];mtm=cash+np.dot(shares,o[i]);trade=mtm*target-shares*o[i]
  cash-=trade.sum()+np.abs(trade).sum()*COST;shares+=trade/o[i];curve.append(cash+np.dot(shares,c[i]))
 vals=shares*c[-1];cash+=vals.sum()-np.abs(vals).sum()*COST;curve[-1]=cash;e=np.asarray(curve);yrs=(idx[-1]-idx[0]).total_seconds()/(365.25*86400);ret=np.ones(len(e));ret[1:]=e[1:]/e[:-1]
 return ret,(e[-1]**(1/yrs)-1)*100,(e/np.maximum.accumulate(e)-1).min()*100
rng=np.random.default_rng(SEED);n=len(idx);nb=int(np.ceil(n/BLOCK));starts=rng.integers(0,n,size=(NSIM,nb));B=((starts[:,:,None]+np.arange(BLOCK)[None,None,:])%n).reshape(NSIM,-1)[:,:n]
rows=[]
for sp in np.arange(1,100.0001,.5):
 ret,cagr,hdd=equity(sp/100);se=np.cumprod(ret[B],axis=1);loss=-(se/np.maximum.accumulate(se,axis=1)-1).min(axis=1)*100
 rows.append({"scalar_pct":sp,"cagr_pct":cagr,"historical_dd_pct":hdd,"p50_dd_pct":np.percentile(loss,50),"p90_dd_pct":np.percentile(loss,90),"p95_dd_pct":np.percentile(loss,95),"p99_dd_pct":np.percentile(loss,99)})
g=pd.DataFrame(rows);g.to_csv(OUT/"sentinel10_rsi90_bootstrap_grid.csv",index=False)
for p in [95,99]:
 col=f"p{p}_dd_pct";out=[]
 for ce in range(3,16):
  ok=g[g[col]<=ce]
  if len(ok):
   w=ok.sort_values(["cagr_pct",col],ascending=[False,True]).iloc[0];out.append({"dd_ceiling_pct":ce,**w.to_dict()})
 f=pd.DataFrame(out);f.to_csv(OUT/f"sentinel10_rsi90_p{p}_frontier.csv",index=False);print(f"P{p}");print(f.to_string(index=False))
