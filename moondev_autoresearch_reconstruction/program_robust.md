# Continuous AUTORESEARCH — chronological robustness program

This research lane is intentionally harder to optimize than a single-period
backtest. The host evaluates every candidate across multiple chronological
pre-2023 folds and a full pre-OOS span. You receive only a conservative
composite score and a generic robustness pass/fail result.

## What you may change

- Change `strategy.py` only.
- Make exactly one conceptual strategy change per attempt.
- Preserve class `MoonStrategy`.
- Preserve causal execution. Backtesting.py uses `trade_on_close=False`;
  orders placed from the newest completed bar fill on the following bar.
- Do not read files, environment variables, network resources, shell commands,
  or any data outside the supplied OHLCV arrays.
- Do not run parameter sweeps or optimization loops inside the strategy.

## Anti-overfitting rules

- Do not optimize to one calendar year or infer hidden fold boundaries from
  keep/reject outcomes.
- Prefer structural ideas that should survive different regimes.
- A pure parameter twitch is low value unless it represents a clear hypothesis.
- Do not introduce dozens of thresholds, interacting gates, or special cases.
- Do not add date-specific rules, hard-coded crash dates, halving dates,
  election dates, ticker-specific historical exceptions, or any equivalent
  memorization.
- Do not use future data, centered windows, negative shifts, or same-bar
  information that would not have existed when the decision was made.
- Do not increase `vol_target`, `f_max`, position size, leverage, or cash
  fraction merely to improve return. Risk is a host-level constraint, not an
  optimization shortcut.
- The active profile's drawdown limit is hard on the full search span and on
  every active chronological fold.
- The host also requires minimum trade counts across the full span and folds.
- 2023+ OOS is sealed. Do not reference it or attempt to infer it.

## Research behavior

- Start from the named strategy family supplied by the host.
- Preserve the family's economic or behavioral hypothesis unless your one
  conceptual change deliberately tests an adjacent hypothesis.
- Read the recent experiment ledger and do not repeat dead ideas under renamed
  variables.
- Prefer entries, exits, filters, regime logic, or risk-shaping rules that have
  a plausible causal explanation.
- If a family is only a proxy for a previously researched strategy, do not
  claim it is the exact published/original implementation.
- Keep the code simple enough that a keep/reject result teaches us something.

## Selection

The host's `score` is a conservative chronological robustness score, not the
single-period K metric. A candidate can have a high full-period return and
still be rejected if it is fragile across chronological folds or violates the
active drawdown profile.

The final 2023+ OOS evaluation remains a one-look test after research is frozen.
