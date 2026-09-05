from prepare_market_data import yahoo_rows_from_chart
import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from v4.account_profiles import FTMO_1STEP, FTMO_2STEP, PropStageRule
from v4.alpha_objective import RiskPolicy, hard_gate, metrics_from_equity, pareto_frontier
from v4.campaign import assert_v4_data_boundary, run_integration_demo, synthetic_daily_market
from v4.feature_store import FeatureStoreBuilder
from v4.intraday_protocol import IntradayProtocol, assert_intraday_data
from v4.live_bootstrap import build_rsi2_pullback_strategy, json_safe, pbo_gate, read_market_csv, select_portfolio_history_cohort
from v4.meta_filter import BoostedStumpMetaFilter, walk_forward_probabilities
from v4.motif_library import MotifEvidence, MotifTransferPlanner
from v4.multi_asset_engine import (
    MultiAssetBacktester,
    PortfolioLimits,
    leveraged_hysteresis_rotation,
)
from v4.parameter_optimizer import ParameterSpec, StableParameterOptimizer
from v4.portfolio_optimizer import RobustPortfolioOptimizer
from v4.prop_firm_engine import (
    FundedSimulation,
    PropOptimizationCandidate,
    StageSimulation,
    _candidate_within_risk_tier,
    _simulate_one_stage,
    active_day_proxy,
    daily_adverse_proxy,
    optimize_prop_exposure,
    repeat_payout_projection,
    simulate_stage,
)
from v4.prop_intraday_bootstrap import (
    PRAGUE,
    _frontier_day_brake_mutations,
    _frontier_structural_mutations,
    _frontier_universe_mutations,
    _resolve_prop_symbols,
    aggregate_prague_days,
    aggregate_prague_days_scaled,
    hourly_rotation_strategy,
    hourly_tsmom_strategy,
    program_with_analysis_horizon,
    read_hourly,
)
from v4.regime_engine import RegimeEngine
from v4.risk_overlays import vix_stress_overlay, volatility_target_overlay
from v4.selection_diagnostics import cscv_pbo
from v4.research_allocator import ResearchAllocator, ResearchCell, ResearchObservation
from v4.strategy_examples import independent_trend_basket, leveraged_defensive_rotation, pead_event_weights
from v4.strategy_intake import HypothesisQueue, StrategyHypothesis


class V4AlphaGenerationTests(unittest.TestCase):
    def test_config_is_v4_and_keeps_oos_sealed(self):
        cfg = json.loads((Path(__file__).parent / "v4" / "config.json").read_text())
        self.assertEqual(cfg["protocol"], "alpha_generation_v4")
        self.assertTrue(cfg["boundaries"]["final_oos_sealed"])
        self.assertEqual(cfg["boundaries"]["final_oos_start"], "2023-01-01")
        self.assertEqual(cfg["objective"]["primary"], "maximize_sustainable_cagr_subject_to_hard_risk_and_evidence_gates")

    def test_family_pbo_gate_rejects_missing_diagnostic(self):
        self.assertFalse(pbo_gate(None, 0.25))
        self.assertTrue(pbo_gate({"pbo": 0.20}, 0.25))
        self.assertFalse(pbo_gate({"pbo": 0.30}, 0.25))

    def test_v4_data_boundary_rejects_hidden_and_final_rows_during_search(self):
        data = synthetic_daily_market(bars=200)
        frame = data["QQQ"].copy()
        frame.index = pd.date_range("2022-06-01", periods=len(frame), freq="D")
        with self.assertRaises(RuntimeError):
            assert_v4_data_boundary({"QQQ": frame}, stage="development")

    def test_yahoo_split_dividend_rows_ignore_adjusted_close(self):
        stamps = [
            int(pd.Timestamp("2020-01-01T14:30:00Z").timestamp()),
            int(pd.Timestamp("2020-01-02T14:30:00Z").timestamp()),
            int(pd.Timestamp("2020-01-03T14:30:00Z").timestamp()),
        ]
        split_ts = int(pd.Timestamp("2020-01-03T00:00:00Z").timestamp())
        div_ts = int(pd.Timestamp("2020-01-02T00:00:00Z").timestamp())
        result = {
            "timestamp": stamps,
            "indicators": {
                "quote": [{
                    "open": [100.0, 102.0, 51.0],
                    "high": [101.0, 103.0, 52.0],
                    "low": [99.0, 101.0, 50.0],
                    "close": [100.0, 102.0, 51.0],
                    "volume": [1000.0, 1200.0, 2500.0],
                }],
                # Deliberately absurd mutable adjusted close. Stable mode
                # must not depend on it.
                "adjclose": [{"adjclose": [1.0, 2.0, 3.0]}],
            },
            "events": {
                "splits": {
                    str(split_ts): {
                        "date": split_ts,
                        "numerator": 2.0,
                        "denominator": 1.0,
                        "splitRatio": "2:1",
                    }
                },
                "dividends": {
                    str(div_ts): {
                        "date": div_ts,
                        "amount": 1.0,
                    }
                },
            },
        }
        rows = yahoo_rows_from_chart(
            result,
            adjustment="provider_split_adjusted_v3",
        )
        # Yahoo quote OHLC are already split-adjusted. Never apply the split
        # event a second time.
        self.assertAlmostEqual(rows[0][1], 100.0)
        self.assertAlmostEqual(rows[1][4], 102.0)
        self.assertAlmostEqual(rows[2][1], 51.0)
        self.assertAlmostEqual(rows[0][5], 1000.0)
        self.assertAlmostEqual(rows[1][6], 1.0)

    def test_multi_asset_open_return_includes_next_day_dividend(self):
        idx = pd.date_range("2020-01-01", periods=3, freq="D")
        frame = pd.DataFrame(
            {
                "Open": [100.0, 100.0, 100.0],
                "High": [101.0, 101.0, 101.0],
                "Low": [99.0, 99.0, 99.0],
                "Close": [100.0, 100.0, 100.0],
                "Dividend": [0.0, 1.0, 0.0],
            },
            index=idx,
        )
        engine = MultiAssetBacktester(
            {"A": frame},
            periods_per_year=252,
        )
        r = engine.open_to_next_open_returns()
        self.assertAlmostEqual(float(r.loc[idx[0], "A"]), 0.01)
        self.assertAlmostEqual(float(r.loc[idx[1], "A"]), 0.0)

    def test_multi_asset_signals_execute_next_open(self):
        idx = pd.date_range("2020-01-01", periods=6, freq="D")
        close = np.array([100, 100, 100, 100, 100, 100], dtype=float)
        open_ = np.array([100, 110, 121, 133.1, 146.41, 161.051], dtype=float)
        frame = pd.DataFrame({"Open": open_, "High": np.maximum(open_, close)+1, "Low": np.minimum(open_, close)-1, "Close": close}, index=idx)
        engine = MultiAssetBacktester({"A": frame}, limits=PortfolioLimits(gross_leverage=1, net_min=0, net_max=1, per_asset_abs_weight=1), periods_per_year=365)
        def strat(data, features=None):
            w = pd.DataFrame(0.0, index=idx, columns=["A"])
            w.iloc[1:, 0] = 1.0
            return w
        res = engine.run(strat)
        self.assertEqual(res.execution_weights.iloc[1, 0], 0.0)
        self.assertEqual(res.execution_weights.iloc[2, 0], 1.0)
        self.assertGreater(res.returns.iloc[2], 0.09)

    def test_cost_stress_is_reported_and_worse_than_base_when_turnover_exists(self):
        idx = pd.date_range("2019-01-01", periods=180, freq="D")
        r = np.where(np.arange(len(idx)) % 2 == 0, 0.01, -0.004)
        open_ = 100.0 * np.cumprod(1.0 + r)
        frame = pd.DataFrame({
            "Open": open_,
            "High": open_ * 1.01,
            "Low": open_ * 0.99,
            "Close": open_,
        }, index=idx)
        engine = MultiAssetBacktester(
            {"A": frame},
            limits=PortfolioLimits(gross_leverage=1, net_min=0, net_max=1, per_asset_abs_weight=1),
            periods_per_year=365,
        )
        def strat(data, features=None):
            w = pd.DataFrame(0.0, index=idx, columns=["A"])
            w.iloc[::2, 0] = 1.0
            return w
        res = engine.run(strat, cost_stress_multiplier=5.0)
        self.assertIsNotNone(res.metrics.cost_stress_cagr_pct)
        self.assertLess(res.metrics.cost_stress_cagr_pct, res.metrics.cagr_pct)

    def test_hysteresis_rotation_retains_state_inside_band(self):
        idx = pd.date_range("2020-01-01", periods=8, freq="D")
        close = np.array([100, 100, 100, 104, 102, 101, 98, 96], dtype=float)
        frame = pd.DataFrame(
            {
                "Open": close,
                "High": close + 1.0,
                "Low": close - 1.0,
                "Close": close,
            },
            index=idx,
        )
        strat = leveraged_hysteresis_rotation(
            signal_symbol="QQQ",
            risk_symbol="TQQQ",
            defensive_symbol="SPY",
            sma_window=3,
            entry_band=0.02,
            exit_band=0.02,
        )
        weights = strat(
            {"QQQ": frame, "TQQQ": frame, "SPY": frame},
            None,
        )
        self.assertEqual(float(weights.loc[idx[3], "TQQQ"]), 1.0)
        self.assertEqual(float(weights.loc[idx[4], "TQQQ"]), 1.0)
        self.assertEqual(float(weights.loc[idx[-1], "SPY"]), 1.0)

    def test_hysteresis_rotation_is_prefix_invariant(self):
        idx = pd.date_range("2020-01-01", periods=120, freq="D")
        close = (
            100.0
            + np.sin(np.arange(len(idx)) / 6.0) * 8.0
            + np.arange(len(idx)) * 0.05
        )
        frame = pd.DataFrame(
            {
                "Open": close,
                "High": close + 1.0,
                "Low": close - 1.0,
                "Close": close,
            },
            index=idx,
        )
        strat = leveraged_hysteresis_rotation(
            signal_symbol="QQQ",
            risk_symbol="TQQQ",
            defensive_symbol="SPY",
            sma_window=20,
            entry_band=0.02,
            exit_band=0.02,
        )
        short = {s: frame.iloc[:80] for s in ("QQQ", "TQQQ", "SPY")}
        full = {s: frame for s in ("QQQ", "TQQQ", "SPY")}
        a = strat(short, None)
        b = strat(full, None)
        pd.testing.assert_frame_equal(a, b.loc[a.index])

    def test_volatility_target_overlay_is_prefix_invariant(self):
        data = synthetic_daily_market(bars=500)
        def base(d, features=None):
            idx = next(iter(d.values())).index
            return pd.DataFrame({"QQQ": 1.0}, index=idx)
        overlay = volatility_target_overlay(
            base, target_vol=0.15, periods_per_year=252, lookback=20, max_gross=1.0
        )
        a = overlay({"QQQ": data["QQQ"].iloc[:350]})
        b = overlay({"QQQ": data["QQQ"]})
        pd.testing.assert_frame_equal(a, b.loc[a.index])

    def test_long_history_trend_basket_is_prefix_invariant(self):
        idx = pd.date_range("2018-01-01", periods=320, freq="B")
        data = {}
        for j, symbol in enumerate(("SPY", "IEF", "GLD")):
            close = pd.Series(
                100.0 + j * 10.0 + np.linspace(0.0, 25.0 + j * 3.0, len(idx)),
                index=idx,
            )
            data[symbol] = pd.DataFrame({
                "Open": close,
                "High": close * 1.001,
                "Low": close * 0.999,
                "Close": close,
            }, index=idx)
        strategy = independent_trend_basket(
            symbols=("SPY", "IEF", "GLD"),
            momentum_window=126,
            trend_window=150,
        )
        full = strategy(data)
        cut = 260
        prefix_data = {k: v.iloc[:cut].copy() for k, v in data.items()}
        prefix = strategy(prefix_data)
        pd.testing.assert_frame_equal(full.iloc[:cut], prefix)

    def test_rsi2_pullback_is_prefix_invariant(self):
        data = synthetic_daily_market(bars=650)
        qqq = data["QQQ"].copy()
        tqqq = qqq.copy()
        tqqq[["Open", "High", "Low", "Close"]] *= 1.5
        core = {"QQQ": qqq, "TQQQ": tqqq}
        features = FeatureStoreBuilder().build(core).by_asset
        strategy = build_rsi2_pullback_strategy({
            "entry_rsi": 10.0,
            "target_vol": 0.20,
            "vol_lookback": 20,
        })
        short_data = {k: v.iloc[:500] for k, v in core.items()}
        short_features = {k: v.iloc[:500] for k, v in features.items()}
        a = strategy(short_data, short_features)
        b = strategy(core, features)
        pd.testing.assert_frame_equal(a, b.loc[a.index])

    def test_vix_stress_overlay_is_prefix_invariant(self):
        data = synthetic_daily_market(bars=600)
        idx = data["QQQ"].index
        vix = pd.Series(
            20.0 + 8.0 * np.sin(np.arange(len(idx)) / 17.0),
            index=idx,
        )
        def base(d, features=None):
            ii = next(iter(d.values())).index
            return pd.DataFrame({"QQQ": 1.0}, index=ii)
        overlay = vix_stress_overlay(
            base,
            vix,
            stress_quantile=0.80,
            severe_quantile=0.95,
            stress_scale=0.5,
            severe_scale=0.0,
            min_history=100,
        )
        a = overlay({"QQQ": data["QQQ"].iloc[:450]})
        b = overlay({"QQQ": data["QQQ"]})
        pd.testing.assert_frame_equal(a, b.loc[a.index])

    def test_strict_v4_json_sanitizes_nonfinite_values(self):
        payload = json_safe({
            "bad": float("-inf"),
            "nested": [float("nan"), 1.0],
            "sealed": False,
            "enabled": True,
        })
        raw = json.dumps(payload, allow_nan=False)
        self.assertEqual(
            json.loads(raw),
            {
                "bad": None,
                "nested": [None, 1.0],
                "sealed": False,
                "enabled": True,
            },
        )

    def test_feature_context_join_is_backward_and_lagged(self):
        data = synthetic_daily_market(bars=300)
        idx = data["QQQ"].index
        ctx = pd.DataFrame({"vix": np.arange(len(idx), dtype=float)}, index=idx)
        store = FeatureStoreBuilder().build({"QQQ": data["QQQ"]}, contexts={"risk": ctx}, context_lags={"risk": 1})
        feat = store.by_asset["QQQ"]
        self.assertTrue(np.isnan(feat.iloc[0]["ctx_risk__vix"]))
        self.assertEqual(feat.iloc[10]["ctx_risk__vix"], 9.0)

    def test_daily_loader_normalizes_provider_timestamps_to_calendar_dates(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "daily.csv"
            p.write_text(
                "Date,Open,High,Low,Close,Volume\n"
                "2020-01-02T14:30:00+00:00,100,102,99,101,1000\n"
                "2020-01-03T00:00:00+00:00,101,103,100,102,1100\n",
                encoding="utf-8",
            )
            x = read_market_csv(p)
            self.assertEqual(list(x.index), [
                pd.Timestamp("2020-01-02"),
                pd.Timestamp("2020-01-03"),
            ])
            self.assertIsNone(x.index.tz)

    def test_feature_store_accepts_named_date_indexes(self):
        data = synthetic_daily_market(bars=300)
        for frame in data.values():
            frame.index.name = "Date"
        store = FeatureStoreBuilder().build({"QQQ": data["QQQ"], "SPY": data["SPY"]})
        self.assertEqual(store.cross_sectional.index.names, ["ts", "symbol"])
        self.assertIn("xrank_ret_20", store.cross_sectional.columns)

    def test_regime_prefix_invariant_to_future_append(self):
        data = synthetic_daily_market(bars=700)["QQQ"]
        engine = RegimeEngine()
        a = engine.label_asset(data.iloc[:500])
        b = engine.label_asset(data)
        pd.testing.assert_series_equal(a["vol_regime"], b.loc[a.index, "vol_regime"])

    def test_meta_filter_is_shallow_and_walk_forward(self):
        rng = np.random.default_rng(1)
        idx = pd.RangeIndex(300)
        x = pd.DataFrame({"a": rng.normal(size=300), "b": rng.normal(size=300)}, index=idx)
        y = ((x["a"] + 0.3*x["b"]) > 0).astype(int)
        model = BoostedStumpMetaFilter(n_estimators=8).fit(x.iloc[:200], y.iloc[:200])
        p = model.predict_proba(x.iloc[200:])[:, 1]
        self.assertGreater(((p >= .5) == y.iloc[200:].to_numpy()).mean(), 0.7)
        wf = walk_forward_probabilities(x, y, min_train=100, retrain_every=25, n_estimators=5)
        self.assertTrue(wf.iloc[:100].isna().all())
        self.assertTrue(wf.iloc[150:].notna().any())

    def test_parameter_optimizer_freezes_structure_and_prefers_stability(self):
        opt = StableParameterOptimizer([ParameterSpec("x", (1,2,3,4,5))], max_trials=10, plateau_neighbors=2)
        def evaluator(p):
            scores = {1:[0.1,0.1,0.1],2:[0.5,0.5,0.5],3:[0.55,0.54,0.56],4:[0.5,0.5,0.5],5:[0.1,0.1,0.1]}[p["x"]]
            return {"fold_scores":scores,"gate_ok":True,"structural_fingerprint":"S"}
        result = opt.optimize(evaluator, frozen_structure="S")
        self.assertIn(result.chosen.params["x"], {2,3,4})
        with self.assertRaises(RuntimeError):
            opt.optimize(lambda p:{"fold_scores":[1,1],"gate_ok":True,"structural_fingerprint":"CHANGED"}, frozen_structure="S")

    def test_parameter_optimizer_prioritizes_primary_score_after_gate(self):
        opt = StableParameterOptimizer(
            [ParameterSpec("x", (1, 2, 3))],
            max_trials=5,
            plateau_neighbors=1,
        )
        def evaluator(p):
            return {
                "fold_scores": [100.0, 100.0, 100.0] if p["x"] == 1 else [10.0, 10.0, 10.0],
                "primary_score": {1: 5.0, 2: 20.0, 3: 15.0}[p["x"]],
                "gate_ok": True,
                "structural_fingerprint": "S",
            }
        result = opt.optimize(evaluator, frozen_structure="S")
        self.assertEqual(result.chosen.params["x"], 2)
        self.assertEqual(result.chosen.primary_score, 20.0)

    def test_portfolio_history_cohort_excludes_short_history_from_core(self):
        idx = pd.date_range("2010-01-01", periods=2600, freq="B")
        long_a = pd.Series(0.001, index=idx)
        long_b = pd.Series(0.0005, index=idx)
        short = pd.Series(0.002, index=idx[-800:])
        core, meta = select_portfolio_history_cohort({
            "long_a": long_a,
            "long_b": long_b,
            "short_crypto": short,
        })
        self.assertEqual(set(core), {"long_a", "long_b"})
        self.assertEqual(
            meta["supplemental_strategy_names"],
            ["short_crypto"],
        )
        self.assertGreaterEqual(meta["core_min_years"], 8.0)
        self.assertLess(meta["history"]["short_crypto"]["years"], 4.0)

    def test_portfolio_optimizer_can_choose_diversification(self):
        rng = np.random.default_rng(4)
        a = rng.normal(0.0007,0.015,500)
        b = -0.5*a + rng.normal(0.0007,0.010,500)
        ret = pd.DataFrame({"A":a,"B":b})
        result = RobustPortfolioOptimizer(
            dd_cap_pct=35,
            n_candidates=300,
            bootstrap_reps=30,
            max_weight=1.0,
            max_gross=1.5,
            seed=2,
        ).optimize(ret)
        self.assertIsNotNone(result.chosen)
        self.assertLessEqual(result.chosen.bootstrap_dd_q95_pct, 35)
        self.assertLessEqual(result.chosen.gross_exposure, 1.5 + 1e-12)
        self.assertGreaterEqual(result.chosen.effective_n, 1.0)

    def test_portfolio_max_weight_applies_to_seed_candidates(self):
        optimizer = RobustPortfolioOptimizer(
            dd_cap_pct=35,
            n_candidates=60,
            bootstrap_reps=10,
            max_weight=0.50,
            seed=7,
        )
        rows = optimizer._candidate_compositions(
            3, np.random.default_rng(7)
        )
        self.assertEqual(len(rows), 60)
        self.assertTrue(
            all(float(np.max(w)) <= 0.50 + 1e-12 for w in rows)
        )

    def test_portfolio_bootstrap_rng_is_independent_of_search_rng_consumption(self):
        class BurnSearchRngOptimizer(RobustPortfolioOptimizer):
            def __init__(self, *args, burn=0, **kwargs):
                super().__init__(*args, **kwargs)
                self.burn = int(burn)

            def _candidate_compositions(self, n, rng):
                if self.burn:
                    rng.random(self.burn)
                return [np.full(n, 1.0 / n)]

        rng = np.random.default_rng(2026)
        ret = pd.DataFrame({
            "A": rng.normal(0.0005, 0.012, 700),
            "B": rng.normal(0.0004, 0.010, 700),
            "C": rng.normal(0.0003, 0.008, 700),
        })
        common = dict(
            dd_cap_pct=35,
            n_candidates=1,
            bootstrap_reps=40,
            block=20,
            max_weight=1.0,
            max_gross=1.2,
            seed=17,
        )
        a = BurnSearchRngOptimizer(**common, burn=0).optimize(ret).chosen
        b = BurnSearchRngOptimizer(**common, burn=5000).optimize(ret).chosen
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertAlmostEqual(
            a.bootstrap_median_cagr_pct,
            b.bootstrap_median_cagr_pct,
            places=12,
        )
        self.assertAlmostEqual(
            a.bootstrap_dd_q95_pct,
            b.bootstrap_dd_q95_pct,
            places=12,
        )

    def test_portfolio_optimizer_uses_cash_when_full_investment_breaks_dd_cap(self):
        rng = np.random.default_rng(12)
        r = rng.normal(0.0010, 0.03, 600)
        ret = pd.DataFrame({"volatile_alpha": r})
        result = RobustPortfolioOptimizer(
            dd_cap_pct=20,
            n_candidates=1,
            bootstrap_reps=40,
            max_weight=1.0,
            max_gross=1.0,
            min_gross=0.05,
            seed=9,
        ).optimize(ret)
        self.assertIsNotNone(result.chosen)
        self.assertLess(result.chosen.gross_exposure, 1.0)
        self.assertGreater(result.chosen.cash_weight, 0.0)
        self.assertLessEqual(result.chosen.bootstrap_dd_q95_pct, 20)

    def test_meta_filter_label_delay_blocks_unavailable_previous_outcome(self):
        rng = np.random.default_rng(123)
        idx = pd.RangeIndex(180)
        x = pd.DataFrame({
            "a": rng.normal(size=len(idx)),
            "b": rng.normal(size=len(idx)),
        }, index=idx)
        y = ((x["a"].shift(-2).fillna(0.0)) > 0.0).astype(int)
        p1 = walk_forward_probabilities(
            x, y, min_train=50, retrain_every=1, n_estimators=4, label_delay=1
        )
        y2 = y.copy()
        # At decision row 100, label row 99 is not yet observable and must not
        # affect the fitted model.
        y2.iloc[99] = 1 - int(y2.iloc[99])
        p2 = walk_forward_probabilities(
            x, y2, min_train=50, retrain_every=1, n_estimators=4, label_delay=1
        )
        self.assertAlmostEqual(float(p1.iloc[100]), float(p2.iloc[100]), places=12)

    def test_motif_transfer_reuses_successful_knowledge(self):
        planner = MotifTransferPlanner([
            MotifEvidence("long_term_trend_gate","sentinel63","crypto","private",True,0.1),
            MotifEvidence("long_term_trend_gate","sentinel63","crypto","private",True,0.2),
        ])
        rows = planner.plan([{"id":"sentinel63","markets":["crypto"]}], ["crypto"], ["private"])
        self.assertEqual(rows[0]["motif_id"], "long_term_trend_gate")

    def test_defensive_rotation_can_choose_bond_defense_or_cash(self):
        idx = pd.bdate_range("2018-01-01", periods=120)
        qqq = np.linspace(120.0, 80.0, len(idx))
        tqqq = np.linspace(90.0, 30.0, len(idx))
        ief = np.linspace(90.0, 120.0, len(idx))
        gld = np.full(len(idx), 100.0)
        shy = np.full(len(idx), 100.0)
        def frame(close):
            close = np.asarray(close, dtype=float)
            return pd.DataFrame({
                "Open": close,
                "High": close * 1.001,
                "Low": close * 0.999,
                "Close": close,
            }, index=idx)
        data = {
            "QQQ": frame(qqq),
            "TQQQ": frame(tqqq),
            "IEF": frame(ief),
            "GLD": frame(gld),
            "SHY": frame(shy),
        }
        strat = leveraged_defensive_rotation(
            signal_symbol="QQQ",
            risk_symbol="TQQQ",
            defensive_symbols=("IEF", "GLD", "SHY"),
            risk_sma_window=20,
            risk_momentum_window=10,
            defensive_momentum_window=10,
            defensive_trend_window=20,
        )
        w = strat(data)
        self.assertEqual(w.iloc[-1]["TQQQ"], 0.0)
        self.assertEqual(w.iloc[-1]["IEF"], 1.0)

    def test_v4_cscv_pbo_is_bounded_on_even_fold_matrix(self):
        matrix = np.array([
            [3, 3, 3, 3, -1, -1, -1, -1],
            [-1, -1, -1, -1, 3, 3, 3, 3],
            [2, 2, -1, -1, 2, 2, -1, -1],
            [0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4, 0.4],
            [0.3, 0.2, 0.3, 0.2, 0.3, 0.2, 0.3, 0.2],
        ], dtype=float)
        diag = cscv_pbo(matrix)
        self.assertIsNotNone(diag)
        self.assertGreaterEqual(diag["pbo"], 0.0)
        self.assertLessEqual(diag["pbo"], 1.0)
        self.assertEqual(diag["fold_count"], 8)

    def test_research_allocator_breadth_then_thompson(self):
        cells=[ResearchCell("a","crypto","private"),ResearchCell("b","crypto","private")]
        alloc=ResearchAllocator(min_visits_per_cell=1)
        chosen,info=alloc.select(cells,[],decision_counter=0)
        self.assertEqual(info["method"],"mandatory_breadth")
        obs=[ResearchObservation(cells[0].id,True,.1),ResearchObservation(cells[1].id,False,0)]
        c1,i1=alloc.select(cells,obs,decision_counter=7)
        c2,i2=alloc.select(cells,obs,decision_counter=7)
        self.assertEqual(c1.id,c2.id)
        self.assertEqual(i1["method"],"contextual_thompson")

    def test_intraday_protocol_is_separate_and_sealed(self):
        idx=pd.date_range("2020-01-01",periods=100,freq="60min",tz="UTC")
        p=np.linspace(100,110,len(idx))
        frame=pd.DataFrame({"Open":p,"High":p+1,"Low":p-1,"Close":p},index=idx)
        assert_intraday_data(frame,IntradayProtocol(),stage="development")
        bad=frame.copy()
        bad.index=pd.date_range("2023-01-01",periods=100,freq="60min",tz="UTC")
        with self.assertRaises(RuntimeError):
            assert_intraday_data(bad,IntradayProtocol(),stage="development")

    def test_external_intake_deduplicates_by_rules(self):
        h=StrategyHypothesis(source_type="reddit",source_url="https://x",title="x",markets=("SPY",),timeframe="1d",entry_rules=("RSI < 5",),exit_rules=("RSI > 65",),extraction_confidence=.8)
        h2=StrategyHypothesis(source_type="github",source_url="https://y",title="y",markets=("SPY",),timeframe="1d",entry_rules=("  rsi   < 5 ",),exit_rules=("rsi > 65",),extraction_confidence=.9)
        with tempfile.TemporaryDirectory() as td:
            q=HypothesisQueue(Path(td)/"q.json")
            result=q.add([h,h2])
            self.assertEqual(result["total"],1)

    def test_pead_event_never_starts_before_event(self):
        data=synthetic_daily_market(bars=60)
        q=data["QQQ"]
        event_ts=q.index[20]
        events=pd.DataFrame({"symbol":["QQQ"],"surprise_z":[2.0]},index=pd.DatetimeIndex([event_ts]))
        w=pead_event_weights({"QQQ":q},events,hold_bars=5,top_k=1)
        self.assertEqual(w.loc[:q.index[19],"QQQ"].sum(),0)
        self.assertEqual(w.loc[event_ts,"QQQ"],1)

    def test_alpha_objective_hard_gate_and_pareto(self):
        r=np.array([.002,-.001,.001,.0015,-.0005]*100)
        eq=np.cumprod(1+r)
        m=metrics_from_equity(eq,r,252,2,100,num_trials=5,pbo=.2,cost_stress_cagr_pct=5)
        p=RiskPolicy("x",max_dd_pct=32,min_trades=20,min_psr=.1,min_dsr=.1,max_pbo=.5)
        ok,_=hard_gate(m,p)
        self.assertTrue(ok)
        self.assertEqual(pareto_frontier({"a":m}),["a"])

    def test_ftmo_profiles_are_separate_from_private_objective(self):
        self.assertEqual(FTMO_2STEP.challenge.profit_target_pct, 10.0)
        self.assertEqual(FTMO_2STEP.verification.profit_target_pct, 5.0)
        self.assertEqual(FTMO_2STEP.challenge.max_daily_loss_pct, 5.0)
        self.assertEqual(FTMO_2STEP.challenge.max_loss_pct, 10.0)
        self.assertEqual(FTMO_1STEP.challenge.max_daily_loss_pct, 3.0)
        self.assertTrue(FTMO_1STEP.challenge.trailing_max_loss)
        self.assertEqual(FTMO_1STEP.challenge.best_day_rule_pct, 50.0)
        self.assertEqual(FTMO_1STEP.funded.best_day_rule_pct, 50.0)

    def test_prop_risk_tier_rejects_large_overall_loss_breach(self):
        challenge = StageSimulation(
            stage_id="challenge",
            exposure_scale=0.5,
            paths=1000,
            analysis_horizon_days=180,
            pass_probability=0.5,
            fail_probability=0.26,
            timeout_probability=0.24,
            daily_loss_breach_probability=0.01,
            max_loss_breach_probability=0.25,
            median_days_to_pass=80.0,
            p75_days_to_pass=120.0,
        )
        funded = FundedSimulation(
            exposure_scale=0.5,
            paths=1000,
            reward_window_days=14,
            survival_probability=0.95,
            reward_eligible_probability=0.8,
            positive_reward_probability=0.7,
            expected_reward_pct=1.0,
            median_positive_reward_pct=1.2,
            daily_loss_breach_probability=0.01,
            max_loss_breach_probability=0.01,
            best_day_ineligible_probability=0.0,
        )
        candidate = PropOptimizationCandidate(
            challenge_exposure_scale=0.5,
            verification_exposure_scale=None,
            funded_exposure_scale=0.5,
            challenge=challenge,
            verification=None,
            funded=funded,
            combined_evaluation_pass_probability=0.5,
            expected_evaluation_days_if_passed=80.0,
            payout_efficiency_score=0.01,
        )
        self.assertFalse(
            _candidate_within_risk_tier(
                candidate,
                evaluation_daily_breach_cap=0.15,
                evaluation_max_loss_breach_cap=0.15,
                funded_daily_breach_cap=0.10,
                funded_max_loss_breach_cap=0.05,
                funded_survival_floor=0.85,
            )
        )

    def test_ftmo_horizon_override_is_research_only(self):
        longer = program_with_analysis_horizon(FTMO_2STEP, 504)
        self.assertEqual(longer.challenge.analysis_horizon_days, 504)
        self.assertEqual(longer.verification.analysis_horizon_days, 504)
        self.assertEqual(
            longer.challenge.max_daily_loss_pct,
            FTMO_2STEP.challenge.max_daily_loss_pct,
        )
        self.assertEqual(
            longer.challenge.max_loss_pct,
            FTMO_2STEP.challenge.max_loss_pct,
        )
        self.assertEqual(longer.reward_share, FTMO_2STEP.reward_share)
        self.assertTrue(FTMO_2STEP.challenge_fee_refundable_with_first_reward)
        self.assertTrue(longer.challenge_fee_refundable_with_first_reward)
        self.assertFalse(FTMO_1STEP.challenge_fee_refundable_with_first_reward)
        self.assertEqual(
            FTMO_2STEP.challenge.analysis_horizon_days,
            252,
        )

    def test_repeat_payout_projection_discounts_future_cycles_by_survival(self):
        expected, score = repeat_payout_projection(
            expected_reward_pct=1.0,
            survival_probability=0.5,
            evaluation_pass_probability=0.25,
            evaluation_days=20.0,
            reward_cycle_days=14,
            cycles=3,
        )
        self.assertAlmostEqual(expected, 1.75)
        self.assertAlmostEqual(score, 0.25 * 1.75 / 62.0)
        full_survival, _ = repeat_payout_projection(
            expected_reward_pct=1.0,
            survival_probability=1.0,
            evaluation_pass_probability=1.0,
            evaluation_days=0.0,
            reward_cycle_days=14,
            cycles=3,
        )
        self.assertAlmostEqual(full_survival, 3.0)

    def test_prop_stage_rewards_lower_risk_when_daily_limit_is_tight(self):
        rng = np.random.default_rng(77)
        r = rng.normal(0.0012, 0.015, 1000)
        adverse = np.minimum(rng.normal(-0.006, 0.012, 1000), 0.0)
        active = np.ones(len(r), dtype=bool)
        result = optimize_prop_exposure(
            r,
            adverse,
            active,
            FTMO_1STEP,
            exposure_scales=(0.25, 0.50, 0.75, 1.00),
            paths=250,
            block=10,
            seed=12,
        )
        self.assertIsNotNone(result.selected)
        self.assertGreaterEqual(result.selected.challenge_exposure_scale, 0.25)
        self.assertLessEqual(result.selected.challenge_exposure_scale, 1.0)
        self.assertGreaterEqual(result.selected.funded_exposure_scale, 0.25)
        self.assertLessEqual(result.selected.funded_exposure_scale, 1.0)
        self.assertGreaterEqual(
            result.selected.combined_evaluation_pass_probability, 0.0
        )
        self.assertLessEqual(
            result.selected.combined_evaluation_pass_probability, 1.0
        )
        self.assertIn("max_payout_efficiency", result.views)
        self.assertIn("max_repeat_payout_efficiency", result.views)
        self.assertIn("max_evaluation_pass", result.views)
        self.assertIn("safest_funded", result.views)
        self.assertIn("balanced", result.views)
        self.assertIn("conservative", result.views)

    def test_prop_exposure_scale_order_does_not_change_monte_carlo_paths(self):
        rng = np.random.default_rng(314)
        r = rng.normal(0.0010, 0.012, 800)
        adverse = np.minimum(rng.normal(-0.004, 0.009, 800), 0.0)
        active = np.ones(len(r), dtype=bool)
        a = optimize_prop_exposure(
            r,
            adverse,
            active,
            FTMO_2STEP,
            exposure_scales=(0.25, 0.50, 0.75),
            paths=180,
            block=10,
            seed=991,
        )
        b = optimize_prop_exposure(
            r,
            adverse,
            active,
            FTMO_2STEP,
            exposure_scales=(0.75, 0.50, 0.25),
            paths=180,
            block=10,
            seed=991,
        )
        def by_scale(rows):
            return {round(x.exposure_scale, 8): x for x in rows}
        ac = by_scale(a.challenge_scale_table)
        bc = by_scale(b.challenge_scale_table)
        af = by_scale(a.funded_scale_table)
        bf = by_scale(b.funded_scale_table)
        for scale in ac:
            self.assertAlmostEqual(ac[scale].pass_probability, bc[scale].pass_probability)
            self.assertAlmostEqual(
                ac[scale].daily_loss_breach_probability,
                bc[scale].daily_loss_breach_probability,
            )
            self.assertAlmostEqual(
                af[scale].survival_probability,
                bf[scale].survival_probability,
            )
            self.assertAlmostEqual(
                af[scale].expected_reward_pct,
                bf[scale].expected_reward_pct,
            )

    def test_ftmo_daily_loss_subtracts_fixed_initial_capital_amount(self):
        rule = PropStageRule(
            id="test",
            profit_target_pct=50.0,
            max_daily_loss_pct=5.0,
            max_loss_pct=20.0,
            analysis_horizon_days=2,
        )
        status, days, reason = _simulate_one_stage(
            np.array([0.10, 0.0]),
            np.array([0.0, -0.046]),
            np.array([True, True]),
            rule,
        )
        self.assertEqual(status, "fail")
        self.assertEqual(days, 2)
        self.assertEqual(reason, "daily_loss")

    def test_ftmo_one_step_best_day_rule_delays_passing(self):
        rule = PropStageRule(
            id="one_step_test",
            profit_target_pct=10.0,
            max_daily_loss_pct=3.0,
            max_loss_pct=10.0,
            trailing_max_loss=True,
            best_day_rule_pct=50.0,
            analysis_horizon_days=3,
        )
        status, days, reason = _simulate_one_stage(
            np.array([0.06, 0.04, 0.02]),
            np.array([-0.005, -0.005, -0.005]),
            np.array([True, True, True]),
            rule,
        )
        self.assertEqual(status, "pass")
        self.assertEqual(days, 3)
        self.assertIsNone(reason)

    def test_prop_trading_days_count_new_openings_not_days_held(self):
        idx = pd.date_range("2020-01-01", periods=6, freq="D")
        weights = pd.DataFrame(
            {"A": [0.0, 1.0, 1.0, 1.0, 0.0, 1.0]},
            index=idx,
        )
        opened = active_day_proxy(weights)
        self.assertEqual(int(opened.sum()), 2)
        self.assertTrue(opened.iloc[1])
        self.assertTrue(opened.iloc[5])
        self.assertFalse(opened.iloc[2])

    def test_prop_daily_adverse_proxy_uses_low_for_long_positions(self):
        idx = pd.date_range("2020-01-01", periods=4, freq="D")
        frame = pd.DataFrame(
            {
                "Open": [100, 100, 100, 100],
                "High": [102, 102, 102, 102],
                "Low": [95, 96, 97, 98],
                "Close": [101, 101, 101, 101],
            },
            index=idx,
        )
        weights = pd.DataFrame({"A": [1.0, 1.0, 0.5, 0.0]}, index=idx)
        adverse = daily_adverse_proxy({"A": frame}, weights)
        self.assertAlmostEqual(adverse.iloc[0], -0.05)
        self.assertAlmostEqual(adverse.iloc[2], -0.015)
        self.assertAlmostEqual(adverse.iloc[3], 0.0)

    def test_prop_intraday_loader_accepts_mixed_iso_timestamp_precision(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "hourly.csv"
            p.write_text(
                "Date,Open,High,Low,Close,Volume\n"
                "2020-01-01T00:00:00+00:00,100,101,99,100.5,1\n"
                "2020-01-01T01:00:00.123000+00:00,100.5,102,100,101,1\n",
                encoding="utf-8",
            )
            x = read_hourly(p)
            self.assertEqual(len(x), 2)
            self.assertIsNotNone(x.index.tz)
            self.assertEqual(x.index[1].microsecond, 123000)

    def test_prop_intraday_strategy_flattens_for_prague_midnight(self):
        idx = pd.date_range(
            "2020-01-01T00:00:00Z",
            periods=80,
            freq="h",
            tz="UTC",
        )
        close = np.linspace(100.0, 140.0, len(idx))
        def frame(mult):
            x = close * mult
            return pd.DataFrame({
                "Open": x,
                "High": x * 1.001,
                "Low": x * 0.999,
                "Close": x,
                "Volume": 1.0,
            }, index=idx)
        data = {"BTCUSDT": frame(1.0), "ETHUSDT": frame(0.5)}
        strat = hourly_rotation_strategy(
            {"lookback": 2, "trend": 3, "top_k": 1},
            tuple(sorted(data)),
        )
        weights = strat(data)
        local_dates = pd.Series(idx.tz_convert("Europe/Prague").date, index=idx)
        reset_rows = local_dates.shift(-1).notna() & (
            local_dates.shift(-1) != local_dates
        )
        self.assertTrue((weights.loc[reset_rows].abs().sum(axis=1) == 0.0).all())

    def test_prop_intraday_aggregation_tracks_worst_equity_within_prague_day(self):
        idx = pd.date_range(
            "2020-01-01T00:00:00Z",
            periods=6,
            freq="h",
            tz="UTC",
        )
        returns = pd.Series([0.01, -0.005, 0.002, 0.0, 0.0, 0.0], index=idx)
        adverse = pd.Series([-0.02, -0.01, -0.005, 0.0, 0.0, 0.0], index=idx)
        weights = pd.DataFrame({"A": [1, 1, 1, 0, 0, 0]}, index=idx)
        dr, da, opened = aggregate_prague_days(returns, adverse, weights)
        self.assertGreaterEqual(len(dr), 1)
        self.assertLessEqual(float(da.min()), -0.02)
        self.assertTrue(bool(opened.iloc[0]))

    def test_prop_universe_mutations_remove_bnb_without_losing_core(self):
        data = {
            "BTCUSDT": object(),
            "ETHUSDT": object(),
            "BNBUSDT": object(),
            "LTCUSDT": object(),
        }
        self.assertEqual(
            _resolve_prop_symbols(data, "btc_eth"),
            ("BTCUSDT", "ETHUSDT"),
        )
        self.assertEqual(
            _resolve_prop_symbols(data, "no_bnb"),
            ("BTCUSDT", "ETHUSDT", "LTCUSDT"),
        )
        leader = {
            "params": {
                "family": "cross_sectional_long",
                "lookback": 168,
                "trend": 168,
                "top_k": 1,
                "vol_target": 0.30,
                "vol_lookback": 72,
            },
            "candidate": object(),
        }
        mutations = _frontier_universe_mutations(
            {"p": {"balanced": leader}},
            ("balanced",),
        )
        self.assertEqual(
            {x["universe"] for x in mutations},
            {"no_bnb", "btc_eth"},
        )

    def test_prop_frontier_mutations_are_small_and_nonduplicative(self):
        seeds = [
            {
                "lookback": 168,
                "trend": 336,
                "top_k": 1,
                "vol_target": 0.6,
                "vol_lookback": 168,
            },
            {
                "lookback": 168,
                "trend": 336,
                "top_k": 1,
                "vol_target": 0.6,
                "vol_lookback": 168,
            },
        ]
        rows = _frontier_structural_mutations(seeds)
        self.assertEqual(len(rows), 7)
        keys = {tuple(sorted(row.items())) for row in rows}
        self.assertEqual(len(keys), len(rows))
        self.assertFalse(
            any(
                row["execution_session"] == "avoid_funding_hours"
                and row["rebalance_hours"] == 8
                for row in rows
            )
        )
        self.assertFalse(
            any(
                row["execution_session"] == "all"
                and row["rebalance_hours"] == 1
                for row in rows
            )
        )

    def test_prop_tsmom_can_hold_long_and_short_causally(self):
        idx = pd.date_range(
            "2020-01-01T00:00:00Z",
            periods=40,
            freq="h",
            tz="UTC",
        )
        up = 100.0 * (1.01 ** np.arange(len(idx)))
        down = 100.0 * (0.99 ** np.arange(len(idx)))
        def frame(close):
            return pd.DataFrame(
                {
                    "Open": close,
                    "High": close * 1.001,
                    "Low": close * 0.999,
                    "Close": close,
                    "Volume": 1.0,
                },
                index=idx,
            )
        data = {
            "BTCUSDT": frame(up),
            "ETHUSDT": frame(down),
        }
        params = {
            "family": "tsmom_long_short",
            "lookback": 2,
            "execution_session": "all",
            "rebalance_hours": 1,
        }
        target = hourly_tsmom_strategy(
            params,
            tuple(sorted(data)),
        )(data)
        active = target.abs().sum(axis=1) > 0
        self.assertTrue(active.any())
        row = target.loc[active].iloc[0]
        self.assertGreater(float(row["BTCUSDT"]), 0.0)
        self.assertLess(float(row["ETHUSDT"]), 0.0)
        self.assertAlmostEqual(float(row.abs().sum()), 1.0)

        short_data = {k: v.iloc[:25] for k, v in data.items()}
        a = hourly_tsmom_strategy(
            params,
            tuple(sorted(short_data)),
        )(short_data)
        b = hourly_tsmom_strategy(
            params,
            tuple(sorted(data)),
        )(data)
        pd.testing.assert_frame_equal(
            a.iloc[:-1],
            b.loc[a.index].iloc[:-1],
        )

    def test_prop_intraday_session_filter_uses_next_execution_hour(self):
        idx = pd.date_range(
            "2020-01-01T00:00:00Z",
            periods=48,
            freq="h",
            tz="UTC",
        )
        close = 100.0 * (1.002 ** np.arange(len(idx)))
        frame = pd.DataFrame(
            {
                "Open": close,
                "High": close * 1.001,
                "Low": close * 0.999,
                "Close": close,
                "Volume": 1.0,
            },
            index=idx,
        )
        params = {
            "lookback": 2,
            "trend": 2,
            "top_k": 1,
            "execution_session": "avoid_funding_hours",
            "rebalance_hours": 1,
        }
        target = hourly_rotation_strategy(
            params,
            ("BTCUSDT",),
        )({"BTCUSDT": frame})
        for i in range(len(idx) - 1):
            next_hour = idx[i + 1].hour
            if next_hour in {0, 8, 16}:
                self.assertAlmostEqual(
                    float(target.iloc[i].abs().sum()),
                    0.0,
                )

    def test_prop_intraday_prague_reset_persists_until_rebalance(self):
        idx = pd.date_range(
            "2020-01-01T20:00:00Z",
            periods=10,
            freq="h",
            tz="UTC",
        )
        close = 100.0 * (1.01 ** np.arange(len(idx)))
        frame = pd.DataFrame(
            {
                "Open": close,
                "High": close * 1.001,
                "Low": close * 0.999,
                "Close": close,
                "Volume": 1.0,
            },
            index=idx,
        )
        params = {
            "lookback": 2,
            "trend": 2,
            "top_k": 1,
            "execution_session": "all",
            "rebalance_hours": 4,
        }
        target = hourly_rotation_strategy(
            params,
            ("BTCUSDT",),
        )({"BTCUSDT": frame})
        exec_w = target.shift(1).fillna(0.0)
        prague_dates = pd.Series(
            idx.tz_convert(PRAGUE).date,
            index=idx,
        )
        first_new_day = prague_dates.ne(prague_dates.shift(1))
        reset_exec_positions = np.flatnonzero(first_new_day.to_numpy())
        # Ignore the very first sample row; inspect the actual midnight reset.
        reset_exec_positions = reset_exec_positions[reset_exec_positions > 0]
        self.assertGreater(len(reset_exec_positions), 0)
        p = int(reset_exec_positions[0])
        self.assertAlmostEqual(float(exec_w.iloc[p].abs().sum()), 0.0)
        # No carried pre-midnight position may reappear before an eligible
        # rebalance explicitly establishes a fresh target.
        for q in range(p + 1, len(idx)):
            if idx[q].hour % 4 == 0:
                break
            self.assertAlmostEqual(float(exec_w.iloc[q].abs().sum()), 0.0)

    def test_prop_intraday_rebalance_carries_target_causally(self):
        idx = pd.date_range(
            "2020-01-01T01:00:00Z",
            periods=12,
            freq="h",
            tz="UTC",
        )
        close = 100.0 * (1.003 ** np.arange(len(idx)))
        frame = pd.DataFrame(
            {
                "Open": close,
                "High": close * 1.001,
                "Low": close * 0.999,
                "Close": close,
                "Volume": 1.0,
            },
            index=idx,
        )
        params = {
            "lookback": 2,
            "trend": 2,
            "top_k": 1,
            "execution_session": "all",
            "rebalance_hours": 4,
        }
        target = hourly_rotation_strategy(
            params,
            ("BTCUSDT",),
        )({"BTCUSDT": frame})
        # Once a 4-hour execution rebalance has established exposure, the
        # target is carried between rebalances rather than churned hourly.
        changes = target.diff().abs().sum(axis=1).fillna(
            target.abs().sum(axis=1)
        )
        for i in range(1, len(idx) - 1):
            if changes.iloc[i] > 1e-12:
                next_hour = idx[i + 1].hour
                self.assertEqual(next_hour % 4, 0)

    def test_prop_day_brake_stops_future_hours_but_keeps_trigger_bar(self):
        idx = pd.date_range(
            "2020-01-01T00:00:00Z",
            periods=5,
            freq="h",
            tz="UTC",
        )
        returns = pd.Series([0.01, 0.01, 0.01, 0.01, 0.0], index=idx)
        adverse = pd.Series([-0.002] * 5, index=idx)
        weights = pd.DataFrame({"A": [1.0, 1.0, 1.0, 1.0, 0.0]}, index=idx)
        plain, _, _ = aggregate_prague_days_scaled(
            returns, adverse, weights, scales=(1.0,)
        )
        braked, _, _ = aggregate_prague_days_scaled(
            returns,
            adverse,
            weights,
            scales=(1.0,),
            day_profit_cap=0.015,
            day_loss_cap=0.015,
        )
        self.assertGreater(float(braked[1.0].iloc[0]), 0.015)
        self.assertLess(float(braked[1.0].iloc[0]), float(plain[1.0].iloc[0]))

    def test_prop_day_brake_preserves_trigger_hour_adverse_excursion(self):
        idx = pd.date_range(
            "2020-01-01T00:00:00Z",
            periods=3,
            freq="h",
            tz="UTC",
        )
        returns = pd.Series([0.02, 0.02, 0.0], index=idx)
        adverse = pd.Series([-0.06, -0.002, 0.0], index=idx)
        weights = pd.DataFrame({"A": [1.0, 1.0, 0.0]}, index=idx)
        _, braked_adv, _ = aggregate_prague_days_scaled(
            returns,
            adverse,
            weights,
            scales=(1.0,),
            day_profit_cap=0.015,
            day_loss_cap=0.015,
        )
        # Hitting the profit brake at the close of the first bar cannot erase
        # an adverse excursion that already occurred inside that trigger bar.
        self.assertLessEqual(float(braked_adv[1.0].iloc[0]), -0.06 + 1e-12)

    def test_prop_day_brake_charges_missing_next_day_reentry(self):
        idx = pd.DatetimeIndex([
            "2020-01-01T10:00:00Z",
            "2020-01-01T11:00:00Z",
            "2020-01-02T10:00:00Z",
            "2020-01-02T11:00:00Z",
        ])
        returns = pd.Series([0.02, 0.0, 0.0, 0.0], index=idx)
        adverse = pd.Series([0.0, 0.0, 0.0, 0.0], index=idx)
        # The raw strategy carries the same exposure across the Prague-day
        # boundary, so its own turnover series contains no next-day re-entry.
        weights = pd.DataFrame({"A": [1.0, 1.0, 1.0, 1.0]}, index=idx)
        braked, _, _ = aggregate_prague_days_scaled(
            returns,
            adverse,
            weights,
            scales=(1.0,),
            day_profit_cap=0.015,
            day_loss_cap=0.015,
        )
        self.assertEqual(len(braked[1.0]), 2)
        # 3x stressed 3.25 bp commission + 2 bp slippage = 15.75 bp.
        self.assertLessEqual(float(braked[1.0].iloc[1]), -0.001575 + 1e-12)

    def test_prop_day_brake_mutations_are_compact(self):
        leader = {
            "params": {
                "family": "cross_sectional_long",
                "lookback": 168,
                "trend": 168,
                "top_k": 1,
                "vol_target": 0.30,
                "vol_lookback": 72,
            },
            "candidate": object(),
        }
        payout_leader = {
            "params": {
                **leader["params"],
                "trend": 336,
            },
            "candidate": object(),
        }
        rows = _frontier_day_brake_mutations(
            {
                "p": {
                    "balanced": leader,
                    "conservative": leader,
                    "max_payout_efficiency": payout_leader,
                }
            }
        )
        # Two distinct frontier parameter sets x the fixed 2x2 brake grid.
        self.assertEqual(len(rows), 8)
        self.assertEqual(
            {x["day_profit_cap"] for x in rows},
            {0.010, 0.015},
        )
        self.assertEqual(
            {x["day_loss_cap"] for x in rows},
            {0.010, 0.015},
        )

    def test_prop_intraday_scale_compounds_before_daily_aggregation(self):
        idx = pd.date_range(
            "2020-01-01T00:00:00Z",
            periods=3,
            freq="h",
            tz="UTC",
        )
        returns = pd.Series([0.10, 0.10, 0.0], index=idx)
        adverse = pd.Series([-0.02, -0.01, 0.0], index=idx)
        weights = pd.DataFrame({"A": [1.0, 1.0, 0.0]}, index=idx)
        base_r, _, _ = aggregate_prague_days(returns, adverse, weights)
        scaled_r, _, _ = aggregate_prague_days_scaled(
            returns,
            adverse,
            weights,
            scales=(0.5, 1.0),
        )
        self.assertAlmostEqual(float(scaled_r[1.0].iloc[0]), float(base_r.iloc[0]))
        self.assertAlmostEqual(float(scaled_r[0.5].iloc[0]), 1.05 * 1.05 - 1.0)
        self.assertNotAlmostEqual(
            float(scaled_r[0.5].iloc[0]),
            0.5 * float(base_r.iloc[0]),
            places=8,
        )

    def test_prop_optimizer_accepts_exact_prescaled_daily_paths(self):
        rng = np.random.default_rng(909)
        n = 700
        base = rng.normal(0.0008, 0.012, n)
        adv = np.minimum(rng.normal(-0.004, 0.008, n), 0.0)
        active = np.ones(n, dtype=bool)
        scales = (0.5, 1.0)
        exact_r = {
            0.5: (1.0 + 0.5 * base) ** 2 - 1.0,
            1.0: (1.0 + base) ** 2 - 1.0,
        }
        exact_a = {0.5: 0.5 * adv, 1.0: adv}
        result = optimize_prop_exposure(
            exact_r[1.0],
            exact_a[1.0],
            active,
            FTMO_2STEP,
            exposure_scales=scales,
            paths=120,
            block=10,
            seed=44,
            prescaled_returns_by_scale=exact_r,
            prescaled_adverse_by_scale=exact_a,
        )
        table = {row.exposure_scale: row for row in result.challenge_scale_table}
        self.assertEqual(set(table), {0.5, 1.0})
        self.assertTrue(all(row.paths == 120 for row in table.values()))

    def test_full_v4_integration_demo(self):
        out=run_integration_demo()
        self.assertEqual(out["protocol"],"alpha_generation_v4")
        self.assertFalse(out["final_oos_opened"])
        self.assertTrue(out["intraday_protocol_checked"])
        self.assertIn("portfolio",out)
        self.assertIn("parameter_optimizer",out)
        self.assertIn("research_allocator",out)


if __name__ == "__main__":
    unittest.main()
