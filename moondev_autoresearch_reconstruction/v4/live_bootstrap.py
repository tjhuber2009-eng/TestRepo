"""Development-only v4 bootstrap using real prepared daily market CSVs."""
from __future__ import annotations

from pathlib import Path
import argparse
import json

import numpy as np
import pandas as pd

from .campaign import assert_v4_data_boundary
from .feature_store import FeatureStoreBuilder
from .multi_asset_engine import MultiAssetBacktester, PortfolioLimits, leveraged_regime_rotation
from .parameter_optimizer import ParameterSpec, StableParameterOptimizer
from .portfolio_optimizer import RobustPortfolioOptimizer
from .strategy_examples import cross_sectional_momentum_rotation


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
    return x[[c for c in ["Open","High","Low","Close","Volume"] if c in x.columns]]


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
    if not {"QQQ","TQQQ","SPY"}.issubset(out):
        raise RuntimeError("QQQ/TQQQ/SPY development data are required")
    return out


def run(data_dir: str | Path, output: str | Path) -> dict:
    data = load_data(Path(data_dir))
    assert_v4_data_boundary(data, stage="development")
    store = FeatureStoreBuilder({s:(365 if s.endswith("USDT") else 252) for s in data}).build(data)

    core = {s:data[s] for s in ("QQQ","TQQQ","SPY")}
    eng = MultiAssetBacktester(core, limits=PortfolioLimits(gross_leverage=2.0, net_max=2.0, per_asset_abs_weight=2.0))

    baseline = eng.run(leveraged_regime_rotation(
        signal_symbol="QQQ", risk_symbol="TQQQ", defensive_symbol="SPY",
        sma_window=175, momentum_window=126,
    ))

    def evaluate(params):
        res = eng.run(leveraged_regime_rotation(
            signal_symbol="QQQ", risk_symbol="TQQQ", defensive_symbol="SPY",
            sma_window=int(params["sma"]), momentum_window=int(params["mom"]),
        ))
        arr = res.returns.to_numpy(dtype=float)
        folds = []
        for x in np.array_split(arr, 5):
            if len(x) < 20:
                continue
            sd = np.std(x, ddof=1)
            folds.append(float(np.mean(x)/sd*np.sqrt(252)) if sd>0 else -99.0)
        return {
            "fold_scores":folds,
            "gate_ok": np.isfinite(res.metrics.cagr_pct) and abs(min(res.metrics.max_dd_pct,0)) <= 32.0,
            "structural_fingerprint":"leveraged_regime_rotation_v1",
        }

    param = StableParameterOptimizer([
        ParameterSpec("sma",(150,175,200,225)),
        ParameterSpec("mom",(60,126,200)),
    ],max_trials=32).optimize(evaluate,frozen_structure="leveraged_regime_rotation_v1")

    if param.chosen is not None:
        chosen_strategy = leveraged_regime_rotation(
            signal_symbol="QQQ", risk_symbol="TQQQ", defensive_symbol="SPY",
            sma_window=int(param.chosen.params["sma"]), momentum_window=int(param.chosen.params["mom"]),
        )
        optimized = eng.run(chosen_strategy)
    else:
        optimized = baseline

    momentum_assets = {s:data[s] for s in data if s in {"QQQ","SPY","BTCUSDT","ETHUSDT"}}
    mom_eng = MultiAssetBacktester(momentum_assets, limits=PortfolioLimits(gross_leverage=1.0, net_min=0, net_max=1, per_asset_abs_weight=1.0), periods_per_year=252)
    momentum = mom_eng.run(cross_sectional_momentum_rotation(
        lookback=126, trend_window=200, top_k=min(2,len(momentum_assets)), eligible_symbols=tuple(momentum_assets)
    ))

    returns = pd.concat([
        baseline.returns.rename("rotation_baseline"),
        optimized.returns.rename("rotation_stable_params"),
        momentum.returns.rename("cross_asset_momentum"),
    ],axis=1).dropna()
    portfolio = RobustPortfolioOptimizer(
        dd_cap_pct=32.0,
        n_candidates=1200,
        bootstrap_reps=100,
        block=20,
        max_weight=0.80,
    ).optimize(returns)

    payload = {
        "protocol":"alpha_generation_v4",
        "stage":"development_only",
        "data_end":max(frame.index.max().strftime("%Y-%m-%d") for frame in data.values()),
        "hidden_validation_opened":False,
        "final_oos_opened":False,
        "assets":sorted(data),
        "feature_manifest":store.manifest,
        "strategies":{
            "rotation_baseline":baseline.summary(),
            "rotation_stable_params":optimized.summary(),
            "cross_asset_momentum":momentum.summary(),
        },
        "parameter_optimizer":param.to_dict(),
        "portfolio":portfolio.to_dict(),
    }
    out=Path(output)
    out.parent.mkdir(parents=True,exist_ok=True)
    out.write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    return payload


if __name__ == "__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--data-dir",default="data")
    ap.add_argument("--output",default="v4_state/development-bootstrap.json")
    args=ap.parse_args()
    payload=run(args.data_dir,args.output)
    print(json.dumps({"stage":payload["stage"],"assets":payload["assets"],"final_oos_opened":payload["final_oos_opened"]},indent=2))
