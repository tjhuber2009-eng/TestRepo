# NVIDIA AUTORESEARCH — 50-iteration campaign

Run: https://github.com/tjhuber2009-eng/TestRepo/actions/runs/33831878648

## Integrity

- Data: checksum-verified Binance Data Vision ETHUSDT 6h
- Research window: 2017-08-17 through 2022-12-31 only
- OOS 2023+ was not downloaded or opened
- Backtest commission: frozen at 0.20% per order
- Candidate execution: next-bar (trade_on_close=False)

## Run status

- research process: success
- campaign audit: failure
- iterations: 50
- kept: 8
- rejected: 36
- crashes: 6
- starting K: 1.7138

## Final champion

- K: **2.6069**
- return: **979.1%**
- signed Sharpe: **1.096**
- annualized vol: **50.76%**
- max DD: **-19.68%**
- trades: **96**
- win rate: **41.67%**
- profit factor: **2.447**

## Kept improvements

- iter 10: K 1.7138 -> 1.8478 | return 670.44% | Sharpe 0.905 | DD -33.88% | Increase stop-loss ATR multiplier from 3.0 to 3.5 to allow wider stops and capture larger trends.
- iter 13: K 1.8478 -> 1.8884 | return 686.77% | Sharpe 0.915 | DD -33.88% | Increase stop-loss ATR multiplier from 3.5 to 4.0 to allow even wider stops and capture larger trends.
- iter 20: K 1.8884 -> 2.1803 | return 790.09% | Sharpe 0.997 | DD -31.32% | Add SMA(30) filter to exit condition: exit if price breaks below Donchian low OR SMA(30)
- iter 35: K 2.1803 -> 2.2847 | return 840.15% | Sharpe 1.02 | DD -27.77% | Add long SMA(50) filter to entry condition: require price above both Donchian high and SMA(50) for long entries
- iter 37: K 2.2847 -> 2.3607 | return 875.12% | Sharpe 1.037 | DD -26.45% | Require Donchian high to exceed SMA(50) for long entry to filter breakouts below long-term average
- iter 41: K 2.3607 -> 2.4039 | return 896.8% | Sharpe 1.045 | DD -24.79% | Add condition to long entry requiring price to exceed SMA(50) by at least 0.5 * ATR
- iter 46: K 2.4039 -> 2.429 | return 913.36% | Sharpe 1.049 | DD -24.79% | Remove the requirement that the entry lookback high must exceed the SMA(50) for long entries
- iter 50: K 2.429 -> 2.6069 | return 979.1% | Sharpe 1.096 | DD -19.68% | Increase SMA long lookback for entry filter from 50 to 60 to require stronger long-term trend alignment

## Top 10 scored candidates

| iter | verdict | base_score | score | ret_pct | sharpe | ann_vol | trades | max_dd | desc |
|---|---|---|---|---|---|---|---|---|---|
| 50 | KEPT | 2.429 | 2.6069 | 979.1 | 1.096 | 50.76 | 96 | -19.68 | Increase SMA long lookback for entry filter from 50 to 60 to require stronger long-term trend alignment |
| 46 | KEPT | 2.4039 | 2.429 | 913.36 | 1.049 | 51.31 | 103 | -24.79 | Remove the requirement that the entry lookback high must exceed the SMA(50) for long entries |
| 41 | KEPT | 2.3607 | 2.4039 | 896.8 | 1.045 | 51.03 | 102 | -24.79 | Add condition to long entry requiring price to exceed SMA(50) by at least 0.5 * ATR |
| 37 | KEPT | 2.2847 | 2.3607 | 875.12 | 1.037 | 50.86 | 104 | -26.45 | Require Donchian high to exceed SMA(50) for long entry to filter breakouts below long-term average |
| 48 | REJECTED | 2.429 | 2.3261 | 860.85 | 1.028 | 50.88 | 106 | -26.45 | Reduce ATR multiplier for SMA(50) entry filter from 0.5 to 0.3 to allow more long entries |
| 4 | REJECTED | 1.7138 | 2.3213 | 1224.55 | 0.898 | 68.64 | 78 | -41.85 | Change exit lookback from 10 to 20 bars to use symmetric Donchian channels for entry and exit. |
| 35 | KEPT | 2.1803 | 2.2847 | 840.15 | 1.02 | 50.7 | 108 | -27.77 | Add long SMA(50) filter to entry condition: require price above both Donchian high and SMA(50) for long entries |
| 22 | REJECTED | 2.1803 | 2.279 | 712.34 | 1.088 | 43.77 | 128 | -31.46 | Decrease SMA lookback for exit filter from 30 to 20 to increase responsiveness |
| 39 | REJECTED | 2.3607 | 2.2621 | 801.9 | 1.029 | 49.12 | 99 | -23.31 | Replace SMA(50) of Close in entry filter with SMA(50) of High to require that the recent high exceeds the long-term average high. |
| 36 | REJECTED | 2.2847 | 2.2203 | 680.67 | 1.08 | 43.07 | 80 | -17.54 | Add rising SMA(50) condition to long entry requirement: require current SMA(50) > prior SMA(50) |

## Interpretation

This is in-sample research, not evidence of future profitability. Any champion must remain sealed from 2023+ until the research campaign is frozen and an explicit OOS validation is authorized.
