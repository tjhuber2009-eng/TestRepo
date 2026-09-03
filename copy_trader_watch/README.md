# Copy Trader Watch

A free, read-only forward monitor for selected U.S. eToro Popular Investors.

It is designed to replace a paid monitoring service or a ChatGPT automation slot. GitHub Actions runs the watcher once per day, commits the updated history/report, and opens a GitHub issue only when a **new** configured warning condition appears.

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
- daily loss alerts
- copier-count deterioration
- portfolio concentration, with multiple lots aggregated by instrument
- win ratio and trade count
- SPY and QQQ performance from the same monitoring baseline
- a transparent return/drawdown/concentration/risk research score

## Free data sources

1. [`weirdapps/etoro_census`](https://github.com/weirdapps/etoro_census), which publishes a daily snapshot generated from eToro's public API.
2. [Stooq](https://stooq.com/) CSV quotes for SPY and QQQ.

No broker password, eToro API key, paid signal subscription, or trading API key is required.

## What it does **not** do

It does not log into eToro, click CopyTrader, place orders, rebalance money, or claim that historical returns will persist. It is a research/alerting program.

## GitHub Actions

Workflow: `.github/workflows/copy-trader-watch.yml`

- Pull requests/pushes: run unit tests.
- Manual dispatch: run tests + the watcher.
- Schedule: runs every day at 02:30 UTC (7:30 PM Pacific during daylight time).
- Scheduled/manual watcher runs commit `data/` and `reports/` changes back to the repository.
- When a warning condition becomes newly active, the watcher opens a GitHub issue using the built-in `GITHUB_TOKEN`.
- It tracks active alert keys, so a persistent condition does not create a new issue every day. If a condition clears and later returns, it can alert again.

Scheduled workflows only run from GitHub's default branch, so the PR containing this project must be merged before nightly monitoring starts.

## Alerts

Default thresholds in `config.json`:

- daily loss <= -5%
- forward-test drawdown <= -10%
- risk score >= 7
- single-day/observation risk-score jump >= 2
- top instrument >= 50% of exposure
- top two instruments >= 75%
- top-instrument concentration jump >= 20 percentage points
- copier-count drop >= 50% from the previous observation
- candidate disappears from the upstream census

These are screening thresholds, not trading rules.

## Local run

```bash
cd copy_trader_watch
python -m pip install -r requirements.txt
python -m pytest -q tests
python watch.py
```

Outputs:

- `data/history.json` — immutable-by-date daily observations (same date is replaced on a rerun)
- `data/alerts.json` — currently active alert conditions
- `data/state.json` — active-alert state used to suppress duplicate issues
- `reports/latest.md` — current ranking, benchmark comparison, and warnings

## Ranking methodology

The report's `research_score` is intentionally simple and inspectable:

1. Compute forward return from the first stored YTD-return observation.
2. Divide by at least 5 percentage points of drawdown (prevents tiny early drawdown from producing absurd ratios).
3. Penalize eToro risk scores above 4.
4. Penalize top-instrument concentration above 35%.

The score is only a prioritization tool for further research; it is not a forecast or investment recommendation.

## Important limitations

- The upstream census is a third-party public mirror of eToro API data. If its schema or URL changes, the watcher can fail until updated.
- Forward drawdown begins when this monitor begins; it is **not** the investor's lifetime max drawdown.
- eToro YTD return is used to reconstruct same-year forward return. If monitoring crosses January 1, start a fresh history file or extend the code with a year-boundary return chain.
- Public portfolio snapshots cannot prove that an investor is currently copy-eligible for every U.S. account. A missing candidate is treated as a warning, not proof of delisting.
- Stooq benchmark prices may reflect the latest completed market session rather than the exact eToro snapshot timestamp.
