# Current Research State

## Governing decisions

1. MP-001R1 is the corrected Stage-1 aggregate benchmark and showed no predictive evidence.
2. The aggregate Welch–Goyal branch is closed without tuning.
3. The OSAP × QuantConnect route is not defensible with free data because OSAP uses CRSP PERMNO and no authoritative free point-in-time bridge was found.
4. Sharadar appears suitable but paid data is deferred while a free proof-of-concept remains available.
5. The active path is QuantRocket's free learning bundle, `usstock-learn-1d`, covering 2007–2011.
6. The intended feature family remains `Alpha158-minus-VWAP`: 157 Qlib Alpha158 expressions, with only `VWAP0` removed.
7. No hyperparameter search is authorized.
8. No TRUE-OOS period is defined or opened.
9. Every model result must be preserved, including losers.
10. Terminal delisting semantics are a hard gate. Missing next-session labels may not be silently set to 0, -100%, or dropped.

## GitHub execution design

The current GitHub workflow performs data audit only.

It does not train Ridge or LightGBM. After the audit artifact is reviewed:

- if data are not defensible, stop;
- if data are defensible, commit the exact MP-002F preregistration;
- only after that commit may the frozen models run.

## Intended model set after freeze

1. cross-sectional/null benchmark;
2. Ridge, alpha = 1;
3. one published/reference LightGBM configuration.

No model zoo, feature selection, threshold optimization, or post-result specification changes.
