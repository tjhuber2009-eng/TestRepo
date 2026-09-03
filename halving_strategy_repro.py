import json, math, os, time
import numpy as np
import pandas as pd
import requests

CLAIM_RETURN_PCT = 126_946_455.02
CLAIM_FINAL = 10_000 * (1 + CLAIM_RETURN_PCT / 100)
INITIAL = 10_000.0
CURRENT_END = pd.Timestamp('2026-09-02')
HALVINGS = [pd.Timestamp(x) for x in ['2012-11-28','2016-07-09','2020-05-11','2024-04-19']]
EARLY_URL = 'https://raw.githubusercontent.com/nileshiq/Bitcoin-Historical-Prices-Activity-2010-2024-/main/bitcoin_2010-07-27_2024-04-25.csv'


def fetch_early():
    r=requests.get(EARLY_URL, timeout=60, headers={'User-Agent':'halving-independent-repro/1.0'})
    r.raise_for_status()
    d=pd.read_csv(pd.io.common.BytesIO(r.content))
    d.columns=[c.strip().lstrip('\ufeff') for c in d.columns]
    d['Date']=pd.to_datetime(d['Start']).dt.tz_localize(None)
    for c in ['Open','High','Low','Close','Volume','Market Cap']:
        if c in d.columns: d[c]=pd.to_numeric(d[c],errors='coerce')
    d=d.set_index('Date').sort_index()
    return d[[c for c in ['Open','High','Low','Close','Volume','Market Cap'] if c in d.columns]].dropna(subset=['Open','Close'])


def fetch_bitstamp(start='2013-01-01', end='2026-09-02'):
    endpoint='https://www.bitstamp.net/api/v2/ohlc/btcusd/'
    a=pd.Timestamp(start, tz='UTC'); z=pd.Timestamp(end, tz='UTC')
    rows=[]; cur=a; s=requests.Session(); s.headers.update({'User-Agent':'halving-independent-repro/1.0'})
    while cur <= z:
        ce=min(cur+pd.Timedelta(days=899), z)
        p={'step':86400,'limit':1000,'start':int(cur.timestamp()),'end':int((ce+pd.Timedelta(hours=23)).timestamp()),'exclude_current_candle':'true'}
        last=None
        for k in range(5):
            try:
                rr=s.get(endpoint,params=p,timeout=45); rr.raise_for_status()
                b=rr.json().get('data',{}).get('ohlc',[])
                if not b: raise RuntimeError('empty block')
                rows += b; last=None; break
            except Exception as e:
                last=e; time.sleep(2**k)
        if last: raise last
        cur=ce+pd.Timedelta(days=1)
    d=pd.DataFrame(rows)
    for c in ['open','high','low','close','volume']: d[c]=pd.to_numeric(d[c],errors='coerce')
    d['Date']=pd.to_datetime(pd.to_numeric(d['timestamp']),unit='s',utc=True).dt.tz_localize(None).dt.normalize()
    d=d.rename(columns={'open':'Open','high':'High','low':'Low','close':'Close','volume':'Volume'})
    return d[['Date','Open','High','Low','Close','Volume']].dropna().drop_duplicates('Date').sort_values('Date').set_index('Date')


def data():
    early=fetch_early(); bs=fetch_bitstamp()
    x=pd.concat([early.loc[:'2012-12-31'],bs]).sort_index()
    x=x[~x.index.duplicated(keep='last')]
    return x.loc[:CURRENT_END]


def nearest_bar(idx,target):
    p=idx.searchsorted(pd.Timestamp(target),side='left')
    return idx[min(p,len(idx)-1)]


def schedule(df,pre_days,post_days):
    return [(nearest_bar(df.index,h-pd.Timedelta(days=pre_days)),nearest_bar(df.index,h+pd.Timedelta(days=post_days)),h) for h in HALVINGS]


def fill(df,dt,mode):
    if mode=='same_open': return float(df.loc[dt,'Open']),dt
    if mode=='same_close': return float(df.loc[dt,'Close']),dt
    if mode=='next_open':
        i=df.index.get_loc(dt); j=min(i+1,len(df)-1)
        return float(df.iloc[j].Open),df.index[j]
    raise ValueError(mode)


def fast_bt(df,pre_days,post_days,mode='next_open',end='2025-01-31',fee_side=0.0):
    """Exact fill/trade calculation without constructing a daily equity curve; used only for parameter-search diagnostics."""
    end=pd.Timestamp(end); actions=[]
    for en,ex,h in schedule(df,pre_days,post_days):
        if en<=end: actions.append((en,0,'buy',h))
        if ex<=end: actions.append((ex,1,'sell',h))
    actions.sort()
    cash=INITIAL; qty=0.0; active=None; trades=[]
    for dt,_,act,h in actions:
        p,fd=fill(df,dt,mode)
        if fd>end: continue
        if act=='buy' and qty==0:
            cap=cash; qty=cash/(p*(1+fee_side)); cash-=qty*p*(1+fee_side); active=(fd,p,cap,h)
        elif act=='sell' and qty>0:
            cash += qty*p*(1-fee_side)
            trades.append((active[0],fd,active[1],p,cash-active[2],(cash/active[2]-1)*100))
            qty=0.0; active=None
    if qty>0:
        last=df.loc[:end].index[-1]; p=float(df.loc[last,'Close']); cash += qty*p*(1-fee_side)
        trades.append((active[0],last,active[1],p,cash-active[2],(cash/active[2]-1)*100)); qty=0.0
    tr=pd.DataFrame(trades,columns=['entry','exit','entry_px','exit_px','pnl','ret_pct'])
    wins=int((tr.pnl>0).sum()) if len(tr) else 0
    gp=float(tr.loc[tr.pnl>0,'pnl'].sum()) if len(tr) else 0; gl=float(-tr.loc[tr.pnl<0,'pnl'].sum()) if len(tr) else 0
    return {'final':cash,'return_pct':(cash/INITIAL-1)*100,'trades':len(tr),'wins':wins,'win_pct':100*wins/len(tr) if len(tr) else np.nan,'profit_factor':gp/gl if gl>0 else np.inf}


def bt(df,pre_days,post_days,mode='next_open',end='2025-01-31',fee_side=0.0):
    end=pd.Timestamp(end); d=df.loc[:end].copy(); ev=schedule(df,pre_days,post_days)
    cash=INITIAL; qty=0.0; trades=[]; curve=[]; active=None; actions={}
    for en,ex,h in ev:
        if en<=end: actions.setdefault(en,[]).append(('buy',h))
        if ex<=end: actions.setdefault(ex,[]).append(('sell',h))
    for dt,row in d.iterrows():
        for act,h in actions.get(dt,[]):
            p,fd=fill(df,dt,mode)
            if fd>end: continue
            if act=='buy' and qty==0:
                cap=cash; qty=cash/(p*(1+fee_side)); cash-=qty*p*(1+fee_side); active={'entry':fd,'entry_px':p,'entry_cap':cap,'halving':h}
            elif act=='sell' and qty>0:
                cash += qty*p*(1-fee_side); trades.append({**active,'exit':fd,'exit_px':p,'pnl':cash-active['entry_cap'],'ret_pct':(cash/active['entry_cap']-1)*100}); qty=0.0; active=None
        curve.append((dt,cash+qty*float(row.Close)))
    if qty>0:
        dt=d.index[-1]; p=float(d.iloc[-1].Close); cash += qty*p*(1-fee_side)
        trades.append({**active,'exit':dt,'exit_px':p,'pnl':cash-active['entry_cap'],'ret_pct':(cash/active['entry_cap']-1)*100,'forced_exit':True}); qty=0.0; curve[-1]=(dt,cash)
    tr=pd.DataFrame(trades); eq=pd.Series(dict(curve)).sort_index(); final=float(eq.iloc[-1]); dd=(eq/eq.cummax()-1)*100
    wins=int((tr.pnl>0).sum()) if len(tr) else 0; gp=float(tr.loc[tr.pnl>0,'pnl'].sum()) if len(tr) else 0; gl=float(-tr.loc[tr.pnl<0,'pnl'].sum()) if len(tr) else 0
    years=max((eq.index[-1]-eq.index[0]).days/365.2425,1/365.2425)
    return {'final':final,'return_pct':(final/INITIAL-1)*100,'cagr_pct':(final/INITIAL)**(1/years)*100-100,'trades':len(tr),'wins':wins,'win_pct':100*wins/len(tr) if len(tr) else np.nan,'profit_factor':gp/gl if gl>0 else np.inf,'max_dd_close_pct':float(dd.min()),'start':str(eq.index[0].date()),'end':str(eq.index[-1].date())},tr,eq


def bnh(df,start,end):
    d=df.loc[pd.Timestamp(start):pd.Timestamp(end)]
    return (float(d.iloc[-1].Close)/float(d.iloc[0].Open)-1)*100


def main():
    out='audit_output_halving'; os.makedirs(out,exist_ok=True); df=data(); print('DATA',df.index.min(),df.index.max(),len(df))
    candidates=[(500,500,'public_500_500'),(400,480,'pantera_like_400_480'),(600,525,'round_600_525'),(730,548,'approx_2y_18m')]
    rows=[]
    for pre,post,label in candidates:
        for mode in ['same_open','same_close','next_open']:
            for fee_name,fee in [('zero',0.0),('15bps_side',0.0015),('50bps_side',0.005)]:
                m,tr,eq=bt(df,pre,post,mode,'2025-01-31',fee); rows.append({'label':label,'pre_days':pre,'post_days':post,'mode':mode,'fee':fee_name,**m,'claim_ratio':m['final']/CLAIM_FINAL})
                if label=='approx_2y_18m' and mode=='next_open' and fee_name=='zero': tr.to_csv(f'{out}/two_year_18m_trades.csv',index=False)
    frame=pd.DataFrame(rows); frame.to_csv(f'{out}/candidate_results.csv',index=False)

    fits=[]
    for mode in ['same_open','same_close','next_open']:
        for pre in range(360,901,10):
            for post in range(360,701,10):
                m=fast_bt(df,pre,post,mode,'2025-01-31',0.0)
                if m['trades']==4:
                    fits.append({'mode':mode,'pre_days':pre,'post_days':post,**m,'log_final_error':abs(math.log(max(m['final'],1)/CLAIM_FINAL)),'claim_ratio':m['final']/CLAIM_FINAL})
    fits=pd.DataFrame(fits).sort_values('log_final_error'); fits.head(100).to_csv(f'{out}/offset_grid_top100.csv',index=False)
    bestfast=fits.iloc[0]; bestfull,_,_=bt(df,int(bestfast.pre_days),int(bestfast.post_days),bestfast['mode'],'2025-01-31',0.0)
    best={**bestfast.to_dict(),**bestfull}

    snaps=[]
    for e in pd.date_range('2025-01-01','2025-03-31',freq='D'):
        for mode in ['same_open','same_close','next_open']:
            m=fast_bt(df,730,548,mode,e,0.0)
            if m['trades']==4: snaps.append({'mode':mode,'end':str(e.date()),**m,'claim_ratio':m['final']/CLAIM_FINAL,'abs_pct_gap':abs(m['final']/CLAIM_FINAL-1)*100})
    snaps=pd.DataFrame(snaps).sort_values('abs_pct_gap'); snaps.head(100).to_csv(f'{out}/snapshot_search_top100.csv',index=False)
    sf=snaps.iloc[0]; snapfull,_,_=bt(df,730,548,sf['mode'],sf['end'],0.0); bestsnap={**sf.to_dict(),**snapfull}

    sens=[]
    for pre in range(670,791,10):
        for post in range(488,609,10):
            m=fast_bt(df,pre,post,'next_open','2025-01-31',0.0); sens.append({'pre_days':pre,'post_days':post,**m})
    pd.DataFrame(sens).to_csv(f'{out}/timing_sensitivity.csv',index=False)

    current=[]
    for pre,post,label in candidates:
        m,tr,eq=bt(df,pre,post,'next_open',CURRENT_END,0.0); current.append({'label':label,'pre_days':pre,'post_days':post,'mode':'next_open',**m}); tr.to_csv(f'{out}/current_{label}_trades.csv',index=False)
    pd.DataFrame(current).to_csv(f'{out}/current_results.csv',index=False)

    key={}
    for _,_,label in candidates:
        z=frame[(frame.label==label)&(frame['mode']=='next_open')&(frame.fee=='zero')].iloc[0]
        key[label]={k:(float(z[k]) if isinstance(z[k],(float,np.floating)) else int(z[k]) if isinstance(z[k],(int,np.integer)) else z[k]) for k in ['return_pct','cagr_pct','trades','win_pct','profit_factor','max_dd_close_pct','claim_ratio']}
    first_entry=nearest_bar(df.index,HALVINGS[0]-pd.Timedelta(days=730))
    vals=np.array([x['return_pct'] for x in sens],dtype=float)
    summary={'target':{'return_pct':CLAIM_RETURN_PCT,'final_from_10000':CLAIM_FINAL,'trades':4,'win_pct':100.0,'max_dd_pct':-21.49},'key_next_open_zero':key,'best_offset_grid_match':best,'best_730_548_snapshot_match':bestsnap,'timing_sensitivity_730_548_neighborhood':{'min_return_pct':float(np.min(vals)),'median_return_pct':float(np.median(vals)),'max_return_pct':float(np.max(vals))},'buy_hold_730_548_start_to_2025_01_31_pct':bnh(df,first_entry,'2025-01-31'),'current_next_open_zero':current,'notes':['Offset-grid matches are reverse-engineering diagnostics, not evidence of validity.','Daily-close mark-to-market drawdown is used; intraday drawdown would be equal or worse.','The 2024 trade is force-closed at the requested historical snapshot when its scheduled post-halving exit lies later.']}
    with open(f'{out}/summary.json','w') as f: json.dump(summary,f,indent=2,default=str)
    print(json.dumps(summary,indent=2,default=str))

if __name__=='__main__': main()
