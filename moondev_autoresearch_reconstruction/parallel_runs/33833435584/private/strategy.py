"""
Reconstructed MoonStrategy PRIVATE-ACCOUNT seed with volatility-targeted sizing.

IMPORTANT: Moon Dev's exact original strategy.py was not visible in the supplied
screenshots. The screenshots do, however, explicitly reference vol_target and
f_max as sizing controls and say the frozen margin exists to allow
volatility-targeted units. This seed therefore preserves the simple breakout
logic while replacing the earlier placeholder 95%-of-liquidity sizing with
causal volatility targeting.
"""

import numpy as np
import pandas as pd
from backtesting import Strategy


BARS_PER_YEAR = 4 * 365  # 6-hour crypto bars


def _rolling_high(x, n):
    return pd.Series(x).rolling(n).max().shift(1).to_numpy()


def _rolling_low(x, n):
    return pd.Series(x).rolling(n).min().shift(1).to_numpy()


def _atr(high, low, close, n):
    h = pd.Series(high)
    l = pd.Series(low)
    c = pd.Series(close)
    pc = c.shift(1)
    tr = pd.concat(
        [(h - l), (h - pc).abs(), (l - pc).abs()],
        axis=1,
    ).max(axis=1)
    return tr.rolling(n).mean().shift(1).to_numpy()


def _realized_vol(close, n):
    c = pd.Series(close, dtype=float)
    log_ret = np.log(c / c.shift(1))
    # Shift one bar so sizing at a decision point never uses the decision bar's
    # own return. Annualize 6-hour crypto volatility.
    return (
        log_ret.rolling(n).std(ddof=0).shift(1)
        * np.sqrt(BARS_PER_YEAR)
    ).to_numpy()


def _rolling_mean(x, n):
    return pd.Series(x).rolling(n).mean().shift(1).to_numpy()


class MoonStrategy(Strategy):
    # Structural seed logic.
    entry_lookback = 20
    exit_lookback = 12  # Increased from 10 to 12 to reduce premature exits
    atr_lookback = 20
    stop_atr = 4.0  # Increased from 3.0 to 4.0

    # Reconstructed sizing controls suggested by the screenshots.
    vol_lookback = 30
    vol_target = 0.34
    f_max = 2.0

    # Volatility filter parameters.
    vol_filter_lookback = 30
    vol_filter_multiplier = 2.0

    # Trend filter.
    ma_lookback = 60  # Increased from 50 to 60

    def init(self):
        self.hh = self.I(
            _rolling_high, self.data.High, self.entry_lookback
        )
        self.ll = self.I(
            _rolling_low, self.data.Low, self.exit_lookback
        )
        self.atr = self.I(
            _atr,
            self.data.High,
            self.data.Low,
            self.data.Close,
            self.atr_lookback,
        )
        self.rv = self.I(
            _realized_vol, self.data.Close, self.vol_lookback
        )
        self.rv_ma = self.I(
            _rolling_mean, self.rv, self.vol_filter_lookback
        )
        self.ma = self.I(
            _rolling_mean, self.data.Close, self.ma_lookback
        )

    def _entry_units(self, px, realized_vol):
        if (
            not np.isfinite(px)
            or px <= 0
            or not np.isfinite(realized_vol)
            or realized_vol <= 0
        ):
            return 0

        exposure = min(self.f_max, self.vol_target / realized_vol)
        notional = float(self.equity) * exposure
        return max(0, int(notional / px))

    def next(self):
        warmup = max(
            self.entry_lookback,
            self.exit_lookback,
            self.atr_lookback,
            self.vol_lookback,
            self.vol_filter_lookback,
            self.ma_lookback,
        ) + 2
        if len(self.data.Close) < warmup:
            return

        px = float(self.data.Close[-1])
        hh = float(self.hh[-1])
        ll = float(self.ll[-1])
        atr = float(self.atr[-1])
        rv = float(self.rv[-1])
        rv_ma = float(self.rv_ma[-1])
        ma = float(self.ma[-1])

        if not np.isfinite([px, hh, ll, atr, rv, rv_ma, ma]).all() or atr <= 0 or rv_ma <= 0:
            return

        if not self.position:
            if px > hh and rv < self.vol_filter_multiplier * rv_ma and px > ma + 0.5 * atr:
                units = self._entry_units(px, rv)
                if units >= 1:
                    self.buy(
                        size=units,
                        sl=px - self.stop_atr * atr,
                    )
        elif px < ll:
            self.position.close()
