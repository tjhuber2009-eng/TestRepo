# Copy Trader Watch

A free, read-only copy-trader research and forward-testing system that runs in GitHub Actions.

V2 remains the conservative forward monitor for selected U.S. eToro investors. V3 is platform-agnostic and now follows a **forward-first** rule: small historical samples are never a rejection condition. Historical age/trade count are reported only as evidence confidence; actual forward observations determine ranking once available.

## Core rule: do not reject small samples

V3 separates three concepts that were previously mixed together:

1. **Seed Score** — historical return/risk/copyability triage used only before a forward result exists. Track-record age and trade count are deliberately excluded.
2. **Evidence Confidence** — source quality, age, trade count and metric completeness. This tells us how much context exists, but it never blocks admission and never discounts Forward Score.
3. **Forward Score** — calculated from observed forward return and forward drawdown. As soon as two usable observations exist, this controls the ranking. Observation count does not multiply or discount the score.

A two-trade signal and a 5,000-trade signal with the same observed forward path receive the same Forward Score. The 5,000-trade signal simply has higher Evidence Confidence.

## V3 outputs

`run_v3.py` writes:

- `data/v3_history.json` — daily normalized multi-platform snapshots using the Pacific observation date
- `data/v3_forward.json` — persistent per-candidate forward-test state, retained even when a candidate later disappears and returns
- `reports/v3_latest.md` — forward leaderboard, free forward-test universe, awaiting-forward list, practical shortlist and historical seed leaderboard

Same-date reruns replace that day's forward observation rather than inflating the sample count.

## Enabled sources

1. **eToro** — imports resolved U.S. candidates from V2 and chains successive public YTD observations with year-reset handling.
2. **Hyperliquid** — official public `info` API. Historical research remains U.S.-research-only. Forward testing uses changes in the official all-time P&L index divided by prior observed account value rather than changes in a rolling monthly return.
3. **MQL5 Signals** — scans up to 20 public MT5 Signals pages. Under the current free-only configuration, every free signal discovered in that scan is retained for forward monitoring; it is not dropped for being new or having few trades.
4. **Polymarket** — official Data API. Candidate admission follows current monthly P&L rank without requiring all-time persistence or a minimum number of closed positions. Forward research uses changes in official monthly P&L scaled by prior observed portfolio value and remains research-only for this U.S. workflow.
5. **Collective2** — adapter and parser exist, but GitHub Actions currently receives HTTP 403 from the configured public pages. The source is reported unavailable instead of using stale data. An authenticated official-API adapter is the intended future route.

ZuluTrade and exchange-leaderboard adapters remain future slots only until a stable unattended public/authenticated source is available.

## Practical versus forward-test eligibility

These are separate.

A candidate can enter the forward test even when it is not currently practical to copy. Practical constraints may include:

- paid subscription cost;
- geographic restrictions;
- real-vs-demo verification;
- broker/symbol compatibility;
- minimum/suggested capital;
- inability to establish a valid non-rolling forward metric.

**Historical sample size is not one of those constraints.**

## Source-specific notes

### eToro

V3 uses the existing V2 public-data observations. Forward return is chained from successive YTD values and handles calendar-year resets. Actual Copy availability still must be visible in the user's eToro account.

### Hyperliquid

For each configured public wallet V3 queries `portfolio`, `userFills`, and `clearinghouseState`. The displayed historical month return/drawdown is reconstructed from P&L change rather than raw account-value change. The forward tracker separately stores the latest all-time P&L index and account value, then measures subsequent P&L-index change against prior observed capital. `userFills` is upstream-capped, so fill statistics are recent-sample context only.

### MQL5

V3 supports both the current div-grid and historical table layouts. It records fee/free flag, growth, weeks, trades, win rate, activity, PF, drawdown, leverage and subscribers. The current configuration scans 20 pages and returns **free signals only**, with a retention cap above the number of scanned rows so a free signal is not discarded because its Seed Score or sample size is low. Real/demo status and broker compatibility remain execution checks.

### Polymarket

V3 uses the official leaderboard, closed-position, current-position and portfolio-value endpoints. Closed-position cost ROI remains a capital-efficiency proxy, not account equity return. Monthly rank—not sample depth or all-time persistence—controls discovery admission.

### Collective2

The adapter understands subscription fee, annual/cumulative return, maximum drawdown, strategy age, Sharpe, profitable percentage, leverage and suggested capital. Historical age no longer gates admission. The live GitHub Actions source is currently unavailable because Collective2 returns HTTP 403 to unattended requests.

## GitHub Actions

The workflow runs all tests on pull requests. Pushes to `main`, manual dispatches and the nightly **7:30 PM America/Los_Angeles** schedule run V2 and V3 and commit generated data/reports.

No broker password, exchange private key, paid signal subscription or trading API key is required.

## Local run

```bash
cd copy_trader_watch
python -m pip install -r requirements.txt
python -m pytest -q tests
python run.py
python run_v3.py
```

## Scope

The repository does not log into a broker, press Copy, place orders, rebalance capital or bypass geographic restrictions. It is a public-data research and forward-monitoring system. Historical profitability does not establish future profitability, and provider results do not guarantee copier results.
