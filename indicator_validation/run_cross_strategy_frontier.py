#!/usr/bin/env python3
from pathlib import Path
import itertools
import pandas as pd, numpy as np, yfinance as yf

OUT=Path("indicator_validation/output_cross_strategy_frontier"); OUT.mkdir(parents=True,exist_ok=True)
END="2026-09-03"; START="2020-09-01"; COST=0.0007
CEILINGS=list(range(3,61)); SCALARS=np.arange(0.25,100.0001,0.25)/100
C12=["BTC-USD","ETH-USD","BNB-USD","SOL-USD","XRP-USD","ADA-USD","DOGE-USD","LTC-USD","BCH-USD","LINK-USD","DOT-USD","AVAX-USD"]
E11=["SPY","QQQ","IWM","GLD","SLV","IEF","TLT","DBC","VNQ","EFA","EEM"]
ALL=C12+E11
SLEEVES=["sentinel","rsi2","double7","ibs"]

def dl(sym):
    x=yf.download(sym,start="2020-01-01",end=END,interval="1d",auto_adjust=False,progress=False,threads=False)
    if isinstance(x.columns,pd.MultiIndex): x.columns=x.columns.get_level_values(0)
    x=x.rename(columns=str.lower).reset_index(); x=x.rename(columns={x.columns[0]:"datetime"})
    x["datetime"]=pd.to_datetime(x["datetime"],utc=True)
    return x[["datetime","open","high","low","close"]].dropna().drop_duplicates("datetime").set_index("datetime").sort_index()
raw={s:dl(s) for s in ALL}
idx=None
for s in ALL: idx=raw[s].index if idx is None else idx.intersection(raw[s].index)
idx=idx[idx>=pd.Timestamp(START,tz="UTC")]
O=pd.DataFrame({s:raw[s].loc[idx,"open"] for s in ALL},index=idx)
H=pd.DataFrame({s:raw[s].loc[idx,"high"] for s in ALL},index=idx)
L=pd.DataFrame({s:raw[s].loc[idx,"low"] for s in ALL},index=idx)
C=pd.DataFrame({s:raw[s].loc[idx,"close"] for s in ALL},index=idx)

def sentinel_state(close):
    ema=close.ewm(span=63,adjust=False).mean(); sd=close.rolling(63).std(ddof=0); z=(close-ema)/sd
    bull=(z>.5).fillna(False).to_numpy(); bear=(z<-.5).fillna(False).to_numpy()
    st=np.zeros(len(close),bool); on=False
    for i in range(len(close)):
        if bull[i]: on=True
        elif bear[i]: on=False
        st[i]=on
    return st

def rsi_wilder(close,n=2):
    d=close.diff(); up=d.clip(lower=0); dn=(-d.clip(upper=0))
    au=up.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    ad=dn.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
    rs=au/ad.replace(0,np.nan)
    r=100-100/(1+rs)
    r[(ad==0)&(au>0)]=100; r[(au==0)&(ad>0)]=0
    return r

def state_machine(entry,exit_):
    e=np.asarray(entry.fillna(False),bool); x=np.asarray(exit_.fillna(False),bool)
    st=np.zeros(len(e),bool); on=False
    for i in range(len(e)):
        if on and x[i]: on=False
        elif (not on) and e[i]: on=True
        st[i]=on
    return st

# One target matrix per strategy sleeve; static equal capital across the complete eligible universe.
targets={}
T=np.zeros((len(idx),len(ALL)))
for j,s in enumerate(ALL): T[:,j]=sentinel_state(raw[s]["close"]).astype(float)[raw[s].index.get_indexer(idx)]
T/=len(ALL); targets["sentinel"]=T

for sleeve in ["rsi2","double7","ibs"]:
    T=np.zeros((len(idx),len(ALL)))
    for s in E11:
        d=raw[s]; close=d["close"]; sma200=close.rolling(200).mean()
        if sleeve=="rsi2":
            r=rsi_wilder(close,2); sma5=close.rolling(5).mean()
            entry=(close>sma200)&(r<5); exit_=(close>sma5)
        elif sleeve=="double7":
            lo7=close.rolling(7).min(); hi7=close.rolling(7).max()
            entry=(close>sma200)&(close<=lo7); exit_=(close>=hi7)
        else:
            denom=(d["high"]-d["low"]).replace(0,np.nan); ibs=(close-d["low"])/denom
            entry=ibs<.2; exit_=ibs>.8
        st=state_machine(entry,exit_)
        pos=d.index.get_indexer(idx); T[:,ALL.index(s)]=st[pos].astype(float)/len(E11)
    targets[sleeve]=T

def run_scalars(Oarr,Carr,T,scalars):
    k=len(scalars); m=Oarr.shape[1]; shares=np.zeros((k,m)); cash=np.ones(k); peak=np.ones(k); mdd=np.zeros(k); turnover=np.zeros(k)
    for i in range(len(Oarr)):
        base=np.zeros(m) if i==0 else T[i-1]; target=scalars[:,None]*base[None,:]
        mtm=cash+(shares*Oarr[i]).sum(1); desired=mtm[:,None]*target; current=shares*Oarr[i]; trade=desired-current
        cash-=trade.sum(1)+np.abs(trade).sum(1)*COST; shares+=trade/Oarr[i]; turnover+=np.abs(trade).sum(1)
        eq=cash+(shares*Carr[i]).sum(1); peak=np.maximum(peak,eq); mdd=np.minimum(mdd,eq/peak-1)
    vals=shares*Carr[-1]; cash+=vals.sum(1)-np.abs(vals).sum(1)*COST; final=cash
    peak=np.maximum(peak,final); mdd=np.minimum(mdd,final/peak-1)
    return final,mdd,turnover

def grid_for(mask):
    Om=O.loc[mask].to_numpy(float); Cm=C.loc[mask].to_numpy(float); dates=O.loc[mask].index
    rows=[]
    for r in range(1,len(SLEEVES)+1):
      for combo in itertools.combinations(SLEEVES,r):
        T=sum(targets[x][mask] for x in combo)/len(combo)
        final,mdd,turn=run_scalars(Om,Cm,T,SCALARS)
        yrs=max((dates[-1]-dates[0]).total_seconds()/(365.25*86400),1/365.25)
        cagr=np.where(final>0,(final**(1/yrs)-1)*100,-100)
        for q in range(len(SCALARS)):
            rows.append({"combo":"+".join(combo),"n_sleeves":len(combo),"scalar_pct":SCALARS[q]*100,
                         "cagr_pct":cagr[q],"maxdd_pct":mdd[q]*100,"net_pct":(final[q]-1)*100,"turnover_x":turn[q]})
    return pd.DataFrame(rows)

full_mask=np.ones(len(idx),dtype=bool)
g=grid_for(full_mask); g.to_csv(OUT/"cross_strategy_grid.csv",index=False)
def frontier(g):
    out=[]
    for ce in CEILINGS:
        ok=g[g.maxdd_pct>=-ce]
        if len(ok):
            w=ok.sort_values(["cagr_pct","maxdd_pct"],ascending=[False,False]).iloc[0]; out.append({"dd_ceiling_pct":ce,**w.to_dict()})
    return pd.DataFrame(out)
f=frontier(g); f.to_csv(OUT/"cross_strategy_full_frontier.csv",index=False)

cut=pd.Timestamp("2023-01-01",tz="UTC"); trainmask=np.asarray(idx<cut); testmask=np.asarray(idx>=cut)
tg=grid_for(trainmask); tf=frontier(tg); wf=[]
for _,w in tf.iterrows():
    combo=w.combo.split("+"); T=sum(targets[x][testmask] for x in combo)/len(combo)
    final,mdd,turn=run_scalars(O.loc[testmask].to_numpy(float),C.loc[testmask].to_numpy(float),T,np.array([float(w.scalar_pct)/100]))
    dates=O.loc[testmask].index; yrs=(dates[-1]-dates[0]).total_seconds()/(365.25*86400); cagr=(final[0]**(1/yrs)-1)*100
    wf.append({"dd_ceiling_pct":int(w.dd_ceiling_pct),"combo":w.combo,"scalar_pct":w.scalar_pct,
               "train_cagr_pct":w.cagr_pct,"train_maxdd_pct":w.maxdd_pct,
               "test_cagr_pct":cagr,"test_maxdd_pct":mdd[0]*100,"test_net_pct":(final[0]-1)*100,
               "test_pass_ceiling":mdd[0]*100>=-float(w.dd_ceiling_pct)})
wf=pd.DataFrame(wf); wf.to_csv(OUT/"cross_strategy_walkforward.csv",index=False)

# Individual sleeve diagnostics at 100% scalar
diag=[]
for sleeve in SLEEVES:
    z=g[(g.combo==sleeve)&(np.isclose(g.scalar_pct,100))].iloc[0]
    diag.append(z.to_dict())
pd.DataFrame(diag).to_csv(OUT/"sleeve_diagnostics.csv",index=False)

print("INDIVIDUAL SLEEVES"); print(pd.DataFrame(diag).to_string(index=False))
print("\nCROSS-STRATEGY FRONTIER"); print(f[["dd_ceiling_pct","combo","scalar_pct","cagr_pct","maxdd_pct","net_pct"]].to_string(index=False))
print("\nWALKFORWARD"); print(wf.to_string(index=False))
