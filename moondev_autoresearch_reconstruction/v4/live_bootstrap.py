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
from .multi_asset_engine import MultiAssetBacktester, PortfolioLimits, leveraged_regime_rotation
from .parameter_optimizer import ParameterSpec, StableParameterOptimizer
from .portfolio_optimizer import RobustPortfolioOptimizer
from .risk_overlays import volatility_target_overlay
from .strategy_examples import cross_sectional_momentum_rotation


def json_safe(value):
    if isinstance(value, dict):
        return {str(k): json_safe(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [json_safe(v) for v in value]
    if isinstance(value, (float, np.floating)):
        return float(value) if math.isfinite(float(value)) else None
    if isinstance(value, (int, np.integer)):
        return int(value)
    if isinstance(value, (bool, np.bool_)):
        return bool(value)
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
    for chunk in np.array_split(arr, 5):
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


def run(data_dir: str | Path, output: str | Path) -> dict:
    data = load_data(Path(data_dir))
    assert_v4_data_boundary(data, stage="development")
    store = FeatureStoreBuilder(
        {s: (365 if s.endswith("USDT") else 252) for s in data}
    ).build(data)

    private = risk_policy("private")
    cost_stress = 3.0

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

    # Diagnostic only: deliberately evaluate the raw full-weight structure
    # under the real private risk gate. It must not be treated as eligible if
    # it exceeds the 32% drawdown cap.
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

    rotation_optimized = None
    if rotation_param.chosen is not None:
        rotation_optimized = eng.run(
            build_rotation_strategy(rotation_param.chosen.params),
            risk_policy=private,
            num_trials=rotation_trial_count,
            cost_stress_multiplier=cost_stress,
        )

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
            "fold_scores": fold_cagr_scores(res.returns, 252.0, private.max_dd_pct),
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

    momentum_optimized = None
    if momentum_param.chosen is not None:
        momentum_optimized = mom_eng.run(
            build_momentum_strategy(momentum_param.chosen.params, momentum_symbols),
            risk_policy=private,
            num_trials=momentum_trial_count,
            cost_stress_multiplier=cost_stress,
        )

    eligible_returns = {}
    if rotation_optimized is not None and rotation_optimized.gate_ok:
        eligible_returns["rotation_risk_budgeted"] = rotation_optimized.returns
    if momentum_optimized is not None and momentum_optimized.gate_ok:
        eligible_returns["cross_asset_momentum_risk_budgeted"] = momentum_optimized.returns

    portfolio = None
    if eligible_returns:
        returns = pd.concat(
            [series.rename(name) for name, series in eligible_returns.items()],
            axis=1,
        ).dropna()
        if len(returns) >= 50:
            portfolio = RobustPortfolioOptimizer(
                dd_cap_pct=private.max_dd_pct,
                n_candidates=1600,
                bootstrap_reps=150,
                block=20,
                max_weight=1.0 if len(eligible_returns) == 1 else 0.85,
            ).optimize(returns)

    strategies = {
        "rotation_raw_diagnostic": rotation_raw.summary(),
        "cross_asset_momentum_raw_diagnostic": momentum_raw.summary(),
    }
    if rotation_optimized is not None:
        strategies["rotation_risk_budgeted"] = rotation_optimized.summary()
    if momentum_optimized is not None:
        strategies["cross_asset_momentum_risk_budgeted"] = momentum_optimized.summary()

    payload = {
        "protocol": "alpha_generation_v4",
        "stage": "development_only",
        "data_end": max(frame.index.max().strftime("%Y-%m-%d") for frame in data.values()),
        "hidden_validation_opened": False,
        "final_oos_opened": False,
        "assets": sorted(data),
        "risk_profile": {
            "name": private.name,
            "max_dd_pct": private.max_dd_pct,
            "min_psr": private.min_psr,
            "min_dsr": private.min_dsr,
            "cost_stress_multiplier": cost_stress,
        },
        "feature_manifest": store.manifest,
        "strategies": strategies,
        "rotation_parameter_optimizer": rotation_param.to_dict(),
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
