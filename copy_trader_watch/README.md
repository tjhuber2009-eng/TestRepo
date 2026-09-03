# Copy Trader Watch

A free, read-only forward monitor for selected U.S. eToro investors.

It replaces a paid monitoring service or ChatGPT automation slot with GitHub Actions. The workflow runs once per day, commits the updated history/report, and opens a GitHub issue only when a **new** configured warning condition appears.

## Current research set

- `jonathandm25`
- `bgsully111`
- `WilEscobar`
- `Luisitoalana`

Edit `config.json` to add/remove candidates or change thresholds.

## What V2 improves

- chains forward returns correctly across January YTD resets instead of corrupting a multi-year test
- records the Pacific trading-day observation date, not the next UTC calendar date
- delays ranking scores until a candidate has at least 5 resolved observations
- requires 2 consecutive unresolved observations before issuing a missing-data alert
- records candidate source provenance/timestamps and alerts on stale source data
- records benchmark quote source and market-session date
- throttles the ~89 MB census fallback to once per 7 days for never-resolved profiles, while still using it immediately if a previously working profile fails
- uses a timezone-aware GitHub Actions schedule so 7:30 PM Pacific remains 7:30 PM through daylight-saving changes

## What it tracks

- chained forward return derived from eToro YTD snapshots
- forward-test maximum drawdown from the stored path
- current eToro risk score and sudden risk-score jumps
- observation loss alerts
- copier-count deterioration when that field is available
- top-one and top-two portfolio concentration
- win ratio and trade count
- source freshness and lookup coverage
- SPY and QQQ performance from the same monitoring baseline
- a transparent return/drawdown/concentration/risk research score after the warm-up gate

## Free data sources

Candidate data uses two public layers from [`weirdapps/etoro_census`](https://github.com/weirdapps/etoro_census):

1. **Primary:** its public per-username analysis endpoint. This can resolve an investor even when the account is outside the census top-1,500 list.
2. **Fallback:** the large `census-data-latest.json` daily eToro API snapshot. V2 throttles this expensive fallback for profiles that have never resolved, but still uses it immediately for a previously working profile that suddenly fails.

Benchmarks use Yahoo Finance's public chart endpoint first and the Stooq CSV quote endpoint as a fallback.

No broker password, eToro API key, paid signal subscription, or trading API key is required.

## What it does **not** do

It does not log into eToro, click CopyTrader, place orders, rebalance money, or claim that historical returns will persist. It is a research/alerting program.

## GitHub Actions

Workflow: `.github/workflows/copy-trader-watch.yml`

- Pull requests: run unit tests.
- Pushes to `main`: run tests + the production watcher.
- Manual dispatch: run tests + the production watcher.
- Schedule: every day at **7:30 PM America/Los_Angeles**, using GitHub Actions timezone-aware scheduling.
- Production runs commit `data/` and `reports/` changes back to the repository.
- When a warning condition becomes newly active, the watcher opens a GitHub issue with the built-in `GITHUB_TOKEN`.
- Active alert keys suppress repeated issues. If a condition clears and later returns, it can alert again.

## Alerts

Default thresholds in `config.json`:

- observation loss <= -5%
- forward-test drawdown <= -10%
- risk score >= 7
- risk-score jump >= 2
- top instrument >= 50% of tracked exposure
- top two instruments >= 75%
- top-instrument concentration jump >= 20 percentage points
- copier-count drop >= 50% when copier counts are available
- candidate source data older than 36 hours
- candidate cannot be resolved for 2 consecutive observations

These are screening thresholds, not trading rules.

## Scoring warm-up

The research score is deliberately withheld until a candidate has at least **5 resolved observations**. Before that, the report labels the candidate `warming up N/5` rather than producing a misleading ranking from one or two points.

After warm-up, the score:

1. uses chained forward return from the first stored observation;
2. divides by at least 5 percentage points of drawdown, preventing tiny early drawdown from creating absurd ratios;
3. penalizes eToro risk scores above 4;
4. penalizes top-instrument concentration above 35%.

The score is a prioritization tool for further research, not a forecast or investment recommendation.

## Year-boundary handling

Within a calendar year, period returns are reconstructed from successive YTD values. At a new calendar year, the first new-year YTD value is chained onto the existing forward curve rather than compared directly with the prior year's YTD value. This prevents the January reset from looking like a large loss.

Because this uses daily public observations, a long data gap that crosses December 31 can still omit movement between the last old-year observation and year-end. The data-quality report makes unresolved/stale observations visible rather than silently filling them.

## Local run

```bash
cd copy_trader_watch
python -m pip install -r requirements.txt
python -m pytest -q tests
python run.py
```

`run.py` is the production entrypoint. `watch.py` contains the source-agnostic scoring, persistence, reporting, and alert logic.

Outputs:

- `data/history.json` — one observation per Pacific date; a same-date rerun replaces the old row
- `data/alerts.json` — currently active alert conditions
- `data/state.json` — active-alert state plus census-fallback throttle state
- `reports/latest.md` — current ranking, benchmark comparison, source freshness, coverage, and warnings

## Important limitations

- The per-user endpoint and census are third-party public mirrors of eToro API data. Schema or availability changes can require code changes.
- Forward drawdown begins when this monitor begins; it is **not** the investor's lifetime max drawdown.
- The per-user endpoint exposes only the top positions. Top-one/top-two concentration remains useful, but `unique_instruments` is not a complete portfolio-count measure on that source.
- Copier count is not supplied consistently by the per-user endpoint and may therefore be unavailable for investors outside the census snapshot.
- Public data cannot prove that an investor is currently copy-eligible for every U.S. account.
- Benchmark prices can reflect the latest completed market session rather than the exact eToro snapshot timestamp; V2 records that session date explicitly.
