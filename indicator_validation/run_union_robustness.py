#!/usr/bin/env python3
from pathlib import Path
import pandas as pd, numpy as np, yfinance as yf

OUT=Path("indicator_validation/output_union_robustness"); OUT.mkdir(parents=True,exist_ok=True)
START=pd.Timestamp("2020-09-01",tz="UTC"); CUT=pd.Timestamp("2023-01-01",tz="UTC"); END="2026-09-03"
COSTS=[.0007,.0010,.0015,.0025,.0050]; BLOCKS=[5,20,60]; NSIM=3000; SEED=20260905
SCALARS=np.arange(.20,.8001,.005)
C12=["BTC-USD","ETH-USD","BNB-USD","SOL-USD","XRP-USD","ADA-USD","DOGE-USD","LTC-USD","BCH-USD","LINK-USD","DOT-USD","AVAX-USD"]
E11=["SPY","QQQ","IWM","GLD","SLV","IEF","TLT","DBC","VNQ","EFA","EEM"]; ALL=C12+E11
SW=.10

def dl(s):
    x=yf.download(s,start="2020-01-01",end=END,interval="1d",auto_adjust=True,progress=False,threads=False)
    if isinstance(x.columns,pd.MultiIndex): x.columns=x.columns.get_level_values(0)
    x=x.rename(columns=str.lower).reset_index(); x=x.rename(columns={x.columns[0]:"datetime"}); x["datetime"]=pd.to_datetime(x["datetime"],utc=True)
    return x[["datetime","open","close"]].dropna().drop_duplicates("datetime").set_index("datetime").sort_index()
raw={s:dl(s) for s in ALL}
DATES=pd.date_range(START,max(d.index.max() for d in raw.values()),freq="D",tz="UTC"); n=len(DATES); m=len(ALL); pos={d:i for i,d in enumerate(DATES)}
SESSION=np.zeros((n,m),bool); OPEN=np.full((n,m),np.nan); CLOSE=np.full((n,m),np.nan)
for j,s in enumerate(ALL):
    for dt,row in raw[s].loc[raw[s].index>=START].iterrows():
        if dt in pos:
            i=pos[dt]; SESSION[i,j]=True; OPEN[i,j]=float(row.open); CLOSE[i,j]=float(row.close)

def sentinel(c):
    ema=c.ewm(span=63,adjust=False).mean(); sd=c.rolling(63).std(ddof=0); z=(c-ema)/sd
    b=(z>.5).fillna(False).to_numpy(); x=(z<-.5).fillna(False).to_numpy(); st=np.zeros(len(c),bool); on=False
    for i in range(len(c)):
        if b[i]: on=True
        elif x[i]: on=False
        st[i]=on
    return pd.Series(st,index=c.index)
def rsi2(c):
    d=c.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0); au=up.ewm(alpha=.5,adjust=False,min_periods=2).mean(); ad=dn.ewm(alpha=.5,adjust=False,min_periods=2).mean()
    rs=au/ad.replace(0,np.nan); r=100-100/(1+rs); r[(ad==0)&(au>0)]=100; r[(au==0)&(ad>0)]=0; return r
def sm(en,ex):
    e=np.asarray(en.fillna(False),bool); x=np.asarray(ex.fillna(False),bool); st=np.zeros(len(e),bool); on=False
    for i in range(len(e)):
        if on and x[i]: on=False
        elif (not on) and e[i]: on=True
        st[i]=on
    return pd.Series(st,index=en.index)

TARGET=np.full((n,m),np.nan)
for j,s in enumerate(ALL):
    d=raw[s]; ss=(sentinel(d.close).astype(float)/len(ALL)).shift(1).fillna(0)
    if s in E11:
        rr=rsi2(d.close); rs=(sm((d.close>d.close.rolling(200).mean())&(rr<5),d.close>d.close.rolling(5).mean()).astype(float)/len(E11)).shift(1).fillna(0)
    else: rs=pd.Series(0.0,index=d.index)
    base=SW*ss+(1-SW)*rs
    for dt in d.index[d.index>=START]:
        if dt in pos: TARGET[pos[dt],j]=float(base.loc[dt])

def simulate(cost,start,end):
    mask=(DATES>=start)&(DATES<end); inds=np.where(mask)[0]; k=len(SCALARS)
    shares=np.zeros((k,m)); cash=np.ones(k); last_mark=np.full(m,np.nan); last_base=np.zeros(m); last_month=np.array([None]*m,dtype=object)
    peak=np.ones(k); mdd=np.zeros(k); turnover=np.zeros(k); curves=np.empty((len(inds),k))
    for j,s in enumerate(ALL):
        pre=raw[s][raw[s].index<start]
        if len(pre): last_mark[j]=float(pre.close.iloc[-1])
    for di,i in enumerate(inds):
        dt=DATES[i]; trad=SESSION[i]; om=last_mark.copy(); om[trad]=OPEN[i,trad]; valid=np.isfinite(om); eqo=cash+(shares[:,valid]*om[valid]).sum(1)
        base=last_base.copy(); base[trad]=np.nan_to_num(TARGET[i,trad],nan=0)
        event=trad&(~np.isclose(base,last_base,rtol=0,atol=1e-15)); mo=f"{dt.year}-{dt.month}"
        periodic=np.array([trad[j] and last_month[j]!=mo for j in range(m)]); do=event|periodic
        if np.any(do):
            des=eqo[:,None]*(SCALARS[:,None]*base[None,:]); cur=shares*om[None,:]; tr=np.zeros_like(shares); tr[:,do]=des[:,do]-cur[:,do]
            gross=np.abs(tr).sum(1); cash-=tr.sum(1)+gross*cost; shares[:,do]+=tr[:,do]/om[do]; turnover+=gross
        last_base[trad]=base[trad]
        for j in np.where(trad)[0]:
            if last_month[j]!=mo: last_month[j]=mo
        last_mark[trad]=CLOSE[i,trad]; valid=np.isfinite(last_mark); eq=cash+(shares[:,valid]*last_mark[valid]).sum(1)
        curves[di]=eq; peak=np.maximum(peak,eq); mdd=np.minimum(mdd,eq/peak-1)
    dates=DATES[inds]; yrs=max((dates[-1]-dates[0]).total_seconds()/(365.25*86400),1/365.25); final=curves[-1]
    cagr=np.where(final>0,(final**(1/yrs)-1)*100,-100)
    return pd.DataFrame({"scalar_pct":SCALARS*100,"cagr_pct":cagr,"maxdd_pct":mdd*100,"net_pct":(final-1)*100,"turnover_x":turnover}),dates,curves

# Cost sensitivity on frozen 71.5% scale and cost-specific <=3% historical frontier.
rows=[]; frozen=[]
for cost in COSTS:
    zt,dt,ct=simulate(cost,CUT,pd.Timestamp("2100-01-01",tz="UTC")); zf,df,cf=simulate(cost,START,pd.Timestamp("2100-01-01",tz="UTC"))
    for label,z in [("holdout",zt),("full",zf)]:
        q=int(np.argmin(np.abs(z.scalar_pct-71.5))); r=z.iloc[q]
        frozen.append({"cost_one_way_bps":cost*10000,"period":label,**r.to_dict()})
    ok=zf[zf.maxdd_pct>=-3]
    if len(ok):
        w=ok.sort_values(["cagr_pct","maxdd_pct"],ascending=[False,False]).iloc[0]
        rows.append({"cost_one_way_bps":cost*10000,**w.to_dict()})
pd.DataFrame(frozen).to_csv(OUT/"frozen_715_cost_sensitivity.csv",index=False)
pd.DataFrame(rows).to_csv(OUT/"full_historical_3pct_cost_frontier.csv",index=False)

# Bootstrap full-period daily returns for each scalar at base cost 7bp, using union calendar.
z,dates,curves=simulate(.0007,START,pd.Timestamp("2100-01-01",tz="UTC"))
rets=np.ones_like(curves); rets[1:]=curves[1:]/curves[:-1]
bootrows=[]
for block in BLOCKS:
    rng=np.random.default_rng(SEED+block); nb=int(np.ceil(len(dates)/block)); starts=rng.integers(0,len(dates),size=(NSIM,nb))
    B=((starts[:,:,None]+np.arange(block)[None,None,:])%len(dates)).reshape(NSIM,-1)[:,:len(dates)]
    for qi,sp in enumerate(z.scalar_pct):
        se=np.cumprod(rets[B,qi],axis=1); loss=-(se/np.maximum.accumulate(se,axis=1)-1).min(axis=1)*100
        bootrows.append({"block_days":block,"scalar_pct":sp,"cagr_pct":z.iloc[qi].cagr_pct,"historical_dd_pct":z.iloc[qi].maxdd_pct,
                         "p90_dd_pct":np.percentile(loss,90),"p95_dd_pct":np.percentile(loss,95),"p99_dd_pct":np.percentile(loss,99)})
bg=pd.DataFrame(bootrows); bg.to_csv(OUT/"bootstrap_grid.csv",index=False)
front=[]
for block in BLOCKS:
    zz=bg[bg.block_days==block]
    for pct in [95,99]:
        col=f"p{pct}_dd_pct"; ok=zz[zz[col]<=3]
        if len(ok):
            w=ok.sort_values(["cagr_pct",col],ascending=[False,True]).iloc[0]
            front.append({"block_days":block,"confidence_pct":pct,**w.to_dict()})
pd.DataFrame(front).to_csv(OUT/"bootstrap_3pct_frontier.csv",index=False)

print("FROZEN 71.5 COST SENSITIVITY"); print(pd.DataFrame(frozen).to_string(index=False))
print("\nFULL-HISTORICAL <=3% COST FRONTIER"); print(pd.DataFrame(rows).to_string(index=False))
print("\nBOOTSTRAP <=3% FRONTIER"); print(pd.DataFrame(front).to_string(index=False))
