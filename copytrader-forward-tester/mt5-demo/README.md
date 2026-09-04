# MT5 Demo Validation Harness — Free-EA Tournament

This folder measures **your own MT5 demo execution** of free EAs.

## Governing rule

Every executable tournament EA costs $0. Paid EAs and paid signal subscriptions are not used.

The active candidate registry is ../free-ea/candidates.json.

## Recommended layout

Use a separate MT5 demo account for each EA or preset. This prevents one strategy from contaminating another's P/L.

Suggested labels:

- FREE-EMA-GOLD
- FREE-MULTI-XAU-DONCHIAN
- FREE-MULTI-USDJPY-SESSION
- FREE-SAFE-SCALPER
- FREE-APEX
- FREE-AURUM
- FREE-IMPOSSIBLE
- FREE-XAU5M

## Install the reporter

1. In MT5 choose **File → Open Data Folder**.
2. Open **MQL5 → Experts**.
3. Copy CopyTraderDemoReporter.mq5 there and compile it in MetaEditor.
4. Install/compile the free EA on the same demo account.
5. Attach the candidate EA to its required chart.
6. Attach the reporter to a spare chart.
7. Set CandidateId to the registry ID.
8. Leave the account untouched after baseline: no manual trades, deposits or withdrawals.

The reporter refuses non-demo accounts and has no order-sending code.

## Outputs

It writes into MT5 Common Files:

- COPYTRADER_DEMO_<login>_<candidate>_account.csv
- COPYTRADER_DEMO_<login>_<candidate>_positions.csv
- COPYTRADER_DEMO_<login>_<candidate>_deals.csv

Then run:

    python demo_analyzer.py --common-files "C:\\path\\to\\Terminal\\Common\\Files"

The analyzer reports return, PF, wins/losses, max observed equity DD, positions, and cash-flow contamination.

## Important

A **free MQL5 Market product** can be installed as a $0 product and run normally. This is different from the restricted demo build of a paid Market product.

GitHub itself does not host or execute MT5 desktop terminals. MT5 must remain running on your PC or a VPS for genuine forward testing.
