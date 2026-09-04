# PROP AUTORESEARCH — 50 iterations

Run: https://github.com/tjhuber2009-eng/TestRepo/actions/runs/33833435584
Hard max drawdown: **10.0%**
- Data: checksum-verified ETHUSDT 6h, 2017-08-17 through 2022-12-31
- OOS 2023+ remained sealed

- iterations: **50**
- kept: **11**
- rejected: **37**
- crashes: **2**

## Seed

- K: 0.5643
- return: 68.73%
- Sharpe: 1.079
- max DD: -8.26%
- annualized vol: 9.47%

## Final champion

- K: **0.9437**
- return: **91.68%**
- Sharpe: **1.45**
- max DD: **-8.16%**
- annualized vol: **8.87%**
- trades: **110**
- PF: **2.351**

## Kept improvements

- iter 8: K 0.5643 -> 0.5695 | return 71.09% | DD -9.96% | Adjust exit condition to require price to be below lowest low minus 0.2*ATR to let winners run slightly more
- iter 10: K 0.5695 -> 0.6143 | return 70.23% | DD -8.42% | Decrease exit lookback from 10 to 5 bars to make exit more responsive
- iter 15: K 0.6143 -> 0.631 | return 70.94% | DD -8.7% | Decrease exit ATR multiple from 0.2 to 0.15 to exit earlier and reduce drawdown
- iter 18: K 0.631 -> 0.6451 | return 71.96% | DD -8.92% | Add requirement that breakout bar closes in the upper half of its range to entry condition
- iter 20: K 0.6451 -> 0.673 | return 73.44% | DD -9.37% | Increase stop loss multiplier from 3.0 to 3.5 to reduce premature stop outs and let winners run
- iter 22: K 0.673 -> 0.7221 | return 77.42% | DD -9.37% | Add requirement that exit bar closes in the lower half of its range to exit condition to avoid exiting on bullish reversals.
- iter 32: K 0.7221 -> 0.7961 | return 79.79% | DD -9.69% | Decrease exit lookback from 5 to 4 to make exit more responsive
- iter 34: K 0.7961 -> 0.823 | return 80.67% | DD -9.47% | Decrease exit ATR multiple from 0.15 to 0.10 to exit earlier and reduce drawdown
- iter 38: K 0.823 -> 0.8299 | return 81.65% | DD -8.91% | Add requirement that entry bar's volume is above its 20-bar average to filter low-confidence breakouts
- iter 43: K 0.8299 -> 0.8327 | return 81.56% | DD -9.12% | Decrease volume threshold for entry from 1.0 to 0.9 times the 20-bar average to allow more breakouts while maintaining volume filter
- iter 47: K 0.8327 -> 0.9437 | return 91.68% | DD -8.16% | Add volume confirmation (above 20-bar average) to exit signal to avoid low-volume breakdowns

These are in-sample optimization results, not evidence of future profitability.
