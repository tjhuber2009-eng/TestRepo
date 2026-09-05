"""Development-only v4 bootstrap using real prepared daily market CSVs."""
from __future__ import annotations

from pathlib import Path
import argparse
import json
import math

import numpy as np
import pandas as pd

from .campaign import assert_v4_data_boundary, risk_policy
from .feature_store import FeatureStoreBuilder
from .meta_filter import walk_forward_probabilities
from .multi_asset_engine import MultiAssetBacktester, PortfolioLimits, leveraged_regime_rotation
from .parameter_optimizer import ParameterSpec, StableParameterOptimizer
from .portfolio_optimizer import RobustPortfolioOptimizer
from .risk_overlays import drawdown_brake_overlay, probability_filter_overlay, vix_stress_overlay, volatility_target_overlay
from .selection_diagnostics import optimizer_pbo
from .strategy_examples import cross_sectional_momentum_rotation, leveraged_defensive_rotation


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
    return x[[c for c in ["Open", "High", "Low", "Close", "Volume"] if c in x.columns]]


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


def pbo_gate(diagnostic, max_pbo):
    return diagnostic is None or float(diagnostic["pbo"]) <= float(max_pbo)


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

    portfolio = None
    if eligible_returns:
        returns = pd.concat(
            [series.rename(name) for name, series in eligible_returns.items()],
            axis=1,
        ).dropna()
        if len(returns) >= 50:
            portfolio = RobustPortfolioOptimizer(
                dd_cap_pct=private.max_dd_pct,
                n_candidates=1000,
                bootstrap_reps=120,
                block=20,
                max_weight=1.0 if len(eligible_returns) == 1 else 0.90,
            ).optimize(returns)

    strategies = {
        "rotation_raw_diagnostic": rotation_raw.summary(),
        "cash_rotation_raw_diagnostic": cash_raw.summary(),
        "cross_asset_momentum_raw_diagnostic": momentum_raw.summary(),
    }
    if rotation_optimized is not None:
        strategies["rotation_risk_budgeted"] = rotation_optimized.summary()
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

    payload = {
        "protocol": "alpha_generation_v4",
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
        },
        "rotation_parameter_optimizer": rotation_param.to_dict(),
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
        "portfolio": None if portfolio is None else portfolio.to_dict(),
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
