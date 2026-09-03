import json, math, os, time
from datetime import datetime, timezone
import numpy as np
import pandas as pd
import requests

START = pd.Timestamp('2011-08-18', tz='UTC')
TABLE_START = pd.Timestamp('2012-01-01', tz='UTC')
TABLE_END = pd.Timestamp('2024-12-31', tz='UTC')
SEARCH_END_START = pd.Timestamp('2025-01-01', tz='UTC')
SEARCH_END_END = pd.Timestamp('2025-01-31', tz='UTC')
FORWARD_END = pd.Timestamp('2026-09-02', tz='UTC')
INITIAL = 10_000.0
TARGET_RETURN_PCT = 326_357_765.75
TARGET_FINAL = INITIAL * (1 + TARGET_RETURN_PCT/100)
TARGET_TRADES = 27
TARGET_WIN_PCT = 85.19
TARGET_PF = 7.68
PHASES = ['halving','post_halving','bear','consolidation']


def phase(y):
    return PHASES[(int(y)-2012) % 4]


def fetch_bitstamp(start, end):
    """Fetch daily BTCUSD OHLC directly from Bitstamp in bounded chunks."""
    s = requests.Session()
    s.headers.update({'User-Agent':'monthly-seasonality-independent-repro/1.0'})
    rows=[]
    cur=start
    # Bitstamp max 1000 points; use <=900 days/chunk.
    while cur <= end:
        chunk_end=min(cur + pd.Timedelta(days=899), end)
        params={
            'step':86400,
            'start':int(cur.timestamp()),
            'end':int((chunk_end+pd.Timedelta(hours=23,minutes=59)).timestamp()),
            'limit':1000,
        }
        url='https://www.bitstamp.net/api/v2/ohlc/btcusd/'
        last=None
        for attempt in range(5):
            try:
                r=s.get(url,params=params,timeout=60)
                last=(r.status_code,r.text[:300])
                r.raise_for_status()
                data=r.json().get('data',{}).get('ohlc',[])
                rows.extend(data)
                break
            except Exception:
                if attempt==4: raise RuntimeError(f'Bitstamp failed {params} last={last}')
                time.sleep(2**attempt)
        cur=chunk_end+pd.Timedelta(days=1)
    if not rows:
        raise RuntimeError('No Bitstamp rows')
    df=pd.DataFrame(rows)
    for c in ['open','high','low','close','volume']:
        df[c]=pd.to_numeric(df[c],errors='coerce')
    df['Date']=pd.to_datetime(pd.to_numeric(df['timestamp']),unit='s',utc=True).dt.normalize()
    df=df.rename(columns={'open':'Open','high':'High','low':'Low','close':'Close','volume':'Volume'})
    df=df[['Date','Open','High','Low','Close','Volume']].dropna().drop_duplicates('Date').sort_values('Date').set_index('Date')
    return df


def monthly_table(df):
    x=df.loc[TABLE_START:TABLE_END].copy()
    m=x.resample('MS').agg(Open=('Open','first'), Close=('Close','last'))
    m=m.dropna()
    m['ret_pct']=(m['Close']/m['Open']-1)*100
    m['phase']=[phase(i.year) for i in m.index]
    m['month']=m.index.month
    t=m.groupby(['phase','month'])['ret_pct'].mean().unstack()
    return m,t


def signal_series(df, table):
    vals=[]
    for dt in df.index:
        vals.append(bool(table.loc[phase(dt.year),dt.month] > 0))
    return pd.Series(vals,index=df.index,name='signal')


def bt(df, sig, fill_mode='next_open', end=None, fee_side=0.0, force_exit=True):
    if end is not None:
        df=df.loc[:end].copy(); sig=sig.loc[df.index]
    cash=INITIAL; qty=0.0; inpos=False
    entry_px=entry_dt=entry_cap=None
    pending=None; prev=False
    trades=[]; curve=[]

    def buy(px,dt):
        nonlocal cash,qty,inpos,entry_px,entry_dt,entry_cap
        entry_cap=cash
        qty=cash/(px*(1+fee_side))
        cash-=qty*px*(1+fee_side)
        entry_px=px; entry_dt=dt; inpos=True

    def sell(px,dt):
        nonlocal cash,qty,inpos,entry_px,entry_dt,entry_cap
        proceeds=qty*px*(1-fee_side)
        cash+=proceeds
        pnl=cash-entry_cap
        trades.append({'entry':entry_dt,'exit':dt,'entry_px':entry_px,'exit_px':px,'pnl':pnl,'ret_pct':(cash/entry_cap-1)*100})
        qty=0.0; inpos=False; entry_px=entry_dt=entry_cap=None

    for dt,row in df.iterrows():
        cur=bool(sig.loc[dt])
        if fill_mode=='next_open' and pending:
            if pending=='buy' and not inpos: buy(float(row.Open),dt)
            elif pending=='sell' and inpos: sell(float(row.Open),dt)
            pending=None
        elif fill_mode=='month_open':
            # Ex-post table is known before the month begins; emulate exact first-bar-open rebalance.
            if cur != prev:
                if cur and not inpos: buy(float(row.Open),dt)
                elif (not cur) and inpos: sell(float(row.Open),dt)
        elif fill_mode=='same_close':
            if cur != prev:
                if cur and not inpos: buy(float(row.Close),dt)
                elif (not cur) and inpos: sell(float(row.Close),dt)
        eq=cash + (qty*float(row.Close) if inpos else 0)
        curve.append((dt,eq))
        if fill_mode=='next_open' and cur != prev:
            pending='buy' if cur else 'sell'
        prev=cur

    if force_exit and inpos:
        dt=df.index[-1]; sell(float(df.iloc[-1].Close),dt)
        curve[-1]=(dt,cash)

    tr=pd.DataFrame(trades)
    eq=pd.Series(dict(curve)).sort_index()
    final=float(cash if not inpos else eq.iloc[-1])
    ret=(final/INITIAL-1)*100
    peak=eq.cummax(); dd=eq/peak-1
    maxdd=float(dd.min()*100)
    wins=int((tr.pnl>0).sum()) if len(tr) else 0
    winpct=100*wins/len(tr) if len(tr) else np.nan
    gp=float(tr.loc[tr.pnl>0,'pnl'].sum()) if len(tr) else 0
    gl=float(-tr.loc[tr.pnl<0,'pnl'].sum()) if len(tr) else 0
    pf=gp/gl if gl>0 else np.inf
    return {'final':final,'return_pct':ret,'trades':len(tr),'wins':wins,'win_pct':winpct,'profit_factor':pf,'max_dd_close_pct':maxdd},tr,eq


def expanding_causal_table(monthly, asof_month):
    # Only observations whose month ended before current month may contribute.
    hist=monthly[monthly.index < asof_month]
    t=hist.groupby(['phase','month'])['ret_pct'].mean().unstack()
    return t


def causal_bt(df, monthly, start='2012-02-01', end='2025-01-31'):
    # Recompute month classification using only prior completed observations.
    d=df.loc[start:end].copy()
    signals=[]
    last=False
    for dt in d.index:
        if dt.day==1 or not signals:
            t=expanding_causal_table(monthly,dt.normalize().replace(day=1))
            ph=phase(dt.year); mo=dt.month
            if ph in t.index and mo in t.columns and pd.notna(t.loc[ph,mo]):
                last=bool(t.loc[ph,mo]>0)
            else:
                last=False
        signals.append(last)
    sig=pd.Series(signals,index=d.index)
    return bt(d,sig,'next_open',force_exit=True)


def main():
    os.makedirs('audit_output_monthly_seasonality',exist_ok=True)
    df=fetch_bitstamp(START,FORWARD_END)
    print('BITSTAMP_RANGE',df.index.min(),df.index.max(),'ROWS',len(df))
    print('EARLIEST_ROWS')
    print(df.head(10).to_string())
    monthly,table=monthly_table(df)
    print('\nMONTHLY_PHASE_TABLE_PCT')
    print(table.round(2).to_string())
    print('\nCHECK_VALUES post Jan/Feb/Mar, consolidation Oct, bear Dec:',
          table.loc['post_halving',1],table.loc['post_halving',2],table.loc['post_halving',3],
          table.loc['consolidation',10],table.loc['bear',12])
    sig=signal_series(df,table)

    rows=[]
    for mode in ['month_open','next_open','same_close']:
        for end in pd.date_range(SEARCH_END_START,SEARCH_END_END,freq='D'):
            if end not in df.index: continue
            m,tr,eq=bt(df.loc[START:],sig.loc[START:],mode,end=end)
            score=(abs(math.log(max(m['final'],1)/TARGET_FINAL))
                   + abs(m['trades']-TARGET_TRADES)*2
                   + abs(m['win_pct']-TARGET_WIN_PCT)/10
                   + abs(m['profit_factor']-TARGET_PF)/10)
            rows.append({'mode':mode,'end':end,**m,'score':score,'target_final':TARGET_FINAL,'final_ratio_to_claim':m['final']/TARGET_FINAL})
    matches=pd.DataFrame(rows).sort_values('score')
    print('\nTOP_CLAIM_MATCHES')
    print(matches.head(20).to_string(index=False))
    matches.to_csv('audit_output_monthly_seasonality/claim_matches.csv',index=False)
    monthly.to_csv('audit_output_monthly_seasonality/monthly_returns_2012_2024.csv')
    table.to_csv('audit_output_monthly_seasonality/phase_month_average_table.csv')

    best=matches.iloc[0]
    bm,btr,beq=bt(df.loc[START:],sig.loc[START:],best['mode'],end=pd.Timestamp(best['end']))
    btr.to_csv('audit_output_monthly_seasonality/best_match_trades.csv',index=False)
    beq.rename('equity').to_csv('audit_output_monthly_seasonality/best_match_equity.csv')

    # Frozen-table forward checks. Publication/course data table ends 2024; test 2025 and 2026 separately.
    for s,e,name in [
        ('2025-01-01','2025-12-31','2025'),
        ('2026-01-01','2026-09-02','2026_ytd'),
        ('2025-01-01','2026-09-02','post_table_forward')]:
        sub=df.loc[s:e]
        ss=signal_series(sub,table)
        mm,tt,ee=bt(sub,ss,'month_open',force_exit=True)
        print('FORWARD',name,mm)
        tt.to_csv(f'audit_output_monthly_seasonality/{name}_trades.csv',index=False)

    # Strict expanding/causal version: no future monthly outcomes used to classify prior months.
    cm,ctr,ceq=causal_bt(df,monthly,start='2012-02-01',end='2025-01-31')
    print('\nCAUSAL_EXPANDING',cm)
    ctr.to_csv('audit_output_monthly_seasonality/causal_expanding_trades.csv',index=False)
    ceq.rename('equity').to_csv('audit_output_monthly_seasonality/causal_expanding_equity.csv')

    summary={
        'target':{'return_pct':TARGET_RETURN_PCT,'final_from_10000':TARGET_FINAL,'trades':27,'win_pct':85.19,'profit_factor':7.68},
        'best_match':{k:(str(v) if isinstance(v,pd.Timestamp) else float(v) if isinstance(v,(np.floating,float)) else int(v) if isinstance(v,(np.integer,int)) else v) for k,v in best.to_dict().items()},
        'causal_expanding':cm,
        'table':{p:{str(m):float(table.loc[p,m]) for m in table.columns} for p in table.index},
    }
    with open('audit_output_monthly_seasonality/summary.json','w') as f: json.dump(summary,f,indent=2,default=str)

if __name__=='__main__':
    main()
