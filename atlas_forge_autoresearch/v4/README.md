# AUTORESEARCH v4 — Alpha Generation Architecture

V4 is an **architecture fork** from the frozen v3 research protocol. It is
designed to improve the project's ability to discover genuine trading edge
without changing or contaminating v3 evidence.

## Safety boundary

- V3 branch/state remains separate and auditable.
- V4 development/search data ends at **2020-12-31**.
- Hidden pre-OOS validation remains **2021-01-01 through 2022-12-31** and is
  sealed during adaptive search.
- Final OOS begins **2023-01-01** and remains sealed.
- Signals are causal: information through close[t] executes no earlier than
  open[t+1] in the daily multi-asset engine.

## What changed

### 1. CAGR-first risk-constrained objective

`alpha_objective.py` uses hard risk/evidence gates and then maximizes
**sustainable CAGR**. DSR, PSR, PBO, drawdown, cost stress, turnover and
exposure are evidence/constraints rather than substitutes for return. It also
supports a Pareto frontier instead of forcing one scalar leaderboard.

### 2. True multi-asset engine

`multi_asset_engine.py` allows one asset to generate a signal while another
asset is traded, supports cash/defensive allocations, transaction costs,
turnover, borrow cost, gross/net/per-asset limits, and next-open execution.

This removes the need for single-symbol proxies such as “QQQ signal approximated
inside TQQQ”.

### 3. Rich causal feature store

`feature_store.py` builds returns, realized volatility, ATR, gaps, range,
moving-average distance, high/low distance, RSI, volume, dollar volume,
liquidity and cross-sectional ranks. External contexts are joined with
**backward-asof + explicit lag**.

`context_adapters.py` adds public/free adapters for FRED rates/yield curve,
Yahoo context series such as VIX, market breadth, point-in-time earnings events,
and frozen crypto funding/basis/open-interest CSVs.

### 4. Controlled parameter optimization

`parameter_optimizer.py` is the only place where parameter-only optimization
is allowed. The strategy structure is frozen by fingerprint. Selection rewards
broad parameter plateaus and worst-fold behavior while penalizing fold
dispersion and the number of configurations tried.

### 5. Regime-conditioned strategies

`regime_engine.py` creates causal trend, volatility and liquidity regimes.
Volatility/liquidity quantiles use **past-only expanding distributions**, so
future observations cannot change historical labels.

### 6. Meta-filtering

`meta_filter.py` implements a small deterministic boosted-decision-stump
classifier. It is intended to filter existing trade signals using walk-forward
probabilities, not replace the strategy with a black box.

### 7. Mutation motif transfer

`motif_library.py` turns discoveries into reusable knowledge. Initial motifs:

- long-term trend gate
- ATR trailing exit
- volume expansion
- volatility contraction
- relative strength
- breadth confirmation
- time stop
- drawdown recovery gate
- volatility regime gate

Successful motifs are automatically prioritized for transfer to related
family/market/profile cells.

### 8. Adaptive research allocation

`research_allocator.py` adds contextual Thompson sampling across
family/market/profile/motif/timeframe research cells. Every cell receives a
mandatory breadth floor first. After that, compute shifts toward cells with a
higher posterior probability of producing a robust keeper and larger positive
utility.

This is separate from the v3 model-level Thompson allocator.

### 9. Portfolio optimization

`portfolio_optimizer.py` optimizes **strategy portfolios**, not just individual
strategies. It maximizes moving-block-bootstrap median CAGR subject to the
portfolio drawdown cap and uses diversification/Sharpe only as small
tie-breakers.

### 10. Separate intraday protocol

`intraday_protocol.py` creates an isolated intraday research protocol with
timezone-aware bars, session handling, spread/slippage/funding costs, correct
annualization and its own sealed-boundary checks.

### 11. Event-driven/orthogonal strategy families

`strategy_examples.py` includes:

- signal-asset → leveraged traded-asset rotation
- cross-sectional momentum rotation
- PEAD-style timestamped earnings-surprise continuation
- regime-conditioned mean reversion

PEAD requires point-in-time earnings events; the implementation will not infer
an announcement timestamp from fiscal period end.

### 12. External strategy harvesting

`external_harvester.py` discovers public candidates from GitHub, Reddit and
Crossref/academic search without treating claims as evidence.

`strategy_intake.py` converts extracted rules into a deduplicated hypothesis
queue. Every external claim remains **unverified** until exact causal
reconstruction and the normal AUTORESEARCH robustness pipeline.

## Real-data development bootstrap

`live_bootstrap.py` can consume development-only QQQ/TQQQ/SPY/BTC/ETH CSVs and
run:

1. a true QQQ-signal → TQQQ/SPY rotation,
2. structure-frozen stable parameter selection,
3. cross-asset momentum,
4. strategy-return portfolio optimization.

It writes a development-only report and explicitly records that hidden
validation/final OOS were not opened.

## Testing

`test_v4_alpha_generation.py` covers:

- sealed boundaries,
- next-open causal execution,
- lagged context joins,
- regime prefix invariance,
- walk-forward meta filtering,
- structure-frozen parameter optimization,
- portfolio drawdown constraints,
- motif transfer,
- Thompson research allocation,
- intraday isolation,
- external-hypothesis deduplication,
- PEAD event timing,
- CAGR/risk objective,
- full synthetic end-to-end integration.

Synthetic integration data exists **only for CI correctness** and must never be
reported as trading evidence.
