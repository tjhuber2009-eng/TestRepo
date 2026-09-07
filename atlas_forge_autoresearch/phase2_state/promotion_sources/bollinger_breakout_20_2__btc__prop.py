
import math
import numpy as np
import pandas as pd
from backtesting import Strategy

BARS_PER_YEAR = 365
FAMILY = "bollinger_breakout_20_2"
EPS = 1e-12


def ema(s, n):
    s = pd.Series(s, dtype=float)
    return s.ewm(span=n, adjust=False, min_periods=n).mean()


def rma(s, n):
    s = pd.Series(s, dtype=float)
    return s.ewm(alpha=1/n, adjust=False, min_periods=n).mean()


def sma(s, n):
    return pd.Series(s, dtype=float).rolling(n, min_periods=n).mean()


def wma(s, n):
    s = pd.Series(s, dtype=float)
    w = np.arange(1, n + 1, dtype=float)
    return s.rolling(n, min_periods=n).apply(lambda x: np.dot(x, w) / w.sum(), raw=True)


def hma(s, n):
    return wma(2 * wma(s, max(1, n // 2)) - wma(s, n), max(1, int(math.sqrt(n))))


def dema(s, n):
    e = ema(s, n)
    return 2 * e - ema(e, n)


def tema(s, n):
    e1 = ema(s, n); e2 = ema(e1, n); e3 = ema(e2, n)
    return 3 * e1 - 3 * e2 + e3


def zlema(s, n):
    s = pd.Series(s, dtype=float)
    lag = max(1, (n - 1) // 2)
    return ema(s + (s - s.shift(lag)), n)


def tr(h, l, c):
    h = pd.Series(h, dtype=float); l = pd.Series(l, dtype=float); c = pd.Series(c, dtype=float)
    pc = c.shift(1)
    return pd.concat([(h-l).abs(), (h-pc).abs(), (l-pc).abs()], axis=1).max(axis=1)


def atr(h, l, c, n=14):
    return rma(tr(h, l, c), n)


def rsi(c, n=14):
    c = pd.Series(c, dtype=float)
    d = c.diff(); up = d.clip(lower=0); dn = (-d).clip(lower=0)
    rs = rma(up, n) / (rma(dn, n) + EPS)
    return 100 - 100 / (1 + rs)


def macd(c):
    c = pd.Series(c, dtype=float)
    m = ema(c, 12) - ema(c, 26)
    sig = ema(m, 9)
    return m, sig, m - sig


def stochastic(h, l, c, n=14, sm=3):
    h = pd.Series(h, dtype=float); l = pd.Series(l, dtype=float); c = pd.Series(c, dtype=float)
    lo = l.rolling(n).min(); hi = h.rolling(n).max()
    k = 100 * (c - lo) / (hi - lo + EPS)
    k = sma(k, sm); d = sma(k, 3)
    return k, d


def stochrsi(c, n=14):
    x = rsi(c, n); lo = x.rolling(n).min(); hi = x.rolling(n).max()
    k = 100 * (x - lo) / (hi - lo + EPS)
    k = sma(k, 3); d = sma(k, 3)
    return k, d


def cci(h, l, c, n=20):
    h = pd.Series(h, dtype=float); l = pd.Series(l, dtype=float); c = pd.Series(c, dtype=float)
    tp = (h + l + c) / 3
    ma = sma(tp, n)
    md = tp.rolling(n).apply(lambda x: np.mean(np.abs(x - np.mean(x))), raw=True)
    return (tp - ma) / (0.015 * md + EPS)


def willr(h, l, c, n=14):
    h = pd.Series(h, dtype=float); l = pd.Series(l, dtype=float); c = pd.Series(c, dtype=float)
    hi = h.rolling(n).max(); lo = l.rolling(n).min()
    return -100 * (hi - c) / (hi - lo + EPS)


def dmi(h, l, c, n=14):
    h = pd.Series(h, dtype=float); l = pd.Series(l, dtype=float); c = pd.Series(c, dtype=float)
    up = h.diff(); dn = -l.diff()
    plus = np.where((up > dn) & (up > 0), up, 0.0)
    minus = np.where((dn > up) & (dn > 0), dn, 0.0)
    a = atr(h, l, c, n)
    p = 100 * rma(pd.Series(plus), n) / (a + EPS)
    m = 100 * rma(pd.Series(minus), n) / (a + EPS)
    dx = 100 * (p-m).abs() / (p+m+EPS)
    return p, m, rma(dx, n)


def aroon(h, l, n=25):
    h = pd.Series(h, dtype=float); l = pd.Series(l, dtype=float)
    up = h.rolling(n+1).apply(lambda x: 100*np.argmax(x)/n, raw=True)
    dn = l.rolling(n+1).apply(lambda x: 100*np.argmax(-x)/n, raw=True)
    return up, dn


def vortex(h, l, c, n=14):
    h = pd.Series(h, dtype=float); l = pd.Series(l, dtype=float)
    den = tr(h, l, c).rolling(n).sum()
    vp = (h-l.shift(1)).abs().rolling(n).sum()/(den+EPS)
    vm = (l-h.shift(1)).abs().rolling(n).sum()/(den+EPS)
    return vp, vm


def obv(c, v):
    c = pd.Series(c, dtype=float); v = pd.Series(v, dtype=float)
    return (np.sign(c.diff()).fillna(0) * v).cumsum()


def mfi(h, l, c, v, n=14):
    h = pd.Series(h, dtype=float); l = pd.Series(l, dtype=float); c = pd.Series(c, dtype=float); v = pd.Series(v, dtype=float)
    tp = (h+l+c)/3; mf = tp*v
    pos = mf.where(tp.diff()>0, 0.0); neg = mf.where(tp.diff()<0, 0.0)
    ratio = pos.rolling(n).sum()/(neg.rolling(n).sum()+EPS)
    return 100-100/(1+ratio)


def cmf(h, l, c, v, n=20):
    h = pd.Series(h, dtype=float); l = pd.Series(l, dtype=float); c = pd.Series(c, dtype=float); v = pd.Series(v, dtype=float)
    mult = ((c-l)-(h-c))/(h-l+EPS)
    return (mult*v).rolling(n).sum()/(v.rolling(n).sum()+EPS)


def fisher(h, l, n=10):
    h = pd.Series(h, dtype=float); l = pd.Series(l, dtype=float)
    med=(h+l)/2; hi=med.rolling(n).max(); lo=med.rolling(n).min()
    v=2*((med-lo)/(hi-lo+EPS)-0.5)
    v=v.clip(-.999,.999)
    return .5*np.log((1+v)/(1-v))


def wavetrend(h, l, c, n1=10, n2=21):
    h = pd.Series(h, dtype=float); l = pd.Series(l, dtype=float); c = pd.Series(c, dtype=float)
    ap=(h+l+c)/3; esa=ema(ap,n1); d=ema((ap-esa).abs(),n1)
    ci=(ap-esa)/(0.015*d+EPS); wt1=ema(ci,n2); wt2=sma(wt1,4)
    return wt1, wt2


def tsi(c, long=25, short=13, signal=7):
    c = pd.Series(c, dtype=float); m=c.diff()
    num=ema(ema(m,short),long); den=ema(ema(m.abs(),short),long)
    x=100*num/(den+EPS)
    return x, ema(x,signal)


def ultimate(h, l, c, s1=7, s2=14, s3=28):
    h = pd.Series(h, dtype=float); l = pd.Series(l, dtype=float); c = pd.Series(c, dtype=float)
    pc=c.shift(1)
    bp=c-pd.concat([l,pc],axis=1).min(axis=1)
    trr=pd.concat([h,pc],axis=1).max(axis=1)-pd.concat([l,pc],axis=1).min(axis=1)
    a1=bp.rolling(s1).sum()/(trr.rolling(s1).sum()+EPS)
    a2=bp.rolling(s2).sum()/(trr.rolling(s2).sum()+EPS)
    a3=bp.rolling(s3).sum()/(trr.rolling(s3).sum()+EPS)
    return 100*(4*a1+2*a2+a3)/7


def kama(c, n=10, fast=2, slow=30):
    c=pd.Series(c,dtype=float)
    change=(c-c.shift(n)).abs(); vol=c.diff().abs().rolling(n).sum()
    er=change/(vol+EPS)
    sc=(er*(2/(fast+1)-2/(slow+1))+2/(slow+1))**2
    out=pd.Series(np.nan,index=c.index,dtype=float)
    if len(c)<=n: return out
    out.iloc[n]=c.iloc[n]
    for i in range(n+1,len(c)):
        if pd.isna(sc.iloc[i]): continue
        prev=out.iloc[i-1] if pd.notna(out.iloc[i-1]) else c.iloc[i-1]
        out.iloc[i]=prev+sc.iloc[i]*(c.iloc[i]-prev)
    return out


def ssl(h, l, c, n=10):
    h=pd.Series(h,dtype=float); l=pd.Series(l,dtype=float); c=pd.Series(c,dtype=float)
    hs=sma(h,n); ls=sma(l,n); cur=0; state=[]
    for i in range(len(c)):
        if pd.notna(hs.iloc[i]) and c.iloc[i]>hs.iloc[i]: cur=1
        elif pd.notna(ls.iloc[i]) and c.iloc[i]<ls.iloc[i]: cur=-1
        state.append(cur)
    return pd.Series(state,index=c.index,dtype=float)


def utbot(h, l, c, key=1.0, n=10):
    c=pd.Series(c,dtype=float)
    loss=key*atr(h,l,c,n); trail=pd.Series(np.nan,index=c.index); state=pd.Series(0,index=c.index,dtype=float)
    for i in range(1,len(c)):
        if pd.isna(loss.iloc[i]): continue
        prev=trail.iloc[i-1]
        if pd.isna(prev): prev=c.iloc[i-1]
        cur,pc=c.iloc[i],c.iloc[i-1]
        if cur>prev and pc>prev: t=max(prev,cur-loss.iloc[i])
        elif cur<prev and pc<prev: t=min(prev,cur+loss.iloc[i])
        elif cur>prev: t=cur-loss.iloc[i]
        else: t=cur+loss.iloc[i]
        trail.iloc[i]=t
        if cur>t and pc<=prev: state.iloc[i]=1
        elif cur<t and pc>=prev: state.iloc[i]=-1
        else: state.iloc[i]=state.iloc[i-1]
    return state


def supertrend(h,l,c,n=14,mult=2.0):
    h=pd.Series(h,dtype=float); l=pd.Series(l,dtype=float); c=pd.Series(c,dtype=float)
    a=atr(h,l,c,n); hl2=(h+l)/2; ub=hl2+mult*a; lb=hl2-mult*a
    fub=ub.copy(); flb=lb.copy(); trend=pd.Series(np.nan,index=c.index)
    for i in range(1,len(c)):
        if pd.isna(a.iloc[i]): continue
        if pd.isna(fub.iloc[i-1]): fub.iloc[i-1]=ub.iloc[i-1]
        if pd.isna(flb.iloc[i-1]): flb.iloc[i-1]=lb.iloc[i-1]
        fub.iloc[i]=ub.iloc[i] if (ub.iloc[i]<fub.iloc[i-1] or c.iloc[i-1]>fub.iloc[i-1]) else fub.iloc[i-1]
        flb.iloc[i]=lb.iloc[i] if (lb.iloc[i]>flb.iloc[i-1] or c.iloc[i-1]<flb.iloc[i-1]) else flb.iloc[i-1]
        prev=trend.iloc[i-1] if pd.notna(trend.iloc[i-1]) else 1
        if prev<0 and c.iloc[i]>fub.iloc[i]: cur=1
        elif prev>0 and c.iloc[i]<flb.iloc[i]: cur=-1
        else: cur=prev
        trend.iloc[i]=cur
    return trend.fillna(0)


def chandelier(h,l,c,n=22,m=3):
    h=pd.Series(h,dtype=float); l=pd.Series(l,dtype=float); c=pd.Series(c,dtype=float)
    a=atr(h,l,c,n); longstop=h.rolling(n).max()-m*a; shortstop=l.rolling(n).min()+m*a
    state=pd.Series(0,index=c.index,dtype=float); cur=0
    for i in range(1,len(c)):
        if pd.isna(longstop.iloc[i-1]) or pd.isna(shortstop.iloc[i-1]): continue
        if c.iloc[i]>shortstop.iloc[i-1]: cur=1
        elif c.iloc[i]<longstop.iloc[i-1]: cur=-1
        state.iloc[i]=cur
    return state


def _state_loop(index,long_entry,long_exit,short_entry,short_exit):
    out=pd.Series(0,index=index,dtype=float); cur=0
    le=long_entry.fillna(False).to_numpy(); lx=long_exit.fillna(False).to_numpy()
    se=short_entry.fillna(False).to_numpy(); sx=short_exit.fillna(False).to_numpy()
    for i in range(len(out)):
        if cur==1 and lx[i]: cur=0
        elif cur==-1 and sx[i]: cur=0
        if cur==0:
            if le[i] and not se[i]: cur=1
            elif se[i] and not le[i]: cur=-1
        out.iloc[i]=cur
    return out


def _cross_up(a,b):
    if not isinstance(b,pd.Series): b=pd.Series(b,index=a.index)
    return (a>b)&(a.shift(1)<=b.shift(1))


def _cross_dn(a,b):
    if not isinstance(b,pd.Series): b=pd.Series(b,index=a.index)
    return (a<b)&(a.shift(1)>=b.shift(1))


def phase2_signal(close, high, low, volume, dates=None):
    c=pd.Series(close,dtype=float); h=pd.Series(high,dtype=float); l=pd.Series(low,dtype=float); v=pd.Series(volume,dtype=float)
    idx=c.index
    if FAMILY=="bitcoin_cycle_monthly_causal":
        if dates is None:
            raise RuntimeError("bitcoin_cycle_monthly_causal requires dates")
        dti=pd.DatetimeIndex(pd.to_datetime(np.asarray(dates),utc=True))
        s=pd.Series(c.to_numpy(),index=dti,dtype=float)
        monthly_close=s.resample("ME").last()
        monthly_ret=monthly_close.pct_change()
        out=pd.Series(0.0,index=dti)
        cached={}
        for i,dt in enumerate(dti):
            key=(dt.year,dt.month)
            if key not in cached:
                month_start=pd.Timestamp(
                    year=dt.year,month=dt.month,day=1,tz="UTC"
                )
                hist=monthly_ret[monthly_ret.index < month_start].dropna()
                mask=(
                    (hist.index.year % 4 == dt.year % 4)
                    & (hist.index.month == dt.month)
                )
                avg=float(hist[mask].mean()) if mask.any() else float("nan")
                cached[key]=1.0 if np.isfinite(avg) and avg>0 else 0.0
            out.iloc[i]=cached[key]
        return out.to_numpy()
    if FAMILY.startswith("sma_"):
        _,a,b=FAMILY.split("_"); return np.sign(sma(c,int(a))-sma(c,int(b))).fillna(0).to_numpy()
    if FAMILY.startswith("ema_"):
        _,a,b=FAMILY.split("_"); return np.sign(ema(c,int(a))-ema(c,int(b))).fillna(0).to_numpy()
    if FAMILY.startswith("dema_"):
        _,a,b=FAMILY.split("_"); return np.sign(dema(c,int(a))-dema(c,int(b))).fillna(0).to_numpy()
    if FAMILY.startswith("tema_"):
        _,a,b=FAMILY.split("_"); return np.sign(tema(c,int(a))-tema(c,int(b))).fillna(0).to_numpy()
    if FAMILY.startswith("hma_"):
        _,a,b=FAMILY.split("_"); return np.sign(hma(c,int(a))-hma(c,int(b))).fillna(0).to_numpy()
    if FAMILY.startswith("zlema_"):
        _,a,b=FAMILY.split("_"); return np.sign(zlema(c,int(a))-zlema(c,int(b))).fillna(0).to_numpy()
    if FAMILY=="macd_12_26_9":
        m,s,_=macd(c); out=np.sign(m-s)
    elif FAMILY=="ppo_12_26_9":
        p=100*(ema(c,12)-ema(c,26))/(ema(c,26)+EPS); out=np.sign(p-ema(p,9))
    elif FAMILY=="roc12_zero": out=np.sign(c.pct_change(12))
    elif FAMILY=="momentum10": out=np.sign(c-c.shift(10))
    elif FAMILY=="rsi14_momentum_55_45":
        r=rsi(c,14); out=pd.Series(np.where(r>55,1,np.where(r<45,-1,0)),index=idx)
    elif FAMILY=="rsi14_reversion_30_70":
        r=rsi(c,14); out=_state_loop(idx,_cross_up(r,30),r>=50,_cross_dn(r,70),r<=50)
    elif FAMILY=="stochastic_14_3_3":
        k,d=stochastic(h,l,c); out=_state_loop(idx,_cross_up(k,d)&(k<30),k>=50,_cross_dn(k,d)&(k>70),k<=50)
    elif FAMILY=="stochrsi_14":
        k,d=stochrsi(c); out=_state_loop(idx,_cross_up(k,d)&(k<30),k>=50,_cross_dn(k,d)&(k>70),k<=50)
    elif FAMILY=="utbot_key1_atr10": out=utbot(h,l,c,1.0,10)
    elif FAMILY=="cci20_100":
        x=cci(h,l,c,20); out=_state_loop(idx,_cross_up(x,100),x<0,_cross_dn(x,-100),x>0)
    elif FAMILY=="williams_r14":
        x=willr(h,l,c,14); out=_state_loop(idx,_cross_up(x,-80),x>=-50,_cross_dn(x,-20),x<=-50)
    elif FAMILY=="bollinger_breakout_20_2":
        mid=sma(c,20); sd=c.rolling(20).std(); up=mid+2*sd; lo=mid-2*sd
        out=_state_loop(idx,_cross_up(c,up),c<=mid,_cross_dn(c,lo),c>=mid)
    elif FAMILY=="keltner_breakout_20_2atr":
        mid=ema(c,20); a=atr(h,l,c,10); up=mid+2*a; lo=mid-2*a
        out=_state_loop(idx,_cross_up(c,up),c<=mid,_cross_dn(c,lo),c>=mid)
    elif FAMILY=="donchian_50_state":
        hi=h.shift(1).rolling(50).max(); lo=l.shift(1).rolling(50).min()
        out=pd.Series(0,index=idx,dtype=float); cur=0
        for i in range(len(c)):
            if bool((c>hi).fillna(False).iloc[i]): cur=1
            elif bool((c<lo).fillna(False).iloc[i]): cur=-1
            out.iloc[i]=cur
    elif FAMILY=="supertrend_14_2": out=supertrend(h,l,c,14,2)
    elif FAMILY=="chandelier_22_3": out=chandelier(h,l,c,22,3)
    elif FAMILY=="dmi_adx14_20":
        p,m,a=dmi(h,l,c,14); out=pd.Series(np.where(a>20,np.sign(p-m),0),index=idx)
    elif FAMILY=="aroon25":
        au,ad=aroon(h,l,25); out=pd.Series(np.where((au>70)&(ad<30),1,np.where((ad>70)&(au<30),-1,0)),index=idx)
    elif FAMILY=="vortex14":
        vp,vm=vortex(h,l,c,14); out=np.sign(vp-vm)
    elif FAMILY=="ichimoku_9_26_52":
        ten=(h.rolling(9).max()+l.rolling(9).min())/2
        kij=(h.rolling(26).max()+l.rolling(26).min())/2
        sa=(ten+kij)/2; sb=(h.rolling(52).max()+l.rolling(52).min())/2
        out=pd.Series(np.where((ten>kij)&(c>pd.concat([sa,sb],axis=1).max(axis=1)),1,np.where((ten<kij)&(c<pd.concat([sa,sb],axis=1).min(axis=1)),-1,0)),index=idx)
    elif FAMILY=="obv_ema20":
        x=obv(c,v); out=np.sign(x-ema(x,20))
    elif FAMILY=="mfi14_reversion":
        x=mfi(h,l,c,v,14); out=_state_loop(idx,_cross_up(x,20),x>=50,_cross_dn(x,80),x<=50)
    elif FAMILY=="cmf20_zero": out=np.sign(cmf(h,l,c,v,20))
    elif FAMILY=="chaikin_osc_3_10":
        adl=((((c-l)-(h-c))/(h-l+EPS))*v).cumsum(); out=np.sign(ema(adl,3)-ema(adl,10))
    elif FAMILY=="force_index13": out=np.sign(ema(c.diff()*v,13))
    elif FAMILY=="awesome_oscillator": out=np.sign(sma((h+l)/2,5)-sma((h+l)/2,34))
    elif FAMILY=="fisher10": out=np.sign(fisher(h,l,10))
    elif FAMILY=="wavetrend_10_21":
        a,b=wavetrend(h,l,c,10,21); out=np.sign(a-b)
    elif FAMILY=="tsi_25_13_7":
        a,b=tsi(c,25,13,7); out=np.sign(a-b)
    elif FAMILY=="ultimate_oscillator_50": out=np.sign(ultimate(h,l,c)-50)
    elif FAMILY=="kama10_cross": out=np.sign(c-kama(c,10))
    elif FAMILY=="ssl_channel10": out=ssl(h,l,c,10)
    elif FAMILY=="heikin_ashi_color":
        o=c.copy();  # placeholder overwritten by close-only approximation is forbidden
        out=pd.Series(0,index=idx,dtype=float)
    elif FAMILY=="ha_ema50":
        out=pd.Series(0,index=idx,dtype=float)
    elif FAMILY=="elder_impulse":
        e13=ema(c,13); _,_,hist=macd(c)
        out=pd.Series(np.where((e13.diff()>0)&(hist.diff()>0),1,np.where((e13.diff()<0)&(hist.diff()<0),-1,0)),index=idx)
    elif FAMILY=="utbot_ema200":
        u=utbot(h,l,c,1.0,10); e=ema(c,200)
        out=pd.Series(np.where((u>0)&(c>e),1,np.where((u<0)&(c<e),-1,0)),index=idx)
    elif FAMILY=="trend_magic_cci20":
        x=cci(h,l,c,20); out=pd.Series(0,index=idx,dtype=float); cur=0
        for i in range(len(c)):
            if pd.notna(x.iloc[i]) and x.iloc[i]>100: cur=1
            elif pd.notna(x.iloc[i]) and x.iloc[i]<-100: cur=-1
            out.iloc[i]=cur
    else:
        raise RuntimeError("unknown phase2 family: "+FAMILY)
    return pd.Series(out,index=idx).replace([np.inf,-np.inf],np.nan).fillna(0).clip(-1,1).to_numpy()


def realized_vol(close, n=30):
    c=pd.Series(close,dtype=float); r=np.log(c/c.shift(1))
    return (r.rolling(n).std(ddof=0).shift(1)*np.sqrt(BARS_PER_YEAR)).to_numpy()


class MoonStrategy(Strategy):
    vol_lookback=30
    vol_target=0.08
    f_max=0.5

    def _units(self,px,rv):
        if not np.isfinite(px) or px<=0 or not np.isfinite(rv) or rv<=0: return 0
        exposure=min(self.f_max,self.vol_target/rv)
        return max(0,int((float(self.equity)*exposure)/px))

    def init(self):
        self.sig=self.I(
            phase2_signal,
            self.data.Close,
            self.data.High,
            self.data.Low,
            self.data.Volume,
            np.asarray(self.data.index),
        )
        self.rv=self.I(realized_vol,self.data.Close,self.vol_lookback)

    def next(self):
        if len(self.data.Close)<40: return
        px=float(self.data.Close[-1]); rv=float(self.rv[-1]); sig=float(self.sig[-1])
        if not np.isfinite([px,rv,sig]).all() or rv<=0: return
        desired=1 if sig>0 else (-1 if sig<0 else 0)
        current=1 if self.position.is_long else (-1 if self.position.is_short else 0)
        if desired==current: return
        if self.position: self.position.close()
        if desired==0: return
        units=self._units(px,rv)
        if units<1: return
        if desired>0: self.buy(size=units)
        else: self.sell(size=units)
