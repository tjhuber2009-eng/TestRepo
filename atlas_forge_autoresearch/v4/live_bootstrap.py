"""Development-only v4 bootstrap using real prepared daily market CSVs."""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import math
import subprocess

import numpy as np
import pandas as pd

from .campaign import assert_v4_data_boundary, risk_policy
from .feature_store import FeatureStoreBuilder
from .meta_filter import walk_forward_probabilities
from .multi_asset_engine import (
    MultiAssetBacktester,
    PortfolioLimits,
    leveraged_hysteresis_rotation,
    leveraged_regime_rotation,
)
from .parameter_optimizer import ParameterSpec, StableParameterOptimizer
from .portfolio_optimizer import RobustPortfolioOptimizer
from .risk_overlays import drawdown_brake_overlay, probability_filter_overlay, vix_stress_overlay, volatility_target_overlay
from .selection_diagnostics import optimizer_pbo
from .continuous_bridge import replay_private_promotions
from .dynamic_portfolio import causal_dynamic_allocation
from .satellite_portfolio import (
    build_staggered_satellite_candidates,
    satellite_gross_profile,
)
from .phase2_bridge import replay_private_promotions as replay_phase2_private_promotions
from .strategy_examples import (
    cross_sectional_momentum_rotation,
    independent_trend_basket,
    leveraged_defensive_rotation,
)


PRIVATE_PORTFOLIO_FINANCING_RATE_PCT = 6.0
PRIVATE_PORTFOLIO_FINANCING_STRESS_RATES_PCT = (6.0, 10.0, 14.0)


def research_commit_sha() -> str | None:
    """Return the exact checked-out research commit for result provenance."""
    try:
        return subprocess.check_output(
            ["git", "rev-parse", "HEAD"],
            text=True,
            stderr=subprocess.DEVNULL,
        ).strip()
    except (OSError, subprocess.SubprocessError):
        return None


def json_safe(value):
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (int, np.integer)):
        return int(value)
    return value


def read_market_csv(path: Path) -> pd.DataFrame:
    x = pd.read_csv(path)
    if "Date" not in x:
        raise ValueError(f"{path}: Date column required")
    # This loader is daily-only. Normalize provider-specific timestamps
    # (Yahoo session timestamps vs Binance midnight UTC) onto the same causal
    # calendar date before cross-asset alignment. Intraday data uses the
    # separate intraday protocol and is never normalized this way.
    idx = pd.DatetimeIndex(pd.to_datetime(x.pop("Date"), utc=True)).normalize().tz_localize(None)
    x.index = idx
    x.index.name = "Date"
    if x.index.has_duplicates:
        raise ValueError(f"{path}: duplicate daily dates after normalization")
    x = x.sort_index()
    return x[
        [
            c
            for c in [
                "Open", "High", "Low", "Close", "Volume", "Dividend"
            ]
            if c in x.columns
        ]
    ]


def load_data(root: Path) -> dict[str, pd.DataFrame]:
    mapping = {
        "QQQ": "qqq_1d.csv",
        "TQQQ": "tqqq_1d.csv",
        "SPY": "spy_1d.csv",
        "IEF": "ief_1d.csv",
        "GLD": "gld_1d.csv",
        "SHY": "shy_1d.csv",
        "VIX": "vix_1d.csv",
        "BTCUSDT": "btc_1d.csv",
        "ETHUSDT": "eth_1d.csv",
    }
    out = {}
    for symbol, name in mapping.items():
        p = root / name
        if p.exists():
            out[symbol] = read_market_csv(p)
    if not {"QQQ", "TQQQ", "SPY"}.issubset(out):
        raise RuntimeError("QQQ/TQQQ/SPY development data are required")
    return out



def select_portfolio_history_cohort(
    eligible_returns: dict[str, pd.Series],
    *,
    periods_per_year: float = 252.0,
    min_core_years: float = 8.0,
    min_relative_coverage: float = 0.75,
) -> tuple[dict[str, pd.Series], dict]:
    """Prevent short-history alphas from truncating the core portfolio sample.

    Core strategies must have a development history close to the longest
    eligible strategy. Shorter-history strategies remain reported as
    supplemental hypotheses, but cannot define the evidence window for the
    authoritative robust portfolio.
    """
    if not eligible_returns:
        return {}, {
            "policy": "long_history_core_v1",
            "core_strategy_names": [],
            "supplemental_strategy_names": [],
            "history": {},
            "core_min_years": None,
        }

    history = {}
    max_years = 0.0
    for name, series in eligible_returns.items():
        valid = series.replace([np.inf, -np.inf], np.nan).dropna()
        years = len(valid) / float(periods_per_year)
        max_years = max(max_years, years)
        history[name] = {
            "observations": int(len(valid)),
            "years": float(years),
            "start": None if valid.empty else valid.index.min().strftime("%Y-%m-%d"),
            "end": None if valid.empty else valid.index.max().strftime("%Y-%m-%d"),
        }

    threshold = min(
        max_years,
        max(float(min_core_years), max_years * float(min_relative_coverage)),
    )
    core_names = sorted(
        name for name, row in history.items()
        if float(row["years"]) + 1e-12 >= threshold
    )
    if not core_names:
        longest = max(history, key=lambda name: history[name]["years"])
        core_names = [longest]
    supplemental = sorted(set(eligible_returns) - set(core_names))
    core = {name: eligible_returns[name] for name in core_names}
    metadata = {
        "policy": "long_history_core_v1",
        "periods_per_year": float(periods_per_year),
        "minimum_absolute_years": float(min_core_years),
        "minimum_relative_coverage": float(min_relative_coverage),
        "longest_history_years": float(max_years),
        "core_min_years": float(threshold),
        "core_strategy_names": core_names,
        "supplemental_strategy_names": supplemental,
        "history": history,
        "interpretation": (
            "short-history strategies are retained as supplemental research "
            "but cannot truncate the evidence window of the authoritative "
            "robust portfolio"
        ),
    }
    return core, metadata

def fold_cagr_scores(returns: pd.Series, periods_per_year: float, dd_cap_pct: float) -> list[float]:
    """CAGR-first fold utility with explicit drawdown-over-cap penalty."""
    arr = returns.to_numpy(dtype=float)
    out = []
    for chunk in np.array_split(arr, 8):
        chunk = chunk[np.isfinite(chunk)]
        if len(chunk) < 20:
            continue
        eq = np.cumprod(1.0 + chunk)
        if eq[-1] <= 0:
            out.append(-1e6)
            continue
        years = len(chunk) / float(periods_per_year)
        cagr = ((eq[-1] ** (1.0 / years)) - 1.0) * 100.0
        peaks = np.maximum.accumulate(eq)
        dd = abs(float(np.min(eq / peaks - 1.0) * 100.0))
        # A fold may exceed the whole-period cap without automatically killing
        # the configuration, but it receives a strong penalty.
        score = cagr - 2.0 * max(dd - float(dd_cap_pct), 0.0)
        out.append(float(score))
    return out


def build_cash_rotation_strategy(params):
    base = leveraged_regime_rotation(
        signal_symbol="QQQ",
        risk_symbol="TQQQ",
        defensive_symbol=None,
        sma_window=int(params["sma"]),
        momentum_window=int(params["mom"]),
    )
    return volatility_target_overlay(
        base,
        target_vol=float(params["target_vol"]),
        periods_per_year=252.0,
        lookback=int(params["vol_lookback"]),
        max_gross=1.5,
        max_scale=1.5,
    )


def rotation_meta_probabilities(core, feature_store, base_params):
    """Causal walk-forward odds that the base rotation's next holding pays."""
    base = leveraged_regime_rotation(
        signal_symbol="QQQ",
        risk_symbol="TQQQ",
        defensive_symbol="SPY",
        sma_window=int(base_params["sma"]),
        momentum_window=int(base_params["mom"]),
    )
    target = base(core, None)
    future = pd.DataFrame(
        {
            symbol: frame["Open"].shift(-2) / frame["Open"].shift(-1) - 1.0
            for symbol, frame in core.items()
        },
        index=target.index,
    )
    gross = target.abs().sum(axis=1)
    normalized = target.div(gross.replace(0.0, np.nan), axis=0)
    realized = (normalized * future).sum(axis=1, min_count=1)
    label = (realized > 0.0).astype(float)
    label.loc[gross <= 0.0] = np.nan
    label.loc[realized.isna()] = np.nan

    q = feature_store.by_asset["QQQ"]
    cols = [
        "ret_20", "ret_60", "rv_20", "atr_14_pct", "gap_1",
        "dist_sma_200", "rsi_2", "rsi_14", "volume_ratio_5_20",
    ]
    x = q[[col for col in cols if col in q]].copy()
    if "VIX" in feature_store.by_asset:
        vx = feature_store.by_asset["VIX"]
        for col in ("ret_20", "rv_20", "rsi_14", "dist_sma_20"):
            if col in vx:
                x[f"vix_{col}"] = vx[col]
    x = x.reindex(target.index).replace([np.inf, -np.inf], np.nan)
    label = label.reindex(x.index)
    return walk_forward_probabilities(
        x,
        label,
        min_train=252,
        retrain_every=21,
        n_estimators=10,
        label_delay=1,
    )


def build_meta_rotation_strategy(params, base_params, probabilities):
    base = leveraged_regime_rotation(
        signal_symbol="QQQ",
        risk_symbol="TQQQ",
        defensive_symbol="SPY",
        sma_window=int(base_params["sma"]),
        momentum_window=int(base_params["mom"]),
    )
    filtered = probability_filter_overlay(
        base,
        probabilities,
        threshold=float(params["threshold"]),
        below_scale=float(params["below_scale"]),
    )
    return volatility_target_overlay(
        filtered,
        target_vol=float(params["target_vol"]),
        periods_per_year=252.0,
        lookback=int(base_params["vol_lookback"]),
        max_gross=1.5,
        max_scale=1.5,
    )


def build_vix_cash_strategy(params, base_params, vix_close):
    base = leveraged_regime_rotation(
        signal_symbol="QQQ",
        risk_symbol="TQQQ",
        defensive_symbol=None,
        sma_window=int(base_params["sma"]),
        momentum_window=int(base_params["mom"]),
    )
    stressed = vix_stress_overlay(
        base,
        vix_close,
        stress_quantile=float(params["stress_q"]),
        severe_quantile=float(params["severe_q"]),
        stress_scale=float(params["stress_scale"]),
        severe_scale=float(params["severe_scale"]),
        min_history=252,
    )
    return volatility_target_overlay(
        stressed,
        target_vol=float(params["target_vol"]),
        periods_per_year=252.0,
        lookback=int(base_params["vol_lookback"]),
        max_gross=1.5,
        max_scale=1.5,
    )


def build_rotation_strategy(params):
    base = leveraged_regime_rotation(
        signal_symbol="QQQ",
        risk_symbol="TQQQ",
        defensive_symbol="SPY",
        sma_window=int(params["sma"]),
        momentum_window=int(params["mom"]),
    )
    return volatility_target_overlay(
        base,
        target_vol=float(params["target_vol"]),
        periods_per_year=252.0,
        lookback=int(params["vol_lookback"]),
        max_gross=1.5,
        max_scale=1.5,
    )


def build_hysteresis_rotation_strategy(params):
    base = leveraged_hysteresis_rotation(
        signal_symbol="QQQ",
        risk_symbol="TQQQ",
        defensive_symbol="SPY",
        sma_window=int(params["sma"]),
        entry_band=float(params["entry_band"]),
        exit_band=float(params["exit_band"]),
    )
    return volatility_target_overlay(
        base,
        target_vol=float(params["target_vol"]),
        periods_per_year=252.0,
        lookback=60,
        max_gross=1.5,
        max_scale=1.5,
    )


def build_momentum_strategy(params, symbols):
    base = cross_sectional_momentum_rotation(
        lookback=126,
        trend_window=200,
        top_k=min(2, len(symbols)),
        eligible_symbols=tuple(symbols),
    )
    return volatility_target_overlay(
        base,
        target_vol=float(params["target_vol"]),
        periods_per_year=252.0,
        lookback=int(params["vol_lookback"]),
        max_gross=1.0,
        max_scale=1.0,
    )


def build_long_history_trend_strategy(params, symbols):
    base = independent_trend_basket(
        symbols=tuple(symbols),
        momentum_window=int(params["mom"]),
        trend_window=int(params["trend"]),
    )
    return volatility_target_overlay(
        base,
        target_vol=float(params["target_vol"]),
        periods_per_year=252.0,
        lookback=int(params["vol_lookback"]),
        max_gross=1.0,
        max_scale=1.0,
    )


def build_defensive_strategy(params):
    base = leveraged_defensive_rotation(
        signal_symbol="QQQ",
        risk_symbol="TQQQ",
        defensive_symbols=("IEF", "GLD", "SHY"),
        risk_sma_window=int(params["risk_sma"]),
        risk_momentum_window=int(params["risk_mom"]),
        defensive_momentum_window=int(params["def_mom"]),
        defensive_trend_window=int(params["def_trend"]),
    )
    return volatility_target_overlay(
        base,
        target_vol=float(params["target_vol"]),
        periods_per_year=252.0,
        lookback=int(params["vol_lookback"]),
        max_gross=1.5,
        max_scale=1.5,
    )


def build_defensive_brake_strategy(params, base_params):
    base = leveraged_defensive_rotation(
        signal_symbol="QQQ",
        risk_symbol="TQQQ",
        defensive_symbols=("IEF", "GLD", "SHY"),
        risk_sma_window=int(base_params["risk_sma"]),
        risk_momentum_window=int(base_params["risk_mom"]),
        defensive_momentum_window=int(base_params["def_mom"]),
        defensive_trend_window=int(base_params["def_trend"]),
    )
    braked = drawdown_brake_overlay(
        base,
        soft_drawdown=float(params["soft_dd"]),
        hard_drawdown=float(params["hard_dd"]),
        soft_scale=float(params["soft_scale"]),
        hard_scale=float(params["hard_scale"]),
    )
    return volatility_target_overlay(
        braked,
        target_vol=float(params["target_vol"]),
        periods_per_year=252.0,
        lookback=int(base_params["vol_lookback"]),
        max_gross=1.5,
        max_scale=1.5,
    )



def build_rsi2_pullback_strategy(params):
    """QQQ short-term reversal signal traded through TQQQ.

    Structure is deliberately compact and literature-led: only buy short-term
    oversold QQQ pullbacks while QQQ is above its 200-day trend, then exit
    after QQQ mean-reverts above its 5-day average. Signals are formed at
    close[t] and the engine executes no earlier than open[t+1].
    """
    entry_rsi = float(params["entry_rsi"])

    def base(data, features=None):
        if features is None or "QQQ" not in features:
            raise ValueError("QQQ causal features required for RSI2 pullback")
        if "QQQ" not in data or "TQQQ" not in data:
            raise KeyError("QQQ/TQQQ required for RSI2 pullback")
        index = data["QQQ"].index
        close = pd.to_numeric(data["QQQ"]["Close"], errors="coerce")
        feat = features["QQQ"].reindex(index)
        rsi2 = pd.to_numeric(feat["rsi_2"], errors="coerce")
        sma200 = close.rolling(200, min_periods=200).mean()
        sma5 = close.rolling(5, min_periods=5).mean()
        out = pd.DataFrame(0.0, index=index, columns=sorted(data))
        active = False
        for i, ts in enumerate(index):
            cc = float(close.iloc[i]) if pd.notna(close.iloc[i]) else float("nan")
            rr = float(rsi2.iloc[i]) if pd.notna(rsi2.iloc[i]) else float("nan")
            long_trend = (
                np.isfinite(cc)
                and pd.notna(sma200.iloc[i])
                and cc > float(sma200.iloc[i])
            )
            if active:
                if (
                    not long_trend
                    or (
                        pd.notna(sma5.iloc[i])
                        and cc > float(sma5.iloc[i])
                    )
                ):
                    active = False
            elif long_trend and np.isfinite(rr) and rr < entry_rsi:
                active = True
            if active:
                out.loc[ts, "TQQQ"] = 1.0
        return out

    return volatility_target_overlay(
        base,
        target_vol=float(params["target_vol"]),
        periods_per_year=252.0,
        lookback=int(params["vol_lookback"]),
        max_gross=1.5,
        max_scale=1.5,
    )

def pbo_gate(diagnostic, max_pbo):
    """Require an actual PBO diagnostic before a family can pass evidence gating."""
    return (
        diagnostic is not None
        and float(diagnostic["pbo"]) <= float(max_pbo)
    )


def portfolio_challenger_wins(challenger, baseline, *, q10_tolerance_pct=5.0):
    """Require better bootstrap growth than one fixed baseline architecture."""
    if challenger is None or challenger.chosen is None:
        return False
    if baseline is None or baseline.chosen is None:
        return True
    c = challenger.chosen
    b = baseline.chosen
    return bool(
        c.bootstrap_median_cagr_pct > b.bootstrap_median_cagr_pct + 1e-12
        and c.bootstrap_cagr_q10_pct
        >= b.bootstrap_cagr_q10_pct - float(q10_tolerance_pct)
    )


def select_portfolio_architecture(
    static_portfolio,
    challengers,
    *,
    q10_tolerance_pct=5.0,
):
    """Choose highest bootstrap-median CAGR from challengers clearing one floor.

    All architectures are compared against the same static baseline. This
    removes order dependence where a strong first challenger could accidentally
    raise the lower-tail hurdle for later challengers.
    """
    options = []
    if static_portfolio is not None and static_portfolio.chosen is not None:
        options.append(("static_robust", static_portfolio))
    for name, result in challengers.items():
        if portfolio_challenger_wins(
            result,
            static_portfolio,
            q10_tolerance_pct=q10_tolerance_pct,
        ):
            options.append((name, result))
    if not options:
        return None, None
    return max(
        options,
        key=lambda item: (
            item[1].chosen.bootstrap_median_cagr_pct,
            item[1].chosen.bootstrap_cagr_q10_pct,
            item[1].chosen.cagr_pct,
            item[0],
        ),
    )


def run(data_dir: str | Path, output: str | Path) -> dict:
    data = load_data(Path(data_dir))
    assert_v4_data_boundary(data, stage="development")
    store = FeatureStoreBuilder(
        {s: (365 if s.endswith("USDT") else 252) for s in data}
    ).build(data)

    private = risk_policy("private")
    cost_stress = 3.0

    # ------------------------------------------------------------------
    # Family A: QQQ signal -> TQQQ/SPY rotation
    # ------------------------------------------------------------------
    core = {s: data[s] for s in ("QQQ", "TQQQ", "SPY")}
    eng = MultiAssetBacktester(
        core,
        limits=PortfolioLimits(
            gross_leverage=1.5,
            net_min=0.0,
            net_max=1.5,
            per_asset_abs_weight=1.5,
        ),
        periods_per_year=252.0,
    )
    raw_rotation_strategy = leveraged_regime_rotation(
        signal_symbol="QQQ",
        risk_symbol="TQQQ",
        defensive_symbol="SPY",
        sma_window=175,
        momentum_window=126,
    )
    rotation_raw = eng.run(
        raw_rotation_strategy,
        risk_policy=private,
        num_trials=1,
        cost_stress_multiplier=cost_stress,
    )
    rotation_specs = [
        ParameterSpec("sma", (150, 175, 200, 225)),
        ParameterSpec("mom", (60, 126, 200)),
        ParameterSpec("target_vol", (0.12, 0.16, 0.20, 0.24, 0.28)),
        ParameterSpec("vol_lookback", (20, 60)),
    ]
    rotation_trial_count = int(np.prod([len(s.values) for s in rotation_specs]))

    def evaluate_rotation(params):
        res = eng.run(
            build_rotation_strategy(params),
            risk_policy=private,
            num_trials=rotation_trial_count,
            cost_stress_multiplier=cost_stress,
        )
        return {
            "fold_scores": fold_cagr_scores(res.returns, 252.0, private.max_dd_pct),
            "primary_score": float(res.metrics.cost_stress_cagr_pct),
            "gate_ok": bool(res.gate_ok),
            "structural_fingerprint": "leveraged_regime_rotation_voltarget_v1",
        }

    rotation_param = StableParameterOptimizer(
        rotation_specs,
        max_trials=160,
        plateau_neighbors=6,
        dispersion_penalty=0.20,
        multiple_test_penalty=0.15,
    ).optimize(
        evaluate_rotation,
        frozen_structure="leveraged_regime_rotation_voltarget_v1",
    )
    rotation_pbo = optimizer_pbo(rotation_param)
    rotation_family_ok = pbo_gate(rotation_pbo, private.max_pbo)

    rotation_optimized = None
    if rotation_param.chosen is not None:
        rotation_optimized = eng.run(
            build_rotation_strategy(rotation_param.chosen.params),
            risk_policy=private,
            num_trials=rotation_trial_count,
            pbo=None if rotation_pbo is None else rotation_pbo["pbo"],
            cost_stress_multiplier=cost_stress,
        )

    # ------------------------------------------------------------------
    # Family A1b: walk-forward meta-filter on the winning rotation structure
    # ------------------------------------------------------------------
    meta_param = None
    meta_pbo = None
    meta_family_ok = False
    meta_optimized = None
    if rotation_param.chosen is not None:
        base_rotation = rotation_param.chosen.params
        meta_probs = rotation_meta_probabilities(core, store, base_rotation)
        target0 = float(base_rotation["target_vol"])
        meta_targets = tuple(sorted(set([
            round(target0, 4),
            round(min(target0 + 0.04, 0.40), 4),
            round(min(target0 + 0.08, 0.40), 4),
        ])))
        meta_specs = [
            ParameterSpec("threshold", (0.45, 0.50, 0.55, 0.60, 0.65)),
            ParameterSpec("below_scale", (0.0, 0.50, 0.75)),
            ParameterSpec("target_vol", meta_targets),
        ]
        meta_trial_count = int(np.prod([len(s.values) for s in meta_specs]))

        def evaluate_meta(params):
            res = eng.run(
                build_meta_rotation_strategy(
                    params, base_rotation, meta_probs
                ),
                risk_policy=private,
                num_trials=meta_trial_count,
                cost_stress_multiplier=cost_stress,
            )
            return {
                "fold_scores": fold_cagr_scores(
                    res.returns, 252.0, private.max_dd_pct
                ),
                "primary_score": float(res.metrics.cost_stress_cagr_pct),
                "gate_ok": bool(res.gate_ok),
                "structural_fingerprint": "rotation_walkforward_meta_filter_v1",
            }

        meta_param = StableParameterOptimizer(
            meta_specs,
            max_trials=60,
            plateau_neighbors=5,
            dispersion_penalty=0.20,
            multiple_test_penalty=0.16,
        ).optimize(
            evaluate_meta,
            frozen_structure="rotation_walkforward_meta_filter_v1",
        )
        meta_pbo = optimizer_pbo(meta_param)
        meta_family_ok = pbo_gate(meta_pbo, private.max_pbo)
        if meta_param.chosen is not None:
            meta_optimized = eng.run(
                build_meta_rotation_strategy(
                    meta_param.chosen.params, base_rotation, meta_probs
                ),
                risk_policy=private,
                num_trials=meta_trial_count,
                pbo=None if meta_pbo is None else meta_pbo["pbo"],
                cost_stress_multiplier=cost_stress,
            )

    # ------------------------------------------------------------------
    # Family A1c: QQQ SMA hysteresis -> TQQQ/SPY, causal vol target
    # ------------------------------------------------------------------
    hysteresis_specs = [
        ParameterSpec("sma", (150, 175, 200)),
        ParameterSpec("entry_band", (0.00, 0.02, 0.04)),
        ParameterSpec("exit_band", (0.00, 0.02, 0.04)),
        ParameterSpec("target_vol", (0.24, 0.28, 0.32)),
    ]
    hysteresis_trial_count = int(
        np.prod([len(s.values) for s in hysteresis_specs])
    )

    def evaluate_hysteresis(params):
        res = eng.run(
            build_hysteresis_rotation_strategy(params),
            risk_policy=private,
            num_trials=hysteresis_trial_count,
            cost_stress_multiplier=cost_stress,
        )
        return {
            "fold_scores": fold_cagr_scores(
                res.returns, 252.0, private.max_dd_pct
            ),
            "primary_score": float(res.metrics.cost_stress_cagr_pct),
            "gate_ok": bool(res.gate_ok),
            "structural_fingerprint": "leveraged_sma_hysteresis_voltarget_v1",
        }

    hysteresis_param = StableParameterOptimizer(
        hysteresis_specs,
        max_trials=100,
        plateau_neighbors=6,
        dispersion_penalty=0.20,
        multiple_test_penalty=0.17,
    ).optimize(
        evaluate_hysteresis,
        frozen_structure="leveraged_sma_hysteresis_voltarget_v1",
    )
    hysteresis_pbo = optimizer_pbo(hysteresis_param)
    hysteresis_family_ok = pbo_gate(hysteresis_pbo, private.max_pbo)

    hysteresis_optimized = None
    if hysteresis_param.chosen is not None:
        hysteresis_optimized = eng.run(
            build_hysteresis_rotation_strategy(
                hysteresis_param.chosen.params
            ),
            risk_policy=private,
            num_trials=hysteresis_trial_count,
            pbo=(
                None
                if hysteresis_pbo is None
                else hysteresis_pbo["pbo"]
            ),
            cost_stress_multiplier=cost_stress,
        )

    # ------------------------------------------------------------------
    # Family A1d: literature-led short-term reversal inside QQQ uptrend
    # ------------------------------------------------------------------
    reversal_specs = [
        ParameterSpec("entry_rsi", (5.0, 10.0)),
        ParameterSpec("target_vol", (0.12, 0.16, 0.20, 0.24)),
        ParameterSpec("vol_lookback", (20, 60)),
    ]
    reversal_trial_count = int(
        np.prod([len(s.values) for s in reversal_specs])
    )

    def evaluate_reversal(params):
        res = eng.run(
            build_rsi2_pullback_strategy(params),
            features=store.by_asset,
            risk_policy=private,
            num_trials=reversal_trial_count,
            cost_stress_multiplier=cost_stress,
        )
        return {
            "fold_scores": fold_cagr_scores(
                res.returns, 252.0, private.max_dd_pct
            ),
            "primary_score": float(res.metrics.cost_stress_cagr_pct),
            "gate_ok": bool(res.gate_ok),
            "structural_fingerprint": "qqq_rsi2_pullback_tqqq_voltarget_v1",
        }

    reversal_param = StableParameterOptimizer(
        reversal_specs,
        max_trials=20,
        plateau_neighbors=3,
        dispersion_penalty=0.20,
        multiple_test_penalty=0.16,
    ).optimize(
        evaluate_reversal,
        frozen_structure="qqq_rsi2_pullback_tqqq_voltarget_v1",
    )
    reversal_pbo = optimizer_pbo(reversal_param)
    reversal_family_ok = pbo_gate(reversal_pbo, private.max_pbo)
    reversal_optimized = None
    if reversal_param.chosen is not None:
        reversal_optimized = eng.run(
            build_rsi2_pullback_strategy(
                reversal_param.chosen.params
            ),
            features=store.by_asset,
            risk_policy=private,
            num_trials=reversal_trial_count,
            pbo=(
                None
                if reversal_pbo is None
                else reversal_pbo["pbo"]
            ),
            cost_stress_multiplier=cost_stress,
        )

    # ------------------------------------------------------------------
    # Family A2: QQQ signal -> TQQQ, otherwise cash
    # ------------------------------------------------------------------
    cash_raw_strategy = leveraged_regime_rotation(
        signal_symbol="QQQ",
        risk_symbol="TQQQ",
        defensive_symbol=None,
        sma_window=175,
        momentum_window=126,
    )
    cash_raw = eng.run(
        cash_raw_strategy,
        risk_policy=private,
        num_trials=1,
        cost_stress_multiplier=cost_stress,
    )
    cash_specs = [
        ParameterSpec("sma", (150, 175, 200, 225)),
        ParameterSpec("mom", (60, 126, 200)),
        ParameterSpec("target_vol", (0.16, 0.20, 0.24, 0.28, 0.32, 0.36)),
        ParameterSpec("vol_lookback", (20, 60)),
    ]
    cash_trial_count = int(np.prod([len(s.values) for s in cash_specs]))

    def evaluate_cash(params):
        res = eng.run(
            build_cash_rotation_strategy(params),
            risk_policy=private,
            num_trials=cash_trial_count,
            cost_stress_multiplier=cost_stress,
        )
        return {
            "fold_scores": fold_cagr_scores(
                res.returns, 252.0, private.max_dd_pct
            ),
            "primary_score": float(res.metrics.cost_stress_cagr_pct),
            "gate_ok": bool(res.gate_ok),
            "structural_fingerprint": "leveraged_cash_rotation_voltarget_v1",
        }

    cash_param = StableParameterOptimizer(
        cash_specs,
        max_trials=160,
        plateau_neighbors=6,
        dispersion_penalty=0.20,
        multiple_test_penalty=0.15,
    ).optimize(
        evaluate_cash,
        frozen_structure="leveraged_cash_rotation_voltarget_v1",
    )
    cash_pbo = optimizer_pbo(cash_param)
    cash_family_ok = pbo_gate(cash_pbo, private.max_pbo)

    cash_optimized = None
    if cash_param.chosen is not None:
        cash_optimized = eng.run(
            build_cash_rotation_strategy(cash_param.chosen.params),
            risk_policy=private,
            num_trials=cash_trial_count,
            pbo=None if cash_pbo is None else cash_pbo["pbo"],
            cost_stress_multiplier=cost_stress,
        )

    # ------------------------------------------------------------------
    # Family A3: TQQQ-or-cash plus causal VIX stress regime
    # ------------------------------------------------------------------
    vix_param = None
    vix_pbo = None
    vix_family_ok = False
    vix_optimized = None
    if cash_param.chosen is not None and "VIX" in data:
        base_cash = cash_param.chosen.params
        target0 = float(base_cash["target_vol"])
        vix_targets = tuple(sorted(set([
            round(target0, 4),
            round(min(target0 + 0.04, 0.40), 4),
            round(min(target0 + 0.08, 0.40), 4),
        ])))
        vix_specs = [
            ParameterSpec("stress_q", (0.75, 0.80, 0.85, 0.90)),
            ParameterSpec("severe_q", (0.95, 0.97)),
            ParameterSpec("stress_scale", (0.25, 0.50, 0.75)),
            ParameterSpec("severe_scale", (0.0,)),
            ParameterSpec("target_vol", vix_targets),
        ]
        vix_trial_count = int(np.prod([len(s.values) for s in vix_specs]))
        vix_close = data["VIX"]["Close"]

        def evaluate_vix(params):
            res = eng.run(
                build_vix_cash_strategy(params, base_cash, vix_close),
                risk_policy=private,
                num_trials=vix_trial_count,
                cost_stress_multiplier=cost_stress,
            )
            return {
                "fold_scores": fold_cagr_scores(
                    res.returns, 252.0, private.max_dd_pct
                ),
                "primary_score": float(res.metrics.cost_stress_cagr_pct),
                "gate_ok": bool(res.gate_ok),
                "structural_fingerprint": "cash_rotation_vix_stress_v1",
            }

        vix_param = StableParameterOptimizer(
            vix_specs,
            max_trials=100,
            plateau_neighbors=5,
            dispersion_penalty=0.20,
            multiple_test_penalty=0.16,
        ).optimize(
            evaluate_vix,
            frozen_structure="cash_rotation_vix_stress_v1",
        )
        vix_pbo = optimizer_pbo(vix_param)
        vix_family_ok = pbo_gate(vix_pbo, private.max_pbo)
        if vix_param.chosen is not None:
            vix_optimized = eng.run(
                build_vix_cash_strategy(
                    vix_param.chosen.params, base_cash, vix_close
                ),
                risk_policy=private,
                num_trials=vix_trial_count,
                pbo=None if vix_pbo is None else vix_pbo["pbo"],
                cost_stress_multiplier=cost_stress,
            )

    # ------------------------------------------------------------------
    # Family B: QQQ -> TQQQ, else rotate IEF/GLD/SHY or cash
    # ------------------------------------------------------------------
    defensive_required = {"QQQ", "TQQQ", "IEF", "GLD", "SHY"}
    defensive_raw = None
    defensive_param = None
    defensive_pbo = None
    defensive_family_ok = False
    defensive_optimized = None
    brake_param = None
    brake_pbo = None
    brake_family_ok = False
    defensive_braked = None

    if defensive_required.issubset(data):
        defensive_assets = {s: data[s] for s in sorted(defensive_required)}
        def_eng = MultiAssetBacktester(
            defensive_assets,
            limits=PortfolioLimits(
                gross_leverage=1.5,
                net_min=0.0,
                net_max=1.5,
                per_asset_abs_weight=1.5,
            ),
            periods_per_year=252.0,
        )
        defensive_raw_strategy = leveraged_defensive_rotation(
            signal_symbol="QQQ",
            risk_symbol="TQQQ",
            defensive_symbols=("IEF", "GLD", "SHY"),
            risk_sma_window=175,
            risk_momentum_window=126,
            defensive_momentum_window=126,
            defensive_trend_window=200,
        )
        defensive_raw = def_eng.run(
            defensive_raw_strategy,
            risk_policy=private,
            num_trials=1,
            cost_stress_multiplier=cost_stress,
        )

        defensive_specs = [
            ParameterSpec("risk_sma", (150, 175, 200)),
            ParameterSpec("risk_mom", (60, 126, 200)),
            ParameterSpec("def_mom", (60, 126)),
            ParameterSpec("def_trend", (100, 200)),
            ParameterSpec("target_vol", (0.16, 0.20, 0.24, 0.28, 0.32)),
            ParameterSpec("vol_lookback", (20, 60)),
        ]
        defensive_trial_count = int(np.prod([len(s.values) for s in defensive_specs]))

        def evaluate_defensive(params):
            res = def_eng.run(
                build_defensive_strategy(params),
                risk_policy=private,
                num_trials=defensive_trial_count,
                cost_stress_multiplier=cost_stress,
            )
            return {
                "fold_scores": fold_cagr_scores(
                    res.returns, 252.0, private.max_dd_pct
                ),
                "primary_score": float(res.metrics.cost_stress_cagr_pct),
                "gate_ok": bool(res.gate_ok),
                "structural_fingerprint": "leveraged_defensive_rotation_voltarget_v1",
            }

        defensive_param = StableParameterOptimizer(
            defensive_specs,
            max_trials=400,
            plateau_neighbors=8,
            dispersion_penalty=0.20,
            multiple_test_penalty=0.18,
        ).optimize(
            evaluate_defensive,
            frozen_structure="leveraged_defensive_rotation_voltarget_v1",
        )
        defensive_pbo = optimizer_pbo(defensive_param)
        defensive_family_ok = pbo_gate(defensive_pbo, private.max_pbo)

        if defensive_param.chosen is not None:
            defensive_optimized = def_eng.run(
                build_defensive_strategy(defensive_param.chosen.params),
                risk_policy=private,
                num_trials=defensive_trial_count,
                pbo=None if defensive_pbo is None else defensive_pbo["pbo"],
                cost_stress_multiplier=cost_stress,
            )

            # A separate structural variant asks whether a causal drawdown
            # brake lets us deploy a higher volatility target without breaking
            # the 32% private drawdown ceiling.
            chosen = defensive_param.chosen.params
            target0 = float(chosen["target_vol"])
            target_grid = tuple(sorted(set([
                round(target0, 4),
                round(min(target0 + 0.04, 0.36), 4),
                round(min(target0 + 0.08, 0.36), 4),
            ])))
            brake_specs = [
                ParameterSpec("soft_dd", (0.05, 0.08, 0.10)),
                ParameterSpec("hard_dd", (0.12, 0.16, 0.20)),
                ParameterSpec("soft_scale", (0.65, 0.80)),
                ParameterSpec("hard_scale", (0.35, 0.50)),
                ParameterSpec("target_vol", target_grid),
            ]
            brake_trial_count = int(np.prod([len(s.values) for s in brake_specs]))

            def evaluate_brake(params):
                res = def_eng.run(
                    build_defensive_brake_strategy(params, chosen),
                    risk_policy=private,
                    num_trials=brake_trial_count,
                    cost_stress_multiplier=cost_stress,
                )
                return {
                    "fold_scores": fold_cagr_scores(
                        res.returns, 252.0, private.max_dd_pct
                    ),
                    "primary_score": float(res.metrics.cost_stress_cagr_pct),
                    "gate_ok": bool(res.gate_ok),
                    "structural_fingerprint": "defensive_rotation_drawdown_brake_v1",
                }

            brake_param = StableParameterOptimizer(
                brake_specs,
                max_trials=160,
                plateau_neighbors=6,
                dispersion_penalty=0.20,
                multiple_test_penalty=0.18,
            ).optimize(
                evaluate_brake,
                frozen_structure="defensive_rotation_drawdown_brake_v1",
            )
            brake_pbo = optimizer_pbo(brake_param)
            brake_family_ok = pbo_gate(brake_pbo, private.max_pbo)

            if brake_param.chosen is not None:
                defensive_braked = def_eng.run(
                    build_defensive_brake_strategy(
                        brake_param.chosen.params, chosen
                    ),
                    risk_policy=private,
                    num_trials=brake_trial_count,
                    pbo=None if brake_pbo is None else brake_pbo["pbo"],
                    cost_stress_multiplier=cost_stress,
                )

    # ------------------------------------------------------------------
    # Family C: cross-asset momentum
    # ------------------------------------------------------------------
    momentum_assets = {
        s: data[s]
        for s in data
        if s in {"QQQ", "SPY", "BTCUSDT", "ETHUSDT"}
    }
    mom_eng = MultiAssetBacktester(
        momentum_assets,
        limits=PortfolioLimits(
            gross_leverage=1.0,
            net_min=0.0,
            net_max=1.0,
            per_asset_abs_weight=1.0,
        ),
        periods_per_year=252.0,
    )
    momentum_symbols = tuple(momentum_assets)
    momentum_raw_strategy = cross_sectional_momentum_rotation(
        lookback=126,
        trend_window=200,
        top_k=min(2, len(momentum_assets)),
        eligible_symbols=momentum_symbols,
    )
    momentum_raw = mom_eng.run(
        momentum_raw_strategy,
        risk_policy=private,
        num_trials=1,
        cost_stress_multiplier=cost_stress,
    )
    momentum_specs = [
        ParameterSpec("target_vol", (0.10, 0.14, 0.18, 0.22, 0.26)),
        ParameterSpec("vol_lookback", (20, 60)),
    ]
    momentum_trial_count = int(np.prod([len(s.values) for s in momentum_specs]))

    def evaluate_momentum(params):
        res = mom_eng.run(
            build_momentum_strategy(params, momentum_symbols),
            risk_policy=private,
            num_trials=momentum_trial_count,
            cost_stress_multiplier=cost_stress,
        )
        return {
            "fold_scores": fold_cagr_scores(
                res.returns, 252.0, private.max_dd_pct
            ),
            "primary_score": float(res.metrics.cost_stress_cagr_pct),
            "gate_ok": bool(res.gate_ok),
            "structural_fingerprint": "cross_asset_momentum_voltarget_v1",
        }

    momentum_param = StableParameterOptimizer(
        momentum_specs,
        max_trials=20,
        plateau_neighbors=3,
        dispersion_penalty=0.20,
        multiple_test_penalty=0.15,
    ).optimize(
        evaluate_momentum,
        frozen_structure="cross_asset_momentum_voltarget_v1",
    )
    momentum_pbo = optimizer_pbo(momentum_param)
    momentum_family_ok = pbo_gate(momentum_pbo, private.max_pbo)

    momentum_optimized = None
    if momentum_param.chosen is not None:
        momentum_optimized = mom_eng.run(
            build_momentum_strategy(momentum_param.chosen.params, momentum_symbols),
            risk_policy=private,
            num_trials=momentum_trial_count,
            pbo=None if momentum_pbo is None else momentum_pbo["pbo"],
            cost_stress_multiplier=cost_stress,
        )

    # ------------------------------------------------------------------
    # Family C2: long-history diversified time-series trend basket.
    # Unlike cross-sectional momentum, each asset is independently long/cash.
    # ------------------------------------------------------------------
    trend_assets = {
        s: data[s] for s in ("SPY", "IEF", "GLD") if s in data
    }
    trend_eng = MultiAssetBacktester(
        trend_assets,
        limits=PortfolioLimits(
            gross_leverage=1.0,
            net_min=0.0,
            net_max=1.0,
            per_asset_abs_weight=1.0,
        ),
        periods_per_year=252.0,
    )
    trend_symbols = tuple(trend_assets)
    trend_raw = trend_eng.run(
        independent_trend_basket(
            symbols=trend_symbols,
            momentum_window=252,
            trend_window=200,
        ),
        risk_policy=private,
        num_trials=1,
        cost_stress_multiplier=cost_stress,
    )
    trend_specs = [
        ParameterSpec("mom", (126, 252)),
        ParameterSpec("trend", (150, 200)),
        ParameterSpec("target_vol", (0.10, 0.14, 0.18)),
        ParameterSpec("vol_lookback", (20, 60)),
    ]
    trend_trial_count = int(np.prod([len(s.values) for s in trend_specs]))

    def evaluate_long_history_trend(params):
        res = trend_eng.run(
            build_long_history_trend_strategy(params, trend_symbols),
            risk_policy=private,
            num_trials=trend_trial_count,
            cost_stress_multiplier=cost_stress,
        )
        return {
            "fold_scores": fold_cagr_scores(
                res.returns, 252.0, private.max_dd_pct
            ),
            "primary_score": float(res.metrics.cost_stress_cagr_pct),
            "gate_ok": bool(res.gate_ok),
            "structural_fingerprint": "long_history_cross_asset_trend_basket_v1",
        }

    trend_param = StableParameterOptimizer(
        trend_specs,
        max_trials=30,
        plateau_neighbors=4,
        dispersion_penalty=0.20,
        multiple_test_penalty=0.16,
    ).optimize(
        evaluate_long_history_trend,
        frozen_structure="long_history_cross_asset_trend_basket_v1",
    )
    trend_pbo = optimizer_pbo(trend_param)
    trend_family_ok = pbo_gate(trend_pbo, private.max_pbo)
    trend_optimized = None
    if trend_param.chosen is not None:
        trend_optimized = trend_eng.run(
            build_long_history_trend_strategy(
                trend_param.chosen.params, trend_symbols
            ),
            risk_policy=private,
            num_trials=trend_trial_count,
            pbo=None if trend_pbo is None else trend_pbo["pbo"],
            cost_stress_multiplier=cost_stress,
        )

    # ------------------------------------------------------------------
    # Portfolio-level optimization only uses fully eligible families.
    # ------------------------------------------------------------------
    eligible_returns = {}
    if (
        rotation_optimized is not None
        and rotation_optimized.gate_ok
        and rotation_family_ok
    ):
        eligible_returns["rotation_risk_budgeted"] = rotation_optimized.returns
    if (
        meta_optimized is not None
        and meta_optimized.gate_ok
        and meta_family_ok
    ):
        eligible_returns["rotation_walkforward_meta_filter"] = meta_optimized.returns
    if (
        hysteresis_optimized is not None
        and hysteresis_optimized.gate_ok
        and hysteresis_family_ok
    ):
        eligible_returns["rotation_sma_hysteresis"] = (
            hysteresis_optimized.returns
        )
    if (
        reversal_optimized is not None
        and reversal_optimized.gate_ok
        and reversal_family_ok
    ):
        eligible_returns["qqq_rsi2_pullback"] = reversal_optimized.returns
    if (
        cash_optimized is not None
        and cash_optimized.gate_ok
        and cash_family_ok
    ):
        eligible_returns["cash_rotation_risk_budgeted"] = cash_optimized.returns
    if (
        vix_optimized is not None
        and vix_optimized.gate_ok
        and vix_family_ok
    ):
        eligible_returns["cash_rotation_vix_stress"] = vix_optimized.returns
    if (
        defensive_optimized is not None
        and defensive_optimized.gate_ok
        and defensive_family_ok
    ):
        eligible_returns["defensive_rotation_risk_budgeted"] = (
            defensive_optimized.returns
        )
    if (
        defensive_braked is not None
        and defensive_braked.gate_ok
        and brake_family_ok
    ):
        eligible_returns["defensive_rotation_drawdown_brake"] = (
            defensive_braked.returns
        )
    if (
        momentum_optimized is not None
        and momentum_optimized.gate_ok
        and momentum_family_ok
    ):
        eligible_returns["cross_asset_momentum_risk_budgeted"] = (
            momentum_optimized.returns
        )
    if (
        trend_optimized is not None
        and trend_optimized.gate_ok
        and trend_family_ok
    ):
        eligible_returns["long_history_cross_asset_trend"] = (
            trend_optimized.returns
        )

    # Continuous AUTORESEARCH is the breadth/depth idea generator. Re-evaluate
    # its strongest diversified private champions on the v4 development data
    # and fail closed on PBO before allowing any of them into the authoritative
    # portfolio. This keeps discovery continuous without letting adaptive
    # headline winners silently bypass v4 evidence gates.
    continuous_eligible, continuous_private_transfer = (
        replay_private_promotions(
            data,
            max_dd_pct=private.max_dd_pct,
            cost_stress_multiplier=cost_stress,
        )
    )
    eligible_returns.update(continuous_eligible)

    # Phase 2 is the finite prior-work lane. Consume only its frozen v2
    # promotion artifacts; exact source hashes and both PBO diagnostics are
    # rechecked by the bridge before any strategy can reach this portfolio.
    phase2_eligible, phase2_private_transfer = (
        replay_phase2_private_promotions(
            data,
            max_dd_pct=private.max_dd_pct,
            cost_stress_multiplier=cost_stress,
        )
    )
    eligible_returns.update(phase2_eligible)

    portfolio = None
    static_portfolio = None
    satellite_portfolio = None
    satellite_portfolio_summary = None
    satellite_matched_static_gross = None
    dynamic_portfolio = None
    dynamic_portfolio_summary = None
    dynamic_matched_static_gross = None
    portfolio_financing_sensitivity = {}
    portfolio_selection = {
        "method": "static_vs_staggered_satellite_vs_causal_dynamic_v1",
        "selected": None,
        "reason": "insufficient_eligible_portfolio_history",
    }
    portfolio_concentration_sensitivity = {}
    portfolio_core_returns, portfolio_history_policy = (
        select_portfolio_history_cohort(eligible_returns)
    )
    if portfolio_core_returns:
        returns = pd.concat(
            [
                series.rename(name)
                for name, series in portfolio_core_returns.items()
            ],
            axis=1,
        ).dropna()
        portfolio_history_policy["common_overlap_observations"] = int(
            len(returns)
        )
        portfolio_history_policy["common_overlap_years"] = float(
            len(returns) / 252.0
        )
        portfolio_history_policy["common_overlap_start"] = (
            None if returns.empty else returns.index.min().strftime("%Y-%m-%d")
        )
        portfolio_history_policy["common_overlap_end"] = (
            None if returns.empty else returns.index.max().strftime("%Y-%m-%d")
        )
        if len(returns) >= 50:
            weight_caps = (
                (1.0,)
                if len(portfolio_core_returns) == 1
                else (0.50, 0.55, 0.65, 0.90)
            )
            for weight_cap in weight_caps:
                sensitivity_result = RobustPortfolioOptimizer(
                    dd_cap_pct=private.max_dd_pct,
                    n_candidates=1000,
                    bootstrap_reps=120,
                    block=20,
                    max_weight=weight_cap,
                    annual_financing_rate_pct=PRIVATE_PORTFOLIO_FINANCING_RATE_PCT,
                ).optimize(returns)
                portfolio_concentration_sensitivity[
                    f"{weight_cap:.2f}"
                ] = sensitivity_result
            # 55% is the authoritative multi-strategy cap. The prior
            # paired 65% search selected a composition whose largest normalized
            # strategy weight is ~54.73%, so that exact winner remains feasible
            # at 55% on identical bootstrap paths. This tightens concentration
            # without excluding the current measured optimum.
            authoritative_cap = (
                "1.00" if len(portfolio_core_returns) == 1 else "0.55"
            )
            static_portfolio = portfolio_concentration_sensitivity[
                authoritative_cap
            ]
            portfolio = static_portfolio
            portfolio_selection = {
                "method": "static_vs_staggered_satellite_vs_causal_dynamic_v1",
                "selected": "static_robust",
                "reason": "baseline_architecture",
            }

            # Qualified shorter-history strategies may enter only as a capped
            # staggered-inception satellite sleeve. Before each strategy's
            # first development observation, its sleeve allocation is cash.
            supplemental_names = portfolio_history_policy.get(
                "supplemental_strategy_names", []
            )
            supplemental_returns = {
                name: eligible_returns[name]
                for name in supplemental_names
                if name in eligible_returns
            }
            if (
                static_portfolio is not None
                and static_portfolio.chosen is not None
                and supplemental_returns
            ):
                satellite_streams, satellite_specs = (
                    build_staggered_satellite_candidates(
                        returns,
                        static_portfolio.chosen.weights,
                        supplemental_returns,
                        max_satellite_weight=0.25,
                    )
                )
                satellite_gross_profiles = {
                    name: satellite_gross_profile(
                        stream.index, satellite_specs[name]
                    )
                    for name, stream in satellite_streams.items()
                }
                satellite_results = {}
                for candidate_name, stream in satellite_streams.items():
                    result = RobustPortfolioOptimizer(
                        dd_cap_pct=private.max_dd_pct,
                        n_candidates=1,
                        bootstrap_reps=120,
                        block=20,
                        max_weight=1.0,
                        max_gross=1.5,
                        min_gross=0.10,
                        annual_financing_rate_pct=PRIVATE_PORTFOLIO_FINANCING_RATE_PCT,
                        seed=20260905,
                    ).optimize(
                        stream.to_frame(candidate_name),
                        gross_profiles=satellite_gross_profiles[
                            candidate_name
                        ].to_frame(candidate_name),
                    )
                    if result.chosen is not None:
                        satellite_results[candidate_name] = result

                if satellite_results:
                    eligible_satellite_results = {
                        name: result
                        for name, result in satellite_results.items()
                        if portfolio_challenger_wins(
                            result, static_portfolio
                        )
                    }
                    raw_best_name, raw_best_result = max(
                        satellite_results.items(),
                        key=lambda item: (
                            item[1].chosen.bootstrap_median_cagr_pct,
                            item[1].chosen.bootstrap_cagr_q10_pct,
                            item[1].chosen.cagr_pct,
                            item[0],
                        ),
                    )
                    selected_satellite_name = None
                    if eligible_satellite_results:
                        selected_satellite_name, satellite_portfolio = max(
                            eligible_satellite_results.items(),
                            key=lambda item: (
                                item[1].chosen.bootstrap_median_cagr_pct,
                                item[1].chosen.bootstrap_cagr_q10_pct,
                                item[1].chosen.cagr_pct,
                                item[0],
                            ),
                        )
                    else:
                        satellite_portfolio = raw_best_result

                    summary_name = (
                        selected_satellite_name or raw_best_name
                    )
                    satellite_portfolio_summary = {
                        "selected_candidate": selected_satellite_name,
                        "best_raw_candidate": raw_best_name,
                        "spec": satellite_specs[
                            summary_name
                        ].to_dict(),
                        "candidate_count": len(satellite_results),
                        "eligible_candidate_count": len(
                            eligible_satellite_results
                        ),
                        "policy": (
                            "supplemental sleeve capped at 25% composition; "
                            "pre-inception allocation held as cash; all sleeves "
                            "clear the same static-portfolio Q10 floor"
                        ),
                    }

                    if selected_satellite_name is not None:
                        static_gross = float(
                            static_portfolio.chosen.gross_exposure
                        )
                        satellite_matched_static_gross = (
                            RobustPortfolioOptimizer(
                                dd_cap_pct=private.max_dd_pct,
                                n_candidates=1,
                                bootstrap_reps=120,
                                block=20,
                                max_weight=1.0,
                                max_gross=static_gross,
                                min_gross=static_gross,
                                annual_financing_rate_pct=PRIVATE_PORTFOLIO_FINANCING_RATE_PCT,
                                seed=20260905,
                            ).optimize(
                                satellite_streams[
                                    selected_satellite_name
                                ].to_frame(
                                    selected_satellite_name
                                ),
                                gross_profiles=satellite_gross_profiles[
                                    selected_satellite_name
                                ].to_frame(
                                    selected_satellite_name
                                ),
                            )
                        )

            # Causal strategy-level rotation is evaluated as one additional
            # portfolio architecture, never as a bypass around individual
            # strategy evidence gates. Its weights at t use only returns < t.
            if len(portfolio_core_returns) >= 2 and len(returns) >= 254:
                dynamic = causal_dynamic_allocation(
                    returns,
                    periods_per_year=252.0,
                    min_history=252,
                    growth_lookback=126,
                    risk_lookback=63,
                    rebalance_every=21,
                    max_weight=0.55,
                    correlation_penalty=1.0,
                    downside_penalty=0.75,
                    soft_drawdown=-0.12,
                    hard_drawdown=-0.22,
                    soft_scale=0.75,
                    hard_scale=0.50,
                )
                dynamic_gross_profile = (
                    dynamic.weights.abs().sum(axis=1)
                ).rename("causal_dynamic_allocator")
                dynamic_portfolio = RobustPortfolioOptimizer(
                    dd_cap_pct=private.max_dd_pct,
                    n_candidates=1,
                    bootstrap_reps=120,
                    block=20,
                    max_weight=1.0,
                    max_gross=1.5,
                    min_gross=0.10,
                    annual_financing_rate_pct=PRIVATE_PORTFOLIO_FINANCING_RATE_PCT,
                    seed=20260905,
                ).optimize(
                    dynamic.returns.to_frame("causal_dynamic_allocator"),
                    gross_profiles=dynamic_gross_profile.to_frame(),
                )
                dynamic_portfolio_summary = dynamic.summary()
                if static_portfolio is not None and static_portfolio.chosen is not None:
                    static_gross = float(
                        static_portfolio.chosen.gross_exposure
                    )
                    dynamic_matched_static_gross = RobustPortfolioOptimizer(
                        dd_cap_pct=private.max_dd_pct,
                        n_candidates=1,
                        bootstrap_reps=120,
                        block=20,
                        max_weight=1.0,
                        max_gross=static_gross,
                        min_gross=static_gross,
                        annual_financing_rate_pct=PRIVATE_PORTFOLIO_FINANCING_RATE_PCT,
                        seed=20260905,
                    ).optimize(
                        dynamic.returns.to_frame(
                            "causal_dynamic_allocator"
                        ),
                        gross_profiles=dynamic_gross_profile.to_frame(),
                    )

            challengers = {}
            if (
                satellite_portfolio_summary is not None
                and satellite_portfolio_summary.get(
                    "selected_candidate"
                ) is not None
            ):
                challengers[
                    "staggered_satellite"
                ] = satellite_portfolio
            if portfolio_challenger_wins(
                dynamic_portfolio, static_portfolio
            ):
                challengers["causal_dynamic"] = dynamic_portfolio

            selected_name, selected_result = (
                select_portfolio_architecture(
                    static_portfolio,
                    challengers,
                )
            )
            if selected_result is not None:
                portfolio = selected_result
                portfolio_selection = {
                    "method": (
                        "static_vs_staggered_satellite_vs_"
                        "causal_dynamic_v2"
                    ),
                    "selected": selected_name,
                    "reason": (
                        "highest_bootstrap_median_cagr_among_architectures_"
                        "clearing_common_static_q10_floor"
                    ),
                }

                selected_stream = None
                selected_gross_profile = None
                if selected_name == "staggered_satellite":
                    selected_candidate_name = (
                        satellite_portfolio_summary or {}
                    ).get("selected_candidate")
                    if selected_candidate_name in satellite_streams:
                        selected_stream = satellite_streams[
                            selected_candidate_name
                        ]
                        selected_gross_profile = (
                            satellite_gross_profiles[
                                selected_candidate_name
                            ]
                        )
                elif selected_name == "causal_dynamic":
                    selected_stream = dynamic.returns
                    selected_gross_profile = dynamic_gross_profile
                elif selected_name == "static_robust":
                    chosen = static_portfolio.chosen
                    if chosen is not None and chosen.gross_exposure > 0.0:
                        comp = pd.Series(
                            chosen.weights,
                            dtype=float,
                        ).reindex(returns.columns).fillna(0.0)
                        comp = comp / float(chosen.gross_exposure)
                        selected_stream = returns.mul(
                            comp, axis=1
                        ).sum(axis=1)
                        selected_gross_profile = pd.Series(
                            1.0,
                            index=selected_stream.index,
                            name=selected_stream.name,
                        )

                if selected_stream is not None:
                    for financing_rate in (
                        PRIVATE_PORTFOLIO_FINANCING_STRESS_RATES_PCT
                    ):
                        stress_name = (
                            f"selected_architecture_{financing_rate:.0f}pct"
                        )
                        stressed = RobustPortfolioOptimizer(
                            dd_cap_pct=private.max_dd_pct,
                            n_candidates=1,
                            bootstrap_reps=120,
                            block=20,
                            max_weight=1.0,
                            max_gross=1.5,
                            min_gross=0.10,
                            annual_financing_rate_pct=financing_rate,
                            seed=20260905,
                        ).optimize(
                            selected_stream.rename(stress_name).to_frame(),
                            gross_profiles=(
                                selected_gross_profile.rename(
                                    stress_name
                                ).to_frame()
                                if selected_gross_profile is not None
                                else None
                            ),
                        )
                        portfolio_financing_sensitivity[
                            f"{financing_rate:.0f}%"
                        ] = stressed

    strategies = {
        "rotation_raw_diagnostic": rotation_raw.summary(),
        "cash_rotation_raw_diagnostic": cash_raw.summary(),
        "cross_asset_momentum_raw_diagnostic": momentum_raw.summary(),
        "long_history_cross_asset_trend_raw_diagnostic": trend_raw.summary(),
    }
    if rotation_optimized is not None:
        strategies["rotation_risk_budgeted"] = rotation_optimized.summary()
    if meta_optimized is not None:
        strategies["rotation_walkforward_meta_filter"] = meta_optimized.summary()
    if hysteresis_optimized is not None:
        strategies["rotation_sma_hysteresis"] = hysteresis_optimized.summary()
    if reversal_optimized is not None:
        strategies["qqq_rsi2_pullback"] = reversal_optimized.summary()
    if cash_optimized is not None:
        strategies["cash_rotation_risk_budgeted"] = cash_optimized.summary()
    if vix_optimized is not None:
        strategies["cash_rotation_vix_stress"] = vix_optimized.summary()
    if defensive_raw is not None:
        strategies["defensive_rotation_raw_diagnostic"] = defensive_raw.summary()
    if defensive_optimized is not None:
        strategies["defensive_rotation_risk_budgeted"] = (
            defensive_optimized.summary()
        )
    if defensive_braked is not None:
        strategies["defensive_rotation_drawdown_brake"] = (
            defensive_braked.summary()
        )
    if momentum_optimized is not None:
        strategies["cross_asset_momentum_risk_budgeted"] = (
            momentum_optimized.summary()
        )
    if trend_optimized is not None:
        strategies["long_history_cross_asset_trend"] = (
            trend_optimized.summary()
        )

    payload = {
        "protocol": "alpha_generation_v4",
        "research_commit_sha": research_commit_sha(),
        "stage": "development_only",
        "data_end": max(
            frame.index.max().strftime("%Y-%m-%d") for frame in data.values()
        ),
        "hidden_validation_opened": False,
        "final_oos_opened": False,
        "assets": sorted(data),
        "risk_profile": {
            "name": private.name,
            "max_dd_pct": private.max_dd_pct,
            "min_psr": private.min_psr,
            "min_dsr": private.min_dsr,
            "max_pbo": private.max_pbo,
            "cost_stress_multiplier": cost_stress,
        },
        "feature_manifest": store.manifest,
        "strategies": strategies,
        "selection_diagnostics": {
            "rotation_pbo": rotation_pbo,
            "rotation_family_ok": rotation_family_ok,
            "meta_pbo": meta_pbo,
            "meta_family_ok": meta_family_ok,
            "hysteresis_pbo": hysteresis_pbo,
            "hysteresis_family_ok": hysteresis_family_ok,
            "reversal_pbo": reversal_pbo,
            "reversal_family_ok": reversal_family_ok,
            "cash_pbo": cash_pbo,
            "cash_family_ok": cash_family_ok,
            "vix_pbo": vix_pbo,
            "vix_family_ok": vix_family_ok,
            "defensive_pbo": defensive_pbo,
            "defensive_family_ok": defensive_family_ok,
            "brake_pbo": brake_pbo,
            "brake_family_ok": brake_family_ok,
            "momentum_pbo": momentum_pbo,
            "momentum_family_ok": momentum_family_ok,
            "trend_pbo": trend_pbo,
            "trend_family_ok": trend_family_ok,
        },
        "rotation_parameter_optimizer": rotation_param.to_dict(),
        "meta_parameter_optimizer": (
            None if meta_param is None else meta_param.to_dict()
        ),
        "hysteresis_parameter_optimizer": hysteresis_param.to_dict(),
        "reversal_parameter_optimizer": reversal_param.to_dict(),
        "cash_parameter_optimizer": cash_param.to_dict(),
        "vix_parameter_optimizer": (
            None if vix_param is None else vix_param.to_dict()
        ),
        "defensive_parameter_optimizer": (
            None if defensive_param is None else defensive_param.to_dict()
        ),
        "defensive_brake_parameter_optimizer": (
            None if brake_param is None else brake_param.to_dict()
        ),
        "momentum_parameter_optimizer": momentum_param.to_dict(),
        "trend_parameter_optimizer": trend_param.to_dict(),
        "portfolio": None if portfolio is None else portfolio.to_dict(),
        "portfolio_selection": portfolio_selection,
        "portfolio_static": (
            None if static_portfolio is None else static_portfolio.to_dict()
        ),
        "portfolio_satellite": (
            None
            if satellite_portfolio is None
            else {
                "risk_scaled_result": satellite_portfolio.to_dict(),
                "matched_static_gross_result": (
                    None
                    if satellite_matched_static_gross is None
                    else satellite_matched_static_gross.to_dict()
                ),
                "sleeve": satellite_portfolio_summary,
            }
        ),
        "portfolio_dynamic": (
            None
            if dynamic_portfolio is None
            else {
                "risk_scaled_result": dynamic_portfolio.to_dict(),
                "matched_static_gross_result": (
                    None
                    if dynamic_matched_static_gross is None
                    else dynamic_matched_static_gross.to_dict()
                ),
                "allocator": dynamic_portfolio_summary,
            }
        ),
        "portfolio_financing_policy": {
            "base_annual_rate_pct": PRIVATE_PORTFOLIO_FINANCING_RATE_PCT,
            "stress_rates_pct": list(
                PRIVATE_PORTFOLIO_FINANCING_STRESS_RATES_PCT
            ),
            "charged_on": "gross_exposure_above_1x_only",
            "cash_yield_credit": False,
            "purpose": (
                "prevent leverage from creating artificial CAGR under "
                "zero-cost borrowing"
            ),
        },
        "portfolio_financing_sensitivity": {
            rate: result.to_dict()
            for rate, result in portfolio_financing_sensitivity.items()
        },
        "portfolio_authoritative_concentration_cap": (
            None
            if not portfolio_concentration_sensitivity
            else (1.0 if len(portfolio_core_returns) == 1 else 0.55)
        ),
        "portfolio_concentration_sensitivity": {
            cap: result.to_dict()
            for cap, result in portfolio_concentration_sensitivity.items()
        },
        "portfolio_history_policy": portfolio_history_policy,
        "continuous_private_transfer": continuous_private_transfer,
        "phase2_private_transfer": phase2_private_transfer,
        "eligible_strategy_names": sorted(eligible_returns),
    }
    safe = json_safe(payload)
    out = Path(output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps(safe, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    return safe


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--data-dir", default="data")
    ap.add_argument("--output", default="v4_state/development-bootstrap.json")
    args = ap.parse_args()
    payload = run(args.data_dir, args.output)
    print(json.dumps({
        "stage": payload["stage"],
        "assets": payload["assets"],
        "eligible_strategy_names": payload["eligible_strategy_names"],
        "final_oos_opened": payload["final_oos_opened"],
    }, indent=2, allow_nan=False))
