# Final Free-EA Launch Checklist

## 1. Automatic source candidates

Run:

    powershell -ExecutionPolicy Bypass -File .\copytrader-forward-tester\free-ea\install_free_eas.ps1 -ListTerminals

Then:

    powershell -ExecutionPolicy Bypass -File .\copytrader-forward-tester\free-ea\install_free_eas.ps1 -Mql5Path "C:\Users\YOU\AppData\Roaming\MetaQuotes\Terminal\<id>\MQL5"

This installs:
- ASQ Safe Scalping CodeBase v1.20 + official XAUUSD M5 preset.
- FvgGold v2.00 at pinned MIT commit.
- CopyTraderDemoReporter.

It no longer installs public GitHub projects with no declared license.

## 2. Manual official FREE Market candidates

Inside MT5 Market, search the exact product name. Install only if the product currently displays **FREE**.

First wave:
- Apex Origin v1.20
- Daruma001 v1.18
- Numbit v1.21

Second wave:
- SafeScalperPro Market current free version
- Nang Kwak Gold Trader v1.0
- EA34 Tanin Force v1.20
- Universal Breakout MT5 v2.6
- Aurum Ra Gold EA
- Kabut001 v1.23
- Londoncalling001 v1.20
- The Impossible Gold v2.0
- XAUUSD 5 minute

If the displayed version differs from this file, do not pretend it is the frozen version. Record the installed version as a new baseline.

## 3. Separate demo accounts

Use one account per candidate. Do not combine EAs.

Minimum first-wave accounts:
- FREE-APEX-ORIGIN
- FREE-DARUMA
- FREE-SAFE-CODEBASE
- FREE-FVG-GOLD
- FREE-NUMBIT

## 4. Baseline

For every account:
1. Confirm Demo account.
2. Zero pre-existing positions/orders.
3. Record balance/equity, broker server, symbol names, leverage and account type.
4. Attach only the intended EA.
5. Load exact frozen/default settings.
6. Attach CopyTraderDemoReporter on a spare chart with the matching candidate ID.
7. Enable Algo Trading.
8. Never manually trade, deposit or withdraw after baseline.

## 5. Ranking

Use:

    powershell -ExecutionPolicy Bypass -File .\copytrader-forward-tester\free-ea\rank_tournament.ps1 -CommonFiles "C:\Users\YOU\AppData\Roaming\MetaQuotes\Terminal\Common\Files"

Young candidates remain ranked. Trade count and elapsed time alter confidence, not eligibility.

## 6. Do not execute

- Gold Prop Firm Robot / Gold Reaper relabel
- third-party mirrors of paid EAs
- ApexBreakout no-license source
- DonchianTurtle no-license source
- EMA Gold Trader original malformed/no-license source
- unavailable products
