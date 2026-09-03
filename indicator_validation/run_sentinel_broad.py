#!/usr/bin/env python3
from __future__ import annotations
import json
from pathlib import Path
import numpy as np
import pandas as pd
import yfinance as yf

OUT=Path('indicator_validation/output_sentinel_broad'); OUT.mkdir(parents=True,exist_ok=True)
CRYPTO=['BTC-USD','ETH-USD','BNB-USD','SOL-USD','XRP-USD','ADA-USD','DOGE-USD','LTC-USD','BCH-USD','LINK-USD','DOT-USD','AVAX-USD']
ETF=['SPY','QQQ','IWM','GLD','TLT','EEM','EFA']
LOOKBACK=65; THRESH=0.5; COST=0.0007
END='2026-09-03'

def fetch(sym,start):
    x=yf.download(sym,start=start,end=END,interval='1d',auto_adjust=False,progress=False,threads=False)
    if x is None or len(x)==0: return pd.DataFrame()
    if isinstance(x.columns,pd.MultiIndex): x.columns=x.columns.get_level_values(0)
    x=x.rename(columns=str.lower).reset_index(); x=x.rename(columns={x.columns[0]:'datetime'})
    x['datetime']=pd.to_datetime(x['datetime'],utc=True)
    return x[['datetime','open','high','low','close']].dropna().sort_values('datetime').reset_index(drop=True)

def signal(df,n=LOOKBACK,thr=THRESH):
    ema=df.close.ewm(span=n,adjust=False).mean(); sd=df.close.rolling(n).std(ddof=0); z=(df.close-ema)/sd
    return (z>thr).fillna(False),(z<-thr).fillna(False)

def bt(df,n=LOOKBACK,thr=THRESH,cost=COST):
    bull,bear=signal(df,n,thr); eq=1.; pos=False; qty=0.; entry_eff=0.; start_eq=0.; pending=None; entry_time=None
    curve=[]; trades=[]; exposed=0
    for i,row in df.iterrows():
        if pending=='enter' and not pos:
            px=float(row.open); entry_eff=px*(1+cost); qty=eq/entry_eff; start_eq=eq; pos=True; entry_time=row.datetime; pending=None
        elif pending=='exit' and pos:
            px=float(row.open); eq += qty*(px*(1-cost)-entry_eff); trades.append((entry_time,row.datetime,(eq/start_eq-1)*100)); pos=False; qty=0.; pending=None
        mtm=eq+qty*(float(row.close)-entry_eff) if pos else eq; curve.append(mtm)
        if pos: exposed+=1
        if i<len(df)-1:
            if (not pos) and bool(bull.iloc[i]): pending='enter'
            elif pos and bool(bear.iloc[i]): pending='exit'
    if pos:
        px=float(df.iloc[-1].close); eq += qty*(px*(1-cost)-entry_eff); trades.append((entry_time,df.iloc[-1].datetime,(eq/start_eq-1)*100)); curve[-1]=eq
    e=pd.Series(curve,index=df.datetime,dtype=float); tr=pd.DataFrame(trades,columns=['entry','exit','ret_pct'])
    years=max((df.datetime.iloc[-1]-df.datetime.iloc[0]).total_seconds()/(365.25*86400),1/365.25)
    gp=tr.loc[tr.ret_pct>0,'ret_pct'].sum() if len(tr) else 0.; gl=-tr.loc[tr.ret_pct<=0,'ret_pct'].sum() if len(tr) else 0.
    bh_curve=df.close/float(df.open.iloc[0]); annual=e.resample('YE').last().pct_change().dropna()*100
    return {
      'net_pct':(eq-1)*100,'cagr_pct':(eq**(1/years)-1)*100 if eq>0 else -100.,'pf':gp/gl if gl else (99. if gp else 0.),
      'win_pct':float((tr.ret_pct>0).mean()*100) if len(tr) else 0.,'trades':len(tr),'maxdd_pct':float((e/e.cummax()-1).min()*100),
      'exposure_pct':exposed/max(len(df)-1,1)*100,'mar':((eq**(1/years)-1)*100)/abs(float((e/e.cummax()-1).min()*100)) if float((e/e.cummax()-1).min())<0 else np.nan,
      'bnh_pct':(float(df.close.iloc[-1])/float(df.open.iloc[0])-1)*100,'bnh_cagr_pct':((float(df.close.iloc[-1])/float(df.open.iloc[0]))**(1/years)-1)*100,
      'bnh_maxdd_pct':float((bh_curve/bh_curve.cummax()-1).min()*100),'positive_years':int((annual>0).sum()),'years_count':int(len(annual)),
      'start':str(df.datetime.iloc[0]),'end':str(df.datetime.iloc[-1]),'rows':len(df)
    },tr,e

def main():
    rows=[]; stress=[]
    for domain,symbols,start in [('crypto',CRYPTO,'2020-01-01'),('etf',ETF,'2010-01-01')]:
      for sym in symbols:
        df=fetch(sym,start)
        if len(df)<200: continue
        m,tr,e=bt(df); rows.append({'domain':domain,'symbol':sym,**m}); tr.to_csv(OUT/f"{sym.replace('-','')}_trades.csv",index=False)
        for n in [50,60,65,70,80]:
          for thr in [.4,.5,.6]:
            mm,_,_=bt(df,n,thr); stress.append({'domain':domain,'symbol':sym,'lookback':n,'threshold':thr,**mm})
    r=pd.DataFrame(rows); s=pd.DataFrame(stress)
    r.to_csv(OUT/'sentinel_broad.csv',index=False); s.to_csv(OUT/'sentinel_broad_robustness.csv',index=False)
    print(r.to_string(index=False))
    print('\nDOMAIN SUMMARY')
    print(r.groupby('domain').agg(assets=('symbol','size'),positive=('net_pct',lambda x:int((x>0).sum())),beat_bh=('net_pct',lambda x:0),median_cagr=('cagr_pct','median'),median_dd=('maxdd_pct','median'),median_pf=('pf','median')).to_string())
    print('\nROBUSTNESS')
    print(s.groupby(['domain','symbol']).agg(cells=('net_pct','size'),positive_cells=('net_pct',lambda x:int((x>0).sum())),median_cagr=('cagr_pct','median'),worst_cagr=('cagr_pct','min'),median_pf=('pf','median'),worst_dd=('maxdd_pct','min')).to_string())
if __name__=='__main__': main()
