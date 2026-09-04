# MP-002F Preregistration — DRAFT, NOT FROZEN

Status: **DRAFT — DATA AUDIT REQUIRED**

This document is deliberately incomplete. It may not be promoted to FROZEN until the QuantRocket audit proves the data semantics below.

## Source

- Provider: QuantRocket
- Bundle: `usstock-learn-1d`
- Frequency: daily
- Advertised historical range: 2007–2011
- Intended use: historical pseudo-OOS development only

## Hard data gates

Before freeze, establish:

- later-delisted stocks are actually present;
- point-in-time security identity and security type are usable;
- price adjustment queries do not use future corporate actions;
- the bundle exposes a defensible ordinary next-session total return;
- terminal delisting observations do not create an unmeasured optimistic label bias;
- the 157 retained Alpha158-minus-VWAP expressions can be computed without changing their definitions.

## Feature set

Provisional name: **Alpha158-minus-VWAP**

- Qlib Alpha158 definitions are authoritative.
- Retain 157 features.
- Remove `VWAP0` only.
- Do not synthesize VWAP from OHLC.
- Do not change any retained expression.

Exact Qlib version/commit and the 157-expression manifest must be pinned before freeze.

## Universe

Provisional methodology:

- point-in-time US common stocks;
- minimum 60 prior trading sessions;
- raw/as-traded price floor only if raw point-in-time price is defensibly available;
- trailing dollar-volume percentile chosen for liquidity reasons before any model result is viewed.

The exact percentile is not frozen yet.

## Label

Candidate for continuing securities:

`next_session_total_return = adjusted_close[t+1] / adjusted_close[t] - 1`

This is **not frozen**.

Terminal events must be explicitly handled based on bundle semantics. Do not default terminal labels to zero, -100%, or drop them.

## Chronology

Tentative:

- 2007: warm-up / feature history;
- approximately three years initial training;
- remainder, approximately 2010–2011: expanding pseudo-OOS evaluation.

Exact trading-session boundaries must be frozen before modeling.

## Models after freeze

1. null/cross-sectional baseline;
2. Ridge alpha=1;
3. one published/reference LightGBM configuration.

No tuning.

## Metrics after freeze

Primary:

- Spearman Rank IC
- Pearson IC
- ICIR
- pseudo-OOS R²
- RMSE
- MAE

Diagnostics:

- score-decile realized returns
- top-minus-bottom decile spread
- monotonicity
- turnover
- coverage
- results by calendar year

Gross diagnostics are not executable-profit claims.

## Protected data policy

No TRUE-OOS period is defined, reserved, or inspected in MP-002F.
