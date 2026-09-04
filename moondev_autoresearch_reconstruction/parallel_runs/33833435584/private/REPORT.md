# PRIVATE AUTORESEARCH — 50 iterations

Run: https://github.com/tjhuber2009-eng/TestRepo/actions/runs/33833435584
Hard max drawdown: **32.0%**
- Data: checksum-verified ETHUSDT 6h, 2017-08-17 through 2022-12-31
- OOS 2023+ remained sealed

- iterations: **50**
- kept: **6**
- rejected: **41**
- crashes: **3**

## Seed

- K: 1.6871
- return: 585.15%
- Sharpe: 0.877
- max DD: -31.25%
- annualized vol: 49.08%

## Final champion

- K: **2.2091**
- return: **822.8%**
- Sharpe: **0.994**
- max DD: **-26.29%**
- annualized vol: **51.47%**
- trades: **83**
- PF: **2.625**

## Kept improvements

- iter 4: K 1.6871 -> 1.689 | return 585.16% | DD -31.25% | Add dynamic volatility-adjusted entry threshold using realized volatility relative to its moving average
- iter 8: K 1.689 -> 1.6972 | return 586.2% | DD -28.87% | Add a 50-period simple moving average trend filter to entry logic, requiring price above its MA.
- iter 11: K 1.6972 -> 1.8697 | return 650.45% | DD -30.76% | Increase stop loss multiplier from 3.0 to 4.0 to reduce premature exits and let winners run wider.
- iter 19: K 1.8697 -> 1.9765 | return 686.27% | DD -26.68% | Increase trend filter lookback from 50 to 60 to strengthen entry condition
- iter 34: K 1.9765 -> 2.1253 | return 745.26% | DD -20.67% | Add volatility-adjusted trend filter requiring price to exceed moving average by half the ATR
- iter 44: K 2.1253 -> 2.2091 | return 822.8% | DD -26.29% | Increase exit lookback from 10 to 12 to reduce premature exits and let winners run longer, seeking a balance between the too-fast (8) and too-slow (15) previously tried.

These are in-sample optimization results, not evidence of future profitability.
