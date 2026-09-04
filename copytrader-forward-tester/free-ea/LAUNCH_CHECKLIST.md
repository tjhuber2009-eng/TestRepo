# Free EA Launch Checklist

## Automatic installs

Run in PowerShell from the repository checkout:

    powershell -ExecutionPolicy Bypass -File .\copytrader-forward-tester\free-ea\install_free_eas.ps1 -ListTerminals

Then install into the intended MT5 data folder:

    powershell -ExecutionPolicy Bypass -File .\copytrader-forward-tester\free-ea\install_free_eas.ps1 -Mql5Path "C:\Users\YOU\AppData\Roaming\MetaQuotes\Terminal\<id>\MQL5"

The installer fetches:

- SafeScalper CodeBase v1.20 source + XAUUSD M5 ready preset directly from MQL5 CodeBase.
- ApexBreakout base EA at exact upstream commit 3dab20e20a846edae9ac6fcf56d6b090dbba9f98.
- Apex XAUUSD Donchian, USDJPY V3 Turbo, and V2 Guarded presets.
- CopyTraderDemoReporter.

It intentionally does **not** install ApexBreakoutRecovery or either recovery preset.

## Manual free Market installs

Inside the same MT5 terminal, use Market search and verify the displayed price is **FREE** before installing:

| Candidate | Frozen version | Chart |
|---|---:|---|
| SafeScalperPro | 3.44 | XAUUSD M5 |
| Apex Origin | 1.20 | AUDCAD H1 |
| Aurum Ra Gold EA | 5.10 | XAUUSD; seller schedule |
| The Impossible Gold | 2.0 | XAUUSD M5 |
| XAUUSD 5 minute | 7.3 | XAUUSD M5 |

If the version or price differs, do not silently substitute it. Record the new version and create a new baseline.

## Separate demo accounts

Use one demo account per candidate/preset. At minimum start with:

1. SAFE_SCALPER_CODEBASE_V120
2. APEX_USDJPY_V3_TURBO
3. APEX_XAUUSD_DONCHIAN

This gives one transparent MQL5 CodeBase EA plus two independently configured instances of the strongest non-recovery open-source breakout EA.

## Baseline procedure

For each account:

1. Confirm ACCOUNT_TRADE_MODE is Demo.
2. Confirm there are zero pre-existing positions/orders.
3. Record balance/equity and server.
4. Attach the candidate EA to the specified chart.
5. Load the exact frozen preset when applicable.
6. Attach CopyTraderDemoReporter to a spare chart and set CandidateId exactly.
7. Enable Algo Trading.
8. Do not manually trade or move money after start.

## EMA Gold Trader

Do **not** install it from the original repository in this tournament. Its published source currently has compile-plausibility problems and the repository has no declared license. It remains a historical claim to reproduce later through an independently authored implementation, not a launch candidate.
