#!/usr/bin/env python3
from pathlib import Path
import pandas as pd, numpy as np, yfinance as yf

OUT=Path("indicator_validation/output_union_calendar_audit"); OUT.mkdir(parents=True,exist_ok=True)
START=pd.Timestamp("2020-09-01",tz="UTC"); END="2026-09-03"; COST=.0007
SCALARS=np.arange(1,100.0001,.5)/100
C12=["BTC-USD","ETH-USD","BNB-USD","SOL-USD","XRP-USD","ADA-USD","DOGE-USD","LTC-USD","BCH-USD","LINK-USD","DOT-USD","AVAX-USD"]
E11=["SPY","QQQ","IWM","GLD","SLV","IEF","TLT","DBC","VNQ","EFA","EEM"]; ALL=C12+E11

def dl(sym,adjust):
    x=yf.download(sym,start="2020-01-01",end=END,interval="1d",auto_adjust=adjust,progress=False,threads=False)
    if isinstance(x.columns,pd.MultiIndex): x.columns=x.columns.get_level_values(0)
    x=x.rename(columns=str.lower).reset_index(); x=x.rename(columns={x.columns[0]:"datetime"})
    x["datetime"]=pd.to_datetime(x["datetime"],utc=True)
    return x[["datetime","open","close"]].dropna().drop_duplicates("datetime").set_index("datetime").sort_index()

def sentinel(close):
    ema=close.ewm(span=63,adjust=False).mean(); sd=close.rolling(63).std(ddof=0); z=(close-ema)/sd
    bull=(z>.5).fillna(False).to_numpy(); bear=(z<-.5).fillna(False).to_numpy()
    st=np.zeros(len(close),bool); on=False
    for i in range(len(close)):
        if bull[i]: on=True
        elif bear[i]: on=False
        st[i]=on
    return pd.Series(st,index=close.index)

def rsi2(close):
    d=close.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0)
    au=up.ewm(alpha=.5,adjust=False,min_periods=2).mean(); ad=dn.ewm(alpha=.5,adjust=False,min_periods=2).mean()
    rs=au/ad.replace(0,np.nan); r=100-100/(1+rs)
    r[(ad==0)&(au>0)]=100; r[(au==0)&(ad>0)]=0
    return r

def state_machine(entry,exit_):
    e=np.asarray(entry.fillna(False),bool); x=np.asarray(exit_.fillna(False),bool); st=np.zeros(len(e),bool); on=False
    for i in range(len(e)):
        if on and x[i]: on=False
        elif (not on) and e[i]: on=True
        st[i]=on
    return pd.Series(st,index=entry.index)

def build_targets(raw,sw=.10,rw=.90):
    out={}
    for s in ALL:
        ss=sentinel(raw[s]["close"]).astype(float)/len(ALL)
        if s in E11:
            c=raw[s]["close"]; rr=rsi2(c)
            rs=state_machine((c>c.rolling(200).mean())&(rr<5),c>c.rolling(5).mean()).astype(float)/len(E11)
        else:
            rs=pd.Series(0.0,index=raw[s].index)
        out[s]=(sw*ss+rw*rs).shift(1).fillna(0.0)
    return out

def simulate_grid(raw,targets,rebal):
    dates=pd.date_range(max(min(d.index.min() for d in raw.values()),START),
                        max(d.index.max() for d in raw.values()),freq="D",tz="UTC")
    k=len(SCALARS); m=len(ALL)
    shares=np.zeros((k,m)); cash=np.ones(k); last_mark=np.full(m,np.nan)
    last_base=np.zeros(m); peak=np.ones(k); mdd=np.zeros(k); turnover=np.zeros(k); trade_events=np.zeros(k,int)
    eq_curves=np.empty((len(dates),k))
    for di,dt in enumerate(dates):
        open_marks=last_mark.copy(); tradable=np.zeros(m,bool); base=np.zeros(m)
        for j,s in enumerate(ALL):
            d=raw[s]
            if dt in d.index:
                open_marks[j]=float(d.loc[dt,"open"]); tradable[j]=True; base[j]=float(targets[s].get(dt,0.0))
            else:
                base[j]=last_base[j]
        valid=np.isfinite(open_marks)
        eq_open=cash+(shares[:,valid]*open_marks[valid]).sum(axis=1)
        if rebal=="daily":
            do=tradable
        else:
            do=tradable & (~np.isclose(base,last_base,rtol=0,atol=1e-15))
        if np.any(do):
            des=eq_open[:,None]*(SCALARS[:,None]*base[None,:])
            cur=shares*open_marks[None,:]
            trade=np.zeros_like(shares); trade[:,do]=des[:,do]-cur[:,do]
            gross=np.abs(trade).sum(axis=1)
            cash-=trade.sum(axis=1)+gross*COST
            shares[:,do]+=trade[:,do]/open_marks[do]
            turnover+=gross; trade_events+=(np.abs(trade)>1e-15).sum(axis=1)
        last_base[tradable]=base[tradable]
        for j,s in enumerate(ALL):
            d=raw[s]
            if dt in d.index: last_mark[j]=float(d.loc[dt,"close"])
        valid=np.isfinite(last_mark)
        eq=cash+(shares[:,valid]*last_mark[valid]).sum(axis=1)
        eq_curves[di]=eq; peak=np.maximum(peak,eq); mdd=np.minimum(mdd,eq/peak-1)
    years=(dates[-1]-dates[0]).total_seconds()/(365.25*86400)
    final=eq_curves[-1]; cagr=np.where(final>0,(final**(1/years)-1)*100,-100)
    return pd.DataFrame({"scalar_pct":SCALARS*100,"cagr_pct":cagr,"maxdd_pct":mdd*100,
                         "net_pct":(final-1)*100,"turnover_x":turnover,"trade_events":trade_events}),dates,eq_curves

rows=[]; diag=[]
for adjust in [False,True]:
    raw={s:dl(s,adjust) for s in ALL}; targets=build_targets(raw)
    for rebal in ["daily","signal"]:
        z,dates,curves=simulate_grid(raw,targets,rebal); z.insert(0,"rebal",rebal); z.insert(0,"auto_adjust",adjust); rows.append(z)
        qi=int(np.argmin(np.abs(SCALARS-.7725))); e=pd.Series(curves[:,qi],index=dates); dd=e/e.cummax()-1; worst=dd.idxmin()
        r=z.iloc[qi].to_dict()
        diag.append({**r,"worst_dd_date":str(worst),"worst_dd_weekday":worst.day_name()})
g=pd.concat(rows,ignore_index=True); g.to_csv(OUT/"union_calendar_grid.csv",index=False)
front=[]
for adjust in [False,True]:
  for rebal in ["daily","signal"]:
    z=g[(g.auto_adjust==adjust)&(g.rebal==rebal)]
    for ce in range(3,16):
      ok=z[z.maxdd_pct>=-ce]
      if len(ok):
        w=ok.sort_values(["cagr_pct","maxdd_pct"],ascending=[False,False]).iloc[0]
        front.append({"dd_ceiling_pct":ce,**w.to_dict()})
f=pd.DataFrame(front); f.to_csv(OUT/"union_calendar_frontiers.csv",index=False)
pd.DataFrame(diag).to_csv(OUT/"original_7725_diagnostics.csv",index=False)
print("UNION CALENDAR FRONTIERS"); print(f.to_string(index=False))
print("\nORIGINAL 77.25% SCALAR DIAGNOSTICS"); print(pd.DataFrame(diag).to_string(index=False))
