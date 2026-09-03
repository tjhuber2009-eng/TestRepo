import io, math, os, time, zipfile
from datetime import datetime
import numpy as np
import pandas as pd
import requests

INITIAL=100000.0
ALLOC=0.50
COMMISSION=0.005  # exact Pine source: 0.5% of transacted value, per fill
TICK=0.01
HEAD_START=pd.Timestamp('2017-08-17',tz='UTC')
PUB=pd.Timestamp('2021-06-07',tz='UTC')
END=pd.Timestamp('2026-08-31',tz='UTC')
BASE='https://data.binance.vision/data/spot/monthly/klines/BTCUSDT'


def months(start,end):
    p=pd.Period(start.strftime('%Y-%m'),freq='M'); q=pd.Period(end.strftime('%Y-%m'),freq='M')
    while p<=q:
        yield p.year,p.month
        p+=1


def fetch_interval(interval,start,end):
    frames=[]; s=requests.Session(); s.headers.update({'User-Agent':'seaside-independent-audit/1.0'})
    for y,m in months(start,end):
        ym=f'{y:04d}-{m:02d}'
        url=f'{BASE}/{interval}/BTCUSDT-{interval}-{ym}.zip'
        err=None
        for a in range(4):
            try:
                r=s.get(url,timeout=30)
                if r.status_code==404:
                    err=f'404 {url}'; break
                r.raise_for_status()
                with zipfile.ZipFile(io.BytesIO(r.content)) as z:
                    name=z.namelist()[0]
                    raw=z.read(name)
                d=pd.read_csv(io.BytesIO(raw),header=None)
                # Binance kline 12 cols; recent archives may use microsecond timestamps.
                d=d.iloc[:,:12]
                d.columns=['open_time','Open','High','Low','Close','Volume','close_time','quote_volume','trades','tb_base','tb_quote','ignore']
                ot=pd.to_numeric(d.open_time,errors='coerce')
                unit='us' if ot.max()>10**14 else 'ms'
                d['Date']=pd.to_datetime(ot,unit=unit,utc=True)
                for c in ['Open','High','Low','Close','Volume','quote_volume']:
                    d[c]=pd.to_numeric(d[c],errors='coerce')
                frames.append(d[['Date','Open','High','Low','Close','Volume','quote_volume']])
                err=None; break
            except Exception as e:
                err=repr(e); time.sleep(1+a)
        if err:
            raise RuntimeError(err)
    out=pd.concat(frames,ignore_index=True).drop_duplicates('Date').sort_values('Date')
    return out[(out.Date>=start)&(out.Date<end+pd.Timedelta(days=1))].reset_index(drop=True)


def path_points(o,h,l,c):
    # TradingView default broker-emulator assumption.
    return [o,h,l,c] if abs(o-h)<abs(o-l) else [o,l,h,c]


def trail_exit_from_points(side, entry, points, best=None):
    # Exact one-tick activation and one-tick trailing offset, stepped over OHLC path segments.
    if side==1:
        activation=entry+TICK
        active=best is not None
        b=best
        cur=points[0]
        for nxt in points[1:]:
            if not active:
                # activation occurs only on upward segment crossing entry+tick
                if nxt>cur and nxt>=activation:
                    active=True
                    b=nxt
                    # segment ends at favorable extreme; future reversal may stop us
                cur=nxt
                continue
            # active trailing long
            if nxt>cur:
                b=max(b,nxt)
            elif nxt<cur:
                stop=b-TICK
                if nxt<=stop:
                    return stop,b,True
            cur=nxt
        return None,b,active
    else:
        activation=entry-TICK
        active=best is not None
        b=best
        cur=points[0]
        for nxt in points[1:]:
            if not active:
                if nxt<cur and nxt<=activation:
                    active=True
                    b=nxt
                cur=nxt
                continue
            if nxt<cur:
                b=min(b,nxt)
            elif nxt>cur:
                stop=b+TICK
                if nxt>=stop:
                    return stop,b,True
            cur=nxt
        return None,b,active


def apply_fill(equity, side, entry, exitp, extra_slip=0.0):
    # 50% equity notional. Commission percent on entry and exit. Extra slippage worsens both fills.
    epx=entry*(1+extra_slip*side)
    xpx=exitp*(1-extra_slip*side)
    notional=equity*ALLOC
    qty=notional/epx
    entry_fee=notional*COMMISSION
    pnl=qty*(xpx-epx)*side
    exit_notional=abs(qty*xpx)
    exit_fee=exit_notional*COMMISSION
    return equity + pnl-entry_fee-exit_fee, pnl-entry_fee-exit_fee


def signal_for_day(daily,i):
    # Script calculates at close of bar i: open_i > open_{i-1} => entry order fills next day open.
    if i<=0: return 0
    if daily.Open.iloc[i]>daily.Open.iloc[i-1]: return 1
    if daily.Open.iloc[i]<daily.Open.iloc[i-1]: return -1
    return 0


def run_daily_emulator(daily,start,end,extra_slip=0.0):
    # Mirrors TradingView's default daily OHLC broker assumptions as closely as practical.
    eq=INITIAL; curve=[]; trades=[]
    d=daily[(daily.Date>=start-pd.Timedelta(days=2))&(daily.Date<=end)].reset_index(drop=True)
    for j in range(1,len(d)-1):
        sig=signal_for_day(d,j)
        entrybar=j+1
        if sig==0 or d.Date.iloc[entrybar]<start or d.Date.iloc[entrybar]>end: continue
        row=d.iloc[entrybar]; entry=float(row.Open)
        pts=path_points(entry,float(row.High),float(row.Low),float(row.Close))
        exitp,best,active=trail_exit_from_points(sig,entry,pts,None)
        # With a 1-tick trail, essentially all daily bars exit. If not, walk subsequent daily bars.
        k=entrybar
        while exitp is None and k+1<len(d):
            k+=1; rr=d.iloc[k]
            pts=path_points(float(rr.Open),float(rr.High),float(rr.Low),float(rr.Close))
            exitp,best,active=trail_exit_from_points(sig,entry,pts,best if active else None)
            if k-entrybar>10: break
        if exitp is None: continue
        pre=eq; eq,pnl=apply_fill(eq,sig,entry,float(exitp),extra_slip)
        trades.append({'signal_date':d.Date.iloc[j],'entry_date':row.Date,'exit_date':d.Date.iloc[k], 'side':sig,'entry':entry,'exit':exitp,'pre_eq':pre,'post_eq':eq,'pnl':pnl})
        curve.append((d.Date.iloc[k],eq))
    return eq,pd.DataFrame(trades),pd.DataFrame(curve,columns=['Date','Equity'])


def run_hourly_magnifier(daily,hourly,start,end,extra_slip=0.0):
    # Daily signals/orders, but use hourly OHLC after the daily open. This approximates TradingView's 1D Bar Magnifier,
    # for which TradingView documents 60-minute intrabars.
    eq=INITIAL; trades=[]; curve=[]
    d=daily[(daily.Date>=start-pd.Timedelta(days=2))&(daily.Date<=end)].reset_index(drop=True)
    h=hourly.set_index('Date')
    for j in range(1,len(d)-1):
        sig=signal_for_day(d,j); entrybar=j+1
        if sig==0: continue
        day=d.Date.iloc[entrybar]
        if day<start or day>end: continue
        entry=float(d.Open.iloc[entrybar]); best=None; active=False; exitp=None; exittime=None
        # process hours from day entry until exit; allow carry max 72h for corner cases
        hs=hourly[(hourly.Date>=day)&(hourly.Date<day+pd.Timedelta(hours=72))]
        for _,rr in hs.iterrows():
            pts=path_points(float(rr.Open),float(rr.High),float(rr.Low),float(rr.Close))
            exitp,best,active=trail_exit_from_points(sig,entry,pts,best if active else None)
            if exitp is not None:
                exittime=rr.Date; break
        if exitp is None: continue
        pre=eq; eq,pnl=apply_fill(eq,sig,entry,float(exitp),extra_slip)
        trades.append({'signal_date':d.Date.iloc[j],'entry_date':day,'exit_date':exittime,'side':sig,'entry':entry,'exit':exitp,'pre_eq':pre,'post_eq':eq,'pnl':pnl})
        curve.append((exittime,eq))
    return eq,pd.DataFrame(trades),pd.DataFrame(curve,columns=['Date','Equity'])


def stats(eq,trades,start,end):
    years=(end-start).days/365.2425
    cagr=(eq/INITIAL)**(1/years)-1 if eq>0 else np.nan
    wins=(trades.pnl>0).sum() if len(trades) else 0
    gp=trades.loc[trades.pnl>0,'pnl'].sum() if len(trades) else 0
    gl=-trades.loc[trades.pnl<0,'pnl'].sum() if len(trades) else 0
    pf=gp/gl if gl>0 else np.inf
    return {'final':eq,'return_pct':(eq/INITIAL-1)*100,'cagr_pct':cagr*100,'trades':len(trades),'win_rate_pct':100*wins/len(trades) if len(trades) else np.nan,'profit_factor':pf,'median_hold_hours':((pd.to_datetime(trades.exit_date)-pd.to_datetime(trades.entry_date)).dt.total_seconds()/3600).median() if len(trades) else np.nan}


def main():
    os.makedirs('audit_output_seaside',exist_ok=True)
    daily=fetch_interval('1d',HEAD_START-pd.Timedelta(days=3),END)
    hourly=fetch_interval('1h',HEAD_START,END+pd.Timedelta(days=1))
    daily.to_csv('audit_output_seaside/binance_daily.csv',index=False)
    # hourly omitted from artifact to keep size modest; independently fetched from Binance Data Vision.
    cases=[]
    for name,start,end in [('headline',HEAD_START,PUB),('forward',PUB+pd.Timedelta(days=1),END),('2023_2026',pd.Timestamp('2023-01-01',tz='UTC'),END)]:
        for slipname,slip in [('exact_commission_only',0.0),('plus_5bps_side',0.0005),('plus_10bps_side',0.001)]:
            deq,dt,dc=run_daily_emulator(daily,start,end,slip)
            heq,ht,hc=run_hourly_magnifier(daily,hourly,start,end,slip)
            ds=stats(deq,dt,start,end); hs=stats(heq,ht,start,end)
            cases.append({'period':name,'model':'daily_default_OHLC','extra_slippage':slipname,**ds})
            cases.append({'period':name,'model':'hourly_bar_magnifier','extra_slippage':slipname,**hs})
            if name=='headline' and slip==0:
                dt.to_csv('audit_output_seaside/headline_daily_trades.csv',index=False)
                ht.to_csv('audit_output_seaside/headline_hourly_trades.csv',index=False)
    out=pd.DataFrame(cases)
    out.to_csv('audit_output_seaside/results.csv',index=False)
    print('DAILY_RANGE',daily.Date.min(),daily.Date.max(),'ROWS',len(daily))
    print('HOURLY_RANGE',hourly.Date.min(),hourly.Date.max(),'ROWS',len(hourly))
    print('\n=== SEASIDE420 EXACT SOURCE ECONOMICS ===')
    print('initial_capital',INITIAL,'alloc_pct',ALLOC*100,'commission_pct_each_fill',COMMISSION*100,'trail_activation_ticks',1,'trail_offset_ticks',1,'assumed_tick_usd',TICK)
    print(out.to_string(index=False,float_format=lambda x:f'{x:.4f}'))
    # quantify first-hour capture vs whole-day extreme in headline hourly trades
    ht=pd.read_csv('audit_output_seaside/headline_hourly_trades.csv')
    if len(ht):
        print('HEADLINE_HOURLY_EXIT_SAME_DAY_PCT',100*(pd.to_datetime(ht.exit_date).dt.date==pd.to_datetime(ht.entry_date).dt.date).mean())
        print('HEADLINE_HOURLY_MEDIAN_EXIT_HOUR',pd.to_datetime(ht.exit_date).dt.hour.median())

if __name__=='__main__': main()
