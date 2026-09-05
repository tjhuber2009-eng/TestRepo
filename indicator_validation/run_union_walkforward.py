#!/usr/bin/env python3
from pathlib import Path
import pandas as pd, numpy as np, yfinance as yf

OUT=Path("indicator_validation/output_union_walkforward"); OUT.mkdir(parents=True,exist_ok=True)
START=pd.Timestamp("2020-09-01",tz="UTC"); CUT=pd.Timestamp("2023-01-01",tz="UTC"); END="2026-09-03"; COST=.0007
SCALARS=np.arange(1,100.0001,.5)/100; WEIGHTS=np.arange(0,1.0001,.05)
MODES=["daily","event_weekly","event_monthly","event_only"]
C12=["BTC-USD","ETH-USD","BNB-USD","SOL-USD","XRP-USD","ADA-USD","DOGE-USD","LTC-USD","BCH-USD","LINK-USD","DOT-USD","AVAX-USD"]
E11=["SPY","QQQ","IWM","GLD","SLV","IEF","TLT","DBC","VNQ","EFA","EEM"]; ALL=C12+E11

def dl(s):
    x=yf.download(s,start="2020-01-01",end=END,interval="1d",auto_adjust=True,progress=False,threads=False)
    if isinstance(x.columns,pd.MultiIndex): x.columns=x.columns.get_level_values(0)
    x=x.rename(columns=str.lower).reset_index(); x=x.rename(columns={x.columns[0]:"datetime"}); x["datetime"]=pd.to_datetime(x["datetime"],utc=True)
    return x[["datetime","open","close"]].dropna().drop_duplicates("datetime").set_index("datetime").sort_index()

raw={s:dl(s) for s in ALL}
DATES=pd.date_range(START,max(d.index.max() for d in raw.values()),freq="D",tz="UTC"); n=len(DATES); m=len(ALL)
SESSION=np.zeros((n,m),bool); OPEN=np.full((n,m),np.nan); CLOSE=np.full((n,m),np.nan)
date_pos={d:i for i,d in enumerate(DATES)}
for j,s in enumerate(ALL):
    for dt,row in raw[s].loc[raw[s].index>=START].iterrows():
        if dt in date_pos:
            i=date_pos[dt]; SESSION[i,j]=True; OPEN[i,j]=float(row.open); CLOSE[i,j]=float(row.close)

def sentinel(c):
    ema=c.ewm(span=63,adjust=False).mean(); sd=c.rolling(63).std(ddof=0); z=(c-ema)/sd
    b=(z>.5).fillna(False).to_numpy(); x=(z<-.5).fillna(False).to_numpy(); st=np.zeros(len(c),bool); on=False
    for i in range(len(c)):
        if b[i]: on=True
        elif x[i]: on=False
        st[i]=on
    return pd.Series(st,index=c.index)

def rsi2(c):
    d=c.diff(); up=d.clip(lower=0); dn=-d.clip(upper=0)
    au=up.ewm(alpha=.5,adjust=False,min_periods=2).mean(); ad=dn.ewm(alpha=.5,adjust=False,min_periods=2).mean()
    rs=au/ad.replace(0,np.nan); r=100-100/(1+rs); r[(ad==0)&(au>0)]=100; r[(au==0)&(ad>0)]=0
    return r

def sm(en,ex):
    e=np.asarray(en.fillna(False),bool); x=np.asarray(ex.fillna(False),bool); st=np.zeros(len(e),bool); on=False
    for i in range(len(e)):
        if on and x[i]: on=False
        elif (not on) and e[i]: on=True
        st[i]=on
    return pd.Series(st,index=en.index)

# Targets at each asset session open, based only on previous session close.
TS=np.full((n,m),np.nan); TR=np.full((n,m),np.nan)
for j,s in enumerate(ALL):
    d=raw[s]; ss=(sentinel(d.close).astype(float)/len(ALL)).shift(1).fillna(0)
    if s in E11:
        rr=rsi2(d.close); rs=(sm((d.close>d.close.rolling(200).mean())&(rr<5),d.close>d.close.rolling(5).mean()).astype(float)/len(E11)).shift(1).fillna(0)
    else: rs=pd.Series(0.0,index=d.index)
    for dt in d.index[d.index>=START]:
        if dt in date_pos:
            i=date_pos[dt]; TS[i,j]=float(ss.loc[dt]); TR[i,j]=float(rs.loc[dt])

def simulate_grid(sw,mode,start,end):
    mask=(DATES>=start)&(DATES<end); inds=np.where(mask)[0]; k=len(SCALARS)
    shares=np.zeros((k,m)); cash=np.ones(k); last_mark=np.full(m,np.nan); last_base=np.zeros(m)
    last_week=np.array([None]*m,dtype=object); last_month=np.array([None]*m,dtype=object)
    peak=np.ones(k); mdd=np.zeros(k); turnover=np.zeros(k); trade_events=np.zeros(k,int)
    first_dt=DATES[inds[0]]; last_dt=DATES[inds[-1]]
    # Seed marks from last available close before start.
    for j,s in enumerate(ALL):
        pre=raw[s][raw[s].index<start]
        if len(pre): last_mark[j]=float(pre.close.iloc[-1])
    for i in inds:
        dt=DATES[i]; trad=SESSION[i]; open_marks=last_mark.copy(); open_marks[trad]=OPEN[i,trad]
        valid=np.isfinite(open_marks); eq_open=cash+(shares[:,valid]*open_marks[valid]).sum(1)
        # Current session-open base target; only actual sessions reveal/update target.
        base=last_base.copy()
        sess_s=np.nan_to_num(TS[i],nan=0.0); sess_r=np.nan_to_num(TR[i],nan=0.0)
        base[trad]=sw*sess_s[trad]+(1-sw)*sess_r[trad]
        event=trad & (~np.isclose(base,last_base,rtol=0,atol=1e-15))
        if mode=="daily":
            do=trad
        elif mode=="event_only":
            do=event
        elif mode=="event_weekly":
            wk=f"{dt.isocalendar().year}-{dt.isocalendar().week}"
            periodic=np.array([trad[j] and last_week[j]!=wk for j in range(m)])
            do=event|periodic
        else:
            mo=f"{dt.year}-{dt.month}"
            periodic=np.array([trad[j] and last_month[j]!=mo for j in range(m)])
            do=event|periodic
        if np.any(do):
            des=eq_open[:,None]*(SCALARS[:,None]*base[None,:]); cur=shares*open_marks[None,:]
            tr=np.zeros_like(shares); tr[:,do]=des[:,do]-cur[:,do]
            gross=np.abs(tr).sum(1); cash-=tr.sum(1)+gross*COST; shares[:,do]+=tr[:,do]/open_marks[do]
            turnover+=gross; trade_events+=(np.abs(tr)>1e-15).sum(1)
        last_base[trad]=base[trad]
        if mode=="event_weekly":
            wk=f"{dt.isocalendar().year}-{dt.isocalendar().week}"
            for j in np.where(trad)[0]:
                if last_week[j]!=wk: last_week[j]=wk
        if mode=="event_monthly":
            mo=f"{dt.year}-{dt.month}"
            for j in np.where(trad)[0]:
                if last_month[j]!=mo: last_month[j]=mo
        last_mark[trad]=CLOSE[i,trad]
        valid=np.isfinite(last_mark); eq=cash+(shares[:,valid]*last_mark[valid]).sum(1)
        peak=np.maximum(peak,eq); mdd=np.minimum(mdd,eq/peak-1)
    years=max((last_dt-first_dt).total_seconds()/(365.25*86400),1/365.25); final=eq
    cagr=np.where(final>0,(final**(1/years)-1)*100,-100)
    return pd.DataFrame({"scalar_pct":SCALARS*100,"cagr_pct":cagr,"maxdd_pct":mdd*100,
                         "net_pct":(final-1)*100,"turnover_x":turnover,"trade_events":trade_events})

# Train-only search.
train_rows=[]
for mode in MODES:
    for sw in WEIGHTS:
        z=simulate_grid(sw,mode,START,CUT); z.insert(0,"rsi2_weight_pct",(1-sw)*100); z.insert(0,"sentinel_weight_pct",sw*100); z.insert(0,"mode",mode)
        train_rows.append(z)
train=pd.concat(train_rows,ignore_index=True); train.to_csv(OUT/"train_grid.csv",index=False)

sel=[]
for ce in range(3,16):
    ok=train[train.maxdd_pct>=-ce]
    if len(ok):
        w=ok.sort_values(["cagr_pct","maxdd_pct"],ascending=[False,False]).iloc[0]
        sel.append({"dd_ceiling_pct":ce,**w.to_dict()})
sel=pd.DataFrame(sel); sel.to_csv(OUT/"train_selected_frontier.csv",index=False)

# Test selected configurations fresh on 2023+; also full-period diagnostic.
wf=[]
for _,w in sel.iterrows():
    sw=float(w.sentinel_weight_pct)/100; mode=w["mode"]; target_scalar=float(w.scalar_pct)
    zt=simulate_grid(sw,mode,CUT,pd.Timestamp("2100-01-01",tz="UTC")); qi=int(np.argmin(np.abs(zt.scalar_pct-target_scalar))); t=zt.iloc[qi]
    zf=simulate_grid(sw,mode,START,pd.Timestamp("2100-01-01",tz="UTC")); qf=int(np.argmin(np.abs(zf.scalar_pct-target_scalar))); ff=zf.iloc[qf]
    wf.append({"dd_ceiling_pct":int(w.dd_ceiling_pct),"mode":mode,"sentinel_weight_pct":w.sentinel_weight_pct,"rsi2_weight_pct":w.rsi2_weight_pct,
               "scalar_pct":w.scalar_pct,"train_cagr_pct":w.cagr_pct,"train_maxdd_pct":w.maxdd_pct,
               "test_cagr_pct":t.cagr_pct,"test_maxdd_pct":t.maxdd_pct,"test_net_pct":t.net_pct,"test_turnover_x":t.turnover_x,"test_trade_events":int(t.trade_events),
               "test_pass":t.maxdd_pct>=-float(w.dd_ceiling_pct),"full_cagr_pct":ff.cagr_pct,"full_maxdd_pct":ff.maxdd_pct})
wf=pd.DataFrame(wf); wf.to_csv(OUT/"union_walkforward.csv",index=False)

print("TRAIN-SELECTED FRONTIER"); print(sel.to_string(index=False))
print("\nUNION-CALENDAR WALKFORWARD"); print(wf.to_string(index=False))
