# CopyTrader / Free-EA Forward Research

## Current governing policy

As of 2026-09-04, executable candidates must be **free EAs**.

- Paid EA acquisition: prohibited for this project.
- Paid MQL5 signal subscriptions: prohibited.
- Free MQL5 Market EAs: allowed.
- Open-source MT5 EAs: preferred.
- Demo execution first.
- No real-money auto-approval.

See free-ea/FREE_ONLY_POLICY.json and free-ea/candidates.json.

## Active experiment

The active experiment is now the **Free-EA Forward Tournament** under copytrader-forward-tester/free-ea/.

Actual EA execution must occur in MetaTrader 5. GitHub cannot run MT5 desktop EAs on its Linux hosted runners. The repository therefore handles frozen candidate registry and policy, demo-only account reporting, demo-result analysis, CI validation, and historical benchmark preservation.

The previous DNH / Mon Scalper / Precise public-provider watcher is retained as historical research only. It is no longer the executable candidate set and its scheduled hourly run is disabled.

## Demo harness

Use copytrader-forward-tester/mt5-demo/CopyTraderDemoReporter.mq5 on a separate demo account for every EA/preset. It refuses non-demo accounts and never sends orders.

Analyze the resulting Common Files CSVs with:

    python copytrader-forward-tester/mt5-demo/demo_analyzer.py --common-files "C:\\path\\to\\Terminal\\Common\\Files"

## Historical provider benchmark

The old provider scraper and frozen baseline files remain present for auditability. Run them manually only:

    python copytrader-forward-tester/forward_test.py verify
    python copytrader-forward-tester/forward_test.py status

They do not authorize spending and are not the active free-EA tournament.
