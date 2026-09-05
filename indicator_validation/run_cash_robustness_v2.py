#!/usr/bin/env python3
from pathlib import Path
import pandas as pd, numpy as np, yfinance as yf

OUT=Path("indicator_validation/output_cash_robustness_v2"); OUT.mkdir(parents=True,exist_ok=True)
START=pd.Timestamp("2020-09-01",tz="UTC"); CUT=pd.Timestamp("2023-01-01",tz="UTC"); END="2026-09-03"
BASE_COST=.0007; COSTS=[.0007,.0010,.0015,.0025]; HAIRCUTS=[0,.25,.50,.75,1.0]
PROXIES=["SGOV","BIL","SHV"]; BLOCKS=[5,20,60]; NSIM=1000; SEED=20260905
FROZEN_SW=.20; FROZEN_SCALAR=.525
GRID_SCALARS=np.arange(.10,.8001,.005)
BOOT_SCALARS=np.arange(.10,.6501,.015)
C12=["BTC-USD","ETH-USD","BNB-USD","SOL-USD","XRP-USD","ADA-USD","DOGE-USD","LTC-USD","BCH-USD","LINK-USD","DOT-USD","AVAX-USD"]
E11=["SPY","QQQ","IWM","GLD","SLV","IEF","TLT","DBC","VNQ","EFA","EEM"]; ALL=C12+E11

def dl(s):
    x=yf.download(s,start="2020-01-01",end=END,interval="1d",auto_adjust=True,progress=False,threads=False)
    if isinstance(x.columns,pd.MultiIndex): x.columns=x.columns.get_level_values(0)
    x=x.rename(columns=str.lower).reset_index(); x=x.rename(columns={x.columns[0]:"datetime"})
    x["datetime"]=pd.to_datetime(x["datetime"],utc=True)
    return x[["datetime","open","close"]].dropna().drop_duplicates("datetime").set_index("datetime").sort_index()

raw={s:dl(s) for s in ALL}
cashraw={s:dl(s) for s in PROXIES}
DATES=pd.date_range(START,max(d.index.max() for d in raw.values()),freq="D",tz="UTC"); n=len(DATES); m=len(ALL); pos={d:i for i,d in enumerate(DATES)}
SESSION=np.zeros((n,m),bool); OPEN=np.full((n,m),np.nan); CLOSE=np.full((n,m),np.nan)
for j,s in enumerate(ALL):
    for dt,row in raw[s].loc[raw[s].index>=START].iterrows():
        if dt in pos:
            i=pos[dt]; SESSION[i,j]=True; OPEN[i,j]=float(row.open); CLOSE[i,j]=float(row.close)

RF={}
for p,d in cashraw.items():
    ret=d.close.pct_change().fillna(0)
    arr=np.zeros(n)
    for dt,r in ret.items():
        if dt in pos and dt>=START: arr[pos[dt]]=float(r)
    RF[p]=arr

def sentinel(c):
    ema=c.ewm(span=63,adjust=False).mean(); sd=c.rolling(63).std(ddof=0); z=(c-ema)/sd
    b=(z>.5).fillna(False).to_numpy(); x=(z<-.5).fillna(False).to_numpy()
    st=np.zeros(len(c),bool); on=False
    for i in range(len(c)):
        if b[i]: on=True
        elif x[i]: on=False
        st[i]=on
    return pd.Series(st,index=c.index)

def rsi2(c):
    z=c.diff(); up=z.clip(lower=0); dn=-z.clip(upper=0)
    au=up.ewm(alpha=.5,adjust=False,min_periods=2).mean(); ad=dn.ewm(alpha=.5,adjust=False,min_periods=2).mean()
    rs=au/ad.replace(0,np.nan); r=100-100/(1+rs)
    r[(ad==0)&(au>0)]=100; r[(au==0)&(ad>0)]=0
    return r

def sm(en,ex):
    e=np.asarray(en.fillna(False),bool); x=np.asarray(ex.fillna(False),bool)
    st=np.zeros(len(e),bool); on=False
    for i in range(len(e)):
        if on and x[i]: on=False
        elif (not on) and e[i]: on=True
        st[i]=on
    return pd.Series(st,index=en.index)

TS=np.full((n,m),np.nan); TR=np.full((n,m),np.nan)
for j,s in enumerate(ALL):
    d=raw[s]
    ss=(sentinel(d.close).astype(float)/len(ALL)).shift(1).fillna(0)
    if s in E11:
        rr=rsi2(d.close)
        rs=(sm((d.close>d.close.rolling(200).mean())&(rr<5),d.close>d.close.rolling(5).mean()).astype(float)/len(E11)).shift(1).fillna(0)
    else:
        rs=pd.Series(0.0,index=d.index)
    for dt in d.index[d.index>=START]:
        if dt in pos:
            i=pos[dt]; TS[i,j]=float(ss.loc[dt]); TR[i,j]=float(rs.loc[dt])

def simulate(sw,scales,start,end,cost,proxy,haircut,return_curve=False):
    scales=np.asarray(scales,float); k=len(scales)
    inds=np.where((DATES>=start)&(DATES<end))[0]
    shares=np.zeros((k,m)); cash=np.ones(k); last_mark=np.full(m,np.nan); last_base=np.zeros(m)
    last_month=np.array([None]*m,dtype=object)
    peak=np.ones(k); mdd=np.zeros(k); turnover=np.zeros(k); curves=np.empty((len(inds),k))
    for j,s in enumerate(ALL):
        pre=raw[s][raw[s].index<start]
        if len(pre): last_mark[j]=float(pre.close.iloc[-1])
    rf=RF[proxy]
    for di,i in enumerate(inds):
        dt=DATES[i]; trad=SESSION[i]; om=last_mark.copy(); om[trad]=OPEN[i,trad]
        valid=np.isfinite(om); eqo=cash+(shares[:,valid]*om[valid]).sum(1)
        base=last_base.copy()
        ss=np.nan_to_num(TS[i],nan=0); rr=np.nan_to_num(TR[i],nan=0)
        base[trad]=(sw*ss+(1-sw)*rr)[trad]
        event=trad&(~np.isclose(base,last_base,rtol=0,atol=1e-15)); mo=f"{dt.year}-{dt.month}"
        periodic=np.array([trad[j] and last_month[j]!=mo for j in range(m)]); do=event|periodic
        if np.any(do):
            des=eqo[:,None]*(scales[:,None]*base[None,:]); cur=shares*om[None,:]
            tr=np.zeros_like(shares); tr[:,do]=des[:,do]-cur[:,do]
            gross=np.abs(tr).sum(1); cash-=tr.sum(1)+gross*cost; shares[:,do]+=tr[:,do]/om[do]; turnover+=gross
        last_base[trad]=base[trad]
        for j in np.where(trad)[0]:
            if last_month[j]!=mo: last_month[j]=mo
        if haircut and rf[i]!=0:
            cash*=1+haircut*rf[i]
        last_mark[trad]=CLOSE[i,trad]; valid=np.isfinite(last_mark)
        eq=cash+(shares[:,valid]*last_mark[valid]).sum(1)
        curves[di]=eq; peak=np.maximum(peak,eq); mdd=np.minimum(mdd,eq/peak-1)
    dates=DATES[inds]; yrs=max((dates[-1]-dates[0]).total_seconds()/(365.25*86400),1/365.25)
    final=curves[-1]; cagr=np.where(final>0,(final**(1/yrs)-1)*100,-100)
    g=pd.DataFrame({"scalar_pct":scales*100,"cagr_pct":cagr,"maxdd_pct":mdd*100,
                    "net_pct":(final-1)*100,"turnover_x":turnover})
    return (g,dates,curves) if return_curve else g

def cash_only(proxy,haircut,start,end):
    inds=np.where((DATES>=start)&(DATES<end))[0]; r=RF[proxy][inds]*haircut
    e=np.cumprod(1+r)
    dates=DATES[inds]; yrs=max((dates[-1]-dates[0]).total_seconds()/(365.25*86400),1/365.25)
    cagr=(e[-1]**(1/yrs)-1)*100; dd=np.min(e/np.maximum.accumulate(e)-1)*100
    return cagr,dd,(e[-1]-1)*100

# 1) Frozen 20/80, 52.5% sensitivity to proxy, yield haircut, and transaction cost.
frozen=[]
for proxy in PROXIES:
    for h in HAIRCUTS:
        for cost in COSTS:
            for name,a,b in [("train",START,CUT),("holdout",CUT,pd.Timestamp("2100-01-01",tz="UTC")),("full",START,pd.Timestamp("2100-01-01",tz="UTC"))]:
                z=simulate(FROZEN_SW,[FROZEN_SCALAR],a,b,cost,proxy,h)
                r=z.iloc[0].to_dict(); cc,cd,cn=cash_only(proxy,h,a,b)
                frozen.append({"proxy":proxy,"cash_yield_fraction":h,"cost_bps":cost*10000,"period":name,**r,
                               "cash_only_cagr_pct":cc,"cash_only_maxdd_pct":cd,"cash_only_net_pct":cn,
                               "cagr_minus_cash_pct":r["cagr_pct"]-cc})
pd.DataFrame(frozen).to_csv(OUT/"frozen_sensitivity.csv",index=False)

# 2) Re-run TRAIN-ONLY 3% selection under cash-yield haircuts for SGOV.
select=[]
for h in HAIRCUTS:
    cand=[]
    for sw in np.arange(0,.5001,.05):
        z=simulate(sw,GRID_SCALARS,START,CUT,BASE_COST,"SGOV",h)
        z.insert(0,"rsi2_weight_pct",(1-sw)*100); z.insert(0,"sentinel_weight_pct",sw*100)
        cand.append(z)
    g=pd.concat(cand,ignore_index=True)
    ok=g[g.maxdd_pct>=-3]
    if not len(ok): continue
    w=ok.sort_values(["cagr_pct","maxdd_pct"],ascending=[False,False]).iloc[0]
    zt=simulate(float(w.sentinel_weight_pct)/100,[float(w.scalar_pct)/100],CUT,pd.Timestamp("2100-01-01",tz="UTC"),BASE_COST,"SGOV",h).iloc[0]
    zf=simulate(float(w.sentinel_weight_pct)/100,[float(w.scalar_pct)/100],START,pd.Timestamp("2100-01-01",tz="UTC"),BASE_COST,"SGOV",h).iloc[0]
    cc,cd,cn=cash_only("SGOV",h,CUT,pd.Timestamp("2100-01-01",tz="UTC"))
    select.append({"cash_yield_fraction":h,"sentinel_weight_pct":w.sentinel_weight_pct,"rsi2_weight_pct":w.rsi2_weight_pct,
                   "scalar_pct":w.scalar_pct,"train_cagr_pct":w.cagr_pct,"train_maxdd_pct":w.maxdd_pct,
                   "test_cagr_pct":zt.cagr_pct,"test_maxdd_pct":zt.maxdd_pct,"test_pass":zt.maxdd_pct>=-3,
                   "full_cagr_pct":zf.cagr_pct,"full_maxdd_pct":zf.maxdd_pct,
                   "holdout_cash_only_cagr_pct":cc,"holdout_cagr_minus_cash_pct":zt.cagr_pct-cc})
pd.DataFrame(select).to_csv(OUT/"haircut_train_selected_3pct.csv",index=False)

# 3) Bootstrap the frozen train-selected 20/80 family at SGOV 100%, 75%, 50%.
boot=[]
for h in [.5,.75,1.0]:
    z,dates,curves=simulate(FROZEN_SW,BOOT_SCALARS,START,pd.Timestamp("2100-01-01",tz="UTC"),BASE_COST,"SGOV",h,True)
    ret=np.ones_like(curves); ret[1:]=curves[1:]/curves[:-1]; N=len(dates)
    for block in BLOCKS:
        rng=np.random.default_rng(SEED+block+int(h*100)); nb=int(np.ceil(N/block)); starts=rng.integers(0,N,size=(NSIM,nb))
        B=((starts[:,:,None]+np.arange(block)[None,None,:])%N).reshape(NSIM,-1)[:,:N]
        for qi,sp in enumerate(z.scalar_pct):
            se=np.cumprod(ret[B,qi],axis=1); loss=-(se/np.maximum.accumulate(se,axis=1)-1).min(axis=1)*100
            boot.append({"cash_yield_fraction":h,"block_days":block,"scalar_pct":sp,"cagr_pct":z.iloc[qi].cagr_pct,
                         "historical_dd_pct":z.iloc[qi].maxdd_pct,"p95_dd_pct":np.percentile(loss,95),"p99_dd_pct":np.percentile(loss,99)})
bg=pd.DataFrame(boot); bg.to_csv(OUT/"bootstrap_grid.csv",index=False)
bf=[]
for h in [.5,.75,1.0]:
    for block in BLOCKS:
        zz=bg[(bg.cash_yield_fraction==h)&(bg.block_days==block)]
        for p in [95,99]:
            col=f"p{p}_dd_pct"; ok=zz[zz[col]<=3]
            if len(ok):
                w=ok.sort_values(["cagr_pct",col],ascending=[False,True]).iloc[0]
                bf.append({"cash_yield_fraction":h,"block_days":block,"confidence_pct":p,**w.to_dict()})
pd.DataFrame(bf).to_csv(OUT/"bootstrap_3pct_frontier.csv",index=False)

print("FROZEN 20/80 52.5% SENSITIVITY")
print(pd.DataFrame(frozen).to_string(index=False))
print()\nprint("TRAIN-SELECTED 3% BY CASH-YIELD HAIRCUT")
print(pd.DataFrame(select).to_string(index=False))
print()\nprint("BOOTSTRAP 3% FRONTIER")
print(pd.DataFrame(bf).to_string(index=False))
