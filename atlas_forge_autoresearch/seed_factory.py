"""Generate one researched-family seed strategy.py for continuous AUTORESEARCH.

The factory intentionally distinguishes exact/reconstructed/proxy families in
strategy_library/registry.json. It never emits families marked blocked.
"""

import argparse
import json
from pathlib import Path

HERE = Path(__file__).resolve().parent
REGISTRY = HERE / "strategy_library" / "registry.json"


COMMON = r'''
import numpy as np
import pandas as pd
from backtesting import Strategy

BARS_PER_YEAR = __BARS_PER_YEAR__


def _rolling_high(x, n):
    return pd.Series(x).rolling(n).max().shift(1).to_numpy()


def _rolling_low(x, n):
    return pd.Series(x).rolling(n).min().shift(1).to_numpy()


def _sma(x, n):
    return pd.Series(x, dtype=float).rolling(n).mean().shift(1).to_numpy()


def _ema(x, n):
    return pd.Series(x, dtype=float).ewm(span=n, adjust=False).mean().shift(1).to_numpy()


def _rolling_std(x, n):
    return pd.Series(x, dtype=float).rolling(n).std(ddof=0).shift(1).to_numpy()


def _sma_now(x, n):
    return pd.Series(x, dtype=float).rolling(n).mean().to_numpy()


def _ema_now(x, n):
    return pd.Series(x, dtype=float).ewm(span=n, adjust=False).mean().to_numpy()


def _std_now(x, n):
    return pd.Series(x, dtype=float).rolling(n).std(ddof=0).to_numpy()


def _rolling_min_now(x, n):
    return pd.Series(x, dtype=float).rolling(n).min().to_numpy()


def _rolling_max_now(x, n):
    return pd.Series(x, dtype=float).rolling(n).max().to_numpy()


def _rolling_sum_now(x, n):
    return pd.Series(x, dtype=float).rolling(n).sum().to_numpy()


def _roc_now(x, n):
    s = pd.Series(x, dtype=float)
    return (s / s.shift(n) - 1.0).to_numpy()


def _range_mean_now(high, low, n):
    h = pd.Series(high, dtype=float)
    l = pd.Series(low, dtype=float)
    return (h - l).rolling(n).mean().to_numpy()


def _rsi_now(x, n):
    s = pd.Series(x, dtype=float)
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100/(1+rs)).to_numpy()


def _adx_now(high, low, close, n):
    h = pd.Series(high, dtype=float)
    l = pd.Series(low, dtype=float)
    c = pd.Series(close, dtype=float)
    up = h.diff()
    dn = -l.diff()
    plus_dm = pd.Series(np.where((up > dn) & (up > 0), up, 0.0))
    minus_dm = pd.Series(np.where((dn > up) & (dn > 0), dn, 0.0))
    pc = c.shift(1)
    tr = pd.concat([(h-l).abs(), (h-pc).abs(), (l-pc).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1/n, adjust=False).mean()
    plus = 100 * plus_dm.ewm(alpha=1/n, adjust=False).mean() / atr.replace(0, np.nan)
    minus = 100 * minus_dm.ewm(alpha=1/n, adjust=False).mean() / atr.replace(0, np.nan)
    dx = 100 * (plus-minus).abs() / (plus+minus).replace(0, np.nan)
    return dx.ewm(alpha=1/n, adjust=False).mean().to_numpy()


def _roc(x, n):
    s = pd.Series(x, dtype=float)
    return (s / s.shift(n) - 1.0).shift(1).to_numpy()


def _atr(high, low, close, n):
    h = pd.Series(high, dtype=float)
    l = pd.Series(low, dtype=float)
    c = pd.Series(close, dtype=float)
    pc = c.shift(1)
    tr = pd.concat([(h-l), (h-pc).abs(), (l-pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean().shift(1).to_numpy()


def _realized_vol(close, n):
    c = pd.Series(close, dtype=float)
    r = np.log(c / c.shift(1))
    return (r.rolling(n).std(ddof=0).shift(1) * np.sqrt(BARS_PER_YEAR)).to_numpy()


def _vol_mean(volume, n):
    return pd.Series(volume, dtype=float).rolling(n).mean().shift(1).to_numpy()


def _zscore(x, n):
    s = pd.Series(x, dtype=float)
    m = s.rolling(n).mean()
    sd = s.rolling(n).std(ddof=0)
    return ((s-m)/sd).shift(1).to_numpy()


def _rsi(x, n):
    s = pd.Series(x, dtype=float)
    d = s.diff()
    up = d.clip(lower=0).ewm(alpha=1/n, adjust=False).mean()
    dn = (-d.clip(upper=0)).ewm(alpha=1/n, adjust=False).mean()
    rs = up / dn.replace(0, np.nan)
    return (100 - 100/(1+rs)).shift(1).to_numpy()


def _atr_ratio_mean(high, low, close, atr_n, mean_n):
    atr = pd.Series(_atr(high, low, close, atr_n), dtype=float)
    c = pd.Series(close, dtype=float)
    ratio = atr / c.shift(1)
    return ratio.rolling(mean_n).mean().shift(1).to_numpy()


class AtlasStrategy(Strategy):
    vol_lookback = 30
    vol_target = __VOL_TARGET__
    f_max = __F_MAX__

    def _units(self, px, rv):
        if not np.isfinite(px) or px <= 0 or not np.isfinite(rv) or rv <= 0:
            return 0
        exposure = min(self.f_max, self.vol_target / rv)
        return max(0, int((float(self.equity) * exposure) / px))

    def _buy_with_stop(self, px, atr, stop_mult=3.0):
        units = self._units(px, float(self.rv[-1]))
        if units >= 1:
            self.buy(size=units, sl=px - stop_mult * atr)

__BODY__
'''


BODIES = {
"sma200_regime": r'''
    def init(self):
        self.sma200 = self.I(_sma_now, self.data.Close, 200)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 220:
            return
        px=float(self.data.Close[-1]); sma=float(self.sma200[-1]); rv=float(self.rv[-1])
        if not np.isfinite([px,sma,rv]).all() or rv <= 0:
            return
        if not self.position and px > sma:
            units=self._units(px,rv)
            if units >= 1:
                self.buy(size=units)
        elif self.position and px < sma:
            self.position.close()
''',

"connors_rsi2_65_nextopen": r'''
    def init(self):
        self.sma200 = self.I(_sma_now, self.data.Close, 200)
        self.rsi2 = self.I(_rsi_now, self.data.Close, 2)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 220:
            return
        px=float(self.data.Close[-1]); sma=float(self.sma200[-1]); r=float(self.rsi2[-1]); rv=float(self.rv[-1])
        if not np.isfinite([px,sma,r,rv]).all() or rv <= 0:
            return
        if not self.position and px > sma and r < 5:
            units=self._units(px,rv)
            if units >= 1:
                self.buy(size=units)
        elif self.position and r > 65:
            self.position.close()
''',

"cumulative_rsi3_45_nextopen": r'''
    def init(self):
        self.rsi2 = self.I(_rsi_now, self.data.Close, 2)
        self.rsi3sum = self.I(_rolling_sum_now, self.rsi2, 3)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 50:
            return
        px=float(self.data.Close[-1]); r=float(self.rsi2[-1]); s=float(self.rsi3sum[-1]); rv=float(self.rv[-1])
        if not np.isfinite([px,r,s,rv]).all() or rv <= 0:
            return
        if not self.position and s < 45:
            units=self._units(px,rv)
            if units >= 1:
                self.buy(size=units)
        elif self.position and r > 65:
            self.position.close()
''',

"bear_four_up_rsi2_nextopen": r'''
    def init(self):
        self.sma200 = self.I(_sma_now, self.data.Close, 200)
        self.rsi2 = self.I(_rsi_now, self.data.Close, 2)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 220:
            return
        px=float(self.data.Close[-1]); sma=float(self.sma200[-1]); r=float(self.rsi2[-1]); rv=float(self.rv[-1])
        if not np.isfinite([px,sma,r,rv]).all() or rv <= 0:
            return
        closes=[float(self.data.Close[-i]) for i in range(1,6)]
        four_up = closes[0] > closes[1] > closes[2] > closes[3] > closes[4]
        if not self.position and px < sma and four_up:
            units=self._units(px,rv)
            if units >= 1:
                self.sell(size=units)
        elif self.position and r < 30:
            self.position.close()
''',

"sentinel63": r'''
    def init(self):
        self.ema63 = self.I(_ema_now, self.data.Close, 63)
        self.sd63 = self.I(_std_now, self.data.Close, 63)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 80:
            return
        px=float(self.data.Close[-1]); ema=float(self.ema63[-1]); sd=float(self.sd63[-1]); rv=float(self.rv[-1])
        if not np.isfinite([px,ema,sd,rv]).all() or sd <= 0 or rv <= 0:
            return
        z=(px-ema)/sd
        if not self.position and z > 0.5:
            units=self._units(px,rv)
            if units >= 1:
                self.buy(size=units)
        elif self.position and z < -0.5:
            self.position.close()
''',

"sentinel65": r'''
    def init(self):
        self.ema65 = self.I(_ema_now, self.data.Close, 65)
        self.sd65 = self.I(_std_now, self.data.Close, 65)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 82:
            return
        px=float(self.data.Close[-1]); ema=float(self.ema65[-1]); sd=float(self.sd65[-1]); rv=float(self.rv[-1])
        if not np.isfinite([px,ema,sd,rv]).all() or sd <= 0 or rv <= 0:
            return
        z=(px-ema)/sd
        if not self.position and z > 0.5:
            units=self._units(px,rv)
            if units >= 1:
                self.buy(size=units)
        elif self.position and z < -0.5:
            self.position.close()
''',

"ibs_deep_pullback": r'''
    def init(self):
        self.prior_high10 = self.I(_rolling_high, self.data.High, 10)
        self.avg_range25 = self.I(_range_mean_now, self.data.High, self.data.Low, 25)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 45:
            return
        px=float(self.data.Close[-1]); lo=float(self.data.Low[-1]); hi=float(self.data.High[-1])
        prior=float(self.prior_high10[-1]); avg=float(self.avg_range25[-1]); rv=float(self.rv[-1])
        if not np.isfinite([px,lo,hi,prior,avg,rv]).all() or hi <= lo or rv <= 0:
            return
        ibs=(px-lo)/(hi-lo)
        lower=prior - 2.5*avg
        if not self.position and px < lower and ibs < 0.30:
            units=self._units(px,rv)
            if units >= 1:
                self.buy(size=units)
        elif self.position and len(self.data.High) >= 2 and px > float(self.data.High[-2]):
            self.position.close()
''',

"connors_rsi2": r'''
    def init(self):
        self.sma200 = self.I(_sma_now, self.data.Close, 200)
        self.sma5 = self.I(_sma_now, self.data.Close, 5)
        self.rsi2 = self.I(_rsi_now, self.data.Close, 2)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 220:
            return
        px=float(self.data.Close[-1]); s200=float(self.sma200[-1]); s5=float(self.sma5[-1]); r=float(self.rsi2[-1]); rv=float(self.rv[-1])
        if not np.isfinite([px,s200,s5,r,rv]).all() or rv <= 0:
            return
        if not self.position and px > s200 and r < 5:
            units=self._units(px,rv)
            if units >= 1:
                self.buy(size=units)
        elif self.position and px > s5:
            self.position.close()
''',

"connors_double7": r'''
    def init(self):
        self.sma200 = self.I(_sma_now, self.data.Close, 200)
        self.low7 = self.I(_rolling_min_now, self.data.Close, 7)
        self.high7 = self.I(_rolling_max_now, self.data.Close, 7)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 220:
            return
        px=float(self.data.Close[-1]); s200=float(self.sma200[-1]); lo=float(self.low7[-1]); hi=float(self.high7[-1]); rv=float(self.rv[-1])
        if not np.isfinite([px,s200,lo,hi,rv]).all() or rv <= 0:
            return
        eps=max(1e-12,abs(px)*1e-12)
        if not self.position and px > s200 and px <= lo + eps:
            units=self._units(px,rv)
            if units >= 1:
                self.buy(size=units)
        elif self.position and px >= hi - eps:
            self.position.close()
''',

"btc_rsi_adx": r'''
    def init(self):
        self.sma50 = self.I(_sma_now, self.data.Close, 50)
        self.ema7 = self.I(_ema_now, self.data.Close, 7)
        self.rsi2 = self.I(_rsi_now, self.data.Close, 2)
        self.adx2 = self.I(_adx_now, self.data.High, self.data.Low, self.data.Close, 2)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 70:
            return
        px=float(self.data.Close[-1]); s50=float(self.sma50[-1]); e7=float(self.ema7[-1]); r=float(self.rsi2[-1]); a=float(self.adx2[-1]); rv=float(self.rv[-1])
        if not np.isfinite([px,s50,e7,r,a,rv]).all() or rv <= 0:
            return
        entry = px > s50 and px > e7 and r > a
        if not self.position and entry:
            units=self._units(px,rv)
            if units >= 1:
                self.buy(size=units)
        elif self.position and r < a:
            self.position.close()
''',

"tsmom_252": r'''
    def init(self):
        self.roc252 = self.I(_roc_now, self.data.Close, 252)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 280:
            return
        px=float(self.data.Close[-1]); roc=float(self.roc252[-1]); rv=float(self.rv[-1])
        if not np.isfinite([px,roc,rv]).all() or rv <= 0:
            return
        if not self.position and roc > 0:
            units=self._units(px,rv)
            if units >= 1:
                self.buy(size=units)
        elif self.position and roc <= 0:
            self.position.close()
''',

"donchian_20_10": r'''
    entry_lookback = 20
    exit_lookback = 10

    def init(self):
        self.hh = self.I(_rolling_high, self.data.High, self.entry_lookback)
        self.ll = self.I(_rolling_low, self.data.Low, self.exit_lookback)
        self.atr = self.I(_atr, self.data.High, self.data.Low, self.data.Close, 20)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 40:
            return
        px=float(self.data.Close[-1]); hh=float(self.hh[-1]); ll=float(self.ll[-1]); atr=float(self.atr[-1])
        if not np.isfinite([px,hh,ll,atr,float(self.rv[-1])]).all() or atr <= 0:
            return
        if not self.position and px > hh:
            self._buy_with_stop(px, atr, 3.0)
        elif self.position and px < ll:
            self.position.close()
''',

"turtle_55_20": r'''
    def init(self):
        self.hh = self.I(_rolling_high, self.data.High, 55)
        self.ll = self.I(_rolling_low, self.data.Low, 20)
        self.atr = self.I(_atr, self.data.High, self.data.Low, self.data.Close, 20)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 70:
            return
        px=float(self.data.Close[-1]); hh=float(self.hh[-1]); ll=float(self.ll[-1]); atr=float(self.atr[-1])
        if not np.isfinite([px,hh,ll,atr,float(self.rv[-1])]).all() or atr <= 0:
            return
        if not self.position and px > hh:
            self._buy_with_stop(px, atr, 2.0)
        elif self.position and px < ll:
            self.position.close()
''',

"sma175_regime_proxy": r'''
    def init(self):
        self.sma = self.I(_sma, self.data.Close, 175)
        self.atr = self.I(_atr, self.data.High, self.data.Low, self.data.Close, 20)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 190:
            return
        px=float(self.data.Close[-1]); sma=float(self.sma[-1]); atr=float(self.atr[-1])
        if not np.isfinite([px,sma,atr,float(self.rv[-1])]).all() or atr <= 0:
            return
        if not self.position and px > sma:
            self._buy_with_stop(px, atr, 5.0)
        elif self.position and px < sma:
            self.position.close()
''',

"sma200_roc126_proxy": r'''
    def init(self):
        self.sma = self.I(_sma, self.data.Close, 200)
        self.roc = self.I(_roc, self.data.Close, 126)
        self.atr = self.I(_atr, self.data.High, self.data.Low, self.data.Close, 20)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 220:
            return
        px=float(self.data.Close[-1]); sma=float(self.sma[-1]); roc=float(self.roc[-1]); atr=float(self.atr[-1])
        if not np.isfinite([px,sma,roc,atr,float(self.rv[-1])]).all() or atr <= 0:
            return
        risk_on = px > sma and roc > 0
        if not self.position and risk_on:
            self._buy_with_stop(px, atr, 5.0)
        elif self.position and not risk_on:
            self.position.close()
''',

"roc126_regime": r'''
    def init(self):
        self.roc = self.I(_roc, self.data.Close, 126)
        self.atr = self.I(_atr, self.data.High, self.data.Low, self.data.Close, 20)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 145:
            return
        px=float(self.data.Close[-1]); roc=float(self.roc[-1]); atr=float(self.atr[-1])
        if not np.isfinite([px,roc,atr,float(self.rv[-1])]).all() or atr <= 0:
            return
        if not self.position and roc > 0:
            self._buy_with_stop(px, atr, 5.0)
        elif self.position and roc <= 0:
            self.position.close()
''',

"zanger_volume_breakout_proxy": r'''
    def init(self):
        self.hh = self.I(_rolling_high, self.data.High, 50)
        self.sma50 = self.I(_sma, self.data.Close, 50)
        self.sma20 = self.I(_sma, self.data.Close, 20)
        self.vma = self.I(_vol_mean, self.data.Volume, 20)
        self.atr = self.I(_atr, self.data.High, self.data.Low, self.data.Close, 20)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 70:
            return
        px=float(self.data.Close[-1]); hh=float(self.hh[-1]); s50=float(self.sma50[-1]); s20=float(self.sma20[-1])
        vol=float(self.data.Volume[-1]); vma=float(self.vma[-1]); atr=float(self.atr[-1])
        if not np.isfinite([px,hh,s50,s20,vol,vma,atr,float(self.rv[-1])]).all() or atr <= 0:
            return
        entry = px > hh and px > s50 and vma > 0 and vol > 1.5 * vma
        if not self.position and entry:
            self._buy_with_stop(px, atr, 2.5)
        elif self.position and px < s20:
            self.position.close()
''',

"swing_terminal_breakout_proxy": r'''
    def init(self):
        self.hh = self.I(_rolling_high, self.data.High, 20)
        self.sma50 = self.I(_sma, self.data.Close, 50)
        self.vma = self.I(_vol_mean, self.data.Volume, 20)
        self.atr = self.I(_atr, self.data.High, self.data.Low, self.data.Close, 20)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 70:
            return
        px=float(self.data.Close[-1]); hh=float(self.hh[-1]); s50=float(self.sma50[-1])
        vol=float(self.data.Volume[-1]); vma=float(self.vma[-1]); atr=float(self.atr[-1])
        if not np.isfinite([px,hh,s50,vol,vma,atr,float(self.rv[-1])]).all() or atr <= 0:
            return
        entry = px > hh and px > s50 and (vma <= 0 or vol >= vma)
        if not self.position and entry:
            self._buy_with_stop(px, atr, 2.0)
        elif self.position and px < s50:
            self.position.close()
''',

"swing_terminal_pullback_proxy": r'''
    def init(self):
        self.ema20 = self.I(_ema, self.data.Close, 20)
        self.ema50 = self.I(_ema, self.data.Close, 50)
        self.atr = self.I(_atr, self.data.High, self.data.Low, self.data.Close, 20)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 70:
            return
        px=float(self.data.Close[-1]); e20=float(self.ema20[-1]); e50=float(self.ema50[-1]); atr=float(self.atr[-1])
        if not np.isfinite([px,e20,e50,atr,float(self.rv[-1])]).all() or atr <= 0:
            return
        trend = e20 > e50 and px > e50
        pullback = abs(px - e20) <= 0.40 * atr
        if not self.position and trend and pullback:
            self._buy_with_stop(px, atr, 1.5)
        elif self.position and px < e50:
            self.position.close()
''',

"zscore_mean_reversion": r'''
    def init(self):
        self.z = self.I(_zscore, self.data.Close, 20)
        self.atr = self.I(_atr, self.data.High, self.data.Low, self.data.Close, 20)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 50:
            return
        px=float(self.data.Close[-1]); z=float(self.z[-1]); atr=float(self.atr[-1])
        if not np.isfinite([px,z,atr,float(self.rv[-1])]).all() or atr <= 0:
            return
        if not self.position and z < -1.5:
            self._buy_with_stop(px, atr, 2.0)
        elif self.position and z >= 0:
            self.position.close()
''',

"bollinger_mean_reversion": r'''
    def init(self):
        self.ma = self.I(_sma, self.data.Close, 20)
        self.sd = self.I(_rolling_std, self.data.Close, 20)
        self.atr = self.I(_atr, self.data.High, self.data.Low, self.data.Close, 20)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 50:
            return
        px=float(self.data.Close[-1]); ma=float(self.ma[-1]); sd=float(self.sd[-1]); atr=float(self.atr[-1])
        if not np.isfinite([px,ma,sd,atr,float(self.rv[-1])]).all() or sd <= 0 or atr <= 0:
            return
        if not self.position and px < ma - 2.0 * sd:
            self._buy_with_stop(px, atr, 2.0)
        elif self.position and px >= ma:
            self.position.close()
''',

"atr_channel_trend": r'''
    def init(self):
        self.ema = self.I(_ema, self.data.Close, 50)
        self.atr = self.I(_atr, self.data.High, self.data.Low, self.data.Close, 20)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 70:
            return
        px=float(self.data.Close[-1]); ema=float(self.ema[-1]); atr=float(self.atr[-1])
        if not np.isfinite([px,ema,atr,float(self.rv[-1])]).all() or atr <= 0:
            return
        if not self.position and px > ema + 1.5 * atr:
            self._buy_with_stop(px, atr, 2.5)
        elif self.position and px < ema:
            self.position.close()
''',

"vol_contraction_breakout": r'''
    def init(self):
        self.hh = self.I(_rolling_high, self.data.High, 20)
        self.atr = self.I(_atr, self.data.High, self.data.Low, self.data.Close, 20)
        self.atr_ratio_mean = self.I(_atr_ratio_mean, self.data.High, self.data.Low, self.data.Close, 20, 100)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 130:
            return
        px=float(self.data.Close[-1]); hh=float(self.hh[-1]); atr=float(self.atr[-1]); arm=float(self.atr_ratio_mean[-1])
        if not np.isfinite([px,hh,atr,arm,float(self.rv[-1])]).all() or atr <= 0 or px <= 0:
            return
        contraction = (atr / px) < arm
        if not self.position and px > hh and contraction:
            self._buy_with_stop(px, atr, 2.5)
        elif self.position and px < hh - 2.0 * atr:
            self.position.close()
''',

"qqe_proxy": r'''
    def init(self):
        self.rsi = self.I(_rsi, self.data.Close, 14)
        self.rsi_ma = self.I(_sma, self.rsi, 5)
        self.atr = self.I(_atr, self.data.High, self.data.Low, self.data.Close, 20)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 60:
            return
        px=float(self.data.Close[-1]); r=float(self.rsi[-1]); rm=float(self.rsi_ma[-1]); atr=float(self.atr[-1])
        if not np.isfinite([px,r,rm,atr,float(self.rv[-1])]).all() or atr <= 0:
            return
        if not self.position and r > 55 and r > rm:
            self._buy_with_stop(px, atr, 2.5)
        elif self.position and (r < 50 or r < rm):
            self.position.close()
''',

"halftrend_proxy": r'''
    def init(self):
        self.fast = self.I(_ema, self.data.Close, 20)
        self.slow = self.I(_ema, self.data.Close, 50)
        self.low20 = self.I(_rolling_low, self.data.Low, 20)
        self.atr = self.I(_atr, self.data.High, self.data.Low, self.data.Close, 20)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 70:
            return
        px=float(self.data.Close[-1]); fast=float(self.fast[-1]); slow=float(self.slow[-1]); low20=float(self.low20[-1]); atr=float(self.atr[-1])
        if not np.isfinite([px,fast,slow,low20,atr,float(self.rv[-1])]).all() or atr <= 0:
            return
        if not self.position and fast > slow and px > fast:
            self._buy_with_stop(px, atr, 2.5)
        elif self.position and (fast < slow or px < low20):
            self.position.close()
''',

"donchian_sma50": r'''
    def init(self):
        self.hh = self.I(_rolling_high, self.data.High, 20)
        self.ll = self.I(_rolling_low, self.data.Low, 10)
        self.sma50 = self.I(_sma, self.data.Close, 50)
        self.atr = self.I(_atr, self.data.High, self.data.Low, self.data.Close, 20)
        self.rv = self.I(_realized_vol, self.data.Close, self.vol_lookback)

    def next(self):
        if len(self.data.Close) < 70:
            return
        px=float(self.data.Close[-1]); hh=float(self.hh[-1]); ll=float(self.ll[-1]); sma=float(self.sma50[-1]); atr=float(self.atr[-1])
        if not np.isfinite([px,hh,ll,sma,atr,float(self.rv[-1])]).all() or atr <= 0:
            return
        if not self.position and px > hh and px > sma:
            self._buy_with_stop(px, atr, 3.0)
        elif self.position and (px < ll or px < sma):
            self.position.close()
'''
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--family", required=True)
    ap.add_argument("--output", default="strategy.py")
    ap.add_argument("--bars-per-year", type=int, required=True)
    ap.add_argument("--vol-target", type=float, required=True)
    ap.add_argument("--f-max", type=float, required=True)
    args = ap.parse_args()

    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    row = next((x for x in registry["families"] if x["id"] == args.family), None)
    if row is None:
        raise SystemExit(f"unknown family: {args.family}")
    if row.get("status") != "runnable":
        raise SystemExit(
            f"family {args.family} is blocked; requirements={row.get('requires')}"
        )
    factory = row.get("factory")
    if factory not in BODIES:
        raise SystemExit(f"factory implementation missing: {factory}")

    source = (
        COMMON.replace("__BARS_PER_YEAR__", str(args.bars_per_year))
        .replace("__VOL_TARGET__", repr(float(args.vol_target)))
        .replace("__F_MAX__", repr(float(args.f_max)))
        .replace("__BODY__", BODIES[factory].strip("\n"))
    )
    out = Path(args.output)
    out.write_text(source.rstrip() + "\n", encoding="utf-8")
    print(
        f"generated {args.family} -> {out} "
        f"(bars_per_year={args.bars_per_year}, vol_target={args.vol_target}, f_max={args.f_max})"
    )


if __name__ == "__main__":
    main()
