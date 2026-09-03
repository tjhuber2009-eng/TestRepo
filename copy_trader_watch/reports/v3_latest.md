# Copy Trader Watch V3 — Cross-Platform Report

> Read-only research. No broker login or trade execution. `actionable` means the public-data rules passed, not that profitability is expected.

## Source health

| Platform | Status | Records | Message |
|---|---|---:|---|
| etoro | ok | 2 |  |
| hyperliquid | ok | 5 |  |
| mql5 | ok | 0 |  |

## Free U.S.-actionable candidates

| Rank | Platform | Trader | Return | Window | DD | PF | Trades | Win | Copyability | Score |
|---:|---|---|---:|---|---:|---:|---:|---:|---:|---:|
| 1 | etoro | Jonathan Domínguez Serrata (`jonathandm25`) | 17.44% | ytd | n/a | n/a | 135 | 77.78% | 85.00 | 7.62 |
| 2 | etoro | Luisitoalana (`luisitoalana`) | 4.84% | ytd | n/a | n/a | 238 | 71.85% | 75.00 | 7.31 |

## Cross-platform research leaderboard

| Rank | Platform | Trader | Free | U.S. access | Evidence | Return | Window | DD | PF | Trades | Leverage | Copyability | Score |
|---:|---|---|---|---|---|---:|---|---:|---:|---:|---:|---:|---:|
| 1 | hyperliquid | HL-28839 (`0x28839a…5ca5`) | yes | no | onchain | 139.13% | month | -8.04% | 3.69 | 2000 | 3.00x | 60.00 | 89.92 |
| 2 | hyperliquid | HL-dc528 (`0xdc5289…19f5`) | yes | no | onchain | 183.28% | month | -9.35% | 6.69 | 2000 | 9.99x | 55.00 | 88.73 |
| 3 | hyperliquid | HL-838d (`0x838d8e…520c`) | yes | no | onchain | 102.68% | month | -6.17% | 2.42 | 2000 | 9.86x | 40.00 | 73.32 |
| 4 | hyperliquid | HL-80fb (`0x80fb58…faea`) | yes | no | onchain | 52.50% | month | -5.08% | 8.16 | 2000 | 2.42x | 90.00 | 73.12 |
| 5 | hyperliquid | HL-NMTD (`0xf51763…c032`) | yes | no | onchain | 134.38% | month | -14.15% | 999.00 | 2000 | 6.05x | 40.00 | 66.53 |
| 6 | etoro | Jonathan Domínguez Serrata (`jonathandm25`) | yes | yes | public-profile | 17.44% | ytd | n/a | n/a | 135 | n/a | 85.00 | 7.62 |
| 7 | etoro | Luisitoalana (`luisitoalana`) | yes | yes | public-profile | 4.84% | ytd | n/a | n/a | 238 | n/a | 75.00 | 7.31 |

## Non-actionable reasons

- **hyperliquid / HL-28839:** Research-only for this U.S. workflow; direct Hyperliquid access is not treated as U.S.-actionable.
- **hyperliquid / HL-dc528:** Research-only for this U.S. workflow; direct Hyperliquid access is not treated as U.S.-actionable.
- **hyperliquid / HL-838d:** Research-only for this U.S. workflow; direct Hyperliquid access is not treated as U.S.-actionable.
- **hyperliquid / HL-80fb:** Research-only for this U.S. workflow; direct Hyperliquid access is not treated as U.S.-actionable.
- **hyperliquid / HL-NMTD:** Research-only for this U.S. workflow; direct Hyperliquid access is not treated as U.S.-actionable.

## Method

- eToro uses the existing V2 U.S. candidate monitor and keeps its public-source limitations.
- Hyperliquid uses the official public `info` API. Its return path uses PnL change on the period's initial account-value base to reduce deposit/withdrawal distortion; it is research-only in this U.S. workflow.
- MQL5 scans the public MT5 Signals table. Paid signals may appear in the research leaderboard, but only explicitly free signals can pass the free-cost gate; real-vs-demo status must be verified before actionability.
- The cross-platform score rewards return/drawdown, age, trade sample, PF, source quality, and copyability, while penalizing unknown drawdown, leverage, concentration, high drawdown, and demo-only evidence.
- Missing data is penalized rather than silently imputed.
