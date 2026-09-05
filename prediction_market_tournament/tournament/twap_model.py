from __future__ import annotations

from dataclasses import dataclass
from math import erf, sqrt
from statistics import stdev


@dataclass(frozen=True)
class TwapDistribution:
    mean: float
    std: float
    probability_above_strike: float
    effective_variance_seconds: float


def normal_cdf(x: float) -> float:
    return 0.5 * (1.0 + erf(x / sqrt(2.0)))


def estimate_sigma_per_sqrt_second(points: list[tuple[float, float]]) -> float:
    """Estimate arithmetic-Brownian sigma from timestamp-ms, price points.

    Each increment is normalized by sqrt(dt), so irregular 1s-ish RTDS cadence
    does not silently lower volatility when one update is delayed.
    """
    vals: list[float] = []
    ordered = sorted(points)
    for (t0, p0), (t1, p1) in zip(ordered, ordered[1:]):
        dt = (t1 - t0) / 1000.0
        if dt <= 0 or p0 <= 0 or p1 <= 0:
            continue
        vals.append((p1 - p0) / sqrt(dt))
    return stdev(vals) if len(vals) >= 2 else 0.0


def time_weighted_mean(
    points: list[tuple[float, float]],
    *,
    start_ms: float,
    end_ms: float,
) -> float | None:
    """Piecewise-constant time-weighted mean over [start_ms, end_ms]."""
    if end_ms <= start_ms:
        return None
    pts = sorted((t, p) for t, p in points if p > 0 and t <= end_ms)
    if not pts:
        return None
    before = [x for x in pts if x[0] <= start_ms]
    if before:
        current = before[-1][1]
    else:
        after = [x for x in pts if x[0] >= start_ms]
        if not after:
            return None
        current = after[0][1]

    cursor = start_ms
    area = 0.0
    for t, p in pts:
        if t <= start_ms:
            continue
        if t >= end_ms:
            break
        area += current * (t - cursor)
        cursor = t
        current = p
    area += current * (end_ms - cursor)
    return area / (end_ms - start_ms)


def final_twap_distribution(
    *,
    strike: float,
    current_spot: float,
    sigma_per_sqrt_second: float,
    seconds_remaining: float,
    window_seconds: float = 60.0,
    known_window_mean: float | None = None,
) -> TwapDistribution:
    """Causal approximation for the final rolling TWAP under arithmetic Brownian motion.

    If T >= L, the final L-second average is entirely future and
      Var(A_T) = sigma^2 * (T - 2L/3).

    If 0 < T < L, L-T seconds of the final averaging window are already known.
    Conditioning on that observed segment gives
      E[A_T] = ((L-T)*known_mean + T*spot) / L
      Var(A_T) = sigma^2 * T^3 / (3 L^2).

    This is a probability model, not a claim that BTC follows Brownian motion.
    It is frozen before forward evaluation and exists to make the benchmark
    settlement-mechanism-correct rather than spot-close based.
    """
    if strike <= 0 or current_spot <= 0:
        raise ValueError("strike/current_spot must be > 0")
    if sigma_per_sqrt_second < 0:
        raise ValueError("sigma_per_sqrt_second must be >= 0")
    if window_seconds <= 0:
        raise ValueError("window_seconds must be > 0")

    t = max(0.0, float(seconds_remaining))
    L = float(window_seconds)
    if t >= L:
        mean = current_spot
        eff = max(0.0, t - 2.0 * L / 3.0)
    elif t > 0:
        if known_window_mean is None:
            raise ValueError("known_window_mean required when seconds_remaining < window_seconds")
        known = L - t
        mean = (known * known_window_mean + t * current_spot) / L
        eff = t**3 / (3.0 * L**2)
    else:
        mean = known_window_mean if known_window_mean is not None else current_spot
        eff = 0.0

    std = sigma_per_sqrt_second * sqrt(eff)
    if std <= 0:
        p = 1.0 if mean >= strike else 0.0
    else:
        p = normal_cdf((mean - strike) / std)
    return TwapDistribution(mean, std, p, eff)
