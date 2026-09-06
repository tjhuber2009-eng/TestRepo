# Continuous AUTORESEARCH — nested chronological robustness program

This lane uses a deliberately adversarial research protocol.

During NVIDIA search you are evaluated **only on adaptive development data**.
A separate hidden pre-OOS validation segment exists, but it is not opened until
all adaptive search budgets and champions are frozen. Final 2023+ OOS is absent
from the research dataset entirely.

## What you may change

- Change `strategy.py` only.
- Make exactly **one conceptual strategy change** per attempt.
- Preserve class `AtlasStrategy`.
- Preserve causal execution. Backtesting.py uses `trade_on_close=False`;
  orders placed from the newest completed bar fill on the following bar.
- Do not read files, environment variables, network resources, shell commands,
  or any data outside the supplied OHLCV arrays.
- Do not run parameter sweeps, optimizer loops, random searches, or embedded
  model fitting inside the strategy.

## Anti-overfitting laws

- Do not infer hidden validation dates or regimes from keep/reject outcomes.
  Hidden validation is not part of adaptive search.
- Do not use date-specific rules, crash dates, halving dates, election dates,
  ticker-specific historical exceptions, or other memorization.
- Do not use future data, centered windows, negative shifts, or same-bar
  information that was unavailable when the decision was made.
- Do not add large stacks of thresholds, nested special cases, or dozens of
  numeric constants. The host rejects excessively complex source/ASTs.
- Do not repeat a previously generated strategy under different comments or
  formatting. The host fingerprints semantic ASTs and rejects duplicates.
- Do not increase `vol_target`, `f_max`, leverage, cash fraction, or nominal
  position size merely to raise return.
- The active profile DD cap is hard: prop <=10%, private <=32%.
- Every candidate is also re-run at a higher transaction-cost assumption.
- Weak full-period performance cannot be hidden by one good year: the host
  scores continuous equity-path chronological folds without resetting strategy
  state at fold boundaries.
- A deterministic block-bootstrap Sharpe diagnostic is included in the
  robustness gate.
- 2023+ OOS is sealed and absent. Do not reference or attempt to infer it.

## Research behavior

- Start from the named strategy family supplied by the host.
- Preserve the family's economic/behavioral hypothesis unless the one
  conceptual change explicitly tests an adjacent hypothesis.
- Prefer simple changes with a causal explanation: entry logic, exit logic,
  regime filters, volatility filters, or risk-shaping rules.
- Read the recent experiment ledger and avoid repeating dead ideas.
- If a family is labeled proxy/reconstructed, do not claim exact source parity.
- A weak seed may be used as a baseline so the agent can try to rescue a
  researched family, but a **candidate** is keepable only after passing the
  full development robustness and cost-stress gate.

## Search-budget policy

The controller uses successive halving without consulting hidden validation:

1. every runnable family/market/profile track gets a breadth budget;
2. a frozen top fraction inside each market/profile gets a larger depth budget;
3. a frozen elite fraction gets the largest development budget;
4. only after all adaptive search is finished are frozen champions evaluated
   once on hidden pre-OOS validation.

Do not optimize for selection-stage mechanics. Improve the current strategy on
its stated hypothesis.

## Selection

The host's `score` is a conservative **development robustness score**, not a
single-period CAGR or Sharpe. It blends typical fold performance, weaker folds,
cost-stressed performance, instability penalties, and bootstrap diagnostics.

A high development score is not evidence of future profitability. Hidden
pre-OOS validation and final 2023+ OOS remain separate gates.
