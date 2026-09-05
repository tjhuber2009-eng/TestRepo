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
    return 0.5 * (
        1.0 + erf(x / sqrt(2.0))
    )


def estimate_sigma_per_sqrt_second(
    points: list[
        tuple[float, float]
    ],
) -> float:
    """Estimate arithmetic-Brownian sigma from timestamp-ms, price points."""
    values: list[float] = []
    ordered = sorted(points)
    for (
        (t0, p0),
        (t1, p1),
    ) in zip(
        ordered,
        ordered[1:],
    ):
        dt = (t1 - t0) / 1000.0
        if (
            dt <= 0
            or p0 <= 0
            or p1 <= 0
        ):
            continue
        values.append(
            (p1 - p0) / sqrt(dt)
        )
    return (
        stdev(values)
        if len(values) >= 2
        else 0.0
    )


def time_weighted_mean(
    points: list[
        tuple[float, float]
    ],
    *,
    start_ms: float,
    end_ms: float,
) -> float | None:
    """Piecewise-constant causal time-weighted mean over [start_ms, end_ms]."""
    if end_ms <= start_ms:
        return None
    points_at_or_before_end = sorted(
        (t, p)
        for t, p in points
        if p > 0 and t <= end_ms
    )
    if not points_at_or_before_end:
        return None

    before = [
        point
        for point
        in points_at_or_before_end
        if point[0] <= start_ms
    ]
    # Never fill the beginning of an interval backward from a future tick.
    if not before:
        return None
    current = before[-1][1]

    cursor = start_ms
    area = 0.0
    for (
        timestamp,
        price,
    ) in points_at_or_before_end:
        if timestamp <= start_ms:
            continue
        if timestamp >= end_ms:
            break
        area += (
            current
            * (timestamp - cursor)
        )
        cursor = timestamp
        current = price
    area += current * (
        end_ms - cursor
    )
    return area / (
        end_ms - start_ms
    )


def final_twap_distribution(
    *,
    strike: float,
    current_spot: float,
    sigma_per_sqrt_second: float,
    seconds_remaining: float,
    window_seconds: float = 60.0,
    known_window_mean: float | None = None,
) -> TwapDistribution:
    """Causal approximation for the final rolling TWAP under Brownian motion.

    If T >= L, the final L-second average is entirely future:
      Var(A_T) = sigma^2 * (T - 2L/3).

    If 0 < T < L, L-T seconds are already known:
      E[A_T] = ((L-T)*known_mean + T*spot) / L
      Var(A_T) = sigma^2 * T^3 / (3 L^2).
    """
    if (
        strike <= 0
        or current_spot <= 0
    ):
        raise ValueError(
            "strike/current_spot must be > 0"
        )
    if sigma_per_sqrt_second < 0:
        raise ValueError(
            "sigma_per_sqrt_second must be >= 0"
        )
    if window_seconds <= 0:
        raise ValueError(
            "window_seconds must be > 0"
        )

    remaining = max(
        0.0,
        float(seconds_remaining),
    )
    window = float(window_seconds)
    if remaining >= window:
        mean = current_spot
        effective_variance_seconds = max(
            0.0,
            remaining
            - 2.0 * window / 3.0,
        )
    elif remaining > 0:
        if known_window_mean is None:
            raise ValueError(
                "known_window_mean required "
                "when seconds_remaining "
                "< window_seconds"
            )
        known_seconds = (
            window - remaining
        )
        mean = (
            known_seconds
            * known_window_mean
            + remaining
            * current_spot
        ) / window
        effective_variance_seconds = (
            remaining ** 3
            / (
                3.0
                * window ** 2
            )
        )
    else:
        mean = (
            known_window_mean
            if known_window_mean
            is not None
            else current_spot
        )
        effective_variance_seconds = 0.0

    std = (
        sigma_per_sqrt_second
        * sqrt(
            effective_variance_seconds
        )
    )
    if std <= 0:
        probability = (
            1.0
            if mean >= strike
            else 0.0
        )
    else:
        probability = normal_cdf(
            (mean - strike) / std
        )
    return TwapDistribution(
        mean=mean,
        std=std,
        probability_above_strike=(
            probability
        ),
        effective_variance_seconds=(
            effective_variance_seconds
        ),
    )
