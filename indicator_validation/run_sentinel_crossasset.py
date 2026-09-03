#!/usr/bin/env python3
from __future__ import annotations
from pathlib import Path
import pandas as pd
import yfinance as yf

OUT=Path('indicator_validation/output_sentinel'); OUT.mkdir(parents=True,exist_ok=True)
SYMBOLS=['BTC-USD','ETH-USD','BNB-USD','SOL-USD','XRP-USD']
START='2020-01-01'; END='2026-09-03'; LOOKBACK=65; THRESH=.5; COST=.0007

def fetch_daily(symbol):
    d=yf.download(symbol,start=START,end=END,interval='1d',auto_adjust=False,progress=False,threads=False)
    if len(d)==0: return pd.DataFrame()
    if isinstance(d.columns,pd.MultiIndex): d.columns=d.columns.get_level_values(0)
    d=d.reset_index(); d.columns=[str(c).lower() for c in d.columns]
    t='date' if 'date' in d.columns else 'datetime'; d['datetime']=pd.to_datetime(d[t],utc=True)
    for c in ['open','high','low','close']: d[c]=pd.to_numeric(d[c],errors='coerce')
    return d[['datetime','open','high','low','close']].dropna().sort_values('datetime').drop_duplicates('datetime').reset_index(drop=True)

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
    years=max((df.datetime.iloc[-1]-df.datetime.iloc[0]).total_seconds()/(365.25*86400),1/365.25); bh=(df.close.iloc[-1]/df.open.iloc[0]-1)*100; bhc=df.close/df.open.iloc[0]; bhdd=(bhc/bhc.cummax()-1).min()*100
    return {'start':str(df.datetime.iloc[0]),'end':str(df.datetime.iloc[-1]),'rows':len(df),'net_pct':(eq-1)*100,'cagr_pct':(eq**(1/years)-1)*100 if eq>0 else -100,'pf':gp/gl if gl else (99 if gp else 0),'win_pct':(tr.ret_pct>0).mean()*100 if len(tr) else 0,'trades':len(tr),'maxdd_pct':(e/e.cummax()-1).min()*100,'exposure_pct':exposed/max(len(df)-1,1)*100,'bnh_pct':bh,'bnh_cagr_pct':((df.close.iloc[-1]/df.open.iloc[0])**(1/years)-1)*100,'bnh_maxdd_pct':bhdd},tr,e,z

def main():
    rows=[]; stress=[]
    for sym in SYMBOLS:
        df=fetch_daily(sym)
        if len(df)<200: continue
        m,tr,_,_=backtest(df); rows.append({'symbol':sym,**m}); tr.to_csv(OUT/f'{sym.replace("-","")}_trades.csv',index=False)
        for n in [50,60,65,70,80]:
            for thr in [.4,.5,.6]:
                mm,_,_,_=backtest(df,n,thr); stress.append({'symbol':sym,'lookback':n,'threshold':thr,**mm})
    r=pd.DataFrame(rows); s=pd.DataFrame(stress); r.to_csv(OUT/'sentinel_crossasset.csv',index=False); s.to_csv(OUT/'sentinel_crossasset_robustness.csv',index=False)
    print(r.to_string(index=False)); print('\nROBUSTNESS SUMMARY\n'); print(s.groupby('symbol').agg(cells=('net_pct','size'),positive_cells=('net_pct',lambda x:int((x>0).sum())),median_cagr=('cagr_pct','median'),worst_cagr=('cagr_pct','min'),median_pf=('pf','median'),worst_dd=('maxdd_pct','min')).to_string())
if __name__=='__main__': main()
