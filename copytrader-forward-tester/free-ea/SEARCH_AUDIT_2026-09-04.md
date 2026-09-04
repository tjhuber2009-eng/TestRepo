# Exhaustive Free-EA Search Audit — 2026-09-04

## Terminal classification

**EXHAUSTIVE_PUBLIC_SEARCH_COMPLETE_TO_DIMINISHING_RETURNS**

This means complete for the publicly discoverable free MT5 universe searched on 2026-09-03/04, not permanently complete. New products can appear later.

## Sources exhausted

### MQL5 Market — Free MT5 Expert Advisors
- Traversed the entire current FREE Expert Advisor pagination, not just Popular/New.
- Re-ran the catalog after pagination changed during the session.
- Audited high-return, high-rating, clean-risk, newest and linked-live-signal candidates individually.
- Logged explicit grid/martingale/recovery systems as negative controls rather than allowing headline backtests to rank them.

### MQL5 CodeBase — MT5 Expert Advisors
- Traversed all 41 current Expert Advisor pages.
- Later pages showed sharply diminishing quality: mostly templates, indicator examples, old systems, grids/recovery variants, or no performance evidence.
- Modern clean/source candidates were individually checked.

### GitHub
Broad searches covered MT5/MQL5 Expert Advisor, XAUUSD, scalping, breakout, trend, mean reversion, prop-firm, backtest, profit-factor, drawdown and walk-forward terms.
Detailed source/provenance audits included:
- foeed/FvgGold-EA — MIT
- ymodulus21/donchianturtle-ea — no declared license
- sbrakni/MQL5-trading-bot-claude-experiment — no declared license; separate recovery/martingale edition
- koichi055/ema-gold-trader-ea — no declared license; source formatting/compile plausibility problem
- zhutoutoutousan/profitable-expert-advisor — MIT repository, 2,863-trade XAUUSD report
- BAKOME-Hub/BakomeTrinityEA
- n30dyn4m1c/gold-pro-scalper
- Sandyyy123/xauusd-scalper-research — use prohibited by author
- francomascareloai/EA_SCALPER_XAUUSD — poor reported performance / restrictive terms
- e49nana/Algorithmic-trading — SafeScalper proprietary mirror warning
- additional search hits and forks were reviewed for performance/provenance.

### Reddit
Searched both generic and exact-name terms:
- free MT5/MT4 EAs
- profitable EAs
- XAUUSD bots
- named finalists
- long-running EA experiences
- forward/live vs backtest reports

Reddit produced strong falsification/methodology evidence but almost no independent corroboration for the named current finalists. That absence is recorded as lower confidence, not an exclusion.

### Other sources
- ForexFactory free-EA/source threads
- EarnForex open-source EA catalog and backtests
- ForexCracked large free-EA review/download corpus
- Myfxbook public systems
- FXBlue searches
- MQL5 public signals linked by free products

## Critical falsifications

### Gold Prop Firm Robot
Rejected. It is a Gold Reaper relabel/repack:
- forum identity match,
- exact initial deposit and drawdown figures match the Gold Reaper signal,
- independent AlgoCheck classifies Gold Reaper EA/signal as martingale confirmed / unlimited loss design.
Status: QUARANTINED.

### Third-party commercial mirrors
A download being free is not sufficient. If the original is paid/proprietary and the mirror has no clear redistribution right, it is research-only and never auto-installed.

### ApexBreakout / DonchianTurtle
Public GitHub source is not the same as licensed open source. No declared license => research-only under the final provenance policy.

### EMA Gold Trader
Attractive published backtest, but no license and apparent literal Markdown fences in the .mq5 source => quarantined.

## Final evidence tiers

### Tier A — exact live evidence + legitimate free executable
1. Apex Origin v1.20 — exact MQL5 live signal: +85.45%, 212 trades, PF 3.13, 100% algo, 19.62% equity DD. Risk caveat: linear position scaling and a user grid allegation.
No other exact official-free EA found with comparable current public live evidence.

### Tier B — large seller-validation package, exact official free executable
2. Daruma001 v1.18 — PF 1.77, 4,118 trades, 4.57% seller-reported equity DD; no grid/martingale/averaging; forward track record still young.
3. Numbit v1.21 — PF 2.42, 482 trades, +13.96% fixed-lot test return, 3.15% seller-reported margin DD; only days old.
4. EA34 Tanin Force v1.20 — six-year stress-test claim plus user prop-challenge backtest; independent forward evidence missing.

### Tier C — transparent/reproducible source
5. ASQ Safe Scalping CodeBase v1.20 — official MQL5 CodeBase source + preset.
6. FvgGold v2.00 — MIT source, one concurrent trade, fixed-RR, daily loss cap; developer backtest only.
7. EMA Slope Distance Cocktail XAUUSD — MIT repository; 28% yearly, PF 1.222, 2,863 trades, 14% DD reported; optimization and source-header review needed.

### Tier D — clean official-free systems without sufficient performance proof
Nang Kwak Gold Trader, Universal Breakout MT5, Kabut001, Londoncalling001, Aurum Ra, SafeScalper Market, and others remain admitted for demo comparison but below the evidence leaders.

## Diminishing-returns decision

The final expansion rounds increasingly returned:
- duplicate/repacked systems,
- explicit grid/martingale/recovery,
- seller-only backtests with no forward record,
- generic indicator EAs with no performance statistics,
- source without a license,
- or systems dominated by already-admitted candidates.

The highest-value next work is therefore actual isolated MT5 demo forward testing, not more indiscriminate catalog search.
