import os, json, math
import numpy as np
import pandas as pd
import requests

from dual_signal_audit import fetch_bitstamp, run_engine, annualized_metrics, buy_hold_metrics, pine_ema

EARLY_URL='https://raw.githubusercontent.com/nileshiq/Bitcoin-Historical-Prices-Activity-2010-2024-/main/bitcoin_2010-07-27_2024-04-25.csv'
GUIDE_START='2011-08-18'
GUIDE_END='2025-07-31'
CURRENT_END='2026-09-02'
PUBLIC_DATE='2025-07-28'

GUIDE_TARGET={'return_pct':28440467.49,'win_pct':46.81,'pf':2.48,'max_dd_pct':-32.0,'trades':188}
LANDING_TARGET={'return_pct':29890452.0,'win_pct':47.03,'pf':2.71,'max_dd_pct':-32.0,'trades':185}


def fetch_early():
    os.makedirs('audit_output_trendfusion',exist_ok=True)
    r=requests.get(EARLY_URL,timeout=60,headers={'User-Agent':'trendfusion-independent-audit/1.0'}); r.raise_for_status()
    fn='audit_output_trendfusion/early_btc_2010_2024.csv'; open(fn,'wb').write(r.content)
    d=pd.read_csv(fn); d.columns=[c.strip().lstrip('\ufeff') for c in d.columns]; d['Date']=pd.to_datetime(d['Start'])
    for c in ['Open','High','Low','Close','Volume','Market Cap']:
        if c in d.columns: d[c]=pd.to_numeric(d[c],errors='coerce')
    return d.set_index('Date').sort_index()[['Open','High','Low','Close','Volume','Market Cap']].dropna(subset=['Open','High','Low','Close'])


def stitch(early, bit):
    a=early.loc[:'2012-12-31',['Open','High','Low','Close']].copy()
    b=bit.loc['2013-01-01':,['Open','High','Low','Close']].copy()
    x=pd.concat([a,b]).sort_index()
    return x[~x.index.duplicated(keep='last')]


def pine_rma(s,n):
    """Pine-style Wilder RMA: SMA seed of first n finite observations, then recursive alpha=1/n."""
    x=s.astype(float).to_numpy(); out=np.full(len(x),np.nan); alpha=1.0/n
    seed_i=None
    for i in range(n-1,len(x)):
        w=x[i-n+1:i+1]
        if np.isfinite(w).all():
            out[i]=w.mean(); seed_i=i; break
    if seed_i is None: return pd.Series(out,index=s.index)
    prev=out[seed_i]
    for i in range(seed_i+1,len(x)):
        if np.isfinite(x[i]):
            prev=alpha*x[i]+(1-alpha)*prev; out[i]=prev
    return pd.Series(out,index=s.index)


def dmi_adx(df,di_n=9,adx_n=14):
    h=df.High.astype(float); l=df.Low.astype(float); c=df.Close.astype(float)
    up=h.diff(); down=-l.diff()
    plus_dm=pd.Series(np.where((up>down)&(up>0),up,0.0),index=df.index)
    minus_dm=pd.Series(np.where((down>up)&(down>0),down,0.0),index=df.index)
    prev=c.shift(1)
    tr=pd.concat([(h-l).abs(),(h-prev).abs(),(l-prev).abs()],axis=1).max(axis=1)
    tr.iloc[0]=np.nan
    atr=pine_rma(tr,di_n); psm=pine_rma(plus_dm.where(tr.notna()),di_n); msm=pine_rma(minus_dm.where(tr.notna()),di_n)
    plus=100*psm/atr; minus=100*msm/atr
    denom=plus+minus
    dx=(100*(plus-minus).abs()/denom).where(denom!=0)
    adx=pine_rma(dx,adx_n)
    return plus,minus,adx


def zlema(close,n=35):
    lag=(n-1)//2
    adjusted=close.astype(float)+(close.astype(float)-close.astype(float).shift(lag))
    return pine_ema(adjusted,n)


def linreg(src,n):
    y=src.astype(float).to_numpy(); out=np.full(len(y),np.nan); x=np.arange(n,dtype=float); xm=x.mean(); den=((x-xm)**2).sum()
    for i in range(n-1,len(y)):
        w=y[i-n+1:i+1]
        if np.isfinite(w).all():
            ym=w.mean(); slope=((x-xm)*(w-ym)).sum()/den; out[i]=(ym-slope*xm)+slope*(n-1)
    return pd.Series(out,index=src.index)


def wma(src,n):
    weights=np.arange(1,n+1,dtype=float); den=weights.sum()
    return src.astype(float).rolling(n).apply(lambda x: float(np.dot(x,weights)/den),raw=True)


def ma_series(close,n,kind):
    if kind=='ZLEMA': return zlema(close,n)
    if kind=='EMA': return pine_ema(close,n)
    if kind=='SMA': return close.astype(float).rolling(n).mean()
    if kind=='WMA': return wma(close,n)
    if kind=='LineReg': return linreg(close,n)
    raise ValueError(kind)


def make_state(df,ma_n=35,ma_kind='ZLEMA',di_n=9,adx_n=14,level=21,start_gate=None):
    ma=ma_series(df.Close,ma_n,ma_kind); plus,minus,adx=dmi_adx(df,di_n,adx_n)
    cond=(adx>level)&(plus>minus)&(ma>ma.shift(1))
    state=cond.fillna(False).astype(np.int8)
    # Backtest claim begins at first available Bitstamp-era date; permit indicator warm-up but no earlier trading.
    if start_gate is not None: state.loc[state.index < pd.Timestamp(start_gate)]=0
    detail=pd.DataFrame({'Close':df.Close,'MA':ma,'PlusDI':plus,'MinusDI':minus,'ADX':adx,'LongCondition':cond,'State':state},index=df.index)
    return state,detail


def closed_trade_dd(eng,start,end):
    t=eng.trades.copy()
    if not len(t): return np.nan
    t=t[(pd.to_datetime(t.entry_date)>=pd.Timestamp(start))&(pd.to_datetime(t.exit_date)<=pd.Timestamp(end))]
    if not len(t): return np.nan
    e=t.exit_equity_post_fee.astype(float); return float((e/e.cummax()-1).min()*100)


def return_pf(eng,start,end):
    """Alternate PF using non-compounded trade returns, diagnostic for custom indicator stats."""
    t=eng.trades.copy()
    if not len(t): return np.nan
    t=t[(pd.to_datetime(t.entry_date)>=pd.Timestamp(start))&(pd.to_datetime(t.exit_date)<=pd.Timestamp(end))]
    if not len(t): return np.nan
    r=t.return_pct.astype(float); gp=r[r>0].sum(); gl=-r[r<0].sum(); return float(gp/gl) if gl>0 else np.inf


def row(df,state,source,window,start,end,mode,cost,params):
    eng=run_engine(df,state,cost,mode); m=annualized_metrics(eng.equity,eng.position,eng.trades,start,end)
    if not m: return None,eng
    return {'source':source,'window':window,'mode':mode,'cost_side_bps':cost*10000,**params,**m,
            'closed_trade_only_dd_pct':closed_trade_dd(eng,start,end),'return_based_pf':return_pf(eng,start,end)},eng


def fresh_window(df,params,start,end,mode='next_open',cost=0):
    # Diagnostic fresh-capital subperiod: indicators retain prehistory for warm-up, but trading is disabled before start.
    state,_=make_state(df,**params,start_gate=start); eng=run_engine(df,state,cost,mode)
    return annualized_metrics(eng.equity,eng.position,eng.trades,start,end)


def capacity(early,eng):
    rows=[]
    for _,t in eng.trades.iterrows():
        dt=pd.Timestamp(t.entry_date)
        if dt not in early.index: continue
        notional=float(t.entry_equity_pre_fee)*10000
        vol=float(early.loc[dt].get('Volume',np.nan)); cap=float(early.loc[dt].get('Market Cap',np.nan))
        rows.append({'date':dt,'notional_usd':notional,'daily_volume':vol,'market_cap':cap,
                     'pct_daily_volume':100*notional/vol if np.isfinite(vol) and vol>0 else np.nan,
                     'pct_market_cap':100*notional/cap if np.isfinite(cap) and cap>0 else np.nan})
    return pd.DataFrame(rows)


def main():
    early=fetch_early(); bit=fetch_bitstamp(); hybrid=stitch(early,bit)
    primary_params={'ma_n':35,'ma_kind':'ZLEMA','di_n':9,'adx_n':14,'level':21}
    rows=[]; primary_eng=None
    for source,df in [('hybrid',hybrid),('bitstamp',bit)]:
        state,detail=make_state(df,**primary_params,start_gate=GUIDE_START if source=='hybrid' else None)
        if source=='hybrid': detail.to_csv('audit_output_trendfusion/primary_signal.csv')
        windows=[('guide_claim',GUIDE_START,GUIDE_END),('2013_guide','2013-01-01',GUIDE_END),('2018_guide','2018-01-01',GUIDE_END),('2022_guide','2022-01-01',GUIDE_END)] if source=='hybrid' else [('public_forward','2025-07-29',CURRENT_END),('2023_current','2023-01-01',CURRENT_END),('2024_current','2024-01-01',CURRENT_END)]
        for mode in ['same_close','next_open','next_close']:
            for cost in [0.0,0.0015,0.005]:
                eng=None
                for w,ws,we in windows:
                    rr,eng=row(df,state,source,w,ws,we,mode,cost,primary_params)
                    if rr: rows.append(rr)
                if source=='hybrid' and mode=='next_open' and cost==0:
                    primary_eng=eng; eng.trades.to_csv('audit_output_trendfusion/primary_trades.csv',index=False)
    results=pd.DataFrame(rows); results.to_csv('audit_output_trendfusion/results.csv',index=False)

    # One-factor-at-a-time robustness around public preset. No joint optimization.
    sens=[]
    tests=[]
    for v in range(25,46): tests.append(('ma_n',v,{**primary_params,'ma_n':v}))
    for v in range(7,12): tests.append(('di_n',v,{**primary_params,'di_n':v}))
    for v in range(12,17): tests.append(('adx_n',v,{**primary_params,'adx_n':v}))
    for v in range(18,25): tests.append(('level',v,{**primary_params,'level':v}))
    for k in ['SMA','EMA','WMA','LineReg']: tests.append(('ma_kind',k,{**primary_params,'ma_kind':k}))
    for axis,val,p in tests:
        st,_=make_state(hybrid,**p,start_gate=GUIDE_START); eng=run_engine(hybrid,st,0,'next_open'); m=annualized_metrics(eng.equity,eng.position,eng.trades,GUIDE_START,GUIDE_END)
        if m:
            score=abs(m['closed_trades']-GUIDE_TARGET['trades'])*2 + abs(m['winning_trades']/max(m['closed_trades'],1)*100-GUIDE_TARGET['win_pct']) + abs(math.log(max((1+m['total_return_pct']/100)/(1+GUIDE_TARGET['return_pct']/100),1e-12)))
            sens.append({'axis':axis,'value':val,'score':score,**m,'closed_trade_only_dd_pct':closed_trade_dd(eng,GUIDE_START,GUIDE_END),'return_based_pf':return_pf(eng,GUIDE_START,GUIDE_END)})
    sens=pd.DataFrame(sens).sort_values('score'); sens.to_csv('audit_output_trendfusion/sensitivity.csv',index=False)

    # Benchmarks + fresh-capital subperiod diagnostics.
    bh=[]
    for w,ws,we in [('guide_claim',GUIDE_START,GUIDE_END),('2013_guide','2013-01-01',GUIDE_END),('2018_guide','2018-01-01',GUIDE_END),('public_forward','2025-07-29',CURRENT_END),('2023_current','2023-01-01',CURRENT_END)]:
        d=hybrid if w in ['guide_claim','2013_guide','2018_guide'] else bit; m=buy_hold_metrics(d,ws,we)
        if m: bh.append({'window':w,**m})
    pd.DataFrame(bh).to_csv('audit_output_trendfusion/buy_hold.csv',index=False)
    fresh=[]
    for w,ws,we in [('2013_guide','2013-01-01',GUIDE_END),('2018_guide','2018-01-01',GUIDE_END),('2022_guide','2022-01-01',GUIDE_END),('public_forward','2025-07-29',CURRENT_END),('2023_current','2023-01-01',CURRENT_END)]:
        d=hybrid if w.endswith('guide') else bit; m=fresh_window(d,primary_params,ws,we,'next_open',0)
        if m: fresh.append({'window':w,**m})
    pd.DataFrame(fresh).to_csv('audit_output_trendfusion/fresh_windows.csv',index=False)

    cap=capacity(early,primary_eng) if primary_eng is not None else pd.DataFrame(); cap.to_csv('audit_output_trendfusion/capacity.csv',index=False)

    focus=results[(results.source=='hybrid')&(results.window=='guide_claim')]
    recent=results[(results.source=='bitstamp')&(results['mode']=='next_open')&(results.window.isin(['public_forward','2023_current']))]
    print('=== TRENDFUSION GUIDE TARGET ==='); print(json.dumps(GUIDE_TARGET,indent=2)); print('=== CURRENT LANDING TARGET ==='); print(json.dumps(LANDING_TARGET,indent=2))
    print('\n=== PRIMARY PUBLIC PRESET ==='); print(focus[['mode','cost_side_bps','total_return_pct','cagr_pct','max_dd_pct','closed_trade_only_dd_pct','profit_factor','return_based_pf','closed_trades','winning_trades']].to_string(index=False,float_format=lambda x:f'{x:.3f}'))
    print('\n=== RECENT / PUBLIC FORWARD ==='); print(recent[['window','cost_side_bps','total_return_pct','cagr_pct','max_dd_pct','profit_factor','closed_trades','winning_trades']].to_string(index=False,float_format=lambda x:f'{x:.3f}'))
    print('\n=== SENSITIVITY TOP 25 ==='); print(sens[['axis','value','score','total_return_pct','cagr_pct','max_dd_pct','profit_factor','return_based_pf','closed_trades','winning_trades']].head(25).to_string(index=False,float_format=lambda x:f'{x:.3f}'))
    print('\n=== BUY HOLD ==='); print(pd.DataFrame(bh).to_string(index=False,float_format=lambda x:f'{x:.3f}'))
    print('\n=== FRESH WINDOWS ==='); print(pd.DataFrame(fresh).to_string(index=False,float_format=lambda x:f'{x:.3f}'))
    if len(cap): print('\n=== CAPACITY TAIL ==='); print(cap.tail(15).to_string(index=False,float_format=lambda x:f'{x:.4f}'))

    summary={'guide_target':GUIDE_TARGET,'landing_target':LANDING_TARGET,'public_preset':primary_params,
             'primary':focus.to_dict('records'),'recent':recent.to_dict('records'),'sensitivity_top15':sens.head(15).to_dict('records'),'buy_hold':bh,'fresh':fresh}
    with open('audit_output_trendfusion/summary.json','w') as f: json.dump(summary,f,indent=2,default=str)

if __name__=='__main__': main()
