# Moon Dev AUTORESEARCH — program.md

This file is the research memory and steering wheel. The loop gives you this
file plus the last 30 scored experiments. You propose one change; the frozen
harness decides whether it lives.

## What you may touch

- `strategy.py` only. Nothing else. Not the harness, not the data, not this file.
- One idea per try. If a change needs two knobs to make sense, that is one idea.
- Keep the class name `MoonStrategy`. Keep indicators in `self.I()`.
- You may run `python harness.py --check` to catch a crash (fast, 1500 bars).
  Do NOT run `--is`, `--oos`, `--full`, or `--asset`. The loop scores. You propose.
- Write a one-line description of the idea to `proposal.txt`, then stop.

## Laws (learned the hard way, do not re-learn them)

- The entry gate may only use information known at the close of bar t-1. A gate
  that reads the entry bar's own price deletes the best breakouts.
- No trailing exit unless it is armed by a big move first (12% MFE), and even
  then expect it to lose; past tests showed cut winners keep running (+88bps post-drift).
- Size is not an idea. Raising `f_max`, `vol_target` or the cash fraction is a
  leverage dial; the volatility guard will reject it. Spend the try on entries,
  exits, or gates.
- Fewer, better trades is fine. Fewer than 50 is a reject.
- Real data only. Never write code that fabricates a bar or fills a gap with a guess.

## Do-not-try list (already dead, the log agrees)

- Armed trailing exit, plain form. Rejected by paired t-test; post-drift +88bps.
- MACD confirmation on entry. Does not travel across assets.
- Any change that only raises size.

## Research behavior

- Prefer a falsifiable structural idea over parameter twitching.
- Read the recent result log before changing code. Do not repeat a dead idea
  under a new variable name.
- Keep changes small enough that a keep/reject result teaches something.
- Preserve causality: no future data, no centered windows, no negative shifts,
  no same-bar information that would not have been known when the decision was made.
- Do not alter commission, cash, margin, data windows, score, or guards.
