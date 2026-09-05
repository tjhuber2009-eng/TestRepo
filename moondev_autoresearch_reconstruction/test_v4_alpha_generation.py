import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from v4.alpha_objective import RiskPolicy, hard_gate, metrics_from_equity, pareto_frontier
from v4.campaign import assert_v4_data_boundary, run_integration_demo, synthetic_daily_market
from v4.feature_store import FeatureStoreBuilder
from v4.intraday_protocol import IntradayProtocol, assert_intraday_data
from v4.live_bootstrap import json_safe, read_market_csv
from v4.meta_filter import BoostedStumpMetaFilter, walk_forward_probabilities
from v4.motif_library import MotifEvidence, MotifTransferPlanner
from v4.multi_asset_engine import MultiAssetBacktester, PortfolioLimits
from v4.parameter_optimizer import ParameterSpec, StableParameterOptimizer
from v4.portfolio_optimizer import RobustPortfolioOptimizer
from v4.regime_engine import RegimeEngine
from v4.risk_overlays import volatility_target_overlay
from v4.research_allocator import ResearchAllocator, ResearchCell, ResearchObservation
from v4.strategy_examples import pead_event_weights
from v4.strategy_intake import HypothesisQueue, StrategyHypothesis


class V4AlphaGenerationTests(unittest.TestCase):
    def test_config_is_v4_and_keeps_oos_sealed(self):
        cfg = json.loads((Path(__file__).parent / "v4" / "config.json").read_text())
        self.assertEqual(cfg["protocol"], "alpha_generation_v4")
        self.assertTrue(cfg["boundaries"]["final_oos_sealed"])
        self.assertEqual(cfg["boundaries"]["final_oos_start"], "2023-01-01")
        self.assertEqual(cfg["objective"]["primary"], "maximize_sustainable_cagr_subject_to_hard_risk_and_evidence_gates")

    def test_v4_data_boundary_rejects_hidden_and_final_rows_during_search(self):
        data = synthetic_daily_market(bars=200)
        frame = data["QQQ"].copy()
        frame.index = pd.date_range("2022-06-01", periods=len(frame), freq="D")
        with self.assertRaises(RuntimeError):
            assert_v4_data_boundary({"QQQ": frame}, stage="development")

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

    def test_portfolio_optimizer_can_choose_diversification(self):
        rng = np.random.default_rng(4)
        a = rng.normal(0.0007,0.015,500)
        b = -0.5*a + rng.normal(0.0007,0.010,500)
        ret = pd.DataFrame({"A":a,"B":b})
        result = RobustPortfolioOptimizer(dd_cap_pct=35, n_candidates=300, bootstrap_reps=30, max_weight=1.0, seed=2).optimize(ret)
        self.assertIsNotNone(result.chosen)
        self.assertLessEqual(result.chosen.bootstrap_dd_q95_pct, 35)
        self.assertGreaterEqual(result.chosen.effective_n, 1.0)

    def test_motif_transfer_reuses_successful_knowledge(self):
        planner = MotifTransferPlanner([
            MotifEvidence("long_term_trend_gate","sentinel63","crypto","private",True,0.1),
            MotifEvidence("long_term_trend_gate","sentinel63","crypto","private",True,0.2),
        ])
        rows = planner.plan([{"id":"sentinel63","markets":["crypto"]}], ["crypto"], ["private"])
        self.assertEqual(rows[0]["motif_id"], "long_term_trend_gate")

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
