"""Statistical diagnostics for AUTORESEARCH protocol v3.

All functions operate only on returns/equity already supplied by the caller.
They do not fetch data or inspect hidden/OOS state.
"""

import math
from statistics import NormalDist

import numpy as np


def finite_float(value, default=0.0):
    try:
        x = float(value)
    except Exception:
        return default
    return x if math.isfinite(x) else default


def geometric_cagr(total_return, years):
    total = finite_float(total_return, float("nan"))
    years = finite_float(years, 0.0)
    if years <= 0 or not math.isfinite(total) or total <= -1.0:
        return -1.0 if total <= -1.0 else float("nan")
    return (1.0 + total) ** (1.0 / years) - 1.0


def annualized_log_growth(total_return, years):
    total = finite_float(total_return, float("nan"))
    years = finite_float(years, 0.0)
    if years <= 0 or not math.isfinite(total) or total <= -1.0:
        return float("-inf")
    return math.log1p(total) / years


def annualized_k(total_return, years, sharpe):
    """Sign-safe duration-invariant growth × Sharpe score.

    The magnitude remains |annualized log growth × annualized Sharpe|, but K is
    positive only when BOTH growth and Sharpe are positive. A naive product
    rewards the pathological case where both are negative.
    """
    g = annualized_log_growth(total_return, years)
    s = finite_float(sharpe, float("nan"))
    if not math.isfinite(g) or not math.isfinite(s):
        return float("-inf")
    magnitude = abs(g * s)
    if g > 0.0 and s > 0.0:
        return magnitude
    if magnitude == 0.0:
        return 0.0
    return -magnitude


def moment_skew_kurtosis(returns):
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(r) < 4:
        return 0.0, 3.0
    mean = float(np.mean(r))
    centered = r - mean
    m2 = float(np.mean(centered ** 2))
    if m2 <= 0:
        return 0.0, 3.0
    m3 = float(np.mean(centered ** 3))
    m4 = float(np.mean(centered ** 4))
    skew = m3 / (m2 ** 1.5)
    kurtosis = m4 / (m2 ** 2)
    return float(skew), float(kurtosis)


def probabilistic_sharpe_ratio(returns, benchmark_sharpe_per_period=0.0):
    """Probability that the population Sharpe exceeds a benchmark.

    Bailey/Lopez de Prado finite-sample correction using sample skewness and
    kurtosis. Input Sharpe benchmark is per observation; zero is the project's
    principal diagnostic.
    """
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n < 4:
        return 0.0
    sd = float(np.std(r, ddof=0))
    if sd <= 0:
        return 0.0
    sr = float(np.mean(r) / sd)
    skew, kurt = moment_skew_kurtosis(r)
    denom_sq = 1.0 - skew * sr + ((kurt - 1.0) / 4.0) * sr * sr
    if denom_sq <= 0 or not math.isfinite(denom_sq):
        return 0.0
    z = (sr - float(benchmark_sharpe_per_period)) * math.sqrt(max(n - 1, 1))
    z /= math.sqrt(denom_sq)
    return float(NormalDist().cdf(z))


def tail_metrics(equity, returns, cagr, bars_per_year=1):
    eq = np.asarray(equity, dtype=float)
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    if len(eq) == 0:
        return {
            "ulcer_index_pct": 0.0,
            "daily_cvar_5_pct": 0.0,
            "sortino_per_bar": 0.0,
            "sortino_annualized": 0.0,
            "calmar": 0.0,
        }
    peak = np.maximum.accumulate(eq)
    dd = np.where(peak > 0, eq / peak - 1.0, 0.0)
    ulcer = math.sqrt(float(np.mean((100.0 * dd) ** 2))) if len(dd) else 0.0
    max_dd = abs(float(np.min(dd))) if len(dd) else 0.0

    if len(r):
        q = float(np.quantile(r, 0.05))
        tail = r[r <= q]
        cvar = float(np.mean(tail)) if len(tail) else q
        # Standard downside deviation about a zero minimum acceptable return:
        # square every negative shortfall while zeroing non-negative periods.
        shortfall = np.minimum(r, 0.0)
        downside_dev = math.sqrt(float(np.mean(shortfall ** 2))) if len(r) else 0.0
        sortino_per_bar = (
            float(np.mean(r) / downside_dev) if downside_dev > 0 else 0.0
        )
        bpy = finite_float(bars_per_year, 1.0)
        sortino_annualized = (
            sortino_per_bar * math.sqrt(bpy) if bpy > 0 else 0.0
        )
    else:
        cvar = 0.0
        sortino_per_bar = 0.0
        sortino_annualized = 0.0
    calmar = float(cagr / max_dd) if max_dd > 0 and math.isfinite(cagr) else 0.0
    return {
        "ulcer_index_pct": float(ulcer),
        "daily_cvar_5_pct": float(cvar * 100.0),
        "sortino_per_bar": float(sortino_per_bar),
        "sortino_annualized": float(sortino_annualized),
        "calmar": float(calmar),
    }


def deterministic_block_bootstrap_diagnostics(
    returns,
    *,
    bars_per_year,
    rng,
    reps=500,
    block=10,
):
    """Block-bootstrap lower Sharpe bound and one-sided mean-return p-value."""
    r = np.asarray(returns, dtype=float)
    r = r[np.isfinite(r)]
    n = len(r)
    if n < 30:
        return {
            "sharpe_p10": float("-inf"),
            "mean_positive_pvalue": 1.0,
            "reps": int(reps),
            "block": int(block),
        }

    observed_mean = float(np.mean(r))
    centered = r - observed_mean
    sharpes = []
    null_means = []
    for _ in range(int(reps)):
        pieces = []
        size = 0
        while size < n:
            j = int(rng.integers(0, max(1, n - block + 1)))
            piece = r[j:j + block]
            pieces.append(piece)
            size += len(piece)
        sample = np.concatenate(pieces)[:n]
        sd = float(np.std(sample, ddof=0))
        sharpes.append(
            float(np.mean(sample) / sd * math.sqrt(bars_per_year))
            if sd > 0 else 0.0
        )

        pieces = []
        size = 0
        while size < n:
            j = int(rng.integers(0, max(1, n - block + 1)))
            piece = centered[j:j + block]
            pieces.append(piece)
            size += len(piece)
        null_sample = np.concatenate(pieces)[:n]
        null_means.append(float(np.mean(null_sample)))

    exceed = sum(x >= observed_mean for x in null_means)
    pvalue = (exceed + 1.0) / (len(null_means) + 1.0)
    return {
        "sharpe_p10": float(np.quantile(sharpes, 0.10)),
        "mean_positive_pvalue": float(pvalue),
        "reps": int(reps),
        "block": int(block),
    }
