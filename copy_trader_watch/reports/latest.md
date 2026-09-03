# Copy Trader Watch — Latest Report

Forward-test window: **2026-09-03 → 2026-09-03** (1 observations)

> Research monitor only. It does not place trades and its score is not an investment recommendation.

## Candidate ranking

| Rank | Candidate | Obs | Forward return | Max DD | Return/DD | Risk | Top-1 concentration | Research score |
|---:|---|---:|---:|---:|---:|---:|---:|---:|
| 1 | jonathandm25 | 1 | 0.00% | 0.00% | 0.000 | 4 | 8.09% | warming up 1/5 |
| 2 | Luisitoalana | 1 | 0.00% | 0.00% | 0.000 | 5 | 13.08% | warming up 1/5 |
| 3 | bgsully111 | 0 | unresolved | n/a | n/a | n/a | n/a | n/a |
| 4 | WilEscobar | 0 | unresolved | n/a | n/a | n/a | n/a | n/a |

## Benchmarks

- **SPY:** 0.00% since baseline; close 773.76 (as of 2026-09-03, yahoo)
- **QQQ:** 0.00% since baseline; close 718.69 (as of 2026-09-03, yahoo)

## Data quality

| Candidate | Status | Source | Source timestamp | Source age | Missing streak |
|---|---|---|---|---:|---:|
| jonathandm25 | resolved | etoro-census-public-user-api | 2026-09-03T18:24:58.631Z | 0.0h | 0 |
| bgsully111 | per-user lookup failed: 404 Client Error: Not Found for url: https://etoro-census.vercel.app/api/public/bgsully111; census fallback did not contain candidate | n/a | n/a | n/a | 1 |
| WilEscobar | per-user lookup failed: 500 Server Error: Internal Server Error for url: https://etoro-census.vercel.app/api/public/WilEscobar; census fallback did not contain candidate | n/a | n/a | n/a | 1 |
| Luisitoalana | resolved | etoro-census-public-user-api | 2026-09-03T18:25:32.308Z | 0.0h | 0 |

## Active alerts

No configured alert conditions are active.

## Method notes

- Production candidate data uses the public `weirdapps/etoro_census` per-user endpoint first and its top-1,500 census as a throttled fallback.
- Forward returns are chained from successive YTD observations so a January YTD reset no longer corrupts a multi-year forward test.
- Research scores remain disabled until a candidate has at least 5 resolved observations.
- A missing-data alert requires consecutive unresolved observations, reducing false alarms from one transient API failure.
- Forward max drawdown is calculated from the stored forward path, not from eToro's full historical equity curve.
- SPY/QQQ quotes use Yahoo Finance public chart data first and Stooq as a fallback; the report records the quote session date.
- Missing or stale upstream data is surfaced rather than filled with guesses.
