import io, os, time, zipfile
import pandas as pd
import requests
import btc_flip_audit as audit

MONTH='https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1h'
DAILY='https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1h'
FAPI='https://fapi.binance.com/fapi/v1/klines'


def parse_zip(content):
    with zipfile.ZipFile(io.BytesIO(content)) as z:
        raw=z.read(z.namelist()[0])
    d=pd.read_csv(io.BytesIO(raw),header=None).iloc[:,:12]
    d.columns=['open_time','Open','High','Low','Close','Volume','close_time','quote_volume','trades','tb_base','tb_quote','ignore']
    ot=pd.to_numeric(d.open_time,errors='coerce')
    unit='us' if ot.max()>10**14 else 'ms'
    d['Date']=pd.to_datetime(ot,unit=unit,utc=True).dt.tz_localize(None)
    for c in ['Open','High','Low','Close','Volume','quote_volume']:
        d[c]=pd.to_numeric(d[c],errors='coerce')
    return d[['Date','Open','High','Low','Close','Volume','quote_volume']]


def get(s,url):
    for a in range(4):
        try:
            r=s.get(url,timeout=45)
            if r.status_code==404:
                return None
            r.raise_for_status()
            return r.content
        except Exception:
            if a==3:
                raise
            time.sleep(1+a)


def api_month(s, start, end):
    """Recover early USDT-M futures bars from Binance REST if Data Vision ZIPs are absent."""
    frames=[]
    cursor=int(pd.Timestamp(start,tz='UTC').timestamp()*1000)
    end_ms=int(pd.Timestamp(end,tz='UTC').timestamp()*1000)
    while cursor < end_ms:
        try:
            r=s.get(FAPI,params={'symbol':'BTCUSDT','interval':'1h','startTime':cursor,'endTime':end_ms-1,'limit':1500},timeout=45)
            if r.status_code in (403,451):
                print('FAPI_BLOCKED',r.status_code,start,end)
                return None
            r.raise_for_status()
            rows=r.json()
        except Exception as e:
            print('FAPI_ERROR',repr(e),start,end)
            return None
        if not rows:
            break
        d=pd.DataFrame(rows)
        if d.empty:
            break
        d=d.iloc[:,:12]
        d.columns=['open_time','Open','High','Low','Close','Volume','close_time','quote_volume','trades','tb_base','tb_quote','ignore']
        d['Date']=pd.to_datetime(pd.to_numeric(d.open_time),unit='ms',utc=True).dt.tz_localize(None)
        for c in ['Open','High','Low','Close','Volume','quote_volume']:
            d[c]=pd.to_numeric(d[c],errors='coerce')
        frames.append(d[['Date','Open','High','Low','Close','Volume','quote_volume']])
        nxt=int(rows[-1][0])+3600000
        if nxt<=cursor:
            break
        cursor=nxt
        if len(rows)<1500:
            break
    if not frames:
        return None
    out=pd.concat(frames,ignore_index=True)
    return out[(out.Date>=pd.Timestamp(start))&(out.Date<pd.Timestamp(end))]


def fallback_fetch():
    os.makedirs('audit_output_btc_flip',exist_ok=True)
    s=requests.Session()
    s.headers.update({'User-Agent':'btc-flip-independent-audit/1.0'})
    frames=[]
    missing=[]
    # Futures venue launched in Sep 2019. Prefer official monthly archive, then daily archive,
    # then official Binance USDT-M REST endpoint for launch-period gaps.
    for y,m in audit.months(pd.Timestamp('2019-09-01'),audit.ARCHIVE_END):
        ym=f'{y:04d}-{m:02d}'
        mu=f'{MONTH}/BTCUSDT-1h-{ym}.zip'
        content=get(s,mu)
        if content is not None:
            frames.append(parse_zip(content))
            print('MONTH',ym)
            continue
        p=pd.Period(ym,freq='M')
        found=0
        for day in pd.date_range(p.start_time,p.end_time,freq='D'):
            if day>audit.ARCHIVE_END:
                break
            ds=day.strftime('%Y-%m-%d')
            du=f'{DAILY}/BTCUSDT-1h-{ds}.zip'
            c=get(s,du)
            if c is not None:
                frames.append(parse_zip(c))
                found+=1
        print('DAILY_FALLBACK',ym,'files',found)
        if found==0:
            mstart=p.start_time
            mend=min(p.end_time+pd.Timedelta(seconds=1),audit.ARCHIVE_END+pd.Timedelta(hours=1))
            api=api_month(s,mstart,mend)
            if api is not None and len(api):
                frames.append(api)
                print('FAPI_FALLBACK',ym,'rows',len(api))
            else:
                missing.append(ym)
    if not frames:
        raise RuntimeError('No Binance futures data acquired')
    out=(pd.concat(frames,ignore_index=True)
           .drop_duplicates('Date')
           .sort_values('Date')
           .set_index('Date')
           .sort_index())
    mask=(out.index>=pd.Timestamp('2019-09-01'))&(out.index<=audit.ARCHIVE_END)
    out=out.loc[mask].copy()
    print('DATA_RANGE',out.index.min(),out.index.max(),'ROWS',len(out),'MISSING_MONTHS',missing)
    # Explicit continuity audit; retain missing launch-period months in stdout/summary context rather than hiding them.
    expected=pd.date_range(out.index.min().floor('h'),out.index.max().floor('h'),freq='h') if len(out) else pd.DatetimeIndex([])
    missing_hours=len(expected.difference(out.index)) if len(expected) else 0
    print('MISSING_HOURS',missing_hours)
    out.to_csv('audit_output_btc_flip/binance_futures_1h.csv')
    return out

audit.fetch_hourly=fallback_fetch
if __name__=='__main__':
    audit.main()
