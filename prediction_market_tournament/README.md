# Prediction Market Tournament — PMT-FROZEN-V1

A forward-only, paper-only tournament for structural prediction-market edges.

**Forward status:** NOT STARTED. The V1 clock begins only when
`data/forward_start_v1.json` is deliberately created on the persistent host.

## Objective

Find strategies with high **percentage return, profit factor, and capital
efficiency** without trusting screenshots, backfilled fills, hidden compounding,
or parameters chosen after results are known.

The frozen V1 comparison asks:

> If these exact rules are fixed before the first observation, which lane makes
> the most money over the same 30-day forward window using executable prices,
> actual displayed depth, minimum order sizes, and venue fees?

## Frozen paper account

- Initial paper capital: **$50**
- Maximum allocation per recorded trade: **10% = $5 all-in cash**
- Entry fees are part of the $5 cap, not added on top.
- Maximum concurrent positions: **5**
- The leaderboard replays each signal at the **exact cash size whose book depth
  was observed**. It does not secretly compound a $5 quote into a larger
  hypothetical fill after profits.
- Unresolved positions continue consuming cash and concurrency and are carried
  at share cost; no favorable mark-to-market is assumed.

## Active V1 lanes

### 1. Weather ensemble mispricing — taker

Current Polymarket daily-temperature rules are parsed directly from the market,
including the named Wunderground station-history URL.

Forecast probability is an equal-model blend of:

- ECMWF AIFS 0.25 ensemble
- ECMWF IFS 0.25 ensemble
- NCEP GEFS 0.25 ensemble

Each model first produces its own bracket probability. Those probabilities are
then averaged equally; models with more ensemble members do not receive extra
weight merely because they expose more perturbations.

Execution rules:

- Use the exact resolution-station coordinates.
- Aggregate the station-local calendar day with Open-Meteo
  `timezone=auto`.
- Current whole-degree market resolution is modeled with half-degree bin
  boundaries before bracket scoring.
- Retrieve YES and NO books together.
- Integrate the live CLOB fee curve at every consumed price level.
- Enforce the published minimum order size.
- Compare both executable sides and choose only the higher exact after-fee
  edge.
- Require at least **5 percentage points** of exact after-fee edge.
- At most one recorded signal per market.

### 2. BTC 5-minute Chainlink TWAP dislocation — taker

The market must be the exact timestamp-derived
`btc-updown-5m-{window_start_epoch_seconds}` event and explicitly resolve from
Chainlink BTC/USD 60-second TWAP.

Opening strike:

- Subscribe to Polymarket RTDS `crypto_prices_twap_sixty` for `btc/usd`.
- RTDS filters use the documented compact JSON **string**
  `{"symbol":"btc/usd"}`.
- The opening TWAP is accepted only if both its source timestamp **and the
  collector receive timestamp** are within 3 seconds after the 5-minute
  boundary.
- Missing or late strikes are permanently missed; they are never reconstructed
  from spot data.

120-second checkpoint:

- Evaluate once at **120 seconds remaining**, with at most 3 seconds of
  post-checkpoint lag.
- Require fresh causal `crypto_prices_chainlink` raw data.
- Require at least 30 raw observations from the prior 120 seconds.
- Require the latest raw observation to be no more than 3 seconds old.
- Model the final **60-second average**, not a future spot close.
- Retrieve UP and DOWN books in one batch snapshot.
- Quote the full all-in $5 cash budget through displayed depth and the live fee
  curve.
- Require at least **4 percentage points** of exact after-fee edge.

### 3. BTC late-resolution lane — taker

A separate checkpoint on the same exact BTC 5-minute market:

- Evaluate once at **30 seconds remaining**, with at most 3 seconds of lag.
- Use the same frozen opening TWAP.
- The already observed half of the final 60-second TWAP window is integrated as
  a causal time-weighted known segment.
- Only the unobserved segment retains forecast uncertainty.
- Require fair probability >= **92%**.
- Require at least **2.5 percentage points** of exact after-fee edge.
- Use the same simultaneous UP/DOWN executable-book and all-in-fee treatment.

## Shadow/control lanes

### Favorite/longshot calibration

Shadow-only until a point-in-time calibration database exists. No current
outcome may calibrate an earlier signal.

### Maker/rebate capture

Shadow-only. A paper touch is not a fill. Promotion requires order-book/trade
replay proving the limit order would have executed.

### Complete-set / mutually exclusive dislocation

Shadow-only. Requires exhaustive mutually exclusive events, all legs, negative
risk where applicable, simultaneous-fill modeling, and per-leg fees before it
can become a counted strategy.

### Trade-Halts

External paper control. Historical anomaly results were strong, but
post-freeze behavior was concerning; it is retained as a control rather than
discarded.

## Execution realism

Counted active-lane signals require:

- exact market identity;
- exact token/book identity;
- full displayed depth for the entire cash budget;
- published minimum order size;
- live market fee parameters;
- nonlinear fee integration at each consumed book level;
- a causal observation timestamp after the execution books/rules were
  retrieved;
- no midpoint or spot-price substitution;
- no rescued missed checkpoint.

BTC UP/DOWN books and weather YES/NO books are retrieved through the CLOB batch
order-book endpoint to reduce artificial side-to-side timing skew.

## Settlement

A market must be closed and have an unambiguous one-hot terminal outcome.

If Gamma exposes `umaResolutionStatus`, only explicit final states
(`resolved` or `settled`) are accepted. `requested`, `proposed`, and
`disputed` remain unresolved.

Capital release prefers the later Gamma `updatedAt` timestamp carrying the
final state rather than assuming trading `closedTime` equals oracle finality.

Recorded executed shares and entry fees are reused exactly at settlement.

## Forward freeze

Starting V1 is a deliberate one-shot action.

The marker binds:

- frozen spec SHA-256;
- implementation SHA-256;
- exact runtime fingerprint;
- Python implementation/version;
- OS family and machine architecture;
- installed `websockets` version.

The implementation hash covers runtime code, executable scripts, deployment
files, `pyproject.toml`, data-persistence ignore rules, and the PMT CI
workflow.

After start, a code/spec/runtime mismatch fails closed. Parameter changes
require a new tournament version and a new clock.

## Audit trail

Source observations are JSONL and remotely persisted.

Persistence rules:

- source JSONL files are **append-only**;
- an existing source line may not be edited or deleted;
- `forward_start_v1.json` may only be added once and then is immutable;
- only derived `leaderboard.json` may be replaced;
- Git persistence stages only `prediction_market_tournament/data`;
- runtime lock/temp files are excluded;
- failed pushes are retried even if no new signal arrives in the next hour.

## Equal-window ranking

Official lane comparison uses the exact frozen $50 account over the same
30-day forward decision window.

Primary metrics:

- net return on the $50 account;
- net P&L;
- profit factor;
- capital efficiency;
- peak committed cash;
- maximum drawdown;
- Brier score / calibration;
- resolved/unresolved trade counts;
- open positions;
- calendar age.

Partial windows are provisional. They are not annualized.

A small sample is never eliminated merely for being small.

## Persistent collection

GitHub Actions is **CI-only**. It is not used for live forward collection
because hourly/scheduled CI cannot credibly hit 3-second BTC checkpoints.

The persistent supervisor runs:

- continuous BTC RTDS collection;
- weather discovery/scan every 15 minutes;
- settlement every 5 minutes;
- complete-set shadow scan hourly;
- leaderboard refresh every 5 minutes;
- remote data-only Git persistence hourly.

The supervisor is protected by a singleton process lock and refuses to run
before the deliberate start marker exists.

## Pre-start host checks

`scripts/preflight_forward.py` must pass before V1 starts. It verifies:

- data directory is uncontaminated;
- Polymarket Gamma is reachable;
- CLOB server-time round-trip <= 2 seconds;
- absolute host/CLOB clock offset <= 1.5 seconds;
- actual fresh BTC raw Chainlink updates arrive;
- actual fresh BTC 60-second TWAP updates arrive;
- RTDS source-to-host lag <= 5 seconds;
- current spec, implementation, and runtime hashes can be computed.

A connection that receives no usable TWAP updates does **not** pass.

## Installation / tests

Requires Python 3.12.

```bash
python -m pip install pip==26.2.1
python -m pip install -e . pytest==9.1.1
pytest -q
```

Runtime dependency is pinned to `websockets==17.1`.

## Google e2-micro deployment path

A persistent Ubuntu host setup helper is included:

```bash
deploy/setup_google_e2_micro.sh
```

It installs the environment, runs the complete unit suite, performs the live
preflight, verifies GitHub write access for audit persistence, and installs the
systemd user service.

**It deliberately does not start V1.**

Once the host is fully ready, the two separate launch actions are:

```bash
.venv/bin/python scripts/start_forward.py
systemctl --user enable --now pmt-forward.service
```

Do not run the first command until the final repository review and live
preflight are complete. Once the marker exists, runtime-changing edits
intentionally invalidate PMT-FROZEN-V1.
