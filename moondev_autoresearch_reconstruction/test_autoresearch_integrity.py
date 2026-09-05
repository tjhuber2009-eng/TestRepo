import json
import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

import numpy as np
import pandas as pd

import continuous_runner
import loop
import overfit_diagnostics
import research_metrics
import seed_factory

HERE = Path(__file__).resolve().parent


class AutoresearchIntegrityTests(unittest.TestCase):
    def setUp(self):
        self.registry = json.loads(
            (HERE / "strategy_library" / "registry.json").read_text(encoding="utf-8")
        )
        self.config = json.loads(
            (HERE / "continuous_config.json").read_text(encoding="utf-8")
        )

    def test_all_project_python_sources_compile(self):
        for path in HERE.glob("*.py"):
            source = path.read_text(encoding="utf-8")
            compile(source, str(path), "exec")

    def test_hidden_validation_directory_is_git_ignored(self):
        text = (HERE / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("validation_data/", text)

    def test_v3_state_json_is_strict_and_nonfinite_becomes_null(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "strict.json"
            continuous_runner.save_json(
                p,
                {"neg_inf": float("-inf"), "nan": float("nan"), "ok": 1.25},
            )
            raw = p.read_text(encoding="utf-8")
            self.assertNotIn("Infinity", raw)
            self.assertNotIn("NaN", raw)
            parsed = json.loads(raw)
            self.assertIsNone(parsed["neg_inf"])
            self.assertIsNone(parsed["nan"])
            self.assertEqual(parsed["ok"], 1.25)

    def test_experiment_json_sanitizer_is_strict(self):
        payload = loop.json_safe(
            {"score": float("inf"), "folds": [1.0, float("-inf"), float("nan")]}
        )
        raw = json.dumps(payload, allow_nan=False)
        self.assertNotIn("Infinity", raw)
        self.assertNotIn("NaN", raw)
        self.assertEqual(json.loads(raw)["folds"], [1.0, None, None])

    def test_stale_v2_reset_removes_all_active_protocol_artifacts(self):
        names = [
            "STATE", "TRACKS", "CURSOR", "LEDGER", "PROGRESS",
            "SELECTIONS", "LEADERBOARD",
        ]
        old = {name: getattr(continuous_runner, name) for name in names}
        try:
            with tempfile.TemporaryDirectory() as td:
                state = Path(td) / "continuous_state"
                tracks = state / "tracks"
                tracks.mkdir(parents=True)
                stale_track = tracks / "stale"
                stale_track.mkdir()
                (stale_track / "state_meta.json").write_text(
                    json.dumps({"protocol": "nested_chronological_v2"}),
                    encoding="utf-8",
                )
                paths = {
                    "STATE": state,
                    "TRACKS": tracks,
                    "CURSOR": state / "cursor.json",
                    "LEDGER": state / "cycles.jsonl",
                    "PROGRESS": state / "progress.json",
                    "SELECTIONS": state / "search_selections.json",
                    "LEADERBOARD": state / "leaderboard_latest.json",
                }
                for name, value in paths.items():
                    setattr(continuous_runner, name, value)
                for p in [
                    paths["CURSOR"], paths["PROGRESS"],
                    paths["SELECTIONS"], paths["LEADERBOARD"],
                ]:
                    p.write_text(
                        json.dumps({"protocol": "nested_chronological_v2"}),
                        encoding="utf-8",
                    )
                paths["LEDGER"].write_text('{"legacy":"v2"}\n', encoding="utf-8")
                (state / "ALL_RUNNABLE_TRACKS_TERMINAL").write_text(
                    "legacy\n", encoding="utf-8"
                )
                dash = state / "dashboard"
                dash.mkdir()
                (dash / "old.html").write_text("v2", encoding="utf-8")

                removed = continuous_runner.reset_stale_protocol_state()
                self.assertEqual(removed, 1)
                self.assertFalse(stale_track.exists())
                for key in ["CURSOR", "PROGRESS", "SELECTIONS", "LEADERBOARD", "LEDGER"]:
                    self.assertFalse(paths[key].exists(), key)
                self.assertFalse((state / "ALL_RUNNABLE_TRACKS_TERMINAL").exists())
                self.assertFalse(dash.exists())
        finally:
            for name, value in old.items():
                setattr(continuous_runner, name, value)

    def test_protocol_is_nested_v3(self):
        self.assertEqual(continuous_runner.PROTOCOL, "nested_chronological_v3")
        text = (HERE / "robust_harness.py").read_text(encoding="utf-8")
        self.assertIn('PROTOCOL = "nested_chronological_v3"', text)

    def test_final_oos_never_enters_pre_oos_config(self):
        self.assertEqual(
            self.config["protocol"]["final_oos_start"],
            "2023-01-01",
        )
        for target in self.config["targets"]:
            self.assertLessEqual(target["validation_end"], "2022-12-31")
            self.assertLess(target["validation_start"], "2023-01-01")

    def test_risk_profiles_remain_frozen(self):
        self.assertEqual(self.config["profiles"]["prop"]["max_dd_pct"], 10)
        self.assertEqual(self.config["profiles"]["private"]["max_dd_pct"], 32)

    def test_every_runnable_family_has_factory(self):
        runnable = [
            x for x in self.registry["families"]
            if x.get("status") == "runnable"
        ]
        self.assertGreaterEqual(len(runnable), 20)
        for row in runnable:
            self.assertIn("exactness", row)
            self.assertIn("origin", row)
            self.assertIn(row["factory"], seed_factory.BODIES)

    def test_track_ids_are_unique_and_universe_is_large(self):
        tracks = continuous_runner.build_tracks()
        ids = [x["id"] for x in tracks]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertGreaterEqual(len(ids), 400)

    def test_crashes_and_duplicates_do_not_count_as_valid(self):
        body = (
            "ts\titer\tverdict\tscore\tbase_score\tret_pct\tsharpe\t"
            "ann_vol\ttrades\tmax_dd\tguard\tdesc\n"
            "a\t1\tKEPT\t1\t0\t1\t1\t1\t1\t-1\tok\tx\n"
            "b\t2\tREJECTED\t0\t1\t0\t0\t1\t1\t-1\tok\tx\n"
            "c\t3\tCRASH\tnan\t1\tnan\tnan\tnan\t0\tnan\terr\tx\n"
            "d\t4\tDUPLICATE\tnan\t1\tnan\tnan\tnan\t0\tnan\tdup\tx\n"
        )
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "results.tsv"
            p.write_text(body, encoding="utf-8")
            x = continuous_runner.result_counts_at(p)
        self.assertEqual(x["valid"], 2)
        self.assertEqual(x["crashes"], 1)
        self.assertEqual(x["duplicates"], 1)

    def test_secret_scrub_for_direct_harness_calls(self):
        env = {
            "NVIDIA_API_KEY": "x",
            "GH_TOKEN": "y",
            "OTHER_SECRET": "z",
            "AUTORESEARCH_SYMBOL": "BTCUSDT",
        }
        clean = continuous_runner.safe_harness_env(env)
        self.assertNotIn("NVIDIA_API_KEY", clean)
        self.assertNotIn("GH_TOKEN", clean)
        self.assertNotIn("OTHER_SECRET", clean)
        self.assertEqual(clean["AUTORESEARCH_SYMBOL"], "BTCUSDT")

    def test_successive_halving_targets_are_monotonic(self):
        breadth, depth, elite = 10, 30, 60
        self.assertLessEqual(breadth, depth)
        self.assertLessEqual(depth, elite)

    def test_numeric_only_mutation_is_structurally_detectable(self):
        a = """
class MoonStrategy:
    vol_target = 0.08
    f_max = 0.5
    vol_lookback = 30
    def _units(self, px, rv):
        return int(px)
    def next(self):
        if 1 < 2:
            return
"""
        b = a.replace("1 < 2", "1 < 3")
        self.assertNotEqual(loop.canonical_ast_hash(a), loop.canonical_ast_hash(b))
        self.assertEqual(loop.structural_ast_hash(a), loop.structural_ast_hash(b))

    def test_risk_control_changes_are_detectable(self):
        a = """
class MoonStrategy:
    vol_target = 0.08
    f_max = 0.5
    vol_lookback = 30
    def _units(self, px, rv):
        return int(px)
"""
        b = a.replace("vol_target = 0.08", "vol_target = 0.09")
        self.assertNotEqual(
            loop.risk_control_fingerprint(a),
            loop.risk_control_fingerprint(b),
        )

    def test_localized_method_change_is_allowed(self):
        base = """
def helper(x):
    return x

class MoonStrategy:
    vol_target = 0.08
    f_max = 0.5
    vol_lookback = 30
    def _units(self, px, rv):
        return int(px)
    def init(self):
        self.x = 1
    def next(self):
        if self.x:
            return
"""
        candidate = base.replace(
            "if self.x:\n            return",
            "if self.x > 0:\n            return",
        )
        ok, detail = loop.local_change_guard(candidate, base)
        self.assertTrue(ok, detail)
        self.assertEqual(detail, ["MoonStrategy.next"])

    def test_wholesale_rewrite_is_rejected(self):
        base = """
def h1(x): return x
def h2(x): return x

class MoonStrategy:
    vol_target = 0.08
    f_max = 0.5
    vol_lookback = 30
    def _units(self, px, rv): return int(px)
    def init(self): self.x = 1
    def next(self):
        if self.x: return
"""
        candidate = """
def h1(x): return x + 1
def h2(x): return x + 2
def h3(x): return x + 3

class MoonStrategy:
    vol_target = 0.08
    f_max = 0.5
    vol_lookback = 30
    def _units(self, px, rv): return int(px)
    def init(self): self.x = 2
    def next(self):
        if self.x > 2: return
"""
        ok, _ = loop.local_change_guard(candidate, base)
        self.assertFalse(ok)

    def test_global_safety_ceiling_is_not_normal_seed_bottleneck(self):
        source = (
            seed_factory.COMMON
            .replace("__BARS_PER_YEAR__", "252")
            .replace("__VOL_TARGET__", repr(0.08))
            .replace("__F_MAX__", repr(0.5))
            .replace(
                "__BODY__",
                seed_factory.BODIES["connors_double7"].strip("\n"),
            )
        )
        complexity = loop.ast_complexity(source)
        self.assertLess(complexity["calls"], 360)
        self.assertLess(complexity["nodes"], 4000)

    def test_short_history_falls_back_to_half_year_folds(self):
        env = {
            "AUTORESEARCH_SYMBOL": "SOLUSDT",
            "AUTORESEARCH_MARKET": "crypto",
            "AUTORESEARCH_DATA_FILE": "data/sol_1d.csv",
            "AUTORESEARCH_COMMISSION": "0.001",
            "AUTORESEARCH_MARGIN": "0.25",
            "AUTORESEARCH_BARS_PER_YEAR": "365",
            "AUTORESEARCH_PROFILE": "prop",
            "AUTORESEARCH_MAX_DD_PCT": "10",
            "AUTORESEARCH_VALIDATION_START": "2022-01-01",
            "AUTORESEARCH_VALIDATION_END": "2022-12-31",
            "AUTORESEARCH_MIN_FOLD_BARS": "100",
        }
        old = {k: os.environ.get(k) for k in env}
        try:
            os.environ.update(env)
            import importlib
            import robust_harness
            robust_harness = importlib.reload(robust_harness)
            idx = pd.date_range("2020-08-11", "2021-12-31", freq="D", tz="UTC")
            windows = robust_harness.fold_windows(
                idx, "2020-08-11", "2021-12-31"
            )
            self.assertGreaterEqual(len(windows), 3)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


    def test_protocol_v3_has_extreme_cost_stress(self):
        p = self.config["protocol"]
        self.assertEqual(p["name"], "nested_chronological_v3")
        self.assertEqual(p["cost_stress_multiplier"], 2.0)
        self.assertEqual(p["extreme_cost_stress_multiplier"], 3.0)
        self.assertGreaterEqual(p["bootstrap_reps"], 200)

    def test_annualized_k_is_duration_normalized(self):
        # 20% CAGR for one year and the same 20% CAGR compounded for five
        # years must produce the same K when Sharpe is equal.
        one_total = 1.20 - 1.0
        five_total = (1.20 ** 5) - 1.0
        a = research_metrics.annualized_k(one_total, 1.0, 1.25)
        b = research_metrics.annualized_k(five_total, 5.0, 1.25)
        self.assertAlmostEqual(a, b, places=10)

    def test_annualized_k_never_rewards_negative_growth_and_negative_sharpe(self):
        losing = research_metrics.annualized_k(-0.25, 2.0, -1.1)
        winning = research_metrics.annualized_k(0.25, 2.0, 1.1)
        self.assertLess(losing, 0.0)
        self.assertGreater(winning, 0.0)
        expected = abs(
            research_metrics.annualized_log_growth(-0.25, 2.0) * -1.1
        )
        self.assertAlmostEqual(abs(losing), expected, places=10)

    def test_annualized_k_requires_positive_growth_and_positive_sharpe(self):
        self.assertLess(research_metrics.annualized_k(0.20, 1.0, -0.5), 0.0)
        self.assertLess(research_metrics.annualized_k(-0.20, 1.0, 0.5), 0.0)

    def test_sortino_uses_zero_target_downside_deviation_and_annualizes(self):
        r = np.array([0.02, -0.01, 0.01, -0.02], dtype=float)
        eq = np.cumprod(np.r_[100.0, 1.0 + r])
        out = research_metrics.tail_metrics(eq, r, 0.10, bars_per_year=4)
        downside = np.sqrt(np.mean(np.minimum(r, 0.0) ** 2))
        expected_per_bar = np.mean(r) / downside
        self.assertAlmostEqual(out["sortino_per_bar"], expected_per_bar, places=12)
        self.assertAlmostEqual(
            out["sortino_annualized"], expected_per_bar * 2.0, places=12
        )

    def test_config_records_sign_safe_k_definition(self):
        self.assertEqual(
            self.config["protocol"]["score_definition"],
            "sign_safe_annualized_log_growth_x_sharpe_robust_v3",
        )

    def test_cscv_loader_uses_only_selection_eligible_fixed_slices(self):
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "experiments.jsonl"
            rows = []
            for i in range(7):
                rows.append({
                    "candidate_ast_sha256": f"c{i}",
                    "selection_eligible": i < 5,
                    "cscv_slice_k": [0.01 * (i + j) for j in range(8)],
                    "fold_raw_k": [999.0] * 11,
                })
            p.write_text(
                "".join(json.dumps(x) + "\n" for x in rows),
                encoding="utf-8",
            )
            matrix, ids = overfit_diagnostics.load_experiment_fold_matrix(p)
        self.assertEqual(matrix.shape, (5, 8))
        self.assertEqual(len(ids), 5)

    def test_cscv_requires_even_symmetric_partition(self):
        self.assertIsNone(overfit_diagnostics.cscv_pbo(np.ones((6, 7))))
        out = overfit_diagnostics.cscv_pbo(
            np.asarray([
                [0.8,0.7,0.6,0.5,0.4,0.3,0.2,0.1],
                [0.7,0.6,0.5,0.4,0.3,0.2,0.1,0.0],
                [0.6,0.5,0.4,0.3,0.2,0.1,0.0,-0.1],
                [0.5,0.4,0.3,0.2,0.1,0.0,-0.1,-0.2],
                [0.4,0.3,0.2,0.1,0.0,-0.1,-0.2,-0.3],
            ], dtype=float)
        )
        self.assertIsNotNone(out)
        self.assertEqual(out["fold_count"], 8)
        self.assertEqual(out["partition"], "fixed_even_development_slices")

    def test_probabilistic_sharpe_prefers_positive_edge(self):
        positive = np.array([0.003, -0.001, 0.002, 0.001, -0.0005] * 80)
        flat = np.array([0.001, -0.001] * 200)
        self.assertGreater(
            research_metrics.probabilistic_sharpe_ratio(positive),
            research_metrics.probabilistic_sharpe_ratio(flat),
        )

    def test_block_bootstrap_is_deterministic_given_rng_seed(self):
        r = np.array([0.002, -0.001, 0.0015, -0.0005] * 100)
        a = research_metrics.deterministic_block_bootstrap_diagnostics(
            r, bars_per_year=252, rng=np.random.default_rng(42), reps=100, block=10
        )
        b = research_metrics.deterministic_block_bootstrap_diagnostics(
            r, bars_per_year=252, rng=np.random.default_rng(42), reps=100, block=10
        )
        self.assertEqual(a, b)

    def test_auto_model_router_falls_back_without_tournament(self):
        old = continuous_runner.TOURNAMENT_STATE
        try:
            continuous_runner.TOURNAMENT_STATE = Path("__missing_tournament__.json")
            track = continuous_runner.build_tracks()[0]
            self.assertEqual(
                continuous_runner.select_research_model(track, "auto", 0),
                continuous_runner.DEFAULT_MODEL,
            )
        finally:
            continuous_runner.TOURNAMENT_STATE = old



    def test_pbo_diagnostic_stays_in_probability_bounds(self):
        # Strategies alternate between excellent and poor halves, a pattern
        # that should look unstable under CSCV.
        matrix = np.array([
            [2, 2, 2, -2, -2, -2],
            [-2, -2, -2, 2, 2, 2],
            [1.5, 1.5, -1, -1, 1.5, -1],
            [-1, -1, 1.5, 1.5, -1, 1.5],
            [0.1, 0.1, 0.1, 0.1, 0.1, 0.1],
            [0.2, -0.1, 0.2, -0.1, 0.2, -0.1],
        ], dtype=float)
        d = overfit_diagnostics.cscv_pbo(matrix)
        self.assertIsNotNone(d)
        self.assertGreaterEqual(d["pbo"], 0.0)
        self.assertLessEqual(d["pbo"], 1.0)
        self.assertGreater(d["cscv_splits"], 0)



    def test_depth_promotion_requires_guard_passing_champion(self):
        tracks = continuous_runner.build_tracks()[:2]
        metas = {
            tracks[0]["id"]: {"baseline": {"score": 1.0, "guard_ok": False}},
            tracks[1]["id"]: {"baseline": {"score": 0.5, "guard_ok": True}},
        }
        def meta(track):
            return metas[track["id"]]
        with mock.patch.object(continuous_runner, "is_terminal_block", return_value=False), \
             mock.patch.object(continuous_runner, "track_counts", return_value={"valid": 10}), \
             mock.patch.object(continuous_runner, "track_meta", side_effect=meta), \
             mock.patch.object(continuous_runner, "development_overfit", return_value=None):
            rows = continuous_runner.ranked_viable(tracks, 10)
        self.assertEqual([x[2]["id"] for x in rows], [tracks[1]["id"]])

    def test_hidden_validation_selector_only_uses_elite_ids(self):
        tracks = continuous_runner.build_tracks()[:4]
        elite = tracks[2]["id"]
        old = continuous_runner.SELECTIONS
        with tempfile.TemporaryDirectory() as td:
            continuous_runner.SELECTIONS = Path(td) / "selections.json"
            continuous_runner.save_json(
                continuous_runner.SELECTIONS,
                {"protocol": continuous_runner.PROTOCOL, "depth_ids": [elite], "elite_ids": [elite]},
            )
            try:
                with mock.patch.object(continuous_runner, "is_terminal_block", return_value=False), \
                     mock.patch.object(continuous_runner, "validation_state", return_value=None):
                    result = continuous_runner.next_validation_track(tracks, 0)
                self.assertIsNotNone(result)
                self.assertEqual(result[1]["id"], elite)
            finally:
                continuous_runner.SELECTIONS = old

    def test_no_eligible_depth_tracks_completes_without_hidden_validation(self):
        tracks = continuous_runner.build_tracks()[:3]
        old = continuous_runner.SELECTIONS
        with tempfile.TemporaryDirectory() as td:
            continuous_runner.SELECTIONS = Path(td) / "selections.json"
            try:
                with mock.patch.object(continuous_runner, "breadth_complete", return_value=True), \
                     mock.patch.object(continuous_runner, "freeze_depth_selection", return_value={
                         "protocol": continuous_runner.PROTOCOL,
                         "depth_ids": [],
                         "elite_ids": [],
                     }):
                    phase, plan = continuous_runner.current_search_plan(
                        tracks, 10, 30, 60, 0.25, 0.20
                    )
                self.assertEqual(phase, "complete")
                self.assertEqual(plan, {})
            finally:
                continuous_runner.SELECTIONS = old



    def test_adaptive_data_is_physically_cut_before_hidden_validation(self):
        track = {
            "target": {
                "id": "x",
                "symbol": "X",
                "source": "yahoo",
                "start": "2018-01-01",
                "validation_start": "2021-01-01",
                "validation_end": "2022-12-31",
            }
        }
        old_here = continuous_runner.HERE
        with tempfile.TemporaryDirectory() as td:
            continuous_runner.HERE = Path(td)
            (continuous_runner.HERE / "data").mkdir()
            calls = []
            try:
                with mock.patch.object(
                    continuous_runner,
                    "run",
                    side_effect=lambda cmd, **kwargs: calls.append(list(cmd)),
                ):
                    continuous_runner.prepare_data(track)
                    continuous_runner.prepare_data(track, include_validation=True)
            finally:
                continuous_runner.HERE = old_here
        self.assertEqual(calls[0][calls[0].index("--end") + 1], "2020-12-31")
        self.assertEqual(calls[0][calls[0].index("--output-dir") + 1], "data")
        self.assertEqual(calls[1][calls[1].index("--end") + 1], "2022-12-31")
        self.assertEqual(
            calls[1][calls[1].index("--output-dir") + 1], "validation_data"
        )

    def test_validation_track_is_only_path_requesting_hidden_rows(self):
        track = continuous_runner.build_tracks()[0]
        with mock.patch.object(continuous_runner, "is_terminal_block", return_value=False), \
             mock.patch.object(continuous_runner, "validation_state", return_value=None), \
             mock.patch.object(continuous_runner, "clean_runtime"), \
             mock.patch.object(continuous_runner, "prepare_data") as prep, \
             mock.patch.object(continuous_runner, "restore_state", return_value=False):
            with self.assertRaises(RuntimeError):
                continuous_runner.validate_track(track)
        prep.assert_called_once_with(track, include_validation=True)

    def test_strategy_safety_rejects_dunder_builtin_io_bypass(self):
        tree = __import__("ast").parse(
            'class MoonStrategy:\n'
            '    def next(self):\n'
            '        __builtins__["open"]("hidden.csv")\n'
        )
        with self.assertRaises(ValueError):
            loop.validate_source_safety(tree)

    def test_strategy_safety_rejects_unlisted_pandas_reader_family(self):
        tree = __import__("ast").parse(
            'import pandas as pd\n'
            'class MoonStrategy:\n'
            '    def next(self):\n'
            '        pd.read_fwf("hidden.txt")\n'
        )
        with self.assertRaises(ValueError):
            loop.validate_source_safety(tree)

    def test_harness_stage_boundary_rejects_hidden_rows_during_search(self):
        env = {
            "AUTORESEARCH_SYMBOL": "X",
            "AUTORESEARCH_MARKET": "stock",
            "AUTORESEARCH_DATA_FILE": "data/x_1d.csv",
            "AUTORESEARCH_COMMISSION": "0.001",
            "AUTORESEARCH_MARGIN": "0.5",
            "AUTORESEARCH_BARS_PER_YEAR": "252",
            "AUTORESEARCH_PROFILE": "prop",
            "AUTORESEARCH_MAX_DD_PCT": "10",
            "AUTORESEARCH_VALIDATION_START": "2021-01-01",
            "AUTORESEARCH_VALIDATION_END": "2022-12-31",
        }
        old = {k: os.environ.get(k) for k in env}
        try:
            os.environ.update(env)
            import importlib
            import robust_harness
            robust_harness = importlib.reload(robust_harness)
            df = pd.DataFrame(
                {"Open": [1, 1], "High": [1, 1], "Low": [1, 1], "Close": [1, 1]},
                index=pd.to_datetime(["2020-12-31", "2021-01-02"], utc=True),
            )
            with self.assertRaises(RuntimeError):
                robust_harness.assert_stage_data_boundary(df, "search")
            robust_harness.assert_stage_data_boundary(df, "validation")
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v

    def test_intrabar_proxy_includes_position_still_open_at_backtest_end(self):
        env = {
            "AUTORESEARCH_SYMBOL": "X",
            "AUTORESEARCH_MARKET": "stock",
            "AUTORESEARCH_DATA_FILE": "data/x_1d.csv",
            "AUTORESEARCH_COMMISSION": "0.001",
            "AUTORESEARCH_MARGIN": "0.5",
            "AUTORESEARCH_BARS_PER_YEAR": "252",
            "AUTORESEARCH_PROFILE": "prop",
            "AUTORESEARCH_MAX_DD_PCT": "10",
            "AUTORESEARCH_VALIDATION_START": "2021-01-01",
            "AUTORESEARCH_VALIDATION_END": "2022-12-31",
        }
        old = {k: os.environ.get(k) for k in env}
        try:
            os.environ.update(env)
            import importlib
            import robust_harness
            robust_harness = importlib.reload(robust_harness)

            idx = pd.date_range("2020-01-01", periods=3, freq="D", tz="UTC")
            equity = pd.DataFrame({"Equity": [100.0, 100.0, 100.0]}, index=idx)
            prices = pd.DataFrame(
                {
                    "Open": [100.0, 100.0, 100.0],
                    "High": [100.0, 100.0, 100.0],
                    "Low": [100.0, 100.0, 80.0],
                    "Close": [100.0, 100.0, 100.0],
                },
                index=idx,
            )
            fake_trade = type("T", (), {"entry_bar": 1, "size": 1.0})()
            fake_strategy = type("S", (), {"trades": (fake_trade,)})()
            stats = {
                "_equity_curve": equity,
                "_trades": pd.DataFrame(),
                "_strategy": fake_strategy,
            }
            dd = robust_harness.intrabar_drawdown_proxy(
                stats, prices, "2020-01-01", "2020-01-03"
            )
            self.assertAlmostEqual(dd, -20.0, places=3)
        finally:
            for k, v in old.items():
                if v is None:
                    os.environ.pop(k, None)
                else:
                    os.environ[k] = v


    def test_provisional_tournament_cannot_steer_model_selection(self):
        src = (ROOT / "continuous_runner.py").read_text(encoding="utf-8")
        self.assertIn('if payload.get("provisional"):', src)
        self.assertIn('return []', src[src.index('if payload.get("provisional"):'):])

    def test_paired_fold_improvement_requires_matching_chronology(self):
        base = {"folds": [
            {"name":"Y1","raw_k":0.1},
            {"name":"Y2","raw_k":0.2},
            {"name":"Y3","raw_k":0.3},
            {"name":"Y4","raw_k":0.4},
        ]}
        candidate = {"folds": [
            {"name":"Y1","raw_k":0.2},
            {"name":"Y2","raw_k":0.3},
            {"name":"Y3","raw_k":0.1},
            {"name":"Y4","raw_k":0.5},
        ]}
        d = loop.paired_fold_improvement(base, candidate)
        self.assertEqual(d["comparable_folds"], 4)
        self.assertEqual(d["improved_fold_fraction"], 0.75)
        self.assertGreater(d["median_fold_delta_k"], 0)



if __name__ == "__main__":
    unittest.main()
