# Prediction Market Tournament — PMT-FROZEN-V1

A forward-only, paper-only tournament for structural prediction-market edges.

## Objective

Find strategies with high **percentage return, profit factor, and capital efficiency**
without trusting headline screenshots or tuning on future outcomes.

The frozen V1 comparison is designed to answer one question:

> If these exact rules had been fixed today, which lane makes the most money over
> the same forward time window after executable prices and venue fees?

## Frozen lanes

1. **Weather ensemble mispricing — taker baseline**
   - Model probability from ensemble members rather than one deterministic forecast.
   - Compare against the executable Polymarket ask.
   - Charge the current weather taker fee coefficient.
   - Resolve against the market's stated NOAA/NWS station/rules.

2. **Crypto TWAP dislocation — taker baseline**
   - BTC/USD only in V1. The market must explicitly resolve from Chainlink's 60-second TWAP.
   - Capture the strike from the first official `crypto_prices_twap_sixty` tick at/after
     the 5-minute boundary. If it arrives more than 3 seconds late, skip that market.
   - Evaluate exactly once at **120 seconds remaining** (maximum 3-second checkpoint lag).
   - Estimate short-horizon diffusion from the prior 120 seconds of raw Chainlink updates.
   - Model the distribution of the **final 60-second average**, not a future spot close.
   - The strategy must beat the executable ask by at least 4 percentage points **after fee**.

3. **Late-resolution crypto**
   - Separate, non-overlapping checkpoint: evaluate exactly once at **30 seconds remaining**.
   - Half of the final 60-second averaging interval is then already observed; the model uses
     that known time-weighted segment and only assigns uncertainty to the remaining half.
   - Require fair probability >=92% and at least 2.5 percentage points of after-fee edge.

4. **Favorite/longshot calibration — shadow**
   - No look-ahead calibration.
   - It remains shadow-only until a point-in-time resolution database exists.

5. **Maker/rebate capture — shadow**
   - Maker orders are fee-free, but paper fills are easy to fake.
   - This lane does not count a fill until order-book/trade replay proves the limit
     would have executed.

6. **Complete-set / mutually-exclusive dislocation — shadow**
   - Only exhaustive, mutually exclusive events.
   - Every leg must be executable.
   - A signal exists when the sum of best executable YES asks is below $1 by the
     frozen minimum edge.

7. **Trade-Halts — external control**
   - Kept as a paper-only control because historical performance was extraordinary
     but post-freeze stress tests were poor.

## Rules that cannot be relaxed after forward data arrives

- Paper-forward only. Real-money execution is disabled.
- No parameter change can alter already-recorded signals.
- Every signal stores the frozen spec SHA-256.
- Actual bid/ask is required; midpoint-only "fills" do not count.
- Current category fees are charged.
- Missed fills remain missed fills.
- A small sample is **never eliminated**. It stays provisional and gets ranked with
  explicit uncertainty.
- Strategy returns are compared on the same 30-day window. Shorter histories are
  shown as provisional return-to-date, not annualized headline returns.
- No future resolution may enter a model or calibration used for an earlier signal.

## Ranking

Primary dashboard metrics:

- 30-day net return
- profit factor
- capital efficiency
- max drawdown
- Brier score / probability calibration
- trade count and calendar age

`fixed_window_score` is a convenience ranking, not an optimization target.
Tournament decisions should still inspect the raw metrics.

## Data sources

The V1 code has public adapters for:

- Polymarket Gamma API
- Polymarket CLOB order books
- Polymarket RTDS raw Chainlink and exact 60-second TWAP (`full_accuracy_value`, E18)
- Open-Meteo ensemble forecasts

NOAA/NWS should be treated as the settlement reference when the market rules specify
a NOAA/NWS station. Current crypto Up/Down markets must be audited against their
stated Chainlink TWAP resolution source. V1 never substitutes Binance, Coinbase, or a
last-trade print for a missing official TWAP strike.

## Run tests

```bash
python -m pip install -e .
pytest -q
```

## Freeze verification

```python
from tournament.core import load_frozen_spec
spec, sha = load_frozen_spec("config/frozen_v1.json")
print(sha)
```

Every forward signal should record that SHA. A new strategy version must use a new
config file and a new forward clock; never rewrite `frozen_v1.json`.
