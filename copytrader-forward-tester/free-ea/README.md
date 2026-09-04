# Free-EA Forward Tournament

**Policy: every executable candidate must cost $0.**

Paid EAs and paid signal subscriptions are excluded from the executable experiment. The prior DNH / Mon Scalper / Precise work is retained only as historical benchmark research.

## Initial field

1. **EMA Gold Trader EA** — open source. Published repository test: +110.4%, PF 1.92, 650 trades, 9.57% max equity DD over Jan 2025–Mar 2026. This is a claim to reproduce, not accepted truth.
2. **MQL5 Trading Bot Claude Experiment** — open source. Ships multiple presets; published XAUUSD Donchian results are ~23.4% CAGR / PF 1.44 / 15.7% DD / 253 trades, and USDJPY Session ~19.2% CAGR / PF 1.38 / 9.0% DD / 345 trades.
3. **SafeScalperPro** — free MQL5 Market EA and free source-code/preset publication in MQL5 Code Base.
4. **Apex Origin** — free MQL5 Market EA. AUDCAD H1. Seller discloses linear scaling 0.01/0.02/0.03 and a hard cap on orders, so it is not treated as a clean single-entry system.
5. **Aurum Ra Gold EA** — free. Seller explicitly states no grid/martingale/averaging, but community reports include a stop-loss anomaly. Must be falsified prospectively.
6. **The Impossible Gold** — free. Defined SL/TP and no-grid/no-martingale claim, but a user reported ~33% live DD while an equivalent-period backtest looked profitable. High-value negative-control candidate.
7. **XAUUSD 5 minute** — free and widely used, but a community report showed 57% drawdown on defaults. Include, do not trust.

## Why MATrader AI is not in the first field

It is free, but its advertised realtime MQL5 signal ID 2375266 is currently deleted/not found, and its update history explicitly mentions an adaptive grid engine plus major logic revisions. It may be added later as a quarantined candidate, but it does not start in the clean free tournament.

## Experiment design

Use a separate MT5 **demo account per EA/preset**. Never run two tournament EAs on the same account.

Suggested demo labels:

- FREE-EMA-GOLD
- FREE-MULTI-XAU-DONCHIAN
- FREE-MULTI-USDJPY-SESSION
- FREE-SAFE-SCALPER
- FREE-APEX
- FREE-AURUM
- FREE-IMPOSSIBLE
- FREE-XAU5M

Attach ../mt5-demo/CopyTraderDemoReporter.mq5 on each account with the matching CandidateId. The reporter is demo-only and read-only.

## Ranking

No EA is removed because it has few trades. Rank prospectively by:

1. return on starting demo equity after cash-flow adjustment;
2. profit factor;
3. return / max observed equity DD;
4. trade count and elapsed duration as evidence strength;
5. broker robustness;
6. absence of martingale/grid/recovery behavior;
7. reproducibility from source or stable published build.

Small samples remain admitted with lower confidence.

## Starting conditions

- Use default/recommended settings from the candidate's **current frozen version**.
- Record EA version, broker/server, symbol mapping, spread/account type and exact set file.
- Do not optimize after the tournament starts.
- No deposits/withdrawals after baseline.
- No manual trades.
- No real-money accounts.

## Installation

For MQL5 Market freebies, install the actual **FREE** product from the linked Market page. A free product is the full $0 product; this is different from a demo of a paid Market EA.

For open-source candidates, copy the upstream .mq5 source into MT5's MQL5/Experts directory and compile in MetaEditor.

This repository intentionally does not vendor third-party EA binaries.
