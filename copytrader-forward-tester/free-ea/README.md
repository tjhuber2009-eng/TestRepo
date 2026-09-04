# Free-EA Forward Tournament

**Governing policy: every executable candidate costs $0.**

Paid EAs and paid signal subscriptions are excluded. The previous DNH / Mon Scalper / Precise research remains only as a historical benchmark.

## Ready-to-run launch order

1. **ASQ Safe Scalping CodeBase v1.20** — public MQL5 CodeBase source, XAUUSD M5, ready preset included by the author.
2. **ApexBreakout USDJPY V3 Turbo** — pinned upstream base EA, USDJPY H1. The separate recovery/martingale EA is forbidden.
3. **ApexBreakout XAUUSD Donchian** — same pinned base EA, XAUUSD H1.
4. **SafeScalperPro Market v3.44** — free Market binary, manual MT5 Market install.
5. **Apex Origin v1.20** — free Market binary, AUDCAD H1.
6. **Aurum Ra Gold EA v5.10** — free Market binary, XAUUSD.
7. **The Impossible Gold v2.0** — free Market binary, XAUUSD M5.
8. **XAUUSD 5 minute v7.3** — free Market binary, XAUUSD M5.

**EMA Gold Trader is quarantined**. Its public repository has no declared license and its published .mq5 currently contains apparent Markdown formatting inside executable code, so it is not a plug-and-play launch candidate.

## Important SafeScalper distinction

The CodeBase source v1.20 and Market v3.44 are treated as separate candidates. The current Market branch has evolved from the public source branch and uses different current defaults.

## Install the automatic candidates

From a Windows checkout of this repository:

    powershell -ExecutionPolicy Bypass -File .\copytrader-forward-tester\free-ea\install_free_eas.ps1 -ListTerminals

Then:

    powershell -ExecutionPolicy Bypass -File .\copytrader-forward-tester\free-ea\install_free_eas.ps1 -Mql5Path "C:\Users\YOU\AppData\Roaming\MetaQuotes\Terminal\<terminal-id>\MQL5"

This fetches source directly from the original upstreams and does not vendor third-party code into this repository.

See:

- `FREE_EA_START_MANIFEST.json` — exact frozen launch configurations.
- `UPSTREAM_PINS.json` — exact upstream commit/source pins.
- `LAUNCH_CHECKLIST.md` — full MT5 procedure.
- `../mt5-demo/CopyTraderDemoReporter.mq5` — demo-only account reporter.

## Experiment rules

- one isolated MT5 demo account per EA/preset;
- no manual trades;
- no deposits or withdrawals after baseline;
- no parameter optimization after the forward test starts;
- no candidate removed solely for a small sample;
- rank by forward return, PF, return/max equity DD, then evidence strength and reproducibility;
- no automatic real-money approval.
