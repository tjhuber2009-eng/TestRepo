#!/usr/bin/env python3
from __future__ import annotations
import json, time
from pathlib import Path
import numpy as np
import pandas as pd
import requests

OUT=Path('indicator_validation/output_sentinel'); OUT.mkdir(parents=True,exist_ok=True)
SYMBOLS=['BTCUSDT','ETHUSDT','BNBUSDT','SOLUSDT','XRPUSDT']
START=pd.Timestamp('2020-01-01',tz='UTC')
END=pd.Timestamp('2026-09-03',tz='UTC')
LOOKBACK=65
THRESH=0.5
COST=0.0007

def fetch_daily(symbol):
    url='https://api.binance.com/api/v3/klines'; rows=[]; cur=int(START.timestamp()*1000); end=int(END.timestamp()*1000)
    while cur<end:
        r=requests.get(url,params={'symbol':symbol,'interval':'1d','startTime':cur,'endTime':end-1,'limit':1000},timeout=30); r.raise_for_status(); data=r.json()
        if not data: break
        rows.extend(data); nxt=int(data[-1][0])+86400000
        if nxt<=cur: break
        cur=nxt; time.sleep(.05)
    d=pd.DataFrame(rows,columns=['open_time','open','high','low','close','volume','close_time','quote_volume','trades','taker_base','taker_quote','ignore'])
    if len(d)==0: return d
    d['datetime']=pd.to_datetime(d.open_time,unit='ms',utc=True)
    for c in ['open','high','low','close']: d[c]=pd.to_numeric(d[c])
    return d[['datetime','open','high','low','close']].drop_duplicates('datetime').sort_values('datetime').reset_index(drop=True)

def signals(df,n=LOOKBACK,thr=THRESH):
    ema=df.close.ewm(span=n,adjust=False).mean(); sd=df.close.rolling(n).std(ddof=0); z=(df.close-ema)/sd
    return (z>thr).fillna(False),(z<-thr).fillna(False),z

def backtest(df,n=LOOKBACK,thr=THRESH,cost=COST):
    bull,bear,z=signals(df,n,thr); eq=1.; pos=False; qty=0.; entry_eff=0.; start_eq=0.; pending=None; curve=[]; trs=[]; exposed=0; entry_time=None
    for i,row in df.iterrows():
        if pending=='enter' and not pos:
            px=float(row.open); entry_eff=px*(1+cost); qty=eq/entry_eff; start_eq=eq; pos=True; entry_time=row.datetime; pending=None
        elif pending=='exit' and pos:
            px=float(row.open); eq+=qty*(px*(1-cost)-entry_eff); trs.append({'entry':entry_time,'exit':row.datetime,'ret_pct':(eq/start_eq-1)*100}); pos=False; qty=0.; pending=None
        curve.append(eq+qty*(float(row.close)-entry_eff) if pos else eq)
        if pos: exposed+=1
        if i<len(df)-1:
            if not pos and bool(bull.iloc[i]): pending='enter'
            elif pos and bool(bear.iloc[i]): pending='exit'
    if pos:
        px=float(df.iloc[-1].close); eq+=qty*(px*(1-cost)-entry_eff); trs.append({'entry':entry_time,'exit':df.iloc[-1].datetime,'ret_pct':(eq/start_eq-1)*100}); curve[-1]=eq
    e=pd.Series(curve,index=df.datetime,dtype=float); tr=pd.DataFrame(trs); gp=tr.loc[tr.ret_pct>0,'ret_pct'].sum() if len(tr) else 0; gl=-tr.loc[tr.ret_pct<=0,'ret_pct'].sum() if len(tr) else 0
    years=max((df.datetime.iloc[-1]-df.datetime.iloc[0]).total_seconds()/(365.25*86400),1/365.25)
    bh=(df.close.iloc[-1]/df.open.iloc[0]-1)*100; bhc=df.close/df.open.iloc[0]; bhdd=(bhc/bhc.cummax()-1).min()*100
    return {'start':str(df.datetime.iloc[0]),'end':str(df.datetime.iloc[-1]),'rows':len(df),'net_pct':(eq-1)*100,'cagr_pct':(eq**(1/years)-1)*100 if eq>0 else -100,'pf':gp/gl if gl else (99 if gp else 0),'win_pct':(tr.ret_pct>0).mean()*100 if len(tr) else 0,'trades':len(tr),'maxdd_pct':(e/e.cummax()-1).min()*100,'exposure_pct':exposed/max(len(df)-1,1)*100,'bnh_pct':bh,'bnh_cagr_pct':((df.close.iloc[-1]/df.open.iloc[0])**(1/years)-1)*100,'bnh_maxdd_pct':bhdd},tr,e,z

def main():
    rows=[]; stress=[]
    for sym in SYMBOLS:
        df=fetch_daily(sym)
        if len(df)<200: continue
        m,tr,e,z=backtest(df); rows.append({'symbol':sym,**m}); tr.to_csv(OUT/f'{sym}_trades.csv',index=False)
        # Robustness surface, not optimization. Same predeclared neighborhood for every symbol.
        for n in [50,60,65,70,80]:
            for thr in [.4,.5,.6]:
                mm,_,_,_=backtest(df,n,thr); stress.append({'symbol':sym,'lookback':n,'threshold':thr,**mm})
    pd.DataFrame(rows).to_csv(OUT/'sentinel_crossasset.csv',index=False); pd.DataFrame(stress).to_csv(OUT/'sentinel_crossasset_robustness.csv',index=False)
    print(pd.DataFrame(rows).to_string(index=False)); print('\nROBUSTNESS SUMMARY\n'); s=pd.DataFrame(stress); print(s.groupby('symbol').agg(cells=('net_pct','size'),positive_cells=('net_pct',lambda x:int((x>0).sum())),median_cagr=('cagr_pct','median'),worst_cagr=('cagr_pct','min'),median_pf=('pf','median'),worst_dd=('maxdd_pct','min')).to_string())
if __name__=='__main__': main()
