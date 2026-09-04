# NVIDIA AUTORESEARCH — 50-iteration campaign

Run: https://github.com/tjhuber2009-eng/TestRepo/actions/runs/33832706511

## Integrity

- Data: checksum-verified Binance Data Vision ETHUSDT 6h
- Research window: 2017-08-17 through 2022-12-31 only
- OOS 2023+ was not downloaded or opened
- Backtest commission: frozen at 0.20% per order
- Hard maximum drawdown constraint: 10.0%
- Candidate execution: next-bar (trade_on_close=False)

## Run status

- research process: success
- campaign audit: success
- iterations: 50
- kept: 13
- rejected: 35
- crashes: 2
- starting K: 0.5643

## Final champion

- K: **1.2812**
- return: **123.57%**
- signed Sharpe: **1.592**
- annualized vol: **10.13%**
- max DD: **-5.4%**
- trades: **70**
- win rate: **41.43%**
- profit factor: **3.399**

## Kept improvements

- iter 3: K 0.5643 -> 0.6053 | return 71.48% | Sharpe 1.122 | DD -8.81% | Increase stop loss multiple from 3.0 to 3.5 to allow winners to run
- iter 10: K 0.6053 -> 0.6139 | return 74.58% | Sharpe 1.102 | DD -9.53% | Adjust exit condition to require close to break below recent low by 0.25*ATR for earlier exit in volatile conditions
- iter 11: K 0.6139 -> 0.6529 | return 77.65% | Sharpe 1.136 | DD -9.28% | Add volume filter to entry: require current volume above its 20-period moving average.
- iter 13: K 0.6529 -> 0.7677 | return 88.57% | Sharpe 1.21 | DD -9.36% | Add volume confirmation to exit condition: require current volume above its 20-period moving average when breaking below recent low
- iter 16: K 0.7677 -> 0.8374 | return 96.89% | Sharpe 1.236 | DD -7.3% | Increase exit volume confirmation multiplier to require 1.5x average volume for exit signals
- iter 20: K 0.8374 -> 0.8443 | return 95.15% | Sharpe 1.263 | DD -7.99% | Increase volume moving average lookback period from 20 to 50 for both entry and exit volume filters
- iter 23: K 0.8443 -> 0.9612 | return 97.72% | Sharpe 1.41 | DD -6.35% | Change exit condition to use the lowest close of the last 10 bars instead of the lowest low
- iter 24: K 0.9612 -> 1.0529 | return 105.88% | Sharpe 1.458 | DD -6.61% | Change entry breakout condition from highest high to highest close of the last 20 bars
- iter 30: K 1.0529 -> 1.0782 | return 106.01% | Sharpe 1.492 | DD -6.95% | Increase entry volume threshold to 1.2 times the 50-period volume moving average for breakout entries.
- iter 35: K 1.0782 -> 1.1809 | return 112.93% | Sharpe 1.562 | DD -5.54% | Increase volume moving average lookback period from 50 to 100 for both entry and exit volume filters.
- iter 36: K 1.1809 -> 1.2152 | return 116.08% | Sharpe 1.577 | DD -5.96% | Increase exit volume confirmation multiplier from 1.5 to 1.6 to require stronger volume confirmation for exit signals.
- iter 40: K 1.2152 -> 1.254 | return 119.56% | Sharpe 1.594 | DD -5.4% | Decrease stop loss ATR multiplier from 3.5 to 3.0 to tighten risk control
- iter 49: K 1.254 -> 1.2812 | return 123.57% | Sharpe 1.592 | DD -5.4% | Increase exit ATR multiplier from 0.25 to 0.30 to require a stronger breakdown signal for exit

## Top 10 scored candidates

| iter | verdict | base_score | score | ret_pct | sharpe | ann_vol | trades | max_dd | desc |
|---|---|---|---|---|---|---|---|---|---|
| 49 | KEPT | 1.254 | 1.2812 | 123.57 | 1.592 | 10.13 | 70 | -5.4 | Increase exit ATR multiplier from 0.25 to 0.30 to require a stronger breakdown signal for exit |
| 40 | KEPT | 1.2152 | 1.254 | 119.56 | 1.594 | 9.88 | 71 | -5.4 | Decrease stop loss ATR multiplier from 3.5 to 3.0 to tighten risk control |
| 42 | REJECTED | 1.254 | 1.254 | 119.56 | 1.594 | 9.88 | 71 | -5.4 | Add bullish candle requirement to entry condition: require close > open for breakout entries |
| 36 | KEPT | 1.1809 | 1.2152 | 116.08 | 1.577 | 9.77 | 70 | -5.96 | Increase exit volume confirmation multiplier from 1.5 to 1.6 to require stronger volume confirmation for exit signals. |
| 47 | REJECTED | 1.254 | 1.2126 | 116.46 | 1.57 | 9.83 | 72 | -5.4 | Decrease exit ATR multiplier from 0.25 to 0.22 to exit earlier on breakdowns |
| 38 | REJECTED | 1.2152 | 1.181 | 113.51 | 1.557 | 9.73 | 71 | -5.96 | Reduce exit ATR multiplier from 0.25 to 0.20 to exit earlier on breakdowns |
| 35 | KEPT | 1.0782 | 1.1809 | 112.93 | 1.562 | 9.66 | 75 | -5.54 | Increase volume moving average lookback period from 50 to 100 for both entry and exit volume filters. |
| 41 | REJECTED | 1.254 | 1.1757 | 110.47 | 1.58 | 9.39 | 94 | -5.55 | Change exit volume confirmation to require current volume above its moving average and above previous bar's volume |
| 44 | REJECTED | 1.254 | 1.1178 | 112.46 | 1.483 | 10.14 | 77 | -7.51 | Decrease entry volume threshold to 1.1 and increase exit volume multiplier to 1.7 to allow more entries and require stronger volume for exits. |
| 37 | REJECTED | 1.2152 | 1.1009 | 107.84 | 1.505 | 9.68 | 69 | -6.6 | Increase entry volume threshold multiplier from 1.2 to 1.3 to require stronger volume confirmation for breakout entries. |

## Interpretation

This is in-sample research, not evidence of future profitability. Any champion must remain sealed from 2023+ until the research campaign is frozen and an explicit OOS validation is authorized.
