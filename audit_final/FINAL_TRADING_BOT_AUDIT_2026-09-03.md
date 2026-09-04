# Free / Open Trading Bot Profitability Audit — Final Research State

**Date:** 2026-09-03  
**Branch:** `dual-signal-audit-2026-09`  
**Status:** `RESEARCH_COMPLETE_FORWARD_VALIDATION_REQUIRED`

## Objective

Search aggressively across GitHub, Reddit, TradingView/Pine, Freqtrade, open bot frameworks, event strategies, crypto-perps, leveraged ETFs, and other public sources for the highest-return free/open or fully reconstructable trading systems. Reproduce or falsify the strongest claims, prioritize percentage return/CAGR and capital efficiency, and reject results dependent on lookahead, repainting, impossible fills, optimistic execution, synthetic/accounting artifacts, hidden rules, parameter mining, capacity errors, or hindsight tuning.

## Final conclusion

No candidate clears the full **deployable champion** gate today.

The strongest independently reproduced, economically coherent historical ledger remains **Trade-Halts** at approximately **717.14% CAGR** from a $900 starting account over the committed 2021–2026 ledger. The closest proxy to what the live LUDP/Nasdaq implementation can observe remains approximately **693.61% CAGR**.

However, Trade-Halts is still **HISTORICAL_REPRODUCED / FORWARD_REQUIRED**, not live-validated. The raw ~5 GB minute-bar source is absent, the public 550-trade data is explicitly a testing/README subset rather than the uncommitted full/OOS datasets, and the implementation was changed against the same historical period.

NostalgiaForInfinity X7 (NFI) produces much larger latest-code retrospective numbers in its official monthly CI, but those numbers are **not eligible as a continuous or forward CAGR**. Current X7 did not exist during most of the tested history; the strategy has been repeatedly modified after observing those months; and official CI resets the wallet and force-closes at each monthly boundary. A direct continuous rerun on the isolated audit branch was attempted but GitHub runners were blocked by Binance with HTTP 451; NFI's own CI succeeds through a repository-private proxy secret that cannot legitimately be reused.

The new QuantStrategyLab SOXL V7 candidate has unusually strong research discipline. Its parameters were frozen 2026-08-26 and a fixed 252-session forward window began 2026-08-26. All earlier evidence is explicitly retrospective. As of this audit date, there are only a handful of genuinely untouched sessions, so it is a promising forward watchlist candidate, not a proven strategy.

## Final evidence-weighted ranking

| Rank | Candidate | Best relevant return | Status | Key reason |
|---|---|---:|---|---|
| 1 | Trade-Halts | **717.14% CAGR**; Nasdaq-live proxy **693.61% CAGR** | HISTORICAL_REPRODUCED / FORWARD_REQUIRED | Best coherent independently recomputed high-CAGR ledger; raw minute bars + honest OOS missing |
| 2 | SOXL V7 / SOXL-SOXX trend family | 2024+ backtest ~172% CAGR; recent ~1y research window ~514% CAGR | FROZEN_FORWARD_WATCH | V7 fixed 2026-08-26; 252-session forward evidence only just began |
| 3 | LLM-Auto SOL H4 | **132.12% historical CAGR** | FORWARD_FAILED | 2026 forward collapsed roughly -76% YTD |
| 4 | LeveragedETFMomentum | **126.42% historical CAGR** | FORWARD_WEAK | Recent forward reconstruction negative |
| 5 | LongShortHarvest | ~45.6% CAGR | HISTORICAL_SURVIVOR | Lower return but comparatively better evidence quality |
| — | NFI X7 latest-code monthly CI | 2025 reset-month chain ~+28,759% | DISQUALIFIED_AS_CAGR / FREEZE_AND_FORWARD_ONLY | Hindsight-tuned code + monthly reset; not a continuous untouched result |
| — | SystemTrading | Claimed ~2,600% CAGR | REJECTED | Own walk-forward OOS ~-25.8% CAGR / -74.7% DD |
| — | TwinStar-Quantum | Claimed 628,234.9% CAGR | REJECTED | Leveraged CAGR vs unleveraged MDD + overlapping full-equity compounding |
| — | Monthly Seasonality | Claimed +326M% total | REJECTED | 2012–2024 outcome table applied backward to 2011 |
| — | SeaSide420 BTC Pine | Claimed absurd multi-million % | REJECTED | Intrabar broker-emulator artifact; Bar Magnifier destroys result |
| — | RSI/SMA Pine | ~211% historical CAGR | REJECTED_DEPLOYABLE | Post-publication ~23% CAGR vs BTC ~40% |
| — | Dual Signal Trend Sentinel | ~56.6% reproduced CAGR | REJECTED_DEPLOYABLE | Poor DD and public-forward performance |

## Trade-Halts audit summary

### Reproduced
- $900 initial → ~$35.69M
- 550 trades
- ~70% win rate
- median trade ~+30.34%
- CAGR ~717.14%
- EOD/closed-trade max DD ~-12.96%

### Live-observable proxy
Restricting to Nasdaq LULD events:
- 468 trades
- ~70.94% win rate
- CAGR ~693.61%
- EOD DD ~-17.32%

### Stress survival
- Excluding all adjusted entry prices >$1,000 still ~702% CAGR overall (~680% Nasdaq proxy).
- Capping winners at +50% still leaves roughly ~367% CAGR.
- Capping winners at +100% leaves roughly ~538% CAGR.
- Severe added slippage assumptions still leave several-hundred-percent CAGR in ledger replay.
- The result is not explained by one or two jackpot outliers.

### Unresolved blockers
1. Raw minute bars used to construct the 550 trades are not committed.
2. Public code refers to the README ledger as a **TESTING subset** and separately references uncommitted full/OOS datasets.
3. Strategy/live logic was changed after the historical period was already visible.
4. First-resumption-print fillability and hidden intraday portfolio drawdown are not independently verified.
5. Event fields show selected trades can suffer very large adverse excursions before recovering.
6. Live 1-share operation can test fillability but cannot validate the advertised compounding/capacity.

**Promotion gate:** obtain or reconstruct the full historical event/minute dataset, run a frozen no-retuning replay, then accumulate a genuinely untouched forward sample with actual or broker-paper fills.

## NFI X7 audit summary

Latest official monthly Binance-futures CI uses:
- 3x leverage
- max 6 open trades
- unlimited stake
- 5m data
- limit-order backtesting
- starting wallet reset to 10,000 each month

Latest-code 2025 monthly returns are all positive and mechanically chaining them gives roughly +28,759%. Jan–Jul 2026 mechanically chains to roughly +296%. These are **discovery statistics only**, not a valid continuous CAGR.

Additional cashflow reconciliation on 1,032 official closed trades found aggregate backtest P&L within ~0.05% of independent raw-order cashflow arithmetic, so a simple DCA cash-accounting bug is not the source of the giant backtest profits.

But X7 changed repeatedly after the tested months. It therefore fails the untouched-history requirement.

A continuous 2025/2026 backtest was attempted in this audit:
- workflow: `.github/workflows/nfi-continuous-audit.yml`
- audit run: 33819720943
- outcome: infrastructure-blocked
- cause: Binance `exchangeInfo` returns HTTP 451 from GitHub runner location
- NFI upstream CI relies on a repository-private proxy secret; no attempt was made to copy/bypass it.

**Promotion gate:** freeze one public X7 commit now and run broker/exchange paper-forward without strategy edits. Historical latest-code retests may not be promoted.

## SOXL V7 forward candidate

QuantStrategyLab's V7 policy is frozen:
- freeze: 2026-08-26
- first forward XNYS session: 2026-08-26
- required untouched forward sessions: 252
- costs: 5/10/15 bps
- automatic promotion: forbidden
- separate human decision required after completion

The candidate explicitly states all pre-freeze history is retrospective and not promotion-eligible. This is the correct methodology. As of 2026-09-03 the forward window is far too short for performance conclusions.

## Large Pine / Bitcoin claims below the current CAGR hurdle

Large total-return figures can look bigger than Trade-Halts while being much lower annualized because they span ~14 years of Bitcoin history. Approximate implied CAGRs:
- TrendFusion free: ~145%
- MomentumMagic current free headline: ~152%
- Bitcoin Halving claim (+126.9M% total over ~14y): ~173%
- Pi Cycles: ~88%
- audited Monthly Seasonality: ~205% before correcting its hindsight construction

These remain below the 717% annualized hurdle and therefore do not displace Trade-Halts.

## Exhaustion criterion

The search was expanded repeatedly across:
- GitHub code/result repositories
- Reddit r/algotrading / r/quant and related discussions
- TradingView/Pine/open indicator systems
- Freqtrade / NFI
- OctoBot and other open bot frameworks
- leveraged ETF systems
- crypto futures/perps
- microcap/event/halt systems
- options/futures claims
- strategy research repositories with committed CAGR/DD metrics

The final broad passes produced no new **fully reconstructable, multi-period, economically coherent >717% CAGR** candidate. New high numbers were either:
- short-window annualization,
- promotional/testimonial claims without exact rules,
- hidden/invite-only source,
- explicitly in-sample,
- below the hurdle when annualized,
- or known backtest/accounting artifacts.

This satisfies the diminishing-returns stop rule for the current research universe.

## Final decision

**Project status:** `RESEARCH_COMPLETE_FORWARD_VALIDATION_REQUIRED`

There is no evidence-supported basis to put real money behind any >100% CAGR claim uncovered in this project today.

For the next evidence phase:
1. **Trade-Halts** — highest priority frozen forward/paper-fill test.
2. **SOXL V7** — preserve its already-frozen 252-session forward test without modification.
3. **NFI X7** — optional frozen-current-code paper-only forward experiment; do not use retroactive CI results as expectation.

Do not reopen broad discovery unless a new candidate provides one of:
- independently auditable >717% multi-year CAGR,
- real forward/live performance that beats the current survivors,
- or previously missing raw/OOS data that materially changes a top candidate's classification.
