"""
Reconstructed seed strategy.

IMPORTANT: Moon Dev's original strategy.py was not visible in the supplied
screenshots. This is a deliberately simple breakout seed so the reconstructed
loop is runnable. It is NOT claimed to be Moon Dev's original starting strategy.
"""

import numpy as np
import pandas as pd
from backtesting import Strategy


def _rolling_high(x, n):
    return pd.Series(x).rolling(n).max().shift(1).to_numpy()


def _rolling_low(x, n):
    return pd.Series(x).rolling(n).min().shift(1).to_numpy()


def _atr(high, low, close, n):
    h = pd.Series(high)
    l = pd.Series(low)
    c = pd.Series(close)
    pc = c.shift(1)
    tr = pd.concat([(h - l), (h - pc).abs(), (l - pc).abs()], axis=1).max(axis=1)
    return tr.rolling(n).mean().shift(1).to_numpy()


class MoonStrategy(Strategy):
    # Stable seed knobs. The research agent should change logic, not leverage.
    entry_lookback = 20
    exit_lookback = 10
    atr_lookback = 20
    stop_atr = 3.0
    size_fraction = 0.95

    def init(self):
        self.hh = self.I(_rolling_high, self.data.High, self.entry_lookback)
        self.ll = self.I(_rolling_low, self.data.Low, self.exit_lookback)
        self.atr = self.I(_atr, self.data.High, self.data.Low, self.data.Close, self.atr_lookback)

    def next(self):
        # The breakout levels themselves are shifted, so they contain only
        # information from completed prior bars.
        if len(self.data.Close) < max(self.entry_lookback, self.exit_lookback, self.atr_lookback) + 2:
            return

        px = float(self.data.Close[-1])
        hh = float(self.hh[-1])
        ll = float(self.ll[-1])
        atr = float(self.atr[-1])
        if not np.isfinite([px, hh, ll, atr]).all() or atr <= 0:
            return

        if not self.position:
            if px > hh:
                self.buy(size=self.size_fraction, sl=px - self.stop_atr * atr)
        else:
            if px < ll:
                self.position.close()
