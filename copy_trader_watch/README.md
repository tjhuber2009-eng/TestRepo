# Copy Trader Watch

A free, read-only copy-trader research system that runs in GitHub Actions.

V2 remains the conservative forward monitor for selected U.S. eToro investors. V3 adds a platform-agnostic research layer so eToro, MQL5 Signals, and public Hyperliquid wallets can be normalized and compared without giving any adapter permission to trade.

## V3 architecture

`run_v3.py` collects adapter outputs into a common `TraderSnapshot` schema and writes:

- `data/v3_history.json` — daily normalized multi-platform snapshots
- `reports/v3_latest.md` — free/U.S.-actionable shortlist plus a cross-platform research leaderboard

Adapters currently enabled:

1. **eToro** — imports the resolved U.S. candidates from the existing V2 monitor.
2. **Hyperliquid** — uses the official public `https://api.hyperliquid.xyz/info` endpoint for configured public wallets. It is treated as research-only in this U.S. workflow.
3. **MQL5 Signals** — scans the public MetaTrader 5 Signals table, normalizes growth, drawdown, PF, trade count, win rate, leverage, age, subscription fee and copying-activity proxies.

Future adapter slots are reserved in `platform_config.json` for ZuluTrade, Collective2, Polymarket and additional exchange leaderboards.

## Cross-platform normalization

Each adapter produces the same fields where public evidence allows: platform/ID, source timestamp, free/paid status, U.S. access, evidence class, return/window, drawdown, PF, trade count, win rate, age, leverage, activity, profit concentration, source quality, copyability, actionability and explicit reason. Missing fields remain missing; V3 does not substitute guesses.

## Free and U.S.-actionable gate

The practical shortlist requires `free == true`, U.S. access `yes` or `conditional`, and adapter-specific evidence sufficient to mark the record actionable. A high-return trader can rank highly for research while still being excluded from the practical shortlist.

- Hyperliquid wallets are research records and are not marked U.S.-actionable.
- Paid MQL5 Signals can appear in research but fail the free-cost gate.
- A free MQL5 signal is still not actionable until real-vs-demo status and broker compatibility can be verified.
- Resolved U.S. eToro candidates remain directly actionable research candidates, subject to eToro showing the Copy button in the user's account.

## Hyperliquid methodology

For each configured wallet V3 queries public `portfolio`, `userFills`, and `clearinghouseState`. Return is calculated from PnL change on the period's initial positive account-value base instead of raw account-value change, reducing later deposit/withdrawal distortion. It also reconstructs drawdown, recent PF/win rate, fill frequency, largest winning-fill concentration and current gross-notional/account-value leverage. `userFills` is upstream-capped, so fill statistics are recent-sample metrics rather than lifetime claims.

## MQL5 methodology

V3 reads the public MT5 table view and maps named columns rather than depending on fixed HTML column positions. It records subscription fee/free flag, growth, weeks, trades, win %, activity, PF, drawdown, leverage and subscribers. The list page does not reliably prove whether a free signal is a real or demo account, so V3 refuses to call it actionable on that evidence alone.

## Research score

The score rewards return/drawdown, longer history, larger trade samples, PF above 1, source quality and copyability. It penalizes unknown drawdown, excessive leverage, large drawdown, excessive profit concentration and demo-only evidence. It is a triage tool, not a forecast.

## V2 eToro monitor

V2 continues unchanged: multi-year chained forward returns, Pacific observation dates, a 5-observation scoring warm-up, consecutive-failure missing alerts, source freshness, SPY/QQQ benchmark dates, throttled census fallback and GitHub issue alerts.

V2 outputs `data/history.json`, `data/alerts.json`, `data/state.json`, and `reports/latest.md`.

## Configuration

- `config.json` — V2 eToro candidates and alert thresholds
- `platform_config.json` — V3 platform adapters, public wallets, discovery depth and report size

## GitHub Actions

The workflow runs all tests on pull requests. Pushes to `main`, manual dispatches and the nightly **7:30 PM America/Los_Angeles** schedule run V2 and V3 and commit generated data/reports. No broker password, exchange private key, paid signal subscription or trading API key is required.

## Local run

```bash
cd copy_trader_watch
python -m pip install -r requirements.txt
python -m pytest -q tests
python run.py
python run_v3.py
```

## Safety / scope

This repository does not log into a broker, press Copy, place orders, rebalance capital, or bypass geographic restrictions. It is a public-data research and forward-monitoring system. Historical profitability does not establish future profitability, and provider results do not guarantee copier results.
