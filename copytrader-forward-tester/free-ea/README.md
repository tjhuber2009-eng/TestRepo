# Free-EA Forward Tournament

**Final research state: exhaustive public search complete to diminishing returns (2026-09-04).**

Every executable candidate must cost $0. Paid EAs and paid signal subscriptions are prohibited.

Read first:
- `SEARCH_AUDIT_2026-09-04.md` — what was searched and why the search stopped.
- `PROVENANCE_POLICY.json` — what is legally/operationally allowed into the executable tournament.
- `candidates.json` — final evidence-ranked universe.
- `FREE_EA_START_MANIFEST.json` — first and second forward-test waves.

## First wave

1. **Apex Origin v1.20** — official FREE Market EA; strongest exact public live evidence found. Manual Market install only.
2. **Daruma001 v1.18** — official FREE; strongest large-sample low-DD seller validation package. Manual Market install.
3. **ASQ Safe Scalping CodeBase v1.20** — official free source + XAUUSD M5 preset; automatic installer.
4. **FvgGold v2.00** — MIT-licensed GitHub source; automatic installer.
5. **Numbit v1.21** — official FREE BTCUSD EA; strong seller real-tick metrics but only days old.

Small samples stay admitted. They receive lower evidence confidence rather than automatic rejection.

## Important corrections from the exhaustive audit

- **Gold Prop Firm Robot is not a new clean winner**. Its account identity matches Gold Reaper and it is quarantined; independent risk review labels Gold Reaper martingale-confirmed.
- **ApexBreakout source is public but has no declared license**. It is research-only and is no longer auto-installed.
- **DonchianTurtle also has no declared license** and is research-only.
- **EMA Gold Trader remains quarantined** because of source-format/compile plausibility problems plus no declared license.
- Third-party mirrors of paid/commercial EAs are never executable simply because a download site offers them for $0.

## Automatic installation

From Windows:

    powershell -ExecutionPolicy Bypass -File .\copytrader-forward-tester\free-ea\install_free_eas.ps1 -ListTerminals

Then choose the correct MT5 data folder:

    powershell -ExecutionPolicy Bypass -File .\copytrader-forward-tester\free-ea\install_free_eas.ps1 -Mql5Path "C:\Users\YOU\AppData\Roaming\MetaQuotes\Terminal\<terminal-id>\MQL5"

The automatic installer now installs only:
- ASQ Safe Scalping CodeBase v1.20 from official MQL5 CodeBase;
- FvgGold at pinned MIT commit `a8a521c2c6e619a5f9fc7f80cad63242d1e236b5`;
- CopyTraderDemoReporter.

Official Market EAs must be installed manually from MT5 after confirming the product is still displayed as **FREE**. Freeze the exact installed version at baseline.

## Forward-test rules

- separate MT5 demo account per EA;
- no manual trades;
- no deposits/withdrawals after baseline;
- exact version and settings frozen;
- no optimization after start;
- rank by forward return, PF, return/max observed equity DD, then evidence strength and reproducibility;
- no real-money auto-approval.
