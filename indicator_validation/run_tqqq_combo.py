#!/usr/bin/env python3
from pathlib import Path
import pandas as pd, numpy as np, yfinance as yf

OUT=Path("indicator_validation/output_tqqq_combo"); OUT.mkdir(parents=True,exist_ok=True)
START=pd.Timestamp("2020-09-01",tz="UTC"); CUT=pd.Timestamp("2023-01-01",tz="UTC"); END="2026-09-03"; COST=.0007
SCALARS=np.arange(.01,1.0001,.005); TWS=np.arange(0,.3001,.025)
C12=["BTC-USD","ETH-USD","BNB-USD","SOL-USD","XRP-USD","ADA-USD","DOGE-USD","LTC-USD","BCH-USD","LINK-USD","DOT-USD","AVAX-USD"]
E11=["SPY","QQQ","IWM","GLD","SLV","IEF","TLT","DBC","VNQ","EFA","EEM"]; BASE=C12+E11; ALL=BASE+["TQQQ"]

def dl(s):
    x=yf.download(s,start="2020-01-01",end=END,interval="1d",auto_adjust=True,progress=False,threads=False)
    if isinstance(x.columns,pd.MultiIndex): x.columns=x.columns.get_level_values(0)
    x=x.rename(columns=str.lower).reset_index(); x=x.rename(columns={x.columns[0]:"datetime"}); x["datetime"]=pd.to_datetime(x["datetime"],utc=True)
    return x[["datetime","open","high","low","close"]].dropna().drop_duplicates("datetime").set_index("datetime").sort_index()
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
    z=c.diff(); up=z.clip(lower=0); dn=-z.clip(upper=0); au=up.ewm(alpha=.5,adjust=False,min_periods=2).mean(); ad=dn.ewm(alpha=.5,adjust=False,min_periods=2).mean()
    rs=au/ad.replace(0,np.nan); r=100-100/(1+rs); r[(ad==0)&(au>0)]=100; r[(au==0)&(ad>0)]=0; return r
def sm(en,ex):
    e=np.asarray(en.fillna(False),bool); x=np.asarray(ex.fillna(False),bool); st=np.zeros(len(e),bool); on=False
    for i in range(len(e)):
        if on and x[i]: on=False
        elif (not on) and e[i]: on=True
        st[i]=on
    return pd.Series(st,index=en.index)

# Fixed corrected base sleeve: 10% Sentinel / 90% RSI2.
BASE_T=np.full((n,len(BASE)),np.nan)
for j,s in enumerate(BASE):
    d=raw[s]; ss=(sentinel(d.close).astype(float)/len(BASE)).shift(1).fillna(0)
    if s in E11:
        rr=rsi2(d.close); rs=(sm((d.close>d.close.rolling(200).mean())&(rr<5),d.close>d.close.rolling(5).mean()).astype(float)/len(E11)).shift(1).fillna(0)
    else: rs=pd.Series(0.0,index=d.index)
    t=.10*ss+.90*rs
    for dt in d.index[d.index>=START]:
        if dt in pos: BASE_T[pos[dt],j]=float(t.loc[dt])

# Transparent TQQQ IBS core, no September entries, next-open target.
td=raw["TQQQ"]; ibs=(td.close-td.low)/(td.high-td.low).replace(0,np.nan); st=np.zeros(len(td),bool); on=False
for i,dt in enumerate(td.index):
    if on and ibs.iloc[i]>.95: on=False
    elif (not on) and ibs.iloc[i]<.14 and dt.month!=9: on=True
    st[i]=on
TQQQ_OPEN=pd.Series(st,index=td.index).shift(1).fillna(False).astype(float)
TQ=np.full(n,np.nan)
for dt in td.index[td.index>=START]:
    if dt in pos: TQ[pos[dt]]=float(TQQQ_OPEN.loc[dt])

def simulate(tw,start,end):
    mask=(DATES>=start)&(DATES<end); inds=np.where(mask)[0]; k=len(SCALARS)
    shares=np.zeros((k,m)); cash=np.ones(k); last_mark=np.full(m,np.nan); last_base=np.zeros(m); last_month=np.array([None]*m,dtype=object)
    peak=np.ones(k); mdd=np.zeros(k); turnover=np.zeros(k); events=np.zeros(k,int)
    for j,s in enumerate(ALL):
        pre=raw[s][raw[s].index<start]
        if len(pre): last_mark[j]=float(pre.close.iloc[-1])
    for i in inds:
        dt=DATES[i]; trad=SESSION[i]; om=last_mark.copy(); om[trad]=OPEN[i,trad]; valid=np.isfinite(om); eqo=cash+(shares[:,valid]*om[valid]).sum(1)
        base=last_base.copy()
        # Session-specific desired weights.
        for j in np.where(trad)[0]:
            if j<len(BASE):
                bt=0.0 if np.isnan(BASE_T[i,j]) else BASE_T[i,j]
                base[j]=(1-tw)*bt
            else:
                tq=0.0 if np.isnan(TQ[i]) else TQ[i]
                base[j]=tw*tq
        event=trad&(~np.isclose(base,last_base,rtol=0,atol=1e-15)); mo=f"{dt.year}-{dt.month}"
        periodic=np.array([trad[j] and last_month[j]!=mo for j in range(m)]); do=event|periodic
        if np.any(do):
            des=eqo[:,None]*(SCALARS[:,None]*base[None,:]); cur=shares*om[None,:]; tr=np.zeros_like(shares); tr[:,do]=des[:,do]-cur[:,do]
            gross=np.abs(tr).sum(1); cash-=tr.sum(1)+gross*COST; shares[:,do]+=tr[:,do]/om[do]; turnover+=gross; events+=(np.abs(tr)>1e-15).sum(1)
        last_base[trad]=base[trad]
        for j in np.where(trad)[0]:
            if last_month[j]!=mo: last_month[j]=mo
        last_mark[trad]=CLOSE[i,trad]; valid=np.isfinite(last_mark); eq=cash+(shares[:,valid]*last_mark[valid]).sum(1)
        peak=np.maximum(peak,eq); mdd=np.minimum(mdd,eq/peak-1)
    dates=DATES[inds]; yrs=max((dates[-1]-dates[0]).total_seconds()/(365.25*86400),1/365.25); final=eq
    cagr=np.where(final>0,(final**(1/yrs)-1)*100,-100)
    return pd.DataFrame({"scalar_pct":SCALARS*100,"cagr_pct":cagr,"maxdd_pct":mdd*100,"net_pct":(final-1)*100,"turnover_x":turnover,"trade_events":events})

train_rows=[]
for tw in TWS:
    z=simulate(tw,START,CUT); z.insert(0,"tqqq_weight_pct",tw*100); train_rows.append(z)
train=pd.concat(train_rows,ignore_index=True); train.to_csv(OUT/"train_grid.csv",index=False)
sel=[]
for ce in range(3,16):
    ok=train[train.maxdd_pct>=-ce]
    if len(ok):
        w=ok.sort_values(["cagr_pct","maxdd_pct"],ascending=[False,False]).iloc[0]; sel.append({"dd_ceiling_pct":ce,**w.to_dict()})
sel=pd.DataFrame(sel); sel.to_csv(OUT/"train_selected.csv",index=False)
wf=[]
for _,w in sel.iterrows():
    tw=float(w.tqqq_weight_pct)/100; zt=simulate(tw,CUT,pd.Timestamp("2100-01-01",tz="UTC")); q=int(np.argmin(np.abs(zt.scalar_pct-float(w.scalar_pct)))); t=zt.iloc[q]
    zf=simulate(tw,START,pd.Timestamp("2100-01-01",tz="UTC")); qf=int(np.argmin(np.abs(zf.scalar_pct-float(w.scalar_pct)))); f=zf.iloc[qf]
    wf.append({"dd_ceiling_pct":int(w.dd_ceiling_pct),"tqqq_weight_pct":w.tqqq_weight_pct,"base_weight_pct":100-w.tqqq_weight_pct,"scalar_pct":w.scalar_pct,
               "train_cagr_pct":w.cagr_pct,"train_maxdd_pct":w.maxdd_pct,"test_cagr_pct":t.cagr_pct,"test_maxdd_pct":t.maxdd_pct,
               "test_net_pct":t.net_pct,"test_pass":t.maxdd_pct>=-float(w.dd_ceiling_pct),"full_cagr_pct":f.cagr_pct,"full_maxdd_pct":f.maxdd_pct})
wf=pd.DataFrame(wf); wf.to_csv(OUT/"walkforward.csv",index=False)
print("TRAIN SELECTED"); print(sel.to_string(index=False))
print("\nWALKFORWARD"); print(wf.to_string(index=False))
