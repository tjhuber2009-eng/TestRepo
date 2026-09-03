import os, math, json
import numpy as np
import pandas as pd
import requests
from dual_signal_audit import fetch_bitstamp, run_engine, annualized_metrics, buy_hold_metrics, pine_ema
from rsi_sma_audit import pine_rsi

CLAIM_START='2011-08-18'
CLAIM_END='2026-05-03'
CURRENT_END='2026-09-02'
PUBLIC_GUIDE_APPROX='2026-05-09'  # site post timestamp discovered in public homepage metadata
EARLY_URL='https://raw.githubusercontent.com/nileshiq/Bitcoin-Historical-Prices-Activity-2010-2024-/main/bitcoin_2010-07-27_2024-04-25.csv'
TARGET={'return_pct':41213609.42,'win_pct':60.27,'pf':3.00,'max_dd_pct':-23.36,'trades':73}


def fetch_early():
    r=requests.get(EARLY_URL,timeout=60,headers={'User-Agent':'momentummagic-independent-audit/1.0'}); r.raise_for_status()
    fn='audit_output_momentummagic/early_btc_2010_2024.csv'; open(fn,'wb').write(r.content)
    d=pd.read_csv(fn); d.columns=[c.strip().lstrip('\ufeff') for c in d.columns]
    d['Date']=pd.to_datetime(d['Start'])
    for c in ['Open','High','Low','Close','Volume','Market Cap']: d[c]=pd.to_numeric(d[c],errors='coerce')
    return d.set_index('Date').sort_index()[['Open','High','Low','Close','Volume','Market Cap']].dropna(subset=['Open','Close'])


def linreg(src,n,offset=0):
    y=src.astype(float).to_numpy(); out=np.full(len(y),np.nan)
    x=np.arange(n,dtype=float); xm=x.mean(); den=((x-xm)**2).sum()
    for i in range(n-1,len(y)):
        z=y[i-n+1:i+1]
        if not np.isfinite(z).all(): continue
        ym=z.mean(); slope=((x-xm)*(z-ym)).sum()/den; intercept=ym-slope*xm
        out[i]=intercept+slope*(n-1-offset)
    return pd.Series(out,index=src.index)


def make_state(df,fast=14,slow=42,signal_n=13,trend_n=19,rsi_n=6,exit_variant='opposite_all'):
    c=df.Close.astype(float)
    macd=pine_ema(c,fast)-pine_ema(c,slow)
    sig=pine_ema(macd,signal_n)
    tr=linreg(c,trend_n)
    rsi=pine_rsi(c,rsi_n)
    turn_up=(sig>sig.shift(1))&(sig.shift(1)<=sig.shift(2))
    turn_dn=(sig<sig.shift(1))&(sig.shift(1)>=sig.shift(2))
    buy=turn_up&(tr>tr.shift(1))&(rsi>50)
    if exit_variant=='opposite_all': sell=turn_dn&(tr<tr.shift(1))&(rsi<50)
    elif exit_variant=='signal_turn': sell=turn_dn
    elif exit_variant=='any_filter_fail': sell=turn_dn|(tr<=tr.shift(1))|(rsi<=50)
    else: raise ValueError(exit_variant)
    state=np.zeros(len(df),dtype=np.int8); pos=0
    for i in range(len(df)):
        if bool(buy.iloc[i]): pos=1
        elif bool(sell.iloc[i]): pos=0
        state[i]=pos
    detail=pd.DataFrame({'Close':c,'MACD':macd,'Signal':sig,'TrendLineReg':tr,'RSI6':rsi,'TurnUp':turn_up,'TurnDown':turn_dn,'Buy':buy,'Sell':sell,'State':state},index=df.index)
    return pd.Series(state,index=df.index),detail


def metrics_row(source,df,state,mode,cost,window,start,end,slow,exit_variant):
    eng=run_engine(df,state,cost,mode)
    m=annualized_metrics(eng.equity,eng.position,eng.trades,start,end)
    if not m: return None,eng
    return {'source':source,'window':window,'slow':slow,'exit_variant':exit_variant,'mode':mode,'cost_side_bps':cost*10000,
            'final_from_10000':10000*(1+m['total_return_pct']/100),**m},eng


def stitch(early,bit):
    a=early.loc[:'2012-12-31',['Open','High','Low','Close']].copy()
    b=bit.loc['2013-01-01':,['Open','High','Low','Close']].copy()
    return pd.concat([a,b]).sort_index()[~pd.concat([a,b]).sort_index().index.duplicated(keep='last')]


def closed_trade_dd(eng,start,end):
    # Diagnostic: peak-to-trough using only equity at completed trade exits. This is NOT true mark-to-market drawdown.
    t=eng.trades.copy()
    if not len(t): return np.nan
    t=t[(pd.to_datetime(t.entry_date)>=pd.Timestamp(start))&(pd.to_datetime(t.exit_date)<=pd.Timestamp(end))]
    if not len(t): return np.nan
    e=t.exit_equity_post_fee.astype(float)
    return float((e/e.cummax()-1).min()*100)


def capacity(df,eng):
    rows=[]
    if not len(eng.trades): return pd.DataFrame()
    for _,t in eng.trades.iterrows():
        dt=pd.Timestamp(t.entry_date)
        if dt not in df.index: continue
        eq=float(t.entry_equity_pre_fee)*10000
        vol=float(df.loc[dt].get('Volume',np.nan)); cap=float(df.loc[dt].get('Market Cap',np.nan))
        rows.append({'date':dt,'notional_usd':eq,'daily_volume':vol,'market_cap':cap,
                     'pct_daily_volume':100*eq/vol if np.isfinite(vol) and vol>0 else np.nan,
                     'pct_market_cap':100*eq/cap if np.isfinite(cap) and cap>0 else np.nan})
    return pd.DataFrame(rows)


def main():
    os.makedirs('audit_output_momentummagic',exist_ok=True)
    early=fetch_early(); bit=fetch_bitstamp(); hybrid=stitch(early,bit)
    rows=[]; default_eng=None
    costs=[0.0,0.0015,0.005]
    # Exact publicly recovered preset architecture. Slow=42 is primary screenshot reading; 72 retained as OCR ambiguity stress.
    for source,df in [('hybrid_early_plus_bitstamp',hybrid),('bitstamp',bit)]:
      for slow in [42,72]:
       for exit_variant in ['opposite_all','signal_turn']:
        state,detail=make_state(df,slow=slow,exit_variant=exit_variant)
        if source=='hybrid_early_plus_bitstamp' and slow==42 and exit_variant=='opposite_all': detail.to_csv('audit_output_momentummagic/default_signal.csv')
        for mode in ['same_close','next_open','next_close']:
         for cost in costs:
          windows=[]
          if source.startswith('hybrid'):
              windows=[('claim_full',CLAIM_START,CLAIM_END),('2013_claim','2013-01-01',CLAIM_END),('2018_claim','2018-01-01',CLAIM_END),('2022_claim','2022-01-01',CLAIM_END)]
          else:
              windows=[('bitstamp_2013_claim','2013-01-01',CLAIM_END),('post_current_version',PUBLIC_GUIDE_APPROX,CURRENT_END),('2023_current','2023-01-01',CURRENT_END),('2024_current','2024-01-01',CURRENT_END)]
          eng=None
          for w,ws,we in windows:
              rr,eng=metrics_row(source,df,state,mode,cost,w,ws,we,slow,exit_variant)
              if rr:
                  rr['closed_trade_only_dd_pct']=closed_trade_dd(eng,ws,we)
                  rows.append(rr)
          if source.startswith('hybrid') and slow==42 and exit_variant=='opposite_all' and mode=='next_open' and cost==0:
              default_eng=eng; eng.trades.to_csv('audit_output_momentummagic/default_trades.csv',index=False)
    out=pd.DataFrame(rows); out.to_csv('audit_output_momentummagic/results.csv',index=False)

    # Buy/hold references.
    bh=[]
    for source,d,windows in [
      ('hybrid_early_plus_bitstamp',hybrid,[('claim_full',CLAIM_START,CLAIM_END),('2013_claim','2013-01-01',CLAIM_END),('2018_claim','2018-01-01',CLAIM_END)]),
      ('bitstamp',bit,[('post_current_version',PUBLIC_GUIDE_APPROX,CURRENT_END),('2023_current','2023-01-01',CURRENT_END),('2024_current','2024-01-01',CURRENT_END)])]:
        for w,ws,we in windows:
            m=buy_hold_metrics(d,ws,we)
            if m: bh.append({'source':source,'window':w,**m})
    pd.DataFrame(bh).to_csv('audit_output_momentummagic/buy_hold.csv',index=False)

    # Constrained diagnostic grid: only slow period varies; all other published BTC preset values frozen.
    diag=[]
    for slow in range(30,91):
        state,_=make_state(hybrid,slow=slow,exit_variant='opposite_all')
        for mode in ['same_close','next_open']:
            eng=run_engine(hybrid,state,0,mode); m=annualized_metrics(eng.equity,eng.position,eng.trades,CLAIM_START,CLAIM_END)
            if not m: continue
            score=abs(m['closed_trades']-TARGET['trades'])*5 + abs(m['winning_trades']/max(m['closed_trades'],1)*100-TARGET['win_pct']) + abs(math.log(max((1+m['total_return_pct']/100)/(1+TARGET['return_pct']/100),1e-12)))
            diag.append({'slow':slow,'mode':mode,'score':score,**m,'closed_trade_only_dd_pct':closed_trade_dd(eng,CLAIM_START,CLAIM_END)})
    diag=pd.DataFrame(diag).sort_values('score'); diag.to_csv('audit_output_momentummagic/slow_diagnostic.csv',index=False)

    cap=capacity(early,default_eng) if default_eng is not None else pd.DataFrame(); cap.to_csv('audit_output_momentummagic/capacity.csv',index=False)

    focus=out[(out.source=='hybrid_early_plus_bitstamp')&(out.window=='claim_full')&(out.exit_variant=='opposite_all') & (out.cost_side_bps.isin([0,15,50])) & (out.mode.isin(['same_close','next_open']))]
    recent=out[(out.source=='bitstamp')&(out.window.isin(['post_current_version','2023_current']))&(out.exit_variant=='opposite_all')&(out.slow==42)&(out.mode=='next_open')]
    print('=== MOMENTUMMAGIC CLAIM TARGET ==='); print(json.dumps(TARGET,indent=2))
    print('\n=== PRIMARY RECONSTRUCTION ==='); print(focus[['slow','mode','cost_side_bps','total_return_pct','cagr_pct','max_dd_pct','closed_trade_only_dd_pct','profit_factor','closed_trades','winning_trades']].to_string(index=False,float_format=lambda x:f'{x:.3f}'))
    print('\n=== RECENT / FORWARD ==='); print(recent[['window','cost_side_bps','total_return_pct','cagr_pct','max_dd_pct','profit_factor','closed_trades','winning_trades']].to_string(index=False,float_format=lambda x:f'{x:.3f}'))
    print('\n=== SLOW DIAGNOSTIC TOP 20 ==='); print(diag[['slow','mode','score','total_return_pct','cagr_pct','max_dd_pct','closed_trade_only_dd_pct','profit_factor','closed_trades','winning_trades']].head(20).to_string(index=False,float_format=lambda x:f'{x:.3f}'))
    print('\n=== BUY HOLD ==='); print(pd.DataFrame(bh).to_string(index=False,float_format=lambda x:f'{x:.3f}'))
    if len(cap):
        print('\n=== CAPACITY ===')
        print(cap.sort_values('date').tail(15).to_string(index=False,float_format=lambda x:f'{x:.4f}'))

    summary={'target':TARGET,'primary':focus.to_dict('records'),'recent':recent.to_dict('records'),'slow_top10':diag.head(10).to_dict('records'),'buy_hold':bh}
    with open('audit_output_momentummagic/summary.json','w') as f: json.dump(summary,f,indent=2,default=str)

if __name__=='__main__': main()
