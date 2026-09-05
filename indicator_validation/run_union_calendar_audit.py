#!/usr/bin/env python3
from pathlib import Path
import pandas as pd, numpy as np, yfinance as yf

OUT=Path("indicator_validation/output_union_calendar_audit"); OUT.mkdir(parents=True,exist_ok=True)
START=pd.Timestamp("2020-09-01",tz="UTC"); END="2026-09-03"; COST=.0007
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
    # Per-asset desired weights as of each asset's close; executed next asset session open.
    t={}
    for s in ALL:
        ss=sentinel(raw[s]["close"]).astype(float)/len(ALL)
        if s in E11:
            c=raw[s]["close"]; rr=rsi2(c)
            rs=state_machine((c>c.rolling(200).mean())&(rr<5), c>c.rolling(5).mean()).astype(float)/len(E11)
        else:
            rs=pd.Series(0.0,index=raw[s].index)
        close_target=sw*ss+rw*rs
        # target for session open = prior session close target
        t[s]=close_target.shift(1).fillna(0.0)
    return t

def simulate_union(raw,targets,scalar,rebal="daily"):
    # Union of actual asset sessions; mark every calendar day to capture crypto weekend DD.
    first=min(d.index.min() for d in raw.values()); last=max(d.index.max() for d in raw.values())
    dates=pd.date_range(max(first,START),last,freq="D",tz="UTC")
    shares={s:0.0 for s in ALL}; cash=1.0; last_mark={s:np.nan for s in ALL}
    last_target={s:0.0 for s in ALL}; curve=[]; turnover=0.0; trades=0
    for dt in dates:
        # Build open marks: today's open on a real session, otherwise last close.
        open_marks={}
        tradable=[]
        for s in ALL:
            d=raw[s]
            if dt in d.index:
                open_marks[s]=float(d.loc[dt,"open"]); tradable.append(s)
            else:
                open_marks[s]=last_mark[s]
        # Current portfolio equity at today's opens / stale marks.
        eq_open=cash
        for s in ALL:
            p=open_marks[s]
            if np.isfinite(p): eq_open+=shares[s]*p
        # Execute only assets that actually trade today.
        desired_dollars={}
        for s in tradable:
            tw=scalar*float(targets[s].get(dt,0.0))
            if rebal=="signal":
                # Only alter dollars if desired target state changed from previous session.
                state_changed=not np.isclose(tw,last_target[s],rtol=0,atol=1e-15)
                if not state_changed:
                    continue
            desired_dollars[s]=eq_open*tw
        # Calculate trades off the same pre-trade equity to avoid ordering dependence.
        for s,des in desired_dollars.items():
            p=open_marks[s]; cur=shares[s]*p; tr=des-cur
            if abs(tr)>1e-15:
                cash-=tr+abs(tr)*COST; shares[s]+=tr/p; turnover+=abs(tr); trades+=1
            last_target[s]=scalar*float(targets[s].get(dt,0.0))
        # Update session close marks; non-session assets carry last close.
        for s in ALL:
            d=raw[s]
            if dt in d.index: last_mark[s]=float(d.loc[dt,"close"])
        eq=cash+sum(shares[s]*last_mark[s] for s in ALL if np.isfinite(last_mark[s]))
        curve.append(eq)
    e=pd.Series(curve,index=dates)
    years=(dates[-1]-dates[0]).total_seconds()/(365.25*86400)
    final=float(e.iloc[-1]); dd=float((e/e.cummax()-1).min()*100)
    cagr=(final**(1/years)-1)*100 if final>0 else -100
    return {"cagr_pct":cagr,"maxdd_pct":dd,"net_pct":(final-1)*100,"turnover_x":turnover,"trade_events":trades},e

rows=[]
for adjust in [False,True]:
    raw={s:dl(s,adjust) for s in ALL}; targets=build_targets(raw)
    for rebal in ["daily","signal"]:
        for sp in np.arange(1,100.0001,.5):
            m,_=simulate_union(raw,targets,sp/100,rebal)
            rows.append({"auto_adjust":adjust,"rebal":rebal,"scalar_pct":sp,**m})
g=pd.DataFrame(rows); g.to_csv(OUT/"union_calendar_grid.csv",index=False)

# Frontiers and selected 10/90 historical 3%-ceiling point.
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

# Diagnostics at original train-selected 77.25% scalar.
diag=[]
for adjust in [False,True]:
    raw={s:dl(s,adjust) for s in ALL}; targets=build_targets(raw)
    for rebal in ["daily","signal"]:
        m,e=simulate_union(raw,targets,.7725,rebal)
        # weekend DD contribution: worst DD date and whether weekend
        dd=e/e.cummax()-1; worst=dd.idxmin()
        diag.append({"auto_adjust":adjust,"rebal":rebal,"scalar_pct":77.25,**m,
                     "worst_dd_date":str(worst),"worst_dd_weekday":worst.day_name()})
pd.DataFrame(diag).to_csv(OUT/"original_7725_diagnostics.csv",index=False)

print("UNION CALENDAR FRONTIERS")
print(f.to_string(index=False))
print("\nORIGINAL 77.25% SCALAR DIAGNOSTICS")
print(pd.DataFrame(diag).to_string(index=False))
