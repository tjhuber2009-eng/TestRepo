
import numpy as np
import pandas as pd
from backtesting import Strategy

BARS_PER_YEAR = 365


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


class MoonStrategy(Strategy):
    vol_lookback = 30
    vol_target = 0.12369204
    f_max = 2.0

    def _units(self, px, rv):
        if not np.isfinite(px) or px <= 0 or not np.isfinite(rv) or rv <= 0:
            return 0
        exposure = min(self.f_max, self.vol_target / rv)
        return max(0, int((float(self.equity) * exposure) / px))

    def _buy_with_stop(self, px, atr, stop_mult=3.0):
        units = self._units(px, float(self.rv[-1]))
        if units >= 1:
            self.buy(size=units, sl=px - stop_mult * atr)

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
