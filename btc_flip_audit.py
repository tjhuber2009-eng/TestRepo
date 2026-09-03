import io, os, math, json, time, zipfile
from dataclasses import dataclass
import numpy as np
import pandas as pd
import requests

INITIAL=5000.0
LEV=2.0
COMMISSION=0.0004
SLIP=0.3  # 3 ticks, BTCUSDT tick size 0.1
START=pd.Timestamp('2019-09-08')
CLAIM_END=pd.Timestamp('2026-04-17 23:00:00')
ARCHIVE_END=pd.Timestamp('2026-07-31 23:00:00')
PUBLIC_START=pd.Timestamp('2026-04-18')
BASE='https://data.binance.vision/data/futures/um/monthly/klines/BTCUSDT/1h'

TARGET={'final':572000.0,'cagr_pct':158.0,'max_dd_pct':-20.6,'profit_factor':7.29,'trades':62}


def months(start,end):
    p=pd.Period(start.strftime('%Y-%m'),freq='M'); q=pd.Period(end.strftime('%Y-%m'),freq='M')
    while p<=q:
        yield p.year,p.month; p+=1


def fetch_hourly():
    os.makedirs('audit_output_btc_flip',exist_ok=True)
    frames=[]; s=requests.Session(); s.headers.update({'User-Agent':'btc-flip-independent-audit/1.0'})
    for y,m in months(START-pd.Timedelta(days=60),ARCHIVE_END):
        ym=f'{y:04d}-{m:02d}'; url=f'{BASE}/BTCUSDT-1h-{ym}.zip'; err=None
        for a in range(5):
            try:
                r=s.get(url,timeout=45)
                if r.status_code==404:
                    err=f'404 {url}'; break
                r.raise_for_status()
                with zipfile.ZipFile(io.BytesIO(r.content)) as z: raw=z.read(z.namelist()[0])
                d=pd.read_csv(io.BytesIO(raw),header=None).iloc[:,:12]
                d.columns=['open_time','Open','High','Low','Close','Volume','close_time','quote_volume','trades','tb_base','tb_quote','ignore']
                ot=pd.to_numeric(d.open_time,errors='coerce'); unit='us' if ot.max()>10**14 else 'ms'
                d['Date']=pd.to_datetime(ot,unit=unit,utc=True).dt.tz_localize(None)
                for c in ['Open','High','Low','Close','Volume','quote_volume']: d[c]=pd.to_numeric(d[c],errors='coerce')
                frames.append(d[['Date','Open','High','Low','Close','Volume','quote_volume']]); err=None; break
            except Exception as e:
                err=repr(e); time.sleep(1+a)
        if err:
            if y==2019 and m<9: continue
            raise RuntimeError(err)
    out=pd.concat(frames,ignore_index=True).drop_duplicates('Date').sort_values('Date').set_index('Date')
    out=out.loc[START-pd.Timedelta(days=60):ARCHIVE_END].copy()
    out.to_csv('audit_output_btc_flip/binance_futures_1h.csv')
    return out


def ema(s,n): return s.astype(float).ewm(span=n,adjust=False,min_periods=1).mean()


def rma(s,n):
    x=s.astype(float).to_numpy(); out=np.full(len(x),np.nan); seed=None
    for i in range(n-1,len(x)):
        w=x[i-n+1:i+1]
        if np.isfinite(w).all(): out[i]=w.mean(); seed=i; break
    if seed is None: return pd.Series(out,index=s.index)
    p=out[seed]; a=1.0/n
    for i in range(seed+1,len(x)):
        if np.isfinite(x[i]): p=a*x[i]+(1-a)*p; out[i]=p
    return pd.Series(out,index=s.index)


def rsi(s,n):
    d=s.astype(float).diff(); up=d.clip(lower=0); dn=(-d.clip(upper=0)); au=rma(up,n); ad=rma(dn,n); rs=au/ad
    v=100-100/(1+rs); v=v.mask((ad==0)&(au>0),100); v=v.mask((ad==0)&(au==0),50); return v


def atr(df,n):
    prev=df.Close.shift(1); tr=pd.concat([(df.High-df.Low).abs(),(df.High-prev).abs(),(df.Low-prev).abs()],axis=1).max(axis=1); tr.iloc[0]=np.nan; return rma(tr,n)


def add_indicators(df,mtf_mode='intended'):
    d=df.copy(); c=d.Close
    d['rsi1h']=rsi(c,21); macd=ema(c,12)-ema(c,26); d['macd']=macd; d['macdsig']=ema(macd,9)
    d['atr20']=atr(d,20); d['atrma']=d.atr20.rolling(50).mean(); d['volsma']=d.Volume.rolling(20).mean()
    # Higher timeframe bars are UTC anchored. Author explicitly describes these as prior closed HTF bars.
    day=d.resample('1D',label='left',closed='left').agg({'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}).dropna()
    day['ema50']=ema(day.Close,50)
    h4=d.resample('4h',label='left',closed='left').agg({'Open':'first','High':'max','Low':'min','Close':'last','Volume':'sum'}).dropna(); h4['rsi14']=rsi(h4.Close,14)
    lag=1 if mtf_mode=='intended' else 2
    daily_close=day.Close.shift(lag); daily_ema=day.ema50.shift(lag); h4r=h4.rsi14.shift(lag)
    d['dailyClose']=daily_close.reindex(d.index,method='ffill'); d['dailyEma50']=daily_ema.reindex(d.index,method='ffill'); d['rsi4h']=h4r.reindex(d.index,method='ffill')
    body=(d.Close-d.Open).abs(); prevbody=body.shift(1)
    bull=(d.Close.shift(1)<d.Open.shift(1))&(d.Close>d.Open)&(d.Close>=d.Open.shift(1))&(d.Open<=d.Close.shift(1))&(body>prevbody)
    bear=(d.Close.shift(1)>d.Open.shift(1))&(d.Close<d.Open)&(d.Open>=d.Close.shift(1))&(d.Close<=d.Open.shift(1))&(body>prevbody)
    highvol=d.atr20>d.atrma; volok=d.Volume>1.5*d.volsma
    d['longSignal']=(d.rsi1h>45)&(d.macd>d.macdsig)&bull&highvol&volok&(d.dailyClose>d.dailyEma50)&(d.rsi4h>50)
    d['shortSignal']=(d.rsi1h<55)&(d.macd<d.macdsig)&bear&highvol&volok&(d.dailyClose<d.dailyEma50)&(d.rsi4h<50)
    return d

@dataclass
class Lot:
    ident:str
    side:int
    qty:float
    entry:float
    stop:float


def adverse(px,side,is_entry):
    # long entry / short exit worse upward; short entry / long exit worse downward
    return px + SLIP*(side if is_entry else -side)


def fill_entry(balance,qty,px,side):
    fp=adverse(px,side,True); fee=abs(qty*fp)*COMMISSION; return balance-fee,fp,fee


def fill_exit(balance,lot,qty,px):
    fp=adverse(px,lot.side,False); pnl=qty*(fp-lot.entry)*lot.side; fee=abs(qty*fp)*COMMISSION; return balance+pnl-fee,fp,pnl-fee


def equity(balance,lots,mark): return balance+sum(x.qty*(mark-x.entry)*x.side for x in lots)

def pos_side(lots): return 0 if not lots else lots[0].side

def avg_entry(lots):
    q=sum(x.qty for x in lots); return sum(x.qty*x.entry for x in lots)/q if q>0 else np.nan


def stop_hit_price(row,side,stop):
    if side==1:
        if row.Open<=stop: return float(row.Open)
        if row.Low<=stop: return float(stop)
    else:
        if row.Open>=stop: return float(row.Open)
        if row.High>=stop: return float(stop)
    return None


def run(df,mode='intended_python'):
    # mode intended_python allows the advertised +3R add; pine_declared blocks the second strategy.entry because pyramiding=1,
    # but still executes the script's immediate stop-management/state assignments.
    balance=INITIAL; lots=[]; curve=[]; episodes=[]; current_ep=None
    last_long_sl=-10**9; last_short_sl=-10**9; last_exit=-10**9; halt_until=-10**9; peak=INITIAL
    pending_side=0; pending_bar=None; pending_ref=np.nan; is_flip=False; flip_entry_bar=None; pyramided=False; init_sl=np.nan; stored_sl=np.nan; partial=False

    def start_ep(i,kind):
        nonlocal current_ep
        if current_ep is None: current_ep={'entry_date':df.index[i],'kind':kind,'start_equity':equity(balance,lots,float(df.Close.iloc[i])) if lots else balance,'realized_start':balance,'fills':0}
    def close_ep(i,reason):
        nonlocal current_ep
        if current_ep is not None and not lots:
            current_ep.update({'exit_date':df.index[i],'reason':reason,'end_equity':balance,'pnl':balance-current_ep['realized_start']}); episodes.append(current_ep); current_ep=None

    for i in range(1,len(df)):
        row=df.iloc[i]; px=float(row.Close); timestamp=df.index[i]
        if timestamp<START: continue
        # Existing protective stops execute intrabar before close-based decisions.
        stopped=False; stopped_side=0
        if lots:
            # Each entry can have its own stop after partial/pyramid management.
            for lot in list(lots):
                hp=stop_hit_price(row,lot.side,lot.stop)
                if hp is not None:
                    balance,fp,net=fill_exit(balance,lot,lot.qty,hp); lots.remove(lot); stopped=True; stopped_side=lot.side
                    if current_ep: current_ep['fills']+=1
            if stopped and not lots:
                last_exit=i
                if stopped_side==1: last_long_sl=i
                else: last_short_sl=i
                if not is_flip:
                    pending_side=-stopped_side; pending_bar=i; pending_ref=stored_sl
                is_flip=False; pyramided=False
                close_ep(i,'stop')
        current_eq=equity(balance,lots,px); peak=max(peak,current_eq); dd=(current_eq-peak)/peak if peak>0 else 0
        if dd<=-0.25 and not lots:
            halt_until=i+168; peak=current_eq
        halted=i<halt_until; exit_cd=(i-last_exit)<2; long_cd=(i-last_long_sl)<24; short_cd=(i-last_short_sl)<24
        ls=bool(row.longSignal); ss=bool(row.shortSignal)
        # Pending SL flip: generic cooldown makes the nominal 1h wait effectively >=2h.
        if pending_side!=0 and not lots and (i-pending_bar)>=1 and not halted and not exit_cd:
            if pending_side==-1:
                sh=float(df.High.iloc[max(0,i-9):i+1].max()); swing=sh*(1+0.001); cap=float(pending_ref)*(1+0.015); st=min(swing,cap); dist=st-px
            else:
                sl=float(df.Low.iloc[max(0,i-9):i+1].min()); swing=sl*(1-0.001); cap=float(pending_ref)*(1-0.015); st=max(swing,cap); dist=px-st
            if dist>0:
                qty=(current_eq*LEV)/px; balance,ep,fee=fill_entry(balance,qty,px,pending_side); lots=[Lot('fL' if pending_side==1 else 'fS',pending_side,qty,ep,st)]; stored_sl=st; is_flip=True; flip_entry_bar=i; partial=False; pyramided=False; start_ep(i,'flip'); current_ep['fills']+=1
            pending_side=0
        # Flip 24h time-stop.
        if lots and is_flip and flip_entry_bar is not None and (i-flip_entry_bar)>=24:
            for lot in list(lots): balance,fp,net=fill_exit(balance,lot,lot.qty,px); lots.remove(lot); current_ep['fills']+=1
            last_exit=i; is_flip=False; pyramided=False; close_ep(i,'flip_time'); current_eq=balance
        # Normal entry from bar-close signal.
        if not lots and pending_side==0 and not halted and not exit_cd:
            side=1 if (ls and not long_cd) else -1 if (ss and not short_cd) else 0
            if side:
                if side==1:
                    patt=min(float(row.Low),float(df.Low.iloc[i-1]))*(1-0.001); cap=px*(1-0.025); st=max(patt,cap); dist=px-st
                else:
                    patt=max(float(row.High),float(df.High.iloc[i-1]))*(1+0.001); cap=px*(1+0.025); st=min(patt,cap); dist=st-px
                if dist>0:
                    qty=(current_eq*LEV)/px; balance,ep,fee=fill_entry(balance,qty,px,side); lots=[Lot('L' if side==1 else 'S',side,qty,ep,st)]; stored_sl=st; init_sl=dist; is_flip=False; partial=False; pyramided=False; start_ep(i,'main'); current_ep['fills']+=1
        # Pyramiding at +3R. Script computes from close and original init SL.
        if lots and (not is_flip) and (not pyramided) and np.isfinite(init_sl) and init_sl>0:
            ae=avg_entry(lots); side=pos_side(lots); cr=(px-ae)/init_sl if side==1 else (ae-px)/init_sl
            if cr>=3.0:
                current_eq=equity(balance,lots,px)
                if mode=='intended_python':
                    qty=(current_eq*LEV*0.50)/px; balance,ep,fee=fill_entry(balance,qty,px,side); lots.append(Lot('pL' if side==1 else 'pS',side,qty,ep,np.nan)); current_ep['fills']+=1
                # Pine code moves stops/state regardless of whether second entry is actually accepted.
                base_entry=ae; pst=base_entry+0.5*init_sl if side==1 else base_entry-0.5*init_sl
                for lot in lots: lot.stop=pst
                stored_sl=pst; pyramided=True
        # Partial TP at +6R based on current position average and stored SL; closes 15% of main/flip ID only.
        if lots and not partial and np.isfinite(stored_sl):
            ae=avg_entry(lots); side=pos_side(lots); dist=ae-stored_sl if side==1 else stored_sl-ae
            cr=(px-ae)/dist if side==1 and dist>0 else (ae-px)/dist if side==-1 and dist>0 else -np.inf
            if cr>=6.0:
                target='fL' if is_flip and side==1 else 'fS' if is_flip else 'L' if side==1 else 'S'
                lot=next((x for x in lots if x.ident==target),None)
                if lot is not None and lot.qty>0:
                    q=lot.qty*0.15; balance,fp,net=fill_exit(balance,lot,q,px); lot.qty-=q; current_ep['fills']+=1
                    if lot.qty<1e-12: lots.remove(lot)
                    be=ae*(1+0.001) if side==1 else ae*(1-0.001); new=max(stored_sl,be) if side==1 else min(stored_sl,be); stored_sl=new
                    if lot in lots: lot.stop=new
                    partial=True
        # Opposite confirmed signal closes all at bar close.
        if lots and ((pos_side(lots)==1 and ss) or (pos_side(lots)==-1 and ls)):
            for lot in list(lots): balance,fp,net=fill_exit(balance,lot,lot.qty,px); lots.remove(lot); current_ep['fills']+=1
            last_exit=i; is_flip=False; pyramided=False; close_ep(i,'opposite')
        mark_eq=equity(balance,lots,px); curve.append((timestamp,mark_eq,balance,pos_side(lots)))
    # Mark open position to final close, but don't count as closed episode in PF/win stats.
    curve=pd.DataFrame(curve,columns=['Date','Equity','Balance','Side']).set_index('Date')
    ep=pd.DataFrame(episodes)
    return curve,ep,lots,balance


def stats(curve,ep,start,end):
    c=curve.loc[start:end].copy()
    if len(c)<2: return {}
    base=float(c.Equity.iloc[0]); e=c.Equity/base; years=(c.index[-1]-c.index[0]).total_seconds()/(365.2425*86400)
    total=e.iloc[-1]-1; cagr=e.iloc[-1]**(1/years)-1; dd=e/e.cummax()-1; ret=e.pct_change().dropna(); sharpe=np.sqrt(365*24)*ret.mean()/ret.std(ddof=1) if ret.std(ddof=1)>0 else np.nan
    t=ep[(pd.to_datetime(ep.entry_date)>=start)&(pd.to_datetime(ep.exit_date)<=end)].copy() if len(ep) else pd.DataFrame()
    if len(t):
        pnl=t.pnl.astype(float); gp=pnl[pnl>0].sum(); gl=-pnl[pnl<0].sum(); pf=gp/gl if gl>0 else np.inf; wins=int((pnl>0).sum())
    else: pf=np.nan; wins=0
    return {'start':str(c.index[0]),'end':str(c.index[-1]),'final_from_5000':5000*float(e.iloc[-1]),'total_return_pct':float(total*100),'cagr_pct':float(cagr*100),'max_dd_pct':float(dd.min()*100),'sharpe':float(sharpe),'trades':int(len(t)),'wins':wins,'win_rate_pct':100*wins/len(t) if len(t) else np.nan,'profit_factor':float(pf) if np.isfinite(pf) else 'inf'}


def buyhold(df,start,end,lev=1):
    s=df.loc[start:end].Close; e=(s/s.iloc[0])**lev; years=(s.index[-1]-s.index[0]).total_seconds()/(365.2425*86400); dd=e/e.cummax()-1
    return {'return_pct':float((e.iloc[-1]-1)*100),'cagr_pct':float((e.iloc[-1]**(1/years)-1)*100),'max_dd_pct':float(dd.min()*100)}


def main():
    raw=fetch_hourly(); outputs=[]
    for mtf in ['intended','extra_lag']:
        d=add_indicators(raw,mtf)
        d.to_csv(f'audit_output_btc_flip/signals_{mtf}.csv')
        for mode in ['intended_python','pine_declared']:
            curve,ep,lots,balance=run(d,mode); curve.to_csv(f'audit_output_btc_flip/equity_{mtf}_{mode}.csv'); ep.to_csv(f'audit_output_btc_flip/trades_{mtf}_{mode}.csv',index=False)
            for label,st,en in [('claim_release',START,CLAIM_END),('public_forward',PUBLIC_START,ARCHIVE_END),('2022_release',pd.Timestamp('2022-01-01'),CLAIM_END),('2024_release',pd.Timestamp('2024-01-01'),CLAIM_END)]:
                m=stats(curve,ep,st,en); outputs.append({'mtf':mtf,'mode':mode,'window':label,**m})
    out=pd.DataFrame(outputs); out.to_csv('audit_output_btc_flip/results.csv',index=False)
    bh={'btc_1x_claim':buyhold(raw,START,CLAIM_END,1),'btc_2x_naive_claim':buyhold(raw,START,CLAIM_END,2),'btc_1x_forward':buyhold(raw,PUBLIC_START,ARCHIVE_END,1)}
    primary=out[(out.mtf=='intended')&(out.window=='claim_release')]
    forward=out[(out.mtf=='intended')&(out.window=='public_forward')]
    print('=== AUTHOR TARGET ==='); print(json.dumps(TARGET,indent=2)); print('\n=== PRIMARY ==='); print(primary.to_string(index=False,float_format=lambda x:f'{x:.3f}')); print('\n=== PUBLIC FORWARD ==='); print(forward.to_string(index=False,float_format=lambda x:f'{x:.3f}')); print('\n=== MTF SENSITIVITY ==='); print(out[out.window=='claim_release'].to_string(index=False,float_format=lambda x:f'{x:.3f}')); print('\n=== BENCHMARKS ==='); print(json.dumps(bh,indent=2))
    summary={'target':TARGET,'results':outputs,'benchmarks':bh,'notes':['intended_python allows the advertised +3R same-direction add.','pine_declared blocks that add because TradingView pyramiding=1 permits only one open strategy.entry trade in a direction, while still applying the script stop/state updates.','Both modes use 0.04% per-fill commission and 3 BTCUSDT ticks ($0.30) adverse slippage.','Funding, latency and market impact are excluded to match the author headline as closely as possible.']}
    with open('audit_output_btc_flip/summary.json','w') as f: json.dump(summary,f,indent=2,default=str)

if __name__=='__main__': main()
