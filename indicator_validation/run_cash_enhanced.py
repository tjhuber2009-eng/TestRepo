#!/usr/bin/env python3
from pathlib import Path
import pandas as pd, numpy as np, yfinance as yf

OUT=Path("indicator_validation/output_cash_enhanced"); OUT.mkdir(parents=True,exist_ok=True)
START=pd.Timestamp("2020-09-01",tz="UTC"); CUT=pd.Timestamp("2023-01-01",tz="UTC"); END="2026-09-03"; COST=.0007
SCALARS=np.arange(.01,1.0001,.005); SW=np.arange(0,.5001,.05)
C12=["BTC-USD","ETH-USD","BNB-USD","SOL-USD","XRP-USD","ADA-USD","DOGE-USD","LTC-USD","BCH-USD","LINK-USD","DOT-USD","AVAX-USD"]
E11=["SPY","QQQ","IWM","GLD","SLV","IEF","TLT","DBC","VNQ","EFA","EEM"]; ALL=C12+E11

def dl(s):
    x=yf.download(s,start="2020-01-01",end=END,interval="1d",auto_adjust=True,progress=False,threads=False)
    if isinstance(x.columns,pd.MultiIndex): x.columns=x.columns.get_level_values(0)
    x=x.rename(columns=str.lower).reset_index(); x=x.rename(columns={x.columns[0]:"datetime"}); x["datetime"]=pd.to_datetime(x["datetime"],utc=True)
    return x[["datetime","open","close"]].dropna().drop_duplicates("datetime").set_index("datetime").sort_index()
raw={s:dl(s) for s in ALL}
sg=dl("SGOV")
DATES=pd.date_range(START,max(d.index.max() for d in raw.values()),freq="D",tz="UTC"); n=len(DATES); m=len(ALL); pos={d:i for i,d in enumerate(DATES)}
SESSION=np.zeros((n,m),bool); OPEN=np.full((n,m),np.nan); CLOSE=np.full((n,m),np.nan)
for j,s in enumerate(ALL):
    for dt,row in raw[s].loc[raw[s].index>=START].iterrows():
        if dt in pos:
            i=pos[dt]; SESSION[i,j]=True; OPEN[i,j]=float(row.open); CLOSE[i,j]=float(row.close)
# SGOV total-return proxy daily return, flat on non-sessions.
sgret=sg.close.pct_change().fillna(0)
RF=np.zeros(n)
for dt,r in sgret.items():
    if dt in pos and dt>=START: RF[pos[dt]]=float(r)

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

TS=np.full((n,m),np.nan); TR=np.full((n,m),np.nan)
for j,s in enumerate(ALL):
    d=raw[s]; ss=(sentinel(d.close).astype(float)/len(ALL)).shift(1).fillna(0)
    if s in E11:
        rr=rsi2(d.close); rs=(sm((d.close>d.close.rolling(200).mean())&(rr<5),d.close>d.close.rolling(5).mean()).astype(float)/len(E11)).shift(1).fillna(0)
    else: rs=pd.Series(0.0,index=d.index)
    for dt in d.index[d.index>=START]:
        if dt in pos:
            i=pos[dt]; TS[i,j]=float(ss.loc[dt]); TR[i,j]=float(rs.loc[dt])

def simulate(sw,start,end,cash_yield):
    mask=(DATES>=start)&(DATES<end); inds=np.where(mask)[0]; k=len(SCALARS)
    shares=np.zeros((k,m)); cash=np.ones(k); last_mark=np.full(m,np.nan); last_base=np.zeros(m); last_month=np.array([None]*m,dtype=object)
    peak=np.ones(k); mdd=np.zeros(k); turnover=np.zeros(k); curves=np.empty((len(inds),k))
    for j,s in enumerate(ALL):
        pre=raw[s][raw[s].index<start]
        if len(pre): last_mark[j]=float(pre.close.iloc[-1])
    for di,i in enumerate(inds):
        dt=DATES[i]; trad=SESSION[i]; om=last_mark.copy(); om[trad]=OPEN[i,trad]; valid=np.isfinite(om); eqo=cash+(shares[:,valid]*om[valid]).sum(1)
        base=last_base.copy(); ss=np.nan_to_num(TS[i],nan=0); rr=np.nan_to_num(TR[i],nan=0); base[trad]=(sw*ss+(1-sw)*rr)[trad]
        event=trad&(~np.isclose(base,last_base,rtol=0,atol=1e-15)); mo=f"{dt.year}-{dt.month}"
        periodic=np.array([trad[j] and last_month[j]!=mo for j in range(m)]); do=event|periodic
        if np.any(do):
            des=eqo[:,None]*(SCALARS[:,None]*base[None,:]); cur=shares*om[None,:]; tr=np.zeros_like(shares); tr[:,do]=des[:,do]-cur[:,do]
            gross=np.abs(tr).sum(1); cash-=tr.sum(1)+gross*COST; shares[:,do]+=tr[:,do]/om[do]; turnover+=gross
        last_base[trad]=base[trad]
        for j in np.where(trad)[0]:
            if last_month[j]!=mo: last_month[j]=mo
        # Sweep idle cash through cash-return proxy for the day.
        if cash_yield and RF[i]!=0: cash*=1+RF[i]
        last_mark[trad]=CLOSE[i,trad]; valid=np.isfinite(last_mark); eq=cash+(shares[:,valid]*last_mark[valid]).sum(1)
        curves[di]=eq; peak=np.maximum(peak,eq); mdd=np.minimum(mdd,eq/peak-1)
    dates=DATES[inds]; yrs=max((dates[-1]-dates[0]).total_seconds()/(365.25*86400),1/365.25); final=curves[-1]
    cagr=np.where(final>0,(final**(1/yrs)-1)*100,-100)
    return pd.DataFrame({"scalar_pct":SCALARS*100,"cagr_pct":cagr,"maxdd_pct":mdd*100,"net_pct":(final-1)*100,"turnover_x":turnover}),dates,curves

outputs=[]
for cash_yield in [False,True]:
    train_rows=[]
    for sw in SW:
        z,_,_=simulate(sw,START,CUT,cash_yield); z.insert(0,"rsi2_weight_pct",(1-sw)*100); z.insert(0,"sentinel_weight_pct",sw*100); train_rows.append(z)
    train=pd.concat(train_rows,ignore_index=True)
    sel=[]
    for ce in range(3,16):
        ok=train[train.maxdd_pct>=-ce]
        if len(ok):
            w=ok.sort_values(["cagr_pct","maxdd_pct"],ascending=[False,False]).iloc[0]; sel.append({"dd_ceiling_pct":ce,**w.to_dict()})
    sel=pd.DataFrame(sel)
    wf=[]
    for _,w in sel.iterrows():
        sw=float(w.sentinel_weight_pct)/100; zt,_,_=simulate(sw,CUT,pd.Timestamp("2100-01-01",tz="UTC"),cash_yield)
        q=int(np.argmin(np.abs(zt.scalar_pct-float(w.scalar_pct)))); t=zt.iloc[q]
        zf,_,_=simulate(sw,START,pd.Timestamp("2100-01-01",tz="UTC"),cash_yield); qf=int(np.argmin(np.abs(zf.scalar_pct-float(w.scalar_pct)))); f=zf.iloc[qf]
        wf.append({"dd_ceiling_pct":int(w.dd_ceiling_pct),"sentinel_weight_pct":w.sentinel_weight_pct,"rsi2_weight_pct":w.rsi2_weight_pct,"scalar_pct":w.scalar_pct,
                   "train_cagr_pct":w.cagr_pct,"train_maxdd_pct":w.maxdd_pct,"test_cagr_pct":t.cagr_pct,"test_maxdd_pct":t.maxdd_pct,
                   "test_net_pct":t.net_pct,"test_pass":t.maxdd_pct>=-float(w.dd_ceiling_pct),"full_cagr_pct":f.cagr_pct,"full_maxdd_pct":f.maxdd_pct})
    wf=pd.DataFrame(wf)
    tag="sgov_cash" if cash_yield else "zero_cash"
    train.to_csv(OUT/f"{tag}_train_grid.csv",index=False); sel.to_csv(OUT/f"{tag}_selected.csv",index=False); wf.to_csv(OUT/f"{tag}_walkforward.csv",index=False)
    outputs.append((tag,sel,wf))

for tag,sel,wf in outputs:
    print("\n",tag.upper(),"SELECTED"); print(sel.to_string(index=False)); print("\n",tag.upper(),"WALKFORWARD"); print(wf.to_string(index=False))
