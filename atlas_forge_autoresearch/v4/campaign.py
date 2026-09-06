"""AUTORESEARCH v4 alpha-generation orchestration."""
from __future__ import annotations

from pathlib import Path
from typing import Mapping
import json

import numpy as np
import pandas as pd

from .alpha_objective import RiskPolicy
from .feature_store import FeatureStoreBuilder
from .intraday_protocol import IntradayProtocol, assert_intraday_data
from .meta_filter import BoostedStumpMetaFilter
from .motif_library import MotifEvidence, MotifTransferPlanner
from .multi_asset_engine import MultiAssetBacktester, PortfolioLimits, leveraged_regime_rotation
from .parameter_optimizer import ParameterSpec, StableParameterOptimizer
from .portfolio_optimizer import RobustPortfolioOptimizer
from .regime_engine import RegimeEngine
from .research_allocator import ResearchAllocator, ResearchCell, ResearchObservation
from .strategy_examples import cross_sectional_momentum_rotation

HERE = Path(__file__).resolve().parent
CONFIG = HERE / "config.json"


def load_config(path: str | Path = CONFIG) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def risk_policy(name: str, config: dict | None = None) -> RiskPolicy:
    cfg = (config or load_config())["risk_profiles"][name]
    return RiskPolicy(name=name, **cfg)


def assert_v4_data_boundary(
    market_data: Mapping[str, pd.DataFrame], *, stage: str = "development", config: dict | None = None
) -> None:
    cfg = config or load_config()
    bounds = cfg["boundaries"]
    if stage in {"development", "search", "fit"}:
        end = pd.Timestamp(bounds["development_end"])
    elif stage == "validation":
        end = pd.Timestamp(bounds["hidden_validation_end"])
    else:
        raise ValueError(f"unknown stage {stage}")
    final = pd.Timestamp(bounds["final_oos_start"])
    for symbol, frame in market_data.items():
        if not isinstance(frame.index, pd.DatetimeIndex):
            raise ValueError(f"{symbol}: DatetimeIndex required")
        idx = frame.index.tz_localize(None) if frame.index.tz is not None else frame.index
        if len(idx) and idx.max().normalize() > end:
            raise RuntimeError(f"{symbol}: {stage} data crosses sealed boundary {end.date()}")
        if len(idx) and idx.max().normalize() >= final:
            raise RuntimeError(f"{symbol}: final OOS contamination")


class V4AlphaCampaign:
    def __init__(self, config: dict | None = None):
        self.config = config or load_config()
        if self.config.get("protocol") != "alpha_generation_v4":
            raise ValueError("wrong v4 protocol")

    def prepare_research_context(
        self,
        market_data: Mapping[str, pd.DataFrame],
        *,
        periods_per_year: Mapping[str, float] | None = None,
        contexts: Mapping[str, pd.DataFrame] | None = None,
        context_lags: Mapping[str, int] | None = None,
    ) -> dict:
        assert_v4_data_boundary(market_data, stage="development", config=self.config)
        store = FeatureStoreBuilder(periods_per_year).build(
            market_data, contexts=contexts, context_lags=context_lags
        )
        regimes = RegimeEngine().build(market_data, store.by_asset)
        enriched = {}
        for symbol, feat in store.by_asset.items():
            enriched[symbol] = feat.join(regimes[symbol], how="left")
        return {"feature_store": store, "regimes": regimes, "features": enriched}

    def backtest_multi_asset(self, market_data, strategy, *, profile="private", periods_per_year=252.0):
        assert_v4_data_boundary(market_data, stage="development", config=self.config)
        pol = risk_policy(profile, self.config)
        limits = PortfolioLimits(
            gross_leverage=float(pol.max_gross_exposure or (2.0 if profile == "private" else 1.0)),
            net_min=-1.0,
            net_max=float(pol.max_gross_exposure or 1.0),
            per_asset_abs_weight=float(pol.max_gross_exposure or 1.0),
        )
        engine = MultiAssetBacktester(market_data, limits=limits, periods_per_year=periods_per_year)
        return engine.run(strategy, risk_policy=pol)


def synthetic_daily_market(seed: int = 7, bars: int = 900) -> dict[str, pd.DataFrame]:
    rng = np.random.default_rng(seed)
    idx = pd.bdate_range("2017-01-02", periods=bars)
    common = rng.normal(0.00035, 0.009, bars)
    qqq_r = common + rng.normal(0.00015, 0.004, bars)
    spy_r = 0.75 * common + rng.normal(0.0001, 0.0035, bars)
    tqqq_r = 2.7 * qqq_r - 0.0003 + rng.normal(0, 0.004, bars)
    btc_r = rng.normal(0.001, 0.025, bars) + 0.25 * common

    def frame(rets, start, volume_scale):
        close = start * np.cumprod(1.0 + rets)
        prev = np.r_[close[0] / (1 + rets[0]), close[:-1]]
        open_ = prev * (1.0 + rng.normal(0, 0.0015, bars))
        high = np.maximum(open_, close) * (1.0 + rng.uniform(0.001, 0.012, bars))
        low = np.minimum(open_, close) * (1.0 - rng.uniform(0.001, 0.012, bars))
        volume = rng.lognormal(np.log(volume_scale), 0.35, bars)
        return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": volume}, index=idx)

    return {
        "QQQ": frame(qqq_r, 100.0, 4e7),
        "TQQQ": frame(tqqq_r, 25.0, 2e7),
        "SPY": frame(spy_r, 100.0, 6e7),
        "BTCUSDT": frame(btc_r, 1000.0, 8e4),
    }


def run_integration_demo(output: str | Path | None = None) -> dict:
    data = synthetic_daily_market()
    camp = V4AlphaCampaign()
    context = camp.prepare_research_context(
        data, periods_per_year={s: (365 if "BTC" in s else 252) for s in data}
    )

    engine = MultiAssetBacktester(data, limits=PortfolioLimits(gross_leverage=2.0, net_max=2.0, per_asset_abs_weight=2.0))
    rotation = leveraged_regime_rotation(
        signal_symbol="QQQ", risk_symbol="TQQQ", defensive_symbol="SPY", sma_window=175, momentum_window=126
    )
    r1 = engine.run(rotation)
    mom = cross_sectional_momentum_rotation(
        lookback=126, trend_window=200, top_k=2, eligible_symbols=("QQQ", "SPY", "BTCUSDT")
    )
    r2 = engine.run(mom)

    strategy_returns = pd.concat(
        [r1.returns.rename("leveraged_rotation"), r2.returns.rename("xsection_momentum")], axis=1
    ).dropna()
    portfolio = RobustPortfolioOptimizer(
        dd_cap_pct=32.0, n_candidates=200, bootstrap_reps=30, block=20, max_weight=1.0
    ).optimize(strategy_returns)

    def evaluate(params):
        strat = leveraged_regime_rotation(
            signal_symbol="QQQ",
            risk_symbol="TQQQ",
            defensive_symbol="SPY",
            sma_window=int(params["sma"]),
            momentum_window=int(params["mom"]),
        )
        res = engine.run(strat)
        vals = res.returns.to_numpy()
        chunks = np.array_split(vals, 4)
        fold_scores = [float(np.mean(x) / (np.std(x, ddof=1) + 1e-12) * np.sqrt(252)) for x in chunks if len(x) > 5]
        return {
            "fold_scores": fold_scores,
            "gate_ok": np.isfinite(res.metrics.cagr_pct) and abs(min(res.metrics.max_dd_pct, 0.0)) <= 50.0,
            "structural_fingerprint": "leveraged_regime_rotation_v1",
        }

    param = StableParameterOptimizer(
        [ParameterSpec("sma", (150, 175, 200)), ParameterSpec("mom", (60, 126, 200))],
        max_trials=20,
    ).optimize(evaluate, frozen_structure="leveraged_regime_rotation_v1")

    feat = context["features"]["QQQ"][["ret_20", "rv_20", "rsi_2", "dist_sma_200"]].dropna()
    label = (data["QQQ"]["Open"].shift(-2) / data["QQQ"]["Open"].shift(-1) - 1.0).reindex(feat.index) > 0
    aligned = feat.loc[label.dropna().index]
    yy = label.loc[aligned.index].astype(int)
    cut = min(max(100, len(aligned) // 2), len(aligned) - 1)
    meta = BoostedStumpMetaFilter(n_estimators=6).fit(aligned.iloc[:cut], yy.iloc[:cut])
    meta_prob = meta.predict_proba(aligned.iloc[cut:])[:, 1]

    motif_plan = MotifTransferPlanner([
        MotifEvidence("long_term_trend_gate", "sentinel63", "crypto", "private", True, 0.13),
        MotifEvidence("atr_trailing_exit", "ibs_deep_pullback", "etf", "private", True, 0.064),
    ]).plan(
        [{"id": "sentinel63", "markets": ["crypto", "etf"]}, {"id": "ibs_deep_pullback", "markets": ["etf"]}],
        ["crypto", "etf"],
    )

    cells = [
        ResearchCell("sentinel63", "crypto", "private", "long_term_trend_gate"),
        ResearchCell("ibs_deep_pullback", "etf", "private", "atr_trailing_exit"),
        ResearchCell("xsection_momentum", "multi_asset", "private", "base"),
    ]
    obs = [
        ResearchObservation(cells[0].id, True, 0.13),
        ResearchObservation(cells[0].id, False, 0.0),
        ResearchObservation(cells[1].id, True, 0.064),
        ResearchObservation(cells[1].id, True, 0.03),
    ]
    chosen_cell, alloc = ResearchAllocator(min_visits_per_cell=1).select(cells, obs, decision_counter=3)

    idx = pd.date_range("2020-01-01", periods=200, freq="60min", tz="UTC")
    p = 100 * np.cumprod(1 + np.random.default_rng(8).normal(0, 0.002, len(idx)))
    intraday = pd.DataFrame({"Open": p, "High": p*1.001, "Low": p*0.999, "Close": p}, index=idx)
    assert_intraday_data(intraday, IntradayProtocol(), stage="development")

    summary = {
        "protocol": "alpha_generation_v4",
        "v3_outcomes_touched": False,
        "final_oos_opened": False,
        "feature_assets": sorted(context["features"]),
        "feature_manifest": context["feature_store"].manifest,
        "rotation": r1.summary(),
        "cross_sectional": r2.summary(),
        "portfolio": portfolio.to_dict(),
        "parameter_optimizer": param.to_dict(),
        "meta_filter": {
            "stumps": len(meta.stumps),
            "feature_importance": meta.feature_importance(),
            "holdout_mean_probability": float(np.mean(meta_prob)) if len(meta_prob) else None,
        },
        "motif_transfer_top": motif_plan[:5],
        "research_allocator": {"chosen_cell": chosen_cell.id, **alloc},
        "intraday_protocol_checked": True,
    }
    if output is not None:
        pth = Path(output)
        pth.parent.mkdir(parents=True, exist_ok=True)
        pth.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


if __name__ == "__main__":
    import argparse
    ap = argparse.ArgumentParser()
    ap.add_argument("--demo", action="store_true")
    ap.add_argument("--output", default="v4_state/integration-demo.json")
    args = ap.parse_args()
    if not args.demo:
        raise SystemExit("v4 campaign currently requires --demo or library integration")
    out = run_integration_demo(args.output)
    print(json.dumps({"protocol": out["protocol"], "final_oos_opened": out["final_oos_opened"]}, indent=2))
