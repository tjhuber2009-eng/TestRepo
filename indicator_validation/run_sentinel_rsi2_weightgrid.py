#!/usr/bin/env python3
from pathlib import Path
import pandas as pd, numpy as np, yfinance as yf

OUT=Path("indicator_validation/output_sentinel_rsi2_weightgrid"); OUT.mkdir(parents=True,exist_ok=True)
END="2026-09-03"; START="2020-09-01"; COST=.0007
CEILINGS=list(range(3,31)); SCALARS=np.arange(.25,100.0001,.25)/100; SW=np.arange(0,1.0001,.05)
C12=["BTC-USD","ETH-USD","BNB-USD","SOL-USD","XRP-USD","ADA-USD","DOGE-USD","LTC-USD","BCH-USD","LINK-USD","DOT-USD","AVAX-USD"]
E11=["SPY","QQQ","IWM","GLD","SLV","IEF","TLT","DBC","VNQ","EFA","EEM"]; ALL=C12+E11
def dl(s):
 x=yf.download(s,start="2020-01-01",end=END,interval="1d",auto_adjust=False,progress=False,threads=False)
 if isinstance(x.columns,pd.MultiIndex): x.columns=x.columns.get_level_values(0)
 x=x.rename(columns=str.lower).reset_index(); x=x.rename(columns={x.columns[0]:"datetime"}); x["datetime"]=pd.to_datetime(x["datetime"],utc=True)
 return x[["datetime","open","close"]].dropna().drop_duplicates("datetime").set_index("datetime").sort_index()
raw={s:dl(s) for s in ALL}; idx=None
for s in ALL: idx=raw[s].index if idx is None else idx.intersection(raw[s].index)
idx=idx[idx>=pd.Timestamp(START,tz="UTC")]
O=pd.DataFrame({s:raw[s].loc[idx,"open"] for s in ALL},index=idx); C=pd.DataFrame({s:raw[s].loc[idx,"close"] for s in ALL},index=idx)
def sent(c):
 ema=c.ewm(span=63,adjust=False).mean(); sd=c.rolling(63).std(ddof=0); z=(c-ema)/sd
 b=(z>.5).fillna(False).to_numpy(); x=(z<-.5).fillna(False).to_numpy(); st=np.zeros(len(c),bool); on=False
 for i in range(len(c)):
  if b[i]:on=True
  elif x[i]:on=False
  st[i]=on
 return st
def rsi(c):
 d=c.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0); au=up.ewm(alpha=.5,adjust=False,min_periods=2).mean(); ad=dn.ewm(alpha=.5,adjust=False,min_periods=2).mean()
 rs=au/ad.replace(0,np.nan); r=100-100/(1+rs); r[(ad==0)&(au>0)]=100; r[(au==0)&(ad>0)]=0; return r
def sm(en,ex):
 e=np.asarray(en.fillna(False),bool); x=np.asarray(ex.fillna(False),bool); st=np.zeros(len(e),bool); on=False
 for i in range(len(e)):
  if on and x[i]:on=False
  elif (not on) and e[i]:on=True
  st[i]=on
 return st
Ts=np.zeros((len(idx),len(ALL))); Tr=np.zeros_like(Ts)
for j,s in enumerate(ALL):
 d=raw[s]; p=d.index.get_indexer(idx); Ts[:,j]=sent(d.close)[p]/len(ALL)
for s in E11:
 d=raw[s]; st=sm((d.close>d.close.rolling(200).mean())&(rsi(d.close)<5),d.close>d.close.rolling(5).mean()); p=d.index.get_indexer(idx)
 Tr[:,ALL.index(s)]=st[p]/len(E11)
def run_scalars(mask,T):
 o=O.loc[mask].to_numpy(float); c=C.loc[mask].to_numpy(float); t=T[mask]; k=len(SCALARS); m=o.shape[1]
 shares=np.zeros((k,m)); cash=np.ones(k); peak=np.ones(k); dd=np.zeros(k)
 for i in range(len(o)):
  base=np.zeros(m) if i==0 else t[i-1]; tar=SCALARS[:,None]*base
  mtm=cash+(shares*o[i]).sum(1); trade=mtm[:,None]*tar-shares*o[i]
  cash-=trade.sum(1)+np.abs(trade).sum(1)*COST; shares+=trade/o[i]
  eq=cash+(shares*c[i]).sum(1); peak=np.maximum(peak,eq); dd=np.minimum(dd,eq/peak-1)
 vals=shares*c[-1]; cash+=vals.sum(1)-np.abs(vals).sum(1)*COST; final=cash; peak=np.maximum(peak,final); dd=np.minimum(dd,final/peak-1)
 dates=O.loc[mask].index; yrs=(dates[-1]-dates[0]).total_seconds()/(365.25*86400); cagr=np.where(final>0,(final**(1/yrs)-1)*100,-100)
 return cagr,dd*100,(final-1)*100
def grid(mask):
 rows=[]
 for w in SW:
  T=w*Ts+(1-w)*Tr; cagr,dd,net=run_scalars(mask,T)
  for i,s in enumerate(SCALARS): rows.append({"sentinel_weight_pct":w*100,"rsi2_weight_pct":(1-w)*100,"scalar_pct":s*100,"cagr_pct":cagr[i],"maxdd_pct":dd[i],"net_pct":net[i]})
 return pd.DataFrame(rows)
def frontier(g):
 out=[]
 for ce in CEILINGS:
  ok=g[g.maxdd_pct>=-ce]
  if len(ok):
   w=ok.sort_values(["cagr_pct","maxdd_pct"],ascending=[False,False]).iloc[0]; out.append({"dd_ceiling_pct":ce,**w.to_dict()})
 return pd.DataFrame(out)
full=np.ones(len(idx),bool); fg=grid(full); ff=frontier(fg); fg.to_csv(OUT/"full_weight_grid.csv",index=False); ff.to_csv(OUT/"full_weight_frontier.csv",index=False)
cut=pd.Timestamp("2023-01-01",tz="UTC"); tr=np.asarray(idx<cut); te=np.asarray(idx>=cut); tg=grid(tr); tf=frontier(tg); wf=[]
for _,w in tf.iterrows():
 T=(float(w.sentinel_weight_pct)/100)*Ts+(float(w.rsi2_weight_pct)/100)*Tr
 # one scalar test via temporarily use general vector and pick exact scalar by direct runner logic
 old=SCALARS.copy()
 # reproduce using nearest scalar index
 cagr,dd,net=run_scalars(te,T); qi=int(np.argmin(np.abs(old-float(w.scalar_pct)/100)))
 wf.append({"dd_ceiling_pct":int(w.dd_ceiling_pct),"sentinel_weight_pct":w.sentinel_weight_pct,"rsi2_weight_pct":w.rsi2_weight_pct,"scalar_pct":w.scalar_pct,
            "train_cagr_pct":w.cagr_pct,"train_maxdd_pct":w.maxdd_pct,"test_cagr_pct":cagr[qi],"test_maxdd_pct":dd[qi],"test_net_pct":net[qi],"test_pass":dd[qi]>=-float(w.dd_ceiling_pct)})
wf=pd.DataFrame(wf); wf.to_csv(OUT/"weightgrid_walkforward.csv",index=False)
print("FULL");print(ff.to_string(index=False));print("\nWALKFORWARD");print(wf.to_string(index=False))
