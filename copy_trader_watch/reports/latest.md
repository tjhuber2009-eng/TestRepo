# Copy Trader Watch — Latest Report

Forward-test window: **2026-09-03 → 2026-09-03** (1 observations)

> Research monitor only. It does not place trades and its score is not an investment recommendation.

## Candidate ranking

| Rank | Candidate | Forward return | Max DD | Return/DD | Risk | Top-1 concentration | Copiers | Research score |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | jonathandm25 | 0.00% | 0.00% | 0.000 | 4 | 8.09% | None | 0.000 |
| 2 | Luisitoalana | 0.00% | 0.00% | 0.000 | 5 | 13.08% | None | -0.350 |
| 3 | bgsully111 | missing | n/a | n/a | n/a | n/a | n/a | n/a |
| 4 | WilEscobar | missing | n/a | n/a | n/a | n/a | n/a | n/a |

## Benchmarks

- **SPY:** 0.00% since the same baseline
- **QQQ:** 0.00% since the same baseline

## Active alerts

- **bgsully111 — missing:** Candidate could not be resolved by the configured public data sources; this does not by itself prove the eToro account is unavailable to copy.
- **WilEscobar — missing:** Candidate could not be resolved by the configured public data sources; this does not by itself prove the eToro account is unavailable to copy.

## Method notes

- Production candidate data uses the public `weirdapps/etoro_census` per-user endpoint first and its top-1,500 census as a fallback.
- Forward return is derived from the change in same-year YTD return from the first observation; the monitor resets naturally if you start a new history file in a new calendar year.
- Forward max drawdown is calculated from the stored daily path, not from eToro's full historical equity curve.
- Portfolio concentration aggregates multiple lots when full census data is used; the per-user endpoint supplies already aggregated top positions.
- SPY/QQQ quotes use Yahoo Finance's public chart data first and Stooq as a fallback.
- Missing or stale upstream data is surfaced rather than filled with guesses.
