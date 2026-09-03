#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import numpy as np
import pandas as pd

OUT=Path('indicator_validation/output_survivors'); OUT.mkdir(parents=True,exist_ok=True)
SRC=Path('indicator_validation/output')

def load30():
    p=next(SRC.glob('btcusdt_30m_kline_*.parquet')); d=pd.read_parquet(p); d.columns=[str(c).lower() for c in d.columns]
    t=next(c for c in ['datetime','timestamp','open_time','ts'] if c in d.columns); s=d[t]
    if pd.api.types.is_numeric_dtype(s):
        x=pd.to_numeric(s); med=float(x.abs().median()); unit='ns' if med>1e17 else ('us' if med>1e14 else ('ms' if med>1e11 else 's')); dt=pd.to_datetime(x,unit=unit,utc=True)
    else: dt=pd.to_datetime(s,utc=True)
    o=pd.DataFrame({'datetime':dt});
    for c in ['open','high','low','close']: o[c]=pd.to_numeric(d[c],errors='coerce')
    return o.dropna().sort_values('datetime').drop_duplicates('datetime').reset_index(drop=True)
def resample(df,rule): return df.set_index('datetime').resample(rule).agg(open=('open','first'),high=('high','max'),low=('low','min'),close=('close','last')).dropna().reset_index()
def rma(s,n): return s.ewm(alpha=1/n,adjust=False,min_periods=n).mean()
def atr(df,n=10):
    pc=df.close.shift(1); tr=pd.concat([df.high-df.low,(df.high-pc).abs(),(df.low-pc).abs()],axis=1).max(axis=1); return rma(tr,n)
def metrics(e,trades,df,exposed):
    eq=float(e.iloc[-1]); years=max((df.datetime.iloc[-1]-df.datetime.iloc[0]).total_seconds()/(365.25*86400),1/365.25); arr=pd.Series(trades,dtype=float); gp=float(arr[arr>0].sum()); gl=float(-arr[arr<=0].sum())
    return {'net_pct':(eq-1)*100,'cagr_pct':(eq**(1/years)-1)*100 if eq>0 else -100,'pf':gp/gl if gl else (99 if gp else 0),'win_pct':float((arr>0).mean()*100) if len(arr) else 0,'trades':int(len(arr)),'maxdd_pct':float((e/e.cummax()-1).min()*100),'exposure_pct':100*exposed/max(len(df)-1,1),'bnh_pct':(df.close.iloc[-1]/df.open.iloc[0]-1)*100}
def reverse_backtest(df,long_sig,short_sig,cost):
    eq=1.; side=0; qty=0.; entry_eff=0.; start_eq=0.; pending=0; trades=[]; curve=[]; exposed=0
    for i,row in df.iterrows():
        if pending:
            new=pending; pending=0
            if side:
                exit_eff=float(row.open)*(1-cost if side==1 else 1+cost); eq+=side*qty*(exit_eff-entry_eff); trades.append((eq/start_eq-1)*100); side=0
            start_eq=eq; side=new; entry_eff=float(row.open)*(1+cost if side==1 else 1-cost); qty=eq/entry_eff
        if side: exposed+=1; curve.append(eq+side*qty*(float(row.close)-entry_eff))
        else: curve.append(eq)
        if side!=1 and bool(long_sig.iloc[i]) and i<len(df)-1: pending=1
        elif side!=-1 and bool(short_sig.iloc[i]) and i<len(df)-1: pending=-1
    if side:
        row=df.iloc[-1]; exit_eff=float(row.close)*(1-cost if side==1 else 1+cost); eq+=side*qty*(exit_eff-entry_eff); trades.append((eq/start_eq-1)*100); curve[-1]=eq
    return metrics(pd.Series(curve,index=df.datetime,dtype=float),trades,df,exposed)
def supertrend_signals(df,n=10,mult=3.):
    a=atr(df,n); hl2=(df.high+df.low)/2; ub=np.array(hl2+mult*a,dtype=float,copy=True); lb=np.array(hl2-mult*a,dtype=float,copy=True); c=np.array(df.close,dtype=float,copy=True); direction=np.ones(len(df),dtype=int)
    for i in range(1,len(df)):
        if np.isnan(a.iloc[i]): continue
        if not np.isnan(lb[i-1]): lb[i]=lb[i] if (lb[i]>lb[i-1] or c[i-1]<lb[i-1]) else lb[i-1]
        if not np.isnan(ub[i-1]): ub[i]=ub[i] if (ub[i]<ub[i-1] or c[i-1]>ub[i-1]) else ub[i-1]
        direction[i]=(1 if c[i]<lb[i] else -1) if direction[i-1]==-1 else (-1 if c[i]>ub[i] else 1)
    d=pd.Series(direction,index=df.index); return ((d==-1)&(d.shift(1)==1)).fillna(False),((d==1)&(d.shift(1)==-1)).fillna(False)
def macd_signals(df):
    line=df.close.ewm(span=30,adjust=False).mean()-df.close.ewm(span=63,adjust=False).mean(); sig=line.ewm(span=30,adjust=False).mean(); return ((line>sig)&(line.shift(1)<=sig.shift(1))).fillna(False),((line<sig)&(line.shift(1)>=sig.shift(1))).fillna(False)
def donchian_signals(df):
    u=df.high.rolling(20).max(); l=df.low.rolling(20).min(); return ((u>u.shift(1))&(u.shift(1)>u.shift(2))).fillna(False),((l<l.shift(1))&(l.shift(1)<l.shift(2))).fillna(False)
def bollinger_backtest(df,cost=.00075):
    mid=df.close.rolling(42).mean(); sd=df.close.rolling(42).std(ddof=0); up=mid+2.5*sd; lo=mid-2.5*sd; ls=(df.close>=up).fillna(False); ss=(df.close<=lo).fillna(False)
    eq=1.; side=0; qty=0.; entry=entry_eff=0.; start_eq=0.; pending=0; stop=target=0.; age=0; trades=[]; curve=[]; exposed=0
    def close(px):
        nonlocal eq,side,qty
        eff=px*(1-cost if side==1 else 1+cost); eq+=side*qty*(eff-entry_eff); trades.append((eq/start_eq-1)*100); side=0; qty=0
    for i,row in df.iterrows():
        if pending and side==0:
            side=pending; pending=0; start_eq=eq; entry=float(row.open); entry_eff=entry*(1+cost if side==1 else 1-cost); qty=eq/entry_eff; stop=entry*(1-.015 if side==1 else 1+.015); target=entry*(1+.03 if side==1 else 1-.03); age=0
        if side:
            exposed+=1; age+=1; sh=float(row.low)<=stop if side==1 else float(row.high)>=stop; th=float(row.high)>=target if side==1 else float(row.low)<=target
            if sh: close(stop)
            elif th: close(target)
            elif age>=18 and i<len(df)-1: close(float(df.iloc[i+1].open))
        curve.append(eq+(side*qty*(float(row.close)-entry_eff) if side else 0))
        if side==0 and i<len(df)-1:
            if bool(ls.iloc[i]): pending=1
            elif bool(ss.iloc[i]): pending=-1
    if side: close(float(df.iloc[-1].close)); curve[-1]=eq
    return metrics(pd.Series(curve,index=df.datetime,dtype=float),trades,df,exposed)
def yearly(name,df,runner):
    rows=[]
    for y in range(int(df.datetime.dt.year.min()),int(df.datetime.dt.year.max())+1):
        sub=df[(df.datetime>=pd.Timestamp(f'{y}-01-01',tz='UTC'))&(df.datetime<pd.Timestamp(f'{y+1}-01-01',tz='UTC'))].reset_index(drop=True)
        if len(sub)>=200: rows.append({'strategy':name,'window':str(y),**runner(sub)})
    return rows
def main():
    d30=load30(); d1=resample(d30,'1h'); rows=[]
    l,s=supertrend_signals(d1); rows.append({'strategy':'SuperTrend10x3_1H','window':'FULL',**reverse_backtest(d1,l,s,.0006)}); rows+=yearly('SuperTrend10x3_1H',d1,lambda x: reverse_backtest(x,*supertrend_signals(x),.0006))
    l,s=macd_signals(d30); rows.append({'strategy':'MACD30_63_30_30M','window':'FULL',**reverse_backtest(d30,l,s,.0006)}); rows+=yearly('MACD30_63_30_30M',d30,lambda x: reverse_backtest(x,*macd_signals(x),.0006))
    l,s=donchian_signals(d1); rows.append({'strategy':'Donchian20Double_1H','window':'FULL',**reverse_backtest(d1,l,s,.0006)}); rows+=yearly('Donchian20Double_1H',d1,lambda x: reverse_backtest(x,*donchian_signals(x),.0006))
    rows.append({'strategy':'BB42x2.5_1H','window':'FULL',**bollinger_backtest(d1,.00075)}); rows+=yearly('BB42x2.5_1H',d1,lambda x: bollinger_backtest(x,.00075))
    out=pd.DataFrame(rows); out.to_csv(OUT/'simple_survivors_long.csv',index=False); print(out[out.window=='FULL'].to_string(index=False)); print('\nYEARS\n'); print(out[out.window!='FULL'].to_string(index=False))
if __name__=='__main__': main()
