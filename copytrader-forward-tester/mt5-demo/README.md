# MT5 Demo Validation Harness

This folder is the execution-validation layer for the CopyTrader project.

## What it can and cannot do

The public MQL5 signal pages do not expose enough realtime trade detail to recreate entries. Therefore there are only two legitimate ways to forward-execute the candidate on an MT5 demo account:

1. **Native MQL5 Signal subscription** — MetaTrader copies the provider's trades.
2. **The actual purchased EA** — the EA generates its own trades directly on your MT5 demo account.

MQL5 Market demo EAs cannot run on online charts, even on demo accounts; they work only in Strategy Tester. So this repository does not pretend that a free Market demo can forward trade.

## Current direct-EA routes (checked 2026-09-03)

- Definitely No Hype EA MT5 — XAUUSD H1; MQL5 Market product 189326; current listed price $279.
- Mon Scalper MT5 — XAUUSD M15; product 128708; current listed price $399.
- Precise Pair Trading Pro — two-symbol pair-trading EA; product 148319; current listed price $470.

No purchase is required to use this harness. Do not spend anything merely to install the reporter.

## Recommended experiment layout

Use a **separate MT5 demo account for each candidate**. MQL5 allows only one signal provider per trading account, and isolation also prevents one EA from contaminating another's P/L.

Suggested labels:

- DNH-DEMO
- MON-DEMO
- PRECISE-DEMO

For native MQL5 copy subscriptions, the signal subscription itself may still have a fee. A demo account can be used to evaluate real signals according to MQL5 moderator guidance, but confirm availability and price inside your own terminal before paying.

## Install the reporter

1. In MT5, open **File → Open Data Folder**.
2. Open **MQL5 → Experts**.
3. Copy `CopyTraderDemoReporter.mq5` there.
4. Open MetaEditor and compile it.
5. Attach the reporter to a spare chart **different from the chart used by the candidate EA**.
6. Set `CandidateId` to `DNH`, `MON`, or `PRECISE`.
7. The reporter refuses to initialize unless the account is DEMO.

It does not import the trade library and contains no order-sending calls.

## Data location

The reporter writes to MT5's **Common Files** folder so another program can read it:

- `COPYTRADER_DEMO_<login>_<candidate>_account.csv`
- `COPYTRADER_DEMO_<login>_<candidate>_positions.csv`
- `COPYTRADER_DEMO_<login>_<candidate>_deals.csv`

In MT5, the Common Files location is under the terminal-wide common data directory.

## Analyze the demo execution

Run:

    python demo_analyzer.py --common-files "C:\path\to\Terminal\Common\Files"

The analyzer reports:

- demo equity return
- balance return
- maximum observed equity drawdown
- closed positions
- wins/losses
- gross profit/loss
- profit factor
- whether any deposit/credit/cash-flow event invalidated the clean comparison

## How this connects to GitHub forward testing

The GitHub forward tester measures the **provider account** prospectively.

This harness measures **your demo execution** prospectively.

The decision we ultimately care about is:

    provider forward result
    vs.
    your demo result
    vs.
    execution gap

A candidate is much more useful if your demo account reproduces the provider after broker/spread/slippage differences.

## Remaining blocker

The reporter is free and ready. Actual forward execution requires either:

- a native MQL5 signal subscription, or
- ownership of the full EA.

This repository does not purchase either one automatically.
