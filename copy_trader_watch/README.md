# Copy Trader Watch

A free, read-only forward monitor for selected U.S. eToro investors.

It replaces a paid monitoring service or ChatGPT automation slot with GitHub Actions. The workflow runs once per day, commits the updated history/report, and opens a GitHub issue only when a **new** configured warning condition appears.

## Current research set

- `jonathandm25`
- `bgsully111`
- `WilEscobar`
- `Luisitoalana`

Edit `config.json` to add/remove candidates or change thresholds.

## What it tracks

- same-baseline forward return derived from eToro YTD return snapshots
- forward-test maximum drawdown from the stored daily path
- current eToro risk score and sudden risk-score jumps
- daily/observation loss alerts
- copier-count deterioration when that field is available
- top-one and top-two portfolio concentration
- win ratio and trade count
- SPY and QQQ performance from the same monitoring baseline
- a transparent return/drawdown/concentration/risk research score

## Free data sources

Candidate data uses two public layers from [`weirdapps/etoro_census`](https://github.com/weirdapps/etoro_census):

1. **Primary:** its public per-username analysis endpoint. This can resolve an investor even when the account is outside the census top-1,500 list.
2. **Fallback:** the large `census-data-latest.json` daily eToro API snapshot, downloaded only when a per-user lookup fails.

Benchmarks use Yahoo Finance's public chart endpoint first and the Stooq CSV quote endpoint as a fallback.

No broker password, eToro API key, paid signal subscription, or trading API key is required.

## What it does **not** do

It does not log into eToro, click CopyTrader, place orders, rebalance money, or claim that historical returns will persist. It is a research/alerting program.

## GitHub Actions

Workflow: `.github/workflows/copy-trader-watch.yml`

- Pull requests/pushes: run unit tests.
- Manual dispatch: run tests + the production watcher.
- Schedule: every day at 02:30 UTC (7:30 PM Pacific during daylight time).
- Production runs commit `data/` and `reports/` changes back to the repository.
- When a warning condition becomes newly active, the watcher opens a GitHub issue with the built-in `GITHUB_TOKEN`.
- Active alert keys suppress repeated issues. If a condition clears and later returns, it can alert again.

## Alerts

Default thresholds in `config.json`:

- daily/observation loss <= -5%
- forward-test drawdown <= -10%
- risk score >= 7
- risk-score jump >= 2
- top instrument >= 50% of tracked exposure
- top two instruments >= 75%
- top-instrument concentration jump >= 20 percentage points
- copier-count drop >= 50% when copier counts are available
- candidate cannot be resolved by either public source

These are screening thresholds, not trading rules.

## Local run

```bash
cd copy_trader_watch
python -m pip install -r requirements.txt
python -m pytest -q tests
python run.py
```

`run.py` is the production entrypoint. `watch.py` contains the source-agnostic scoring, persistence, reporting, and alert logic.

Outputs:

- `data/history.json` — one observation per source date; a same-date rerun replaces the old row
- `data/alerts.json` — currently active alert conditions
- `data/state.json` — active-alert state used to suppress duplicate issues
- `reports/latest.md` — current ranking, benchmark comparison, and warnings

## Ranking methodology

The report's `research_score` is intentionally simple and inspectable:

1. Compute forward return from the first stored YTD-return observation.
2. Divide by at least 5 percentage points of drawdown, preventing tiny early drawdown from creating absurd ratios.
3. Penalize eToro risk scores above 4.
4. Penalize top-instrument concentration above 35%.

The score is only a prioritization tool for further research; it is not a forecast or investment recommendation.

## Important limitations

- The per-user endpoint and census are third-party public mirrors of eToro API data. Schema or availability changes can require code changes.
- Forward drawdown begins when this monitor begins; it is **not** the investor's lifetime max drawdown.
- eToro YTD return is used to reconstruct same-calendar-year forward return. A year boundary needs a return-chain extension or a new baseline.
- The per-user endpoint exposes only the top positions. Top-one/top-two concentration remains useful, but `unique_instruments` is not a complete portfolio-count measure on that source.
- Copier count is not supplied by the per-user endpoint and may therefore be unavailable for investors outside the census snapshot.
- Public data cannot prove that an investor is currently copy-eligible for every U.S. account.
- Benchmark prices can reflect the latest completed market session rather than the exact eToro snapshot timestamp.
