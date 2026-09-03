import io, os, time, zipfile
import pandas as pd
import requests
import btc_flip_audit as audit

MONTH='https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1h'
DAILY='https://data.binance.vision/data/futures/um/daily/klines/BTCUSDT/1h'


def parse_zip(content):
    with zipfile.ZipFile(io.BytesIO(content)) as z: raw=z.read(z.namelist()[0])
    d=pd.read_csv(io.BytesIO(raw),header=None).iloc[:,:12]
    d.columns=['open_time','Open','High','Low','Close','Volume','close_time','quote_volume','trades','tb_base','tb_quote','ignore']
    ot=pd.to_numeric(d.open_time,errors='coerce'); unit='us' if ot.max()>10**14 else 'ms'
    d['Date']=pd.to_datetime(ot,unit=unit,utc=True).dt.tz_localize(None)
    for c in ['Open','High','Low','Close','Volume','quote_volume']: d[c]=pd.to_numeric(d[c],errors='coerce')
    return d[['Date','Open','High','Low','Close','Volume','quote_volume']]


def get(s,url):
    for a in range(4):
        try:
            r=s.get(url,timeout=45)
            if r.status_code==404: return None
            r.raise_for_status(); return r.content
        except Exception:
            if a==3: raise
            time.sleep(1+a)


def fallback_fetch():
    os.makedirs('audit_output_btc_flip',exist_ok=True)
    s=requests.Session(); s.headers.update({'User-Agent':'btc-flip-independent-audit/1.0'}); frames=[]
    # Futures venue launched in Sep 2019. Monthly archives are not guaranteed for the first months;
    # use the same Binance Data Vision venue's daily ZIPs whenever a monthly ZIP is absent.
    for y,m in audit.months(pd.Timestamp('2019-09-01'),audit.ARCHIVE_END):
        ym=f'{y:04d}-{m:02d}'; mu=f'{MONTH}/BTCUSDT-1h-{ym}.zip'; content=get(s,mu)
        if content is not None:
            frames.append(parse_zip(content)); print('MONTH',ym); continue
        p=pd.Period(ym,freq='M')
        found=0
        for day in pd.date_range(p.start_time,p.end_time,freq='D'):
            if day>audit.ARCHIVE_END: break
            ds=day.strftime('%Y-%m-%d'); du=f'{DAILY}/BTCUSDT-1h-{ds}.zip'; c=get(s,du)
            if c is not None:
                frames.append(parse_zip(c)); found+=1
        print('DAILY_FALLBACK',ym,'files',found)
    if not frames: raise RuntimeError('No Binance futures data acquired')
    out=pd.concat(frames,ignore_index=True).drop_duplicates('Date').sort_values('Date').set_index('Date')
    out=out.loc[pd.Timestamp('2019-09-01'):audit.ARCHIVE_END].copy()
    print('DATA_RANGE',out.index.min(),out.index.max(),'ROWS',len(out))
    out.to_csv('audit_output_btc_flip/binance_futures_1h.csv')
    return out

audit.fetch_hourly=fallback_fetch
if __name__=='__main__': audit.main()
