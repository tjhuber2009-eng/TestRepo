# CopyTrader Forward Tester

Prospective, fail-closed forward testing for three frozen MQL5 candidates:

- Definitely No Hype EA MT5
- Mon Scalper MT5
- Precise Pair Trading Pro

## What this does

The tester reads only public MQL5 signal aggregates and counts performance only after the frozen baseline trade counts.

It tracks genuinely new post-baseline trades, cash-flow-adjusted forward equity return, realized forward P/L, forward gross profit/loss, forward profit factor, forward win rate, observed forward equity drawdown, evidence maturity, and anomalies such as regressed trade counts or cumulative totals.

The system deliberately fails closed. A stale or inconsistent page does not become forward performance.

## Frozen baselines

The files in config/ are locked by config/BASELINE_LOCK.json.

Baseline trade counts:

- Definitely No Hype EA MT5: 264
- Mon Scalper MT5: 504
- Precise Pair Trading Pro: 808

Do not edit these after forward testing starts.

## GitHub Actions

The workflow at .github/workflows/copytrader-forward-test.yml runs approximately once per hour.

Each run verifies the frozen configuration, runs the built-in self-test, fetches each public MQL5 signal, appends the observation to the SHA-256 chained SQLite ledger, rebuilds status.json/status.csv/status.html, uploads the state as a GitHub Actions artifact, and commits changed state/reports back to the repository.

GitHub scheduled jobs can be delayed during periods of high Actions load, so hourly means scheduled hourly rather than guaranteed to execute at an exact minute.

## Manual commands

From the repository root:

    python copytrader-forward-tester/forward_test.py verify
    python copytrader-forward-tester/forward_test.py self-test
    python copytrader-forward-tester/forward_test.py run
    python copytrader-forward-tester/forward_test.py status

Python 3.10+ is sufficient. No third-party Python packages are required.

## Generated files

- data/forward.sqlite3 — append-only chained observation ledger
- reports/status.json — machine-readable current results
- reports/status.csv — spreadsheet-friendly results
- reports/status.html — simple dashboard
- reports/latest_run.json — classifications from the latest poll

## Important limitation

MQL5 does not expose full realtime trade details publicly. This tester therefore measures prospective provider-account economics from public cumulative aggregates. It does not pretend to reconstruct exact individual fills or copier slippage.

No trades are placed. No broker credentials are used. No candidate is automatically approved for real-money trading.
