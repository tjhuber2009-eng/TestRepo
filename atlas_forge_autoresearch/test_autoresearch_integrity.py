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
import strategy_routing

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

    def test_track_ids_are_unique_and_phase1_universe_is_exactly_514(self):
        tracks = continuous_runner.build_tracks()
        ids = [x["id"] for x in tracks]
        self.assertEqual(len(ids), len(set(ids)))
        self.assertEqual(len(ids), 514)

    def test_every_target_has_explicit_canonical_timeframe(self):
        for name in ("continuous_config.json", "stock_fx_config.json"):
            cfg = json.loads((HERE / name).read_text(encoding="utf-8"))
            self.assertTrue(cfg["targets"])
            for target in cfg["targets"]:
                self.assertEqual(target.get("timeframe"), "1D")
                self.assertEqual(
                    strategy_routing.canonical_timeframe(target["timeframe"]),
                    "1D",
                )

    def test_every_registry_family_has_routing_metadata(self):
        required = {
            "native_markets", "native_instruments", "native_timeframes",
            "evaluation_timeframes", "signal_cadence",
            "source_route_verified", "requires_multi_timeframe",
            "requires_session_clock", "requires_volume",
            "requires_contract_data",
        }
        for family in self.registry["families"]:
            routing = family.get("routing")
            self.assertIsInstance(routing, dict, family["id"])
            self.assertTrue(required <= set(routing), family["id"])
            for tf in routing.get("native_timeframes", []):
                self.assertEqual(
                    strategy_routing.canonical_timeframe(tf), tf, family["id"]
                )
            for tf in routing.get("evaluation_timeframes", []):
                self.assertEqual(
                    strategy_routing.canonical_timeframe(tf), tf, family["id"]
                )

    def test_phase1_routes_reproduction_transfer_and_atlas_variants_explicitly(self):
        tracks = {x["id"]: x for x in continuous_runner.build_tracks()}
        self.assertEqual(
            tracks["btc_rsi_adx__btc__private"]["routing"]["stage"],
            "reproduction",
        )
        self.assertEqual(
            tracks["btc_rsi_adx__sol__private"]["routing"]["stage"],
            "transfer",
        )
        self.assertEqual(
            tracks["turtle_55_20__es__private"]["routing"]["stage"],
            "reproduction",
        )
        self.assertEqual(
            tracks["turtle_55_20__btc__private"]["routing"]["stage"],
            "transfer",
        )
        self.assertEqual(
            tracks["qqe_proxy__btc__private"]["routing"]["stage"],
            "atlas_variant",
        )
        self.assertFalse(
            tracks["qqe_proxy__btc__private"]["routing"]["source_native_match"]
        )
        self.assertTrue(
            all(
                x["routing"]["tested_timeframe"] == "1D"
                for x in tracks.values()
            )
        )
        self.assertFalse(
            any(x["routing"]["stage"] == "blocked" for x in tracks.values())
        )

    def test_phase2_daily_variants_and_monthly_signal_cadence_are_labeled(self):
        import phase2_prior_runner
        tracks = {x["id"]: x for x in phase2_prior_runner.build_tracks()}
        generic = tracks["macd_12_26_9__btc__private"]
        monthly = tracks[
            "bitcoin_cycle_monthly_causal__btc_bitstamp_monthly__private"
        ]
        self.assertEqual(generic["routing"]["stage"], "atlas_variant")
        self.assertEqual(generic["routing"]["tested_timeframe"], "1D")
        self.assertEqual(monthly["routing"]["stage"], "reproduction")
        self.assertEqual(monthly["routing"]["tested_timeframe"], "1D")
        self.assertEqual(monthly["routing"]["signal_cadence"], "monthly")

    def test_phase3_intraday_or_unknown_timeframe_cannot_enter_daily_adapter(self):
        ok, reason = strategy_routing.development_adapter_ready(
            {"timeframe": "1D"}
        )
        self.assertTrue(ok)
        self.assertIn("compatible", reason)

        for spec in (
            {"timeframe": "15M"},
            {"timeframe": "4H"},
            {"timeframe": "unknown"},
            {"timeframes": ["1D", "4H"], "requires_multi_timeframe": True},
            {"timeframe": "1D", "requires_session_clock": True},
        ):
            ok, _ = strategy_routing.development_adapter_ready(spec)
            self.assertFalse(ok, spec)

    def test_phase1_plan_seals_hidden_validation_until_expansion_finishes(self):
        plan = json.loads(
            (HERE / "strategy_library" / "universe_plan.json").read_text(
                encoding="utf-8"
            )
        )
        self.assertEqual(plan["current_stage"], "phase1_fixed_514")
        self.assertFalse(continuous_runner.hidden_validation_allowed_by_universe_plan())
        self.assertEqual(
            plan["hidden_validation_policy"],
            "sealed_until_phase3_universe_and_all_adaptive_search_are_frozen",
        )

    def test_prior_work_backlog_is_staged_but_not_in_phase1_runnable_registry(self):
        backlog = json.loads(
            (HERE / "strategy_library" / "prior_work_backlog.json").read_text(
                encoding="utf-8"
            )
        )
        ids = {x["id"] for x in backlog["items"]}
        self.assertGreaterEqual(len(ids), 30)
        self.assertIn("utbot_linreg_combo", ids)
        self.assertIn("pead", ids)
        self.assertIn("funding_basis", ids)
        runnable = {
            x["id"] for x in self.registry["families"]
            if x.get("status") == "runnable"
        }
        self.assertNotIn("utbot_linreg_combo", runnable)

    def test_phase2_prior_lane_is_isolated_and_large(self):
        import phase2_prior_runner
        import phase2_seed_factory
        phase1_ids = {x["id"] for x in continuous_runner.build_tracks()}
        phase2_tracks = phase2_prior_runner.build_tracks()
        self.assertEqual(len(phase1_ids), 514)
        self.assertGreaterEqual(len(phase2_tracks), 1000)
        self.assertTrue(phase1_ids.isdisjoint({x["id"] for x in phase2_tracks}))
        phase2_sources = {
            x["target"]["id"]: (x["target"]["source"], x["target"]["symbol"])
            for x in phase2_tracks
        }
        self.assertEqual(
            phase2_sources["es"], ("yahoo_futures_proxy", "ES=F")
        )
        self.assertEqual(
            phase2_sources["nq"], ("yahoo_futures_proxy", "NQ=F")
        )
        self.assertEqual(
            phase2_sources["gold"], ("yahoo_futures_proxy", "GC=F")
        )
        phase1_futures = [
            x for x in continuous_runner.build_tracks()
            if x["target"]["market"] == "futures_proxy"
        ]
        self.assertTrue(phase1_futures)
        self.assertEqual(
            {x["target"]["id"] for x in phase1_futures},
            {"es", "nq", "gold", "oil"},
        )
        self.assertTrue(
            all(
                x["target"]["source"] == "yahoo_futures_proxy"
                for x in phase1_futures
            )
        )
        self.assertNotIn(
            "macd_12_26_9",
            {x["id"] for x in self.registry["families"] if x.get("status") == "runnable"},
        )
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "strategy.py"
            phase2_seed_factory.generate(
                "macd_12_26_9", p, 252, 0.08, 0.5
            )
            compile(p.read_text(encoding="utf-8"), str(p), "exec")

    def test_stock_fx_expansion_is_isolated_locked_and_holdout_sealed(self):
        default = (
            "continuous_config.json",
            "continuous_state",
            "strategy_library/universe_plan.json",
        )
        try:
            continuous_runner.configure_paths(
                "stock_fx_config.json",
                "stock_fx_state",
                "strategy_library/stock_fx_universe_plan.json",
            )
            tracks = continuous_runner.build_tracks()
            self.assertEqual(len(tracks), 1110)
            by_market = {}
            for track in tracks:
                by_market[track["target"]["market"]] = (
                    by_market.get(track["target"]["market"], 0) + 1
                )
            self.assertEqual(by_market, {"stock": 900, "forex": 210})
            targets = {track["target"]["id"] for track in tracks}
            self.assertNotIn("aapl", targets)
            self.assertNotIn("nvda", targets)
            self.assertTrue({"msft", "tsla", "brkb", "eurusd", "usdjpy"} <= targets)
            fx_families = {
                track["family"]["id"] for track in tracks
                if track["target"]["market"] == "forex"
            }
            self.assertNotIn("zanger_volume_breakout_proxy", fx_families)
            self.assertNotIn("swing_terminal_breakout_proxy", fx_families)
            self.assertEqual(len(fx_families), 15)
            self.assertFalse(
                continuous_runner.hidden_validation_allowed_by_universe_plan()
            )
            stock_tracks = [
                t for t in tracks if t["target"]["market"] == "stock"
            ]
            fx_tracks = [
                t for t in tracks if t["target"]["market"] == "forex"
            ]
            self.assertTrue(
                all(
                    continuous_runner.development_end(t) == "2020-12-31"
                    for t in stock_tracks
                )
            )
            self.assertTrue(
                all(
                    continuous_runner.development_end(t) == "2021-12-31"
                    for t in fx_tracks
                )
            )
        finally:
            continuous_runner.configure_paths(*default)
        self.assertEqual(len(continuous_runner.build_tracks()), 514)

    def test_stock_fx_scheduler_interleaves_markets_proportionally(self):
        default = (
            "stock_fx_config.json",
            "stock_fx_state",
            "strategy_library/stock_fx_universe_plan.json",
        )
        original = (
            continuous_runner.CONFIG.name,
            continuous_runner.STATE.name,
            str(continuous_runner.UNIVERSE_PLAN.relative_to(HERE)),
        )
        try:
            continuous_runner.configure_paths(*default)
            tracks = continuous_runner.build_tracks()
            first_cycle = tracks[:6]
            first_block = tracks[:37]
            cycle_markets = {t["target"]["market"] for t in first_cycle}
            block_counts = {}
            for track in first_block:
                market = track["target"]["market"]
                block_counts[market] = block_counts.get(market, 0) + 1
            self.assertEqual(cycle_markets, {"stock", "forex"})
            self.assertEqual(block_counts, {"stock": 30, "forex": 7})
        finally:
            continuous_runner.configure_paths(*original)


    def test_causal_monthly_seed_is_phase2_only_and_future_invariant(self):
        import phase2_prior_runner
        import phase2_seed_factory

        monthly_tracks = [
            x for x in phase2_prior_runner.build_tracks()
            if x["family"] == "bitcoin_cycle_monthly_causal"
        ]
        self.assertEqual(len(monthly_tracks), 2)
        self.assertTrue(
            all(x["target"]["source"] == "bitstamp" for x in monthly_tracks)
        )
        self.assertTrue(
            all(x["target"]["symbol"] == "BTCUSD" for x in monthly_tracks)
        )
        registry_row = next(
            x for x in self.registry["families"]
            if x["id"] == "calendar_monthly"
        )
        self.assertEqual(registry_row["status"], "phase2_runnable")
        self.assertEqual(len(continuous_runner.build_tracks()), 514)

        import importlib.util
        dates = pd.date_range("2011-08-31", "2020-12-31", freq="ME", tz="UTC")
        close = pd.Series(
            100.0 * np.cumprod(1.0 + 0.01 * np.sin(np.arange(len(dates)))),
            index=dates,
        )
        high = close * 1.01
        low = close * 0.99
        volume = pd.Series(1_000.0, index=dates)
        with tempfile.TemporaryDirectory() as td:
            strategy_path = Path(td) / "monthly_strategy.py"
            phase2_seed_factory.generate(
                "bitcoin_cycle_monthly_causal",
                strategy_path,
                365,
                0.08,
                0.5,
            )
            spec = importlib.util.spec_from_file_location(
                "monthly_strategy_test", strategy_path
            )
            module = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(module)
            original = module.phase2_signal(
                close.to_numpy(), high.to_numpy(), low.to_numpy(),
                volume.to_numpy(), dates.to_numpy(),
            )
            changed = close.copy()
            changed.loc[changed.index >= "2018-01-01"] *= 25.0
            changed_signal = module.phase2_signal(
                changed.to_numpy(), (changed * 1.01).to_numpy(),
                (changed * 0.99).to_numpy(),
                volume.to_numpy(), dates.to_numpy(),
            )
        cutoff = dates < pd.Timestamp("2018-01-01", tz="UTC")
        np.testing.assert_array_equal(original[cutoff], changed_signal[cutoff])

    def test_recovered_prior_work_classifications_are_locked(self):
        by_id = {x["id"]: x for x in self.registry["families"]}
        self.assertEqual(by_id["calendar_monthly"]["status"], "phase2_runnable")
        self.assertEqual(
            by_id["finlab_rotation_exact"]["status"], "prior_rejected"
        )
        self.assertEqual(
            by_id["finlab_rotation_exact"]["prior_classification"], "FAIL"
        )
        self.assertEqual(
            by_id["hr_dual_alpha"]["status"], "prior_frozen_superior_pass"
        )
        self.assertEqual(
            by_id["hr_dual_alpha"]["prior_classification"], "SUPERIOR_PASS"
        )
        self.assertEqual(
            by_id["hr_dual_alpha"]["source_lock"]["commit"],
            "abfe2babadd20ca4c6c1b36af0545691e3bb6dde",
        )
        self.assertEqual(
            by_id["hr_dual_alpha"]["source_lock"]["implementation_blob_sha"],
            "27a24f0bc1883c497af23ff3a27918e35f3f4c11",
        )

    def test_phase2_followup_cscv_is_deterministic_bounded_and_candidate_specific(self):
        import phase2_followup_runner

        rows = []
        for i in range(7):
            rows.append({
                "track_id": f"s{i}",
                "cscv_slice_k": [
                    float(np.sin((i + 1) * (j + 1)) + 0.1 * i)
                    for j in range(8)
                ],
            })
        a = phase2_followup_runner.cohort_cscv(rows)
        b = phase2_followup_runner.cohort_cscv(list(reversed(rows)))
        self.assertIsNotNone(a)
        self.assertIsNotNone(b)
        self.assertGreaterEqual(a["pbo"], 0.0)
        self.assertLessEqual(a["pbo"], 1.0)
        self.assertEqual(a["cscv_splits"], 70)
        self.assertEqual(a["pbo"], b["pbo"])
        self.assertEqual(a["candidate_count"], 7)
        self.assertEqual(set(a["candidate_diagnostics"]), {f"s{i}" for i in range(7)})

    def test_phase2_followup_small_cohort_stays_provisional(self):
        import phase2_followup_runner

        rows = [
            {"track_id": f"s{i}", "cscv_slice_k": [float(i + j) for j in range(8)]}
            for i in range(4)
        ]
        self.assertIsNone(phase2_followup_runner.cohort_cscv(rows))

    def test_phase2_followup_builds_hashed_promotion_source(self):
        import hashlib
        import phase2_followup_runner as follow

        source = (
            'import numpy as np\n'
            'import pandas as pd\n'
            'from backtesting import Strategy\n'
            'FAMILY = "bollinger_breakout_20_2"\n'
            'class AtlasStrategy(Strategy):\n'
            '    def init(self): pass\n'
            '    def next(self): pass\n'
        )
        source_sha = hashlib.sha256(source.encode("utf-8")).hexdigest()
        rows = []
        for i in range(5):
            rows.append({
                "track_id": f"bollinger_breakout_20_2__btc__private_{i}",
                "family": "bollinger_breakout_20_2",
                "target": "btc",
                "market": "crypto",
                "profile": "private",
                "status": "ok",
                "score": 10.0 - i,
                "cagr_pct": 50.0 - i,
                "sharpe": 1.5,
                "pf": 3.0,
                "max_dd_pct": -10.0,
                "trades": 40,
                "evidence_grade": "A",
                "strategy_sha256": source_sha,
                "harness_sha256": "h",
                "program_sha256": "p",
                "extreme_stress_return_pct": 20.0,
                "bootstrap_mean_positive_pvalue": 0.01,
                "guard_ok": True,
                "lookahead_pass": True,
                "cscv_slice_k": (
                    [10.0] * 8 if i == 0 else [float(4 - i)] * 8
                ),
            })
        selection = {"selection_hash": "frozen"}
        tracks = {
            row["track_id"]: {
                "id": row["track_id"],
                "family": "bollinger_breakout_20_2",
                "target": {
                    "bars_per_year": 365,
                    "commission": 0.001,
                    "margin": 0.25,
                },
                "profile": {
                    "starting_vol_target": 0.30,
                    "f_max": 2.0,
                },
            }
            for row in rows
        }

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            sources = root / "promotion_sources"
            promotion = root / "promotion_queue.json"

            def fake_generate(family, output, bars_per_year, vol_target, f_max):
                Path(output).write_text(source, encoding="utf-8")
                return Path(output)

            with mock.patch.object(follow, "PROMOTION_SOURCES", sources), \
                 mock.patch.object(follow, "PROMOTION", promotion), \
                 mock.patch.object(follow, "track_lookup", return_value=tracks), \
                 mock.patch.object(follow.p2, "generate", side_effect=fake_generate):
                payload = follow.build_promotion(rows, selection)

            ready = [
                row for row in payload["rows"]
                if row["ready_for_v4_replay"]
            ]
            self.assertEqual(len(ready), 1)
            self.assertEqual(ready[0]["track_id"], rows[0]["track_id"])
            self.assertEqual(ready[0]["promotion_source_sha256"], source_sha)
            self.assertEqual(ready[0]["strategy_sha256"], source_sha)
            self.assertTrue((sources / f"{rows[0]['track_id']}.py").exists())
            self.assertEqual(ready[0]["bars_per_year"], 365)
            self.assertEqual(ready[0]["commission"], 0.001)
            self.assertEqual(ready[0]["margin"], 0.25)

    def test_phase2_followup_never_parameter_rescues_or_opens_oos(self):
        import inspect
        import phase2_followup_runner

        src = inspect.getsource(phase2_followup_runner)
        self.assertIn('"parameter_rescue_performed": False', src)
        self.assertIn('"hidden_validation_opened": False', src)
        self.assertIn('"final_oos_opened": False', src)
        self.assertNotIn("--validation", src)
        self.assertEqual(len(continuous_runner.build_tracks()), 514)

    def test_phase3_free_lane_is_finite_and_registry_isolated(self):
        import phase3_free_runner
        self.assertGreaterEqual(len(phase3_free_runner.QUERY_BATCHES), 4)
        self.assertTrue(all(phase3_free_runner.QUERY_BATCHES))
        self.assertFalse((HERE / "phase3_state" / "validation_data").exists())

    def test_phase3_incomplete_specs_are_not_deduplicated(self):
        import phase3_engine_map_runner
        seen = set()
        self.assertFalse(
            phase3_engine_map_runner.register_rule_hash(False, "same", seen)
        )
        self.assertFalse(
            phase3_engine_map_runner.register_rule_hash(False, "same", seen)
        )
        self.assertEqual(seen, set())
        self.assertFalse(
            phase3_engine_map_runner.register_rule_hash(True, "same", seen)
        )
        self.assertTrue(
            phase3_engine_map_runner.register_rule_hash(True, "same", seen)
        )

    def test_phase3_evidence_versioning_terminates_retries(self):
        import phase3_engine_map_runner
        import phase3_reconstruct_runner

        self.assertFalse(phase3_engine_map_runner.hydration_is_current(None))
        self.assertFalse(
            phase3_engine_map_runner.hydration_is_current(
                {"hydration_status": "attempted_no_text", "hydration_version": 1}
            )
        )
        self.assertTrue(
            phase3_engine_map_runner.hydration_is_current(
                {"hydration_status": "attempted_no_text", "hydration_version": 2}
            )
        )
        self.assertTrue(
            phase3_engine_map_runner.hydration_is_current(
                {"hydration_status": "hydrated", "hydration_version": 1}
            )
        )
        self.assertEqual(
            phase3_reconstruct_runner.reconstruction_version_for(None), 1
        )
        self.assertEqual(
            phase3_reconstruct_runner.reconstruction_version_for(
                {"hydration_status": "attempted_no_text", "hydration_version": 2}
            ),
            1,
        )
        self.assertEqual(
            phase3_reconstruct_runner.reconstruction_version_for(
                {"hydration_status": "hydrated", "hydration_version": 1}
            ),
            2,
        )
        self.assertEqual(
            phase3_reconstruct_runner.reconstruction_version_for(
                {"hydration_status": "hydrated", "hydration_version": 2}
            ),
            3,
        )

    def test_stock_fx_config_uses_authenticated_primary_sources_and_no_volume_families(self):
        cfg = json.loads((HERE / "stock_fx_config.json").read_text(encoding="utf-8"))
        fx = [x for x in cfg["targets"] if x["market"] == "forex"]
        stocks = [x for x in cfg["targets"] if x["market"] == "stock"]
        self.assertEqual(len(fx), 7)
        self.assertEqual(len(stocks), 30)
        self.assertTrue(all(x["source"] == "tiingo_fx" for x in fx))
        self.assertTrue(all(x["source"] == "tiingo_eod" for x in stocks))
        self.assertTrue(
            all(x["instrument_fidelity"] == "tiingo_adjusted_eod" for x in stocks)
        )
        self.assertTrue(all(x["validation_start"] == "2022-01-01" for x in fx))
        self.assertNotIn(
            "zanger_volume_breakout_proxy",
            cfg["family_allowlist_by_market"]["forex"],
        )

    def test_tiingo_fx_parser_is_daily_bounded_and_ohlc_valid(self):
        import prepare_market_data as pmd

        start = pmd.dt("2020-01-01")
        end = pmd.dt("2021-12-31").replace(
            hour=23, minute=59, second=59
        )
        payload = [
            {
                "date": "2019-12-31T00:00:00Z",
                "open": 1.10, "high": 1.11, "low": 1.09, "close": 1.105,
            },
            {
                "date": "2020-01-02T00:00:00Z",
                "open": 1.12, "high": 1.13, "low": 1.11, "close": 1.125,
            },
            {
                "date": "2021-12-31T00:00:00Z",
                "open": 1.13, "high": 1.14, "low": 1.12, "close": 1.135,
            },
            {
                "date": "2022-01-03T00:00:00Z",
                "open": 1.14, "high": 1.15, "low": 1.13, "close": 1.145,
            },
        ]
        rows = pmd.tiingo_fx_rows(payload, start, end)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0][:10], "2020-01-02")
        self.assertEqual(rows[-1][0][:10], "2021-12-31")
        self.assertEqual(rows[0][1:5], [1.12, 1.13, 1.11, 1.125])

    def test_tiingo_eod_parser_uses_adjusted_prices_and_is_bounded(self):
        import prepare_market_data as pmd

        start = pmd.dt("2010-01-01")
        end = pmd.dt("2020-12-31").replace(
            hour=23, minute=59, second=59
        )
        payload = [
            {
                "date": "2009-12-31T00:00:00Z",
                "adjOpen": 49.0, "adjHigh": 51.0, "adjLow": 48.0,
                "adjClose": 50.0, "adjVolume": 900.0,
            },
            {
                "date": "2010-01-04T00:00:00Z",
                "open": 100.0, "high": 104.0, "low": 98.0, "close": 102.0,
                "adjOpen": 50.0, "adjHigh": 52.0, "adjLow": 49.0,
                "adjClose": 51.0, "adjVolume": 2000.0,
            },
            {
                "date": "2020-12-31T00:00:00Z",
                "adjOpen": 75.0, "adjHigh": 77.0, "adjLow": 74.0,
                "adjClose": 76.0, "adjVolume": 3000.0,
            },
            {
                "date": "2021-01-04T00:00:00Z",
                "adjOpen": 77.0, "adjHigh": 79.0, "adjLow": 76.0,
                "adjClose": 78.0, "adjVolume": 3100.0,
            },
        ]
        rows = pmd.tiingo_eod_rows(payload, start, end)
        self.assertEqual(len(rows), 2)
        self.assertEqual(rows[0][0][:10], "2010-01-04")
        self.assertEqual(rows[-1][0][:10], "2020-12-31")
        self.assertEqual(rows[0][1:6], [50.0, 52.0, 49.0, 51.0, 2000.0])

    def test_dukascopy_daily_decoder_is_scaled_and_filters_forward_fill(self):
        import lzma
        import struct
        import dukascopy_daily

        candle = struct.Struct("!IIIIIf")
        raw = b"".join([
            candle.pack(0, 110000, 110100, 109900, 110200, 12.5),
            candle.pack(86400, 110000, 110100, 109900, 110200, 12.5),
            candle.pack(172800, 110200, 110300, 110100, 110400, 15.0),
        ])
        rows, audit = dukascopy_daily.decode_daily_blob(
            lzma.compress(raw), "EURUSD", 2020
        )
        self.assertEqual(len(rows), 2)
        self.assertEqual(audit["filtered_forward_fill_records"], 1)
        self.assertEqual(rows[0][0][:10], "2020-01-01")
        self.assertAlmostEqual(rows[0][1], 1.10)
        self.assertAlmostEqual(rows[0][2], 1.102)
        self.assertAlmostEqual(rows[0][3], 1.099)
        self.assertAlmostEqual(rows[0][4], 1.101)

        jpy_raw = candle.pack(
            0, 109500, 109600, 109400, 109700, 9.0
        )
        jpy_rows, _ = dukascopy_daily.decode_daily_blob(
            lzma.compress(jpy_raw), "USDJPY", 2020
        )
        self.assertAlmostEqual(jpy_rows[0][1], 109.5)
        self.assertAlmostEqual(jpy_rows[0][4], 109.6)

    def test_track_state_source_identity_invalidates_only_changed_provider(self):
        track = {
            "target": {
                "source": "yahoo_futures_proxy",
                "symbol": "ES=F",
            }
        }
        legacy_raw = {
            "protocol": continuous_runner.PROTOCOL,
            "symbol": "ES=F",
            "data_manifest": {"source": "yahoo"},
        }
        normalized = {
            "protocol": continuous_runner.PROTOCOL,
            "symbol": "ES=F",
            "data_manifest": {"source": "yahoo_futures_proxy"},
        }
        unrelated = {
            "protocol": continuous_runner.PROTOCOL,
            "symbol": "SPY",
            "data_manifest": {"source": "yahoo"},
        }
        self.assertFalse(
            continuous_runner.track_state_identity_matches(
                track, legacy_raw
            )
        )
        self.assertTrue(
            continuous_runner.track_state_identity_matches(
                track, normalized
            )
        )
        stock_track = {
            "target": {"source": "yahoo", "symbol": "SPY"}
        }
        self.assertTrue(
            continuous_runner.track_state_identity_matches(
                stock_track, unrelated
            )
        )

    def test_track_state_identity_rejects_changed_timeframe(self):
        track = {
            "target": {
                "source": "yahoo",
                "symbol": "SPY",
                "timeframe": "1D",
            }
        }
        same = {
            "protocol": continuous_runner.PROTOCOL,
            "symbol": "SPY",
            "target_source": "yahoo",
            "tested_timeframe": "1D",
        }
        stale = dict(same, tested_timeframe="1H")
        self.assertTrue(
            continuous_runner.track_state_identity_matches(track, same)
        )
        self.assertFalse(
            continuous_runner.track_state_identity_matches(track, stale)
        )

    def test_track_state_identity_prefers_explicit_source_over_manifest(self):
        track = {
            "target": {
                "source": "yahoo_futures_proxy",
                "symbol": "ES=F",
            }
        }
        meta = {
            "protocol": continuous_runner.PROTOCOL,
            "symbol": "ES=F",
            "target_source": "yahoo_futures_proxy",
            "data_manifest": {"source": "yahoo"},
        }
        self.assertTrue(
            continuous_runner.track_state_identity_matches(track, meta)
        )

    def test_yahoo_futures_proxy_normalization_is_explicit_and_conservative(self):
        import prepare_market_data
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "es_1d.csv"
            p.write_text(
                "Date,Open,High,Low,Close,Volume\n"
                "2020-01-02T00:00:00+00:00,100,104,99,105,10\n"
                "2020-01-03T00:00:00+00:00,105,106,101,102,12\n",
                encoding="utf-8",
            )
            meta = prepare_market_data.normalize_yahoo_futures_proxy(p)
            df = pd.read_csv(p)
            self.assertEqual(meta["changed_rows"], 1)
            self.assertEqual(float(df.loc[0, "Open"]), 100.0)
            self.assertEqual(float(df.loc[0, "Close"]), 105.0)
            self.assertEqual(float(df.loc[0, "High"]), 105.0)
            self.assertEqual(float(df.loc[0, "Low"]), 99.0)
            audit = json.loads(
                p.with_suffix(".normalization.json").read_text(
                    encoding="utf-8"
                )
            )
            self.assertEqual(audit["changes"][0]["Date"], "2020-01-02T00:00:00+00:00")

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

    def test_target_env_enables_lane_scoped_research_intelligence(self):
        track = {
            "id": "test_family__btc__private",
            "family": {"id": "test_family"},
            "target": {
                "id": "btc",
                "symbol": "BTCUSDT",
                "market": "crypto",
                "commission": 0.001,
                "margin": 1.0,
                "bars_per_year": 365,
                "start": "2019-01-01",
                "validation_start": "2021-01-01",
                "validation_end": "2022-12-31",
            },
            "profile_name": "private",
            "profile": {"max_dd_pct": 32.0},
        }
        env = continuous_runner.target_env(track)
        self.assertEqual(env["AUTORESEARCH_EVOMIND_ENABLED"], "1")
        self.assertEqual(
            env["AUTORESEARCH_TRACK_ID"],
            "test_family__btc__private",
        )
        self.assertEqual(
            env["AUTORESEARCH_PROTOCOL"],
            continuous_runner.PROTOCOL,
        )
        self.assertEqual(
            Path(env["AUTORESEARCH_EVOMIND_DB"]).name,
            "evomind.db",
        )
        self.assertEqual(
            Path(env["AUTORESEARCH_EVOMIND_DB"]).parent,
            continuous_runner.STATE,
        )
        self.assertEqual(
            env["AUTORESEARCH_YOUTUBE_INTELLIGENCE_ENABLED"],
            "1",
        )
        self.assertEqual(
            Path(env["AUTORESEARCH_YOUTUBE_INTELLIGENCE_DB"]).name,
            "youtube_intelligence.db",
        )
        self.assertEqual(
            Path(env["AUTORESEARCH_YOUTUBE_INTELLIGENCE_DB"]).parent,
            continuous_runner.STATE,
        )
        self.assertEqual(
            Path(env["AUTORESEARCH_YOUTUBE_INTELLIGENCE_FEED"]).name,
            "youtube_intelligence_feed.jsonl",
        )
        self.assertEqual(
            env["AUTORESEARCH_YOUTUBE_PUBLISHED_CUTOFF"],
            "2020-12-31",
        )

    def test_successive_halving_targets_are_monotonic(self):
        breadth, depth, elite = 10, 30, 60
        self.assertLessEqual(breadth, depth)
        self.assertLessEqual(depth, elite)

    def test_numeric_only_mutation_is_structurally_detectable(self):
        a = """
class AtlasStrategy:
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
class AtlasStrategy:
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

class AtlasStrategy:
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
        self.assertEqual(detail, ["AtlasStrategy.next"])

    def test_wholesale_rewrite_is_rejected(self):
        base = """
def h1(x): return x
def h2(x): return x

class AtlasStrategy:
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

class AtlasStrategy:
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

    def test_cscv_loader_includes_baseline_deduplicates_and_matches_fingerprints(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "baseline.json").write_text(
                json.dumps({
                    "strategy_sha256": "baseline",
                    "harness_sha256": "h1",
                    "program_sha256": "p1",
                    "cscv_slices": [
                        {"raw_k": 0.1 + 0.01 * i} for i in range(8)
                    ],
                }),
                encoding="utf-8",
            )
            rows = [
                {
                    "candidate_ast_sha256": f"c{i}",
                    "selection_eligible": True,
                    "harness_sha256": "h1",
                    "program_sha256": "p1",
                    "cscv_slice_k": [0.02 * (i + j) for j in range(8)],
                }
                for i in range(4)
            ]
            rows += [
                dict(rows[-1]),
                {
                    "candidate_ast_sha256": "baseline-different-ast",
                    "candidate_source_sha256": "baseline",
                    "selection_eligible": True,
                    "harness_sha256": "h1",
                    "program_sha256": "p1",
                    "cscv_slice_k": [8.0] * 8,
                },
                {
                    "candidate_ast_sha256": "wrong-harness",
                    "selection_eligible": True,
                    "harness_sha256": "other",
                    "program_sha256": "p1",
                    "cscv_slice_k": [9.0] * 8,
                },
            ]
            p = root / "experiments.jsonl"
            p.write_text(
                "".join(json.dumps(x) + "\n" for x in rows),
                encoding="utf-8",
            )
            matrix, ids = overfit_diagnostics.load_experiment_fold_matrix(
                p,
                baseline_path=root / "baseline.json",
            )
            diag = overfit_diagnostics.track_pbo(p)
        self.assertEqual(matrix.shape, (5, 8))
        self.assertEqual(ids[0], "baseline")
        self.assertEqual(len(ids), len(set(ids)))
        self.assertIsNotNone(diag)
        self.assertEqual(diag["candidate_count"], 5)
        self.assertEqual(
            diag["candidate_pool"],
            "frozen_baseline_plus_unique_selection_eligible_backtests",
        )

    def test_cscv_midrank_ties_are_order_invariant(self):
        tied = np.ones((5, 8), dtype=float)
        out = overfit_diagnostics.cscv_pbo(tied)
        self.assertIsNotNone(out)
        self.assertEqual(out["pbo"], 0.0)
        self.assertAlmostEqual(out["median_oos_logit"], 0.0, places=12)

    def test_cscv_requires_five_distinct_strategies(self):
        self.assertIsNone(
            overfit_diagnostics.cscv_pbo(np.ones((4, 8), dtype=float))
        )
        out = overfit_diagnostics.cscv_pbo(
            np.arange(40, dtype=float).reshape(5, 8)
        )
        self.assertIsNotNone(out)
        self.assertEqual(out["candidate_count"], 5)

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


    def test_thompson_prior_uses_matched_cases_not_repeated_trials(self):
        old_state = continuous_runner.TOURNAMENT_STATE
        old_ledger = continuous_runner.LEDGER
        try:
            with tempfile.TemporaryDirectory() as td:
                td = Path(td)
                continuous_runner.TOURNAMENT_STATE = td / "tournament.json"
                continuous_runner.LEDGER = td / "missing_cycles.jsonl"
                continuous_runner.save_json(
                    continuous_runner.TOURNAMENT_STATE,
                    {
                        "protocol": continuous_runner.PROTOCOL,
                        "provisional": False,
                        "ranking": [{
                            "provider": "nvidia",
                            "model": "model-a",
                            "admitted": 3,
                            "attempts": 6,
                            "would_keep": 3,
                            "case_aggregates": {
                                "case-1": {"keep_rate": 0.5},
                                "case-2": {"keep_rate": 0.0},
                                "case-3": {"keep_rate": 1.0},
                            },
                        }],
                    },
                )
                rows = continuous_runner.tournament_bandit_priors()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["tournament_cases"], 3)
                self.assertEqual(rows[0]["tournament_successes"], 2)
                self.assertEqual(rows[0]["prior_alpha"], 3.0)
                self.assertEqual(rows[0]["prior_beta"], 2.0)
        finally:
            continuous_runner.TOURNAMENT_STATE = old_state
            continuous_runner.LEDGER = old_ledger

    def test_thompson_posterior_updates_one_reward_per_model_visit(self):
        old_state = continuous_runner.TOURNAMENT_STATE
        old_ledger = continuous_runner.LEDGER
        try:
            with tempfile.TemporaryDirectory() as td:
                td = Path(td)
                continuous_runner.TOURNAMENT_STATE = td / "tournament.json"
                continuous_runner.LEDGER = td / "cycles.jsonl"
                continuous_runner.save_json(
                    continuous_runner.TOURNAMENT_STATE,
                    {
                        "protocol": continuous_runner.PROTOCOL,
                        "provisional": False,
                        "ranking": [{
                            "provider": "nvidia",
                            "model": "model-a",
                            "admitted": 2,
                            "case_aggregates": {
                                "case-1": {"keep_rate": 1.0},
                                "case-2": {"keep_rate": 0.0},
                            },
                        }],
                    },
                )
                continuous_runner.LEDGER.write_text(
                    '{"model":"model-a","bandit_reward":1}\n'
                    '{"model":"model-a","bandit_reward":0}\n',
                    encoding="utf-8",
                )
                row = continuous_runner.model_bandit_snapshot()[0]
                self.assertEqual(row["online_visits"], 2)
                self.assertEqual(row["online_successes"], 1)
                self.assertEqual(row["posterior_alpha"], 3.0)
                self.assertEqual(row["posterior_beta"], 3.0)
                self.assertAlmostEqual(row["posterior_keeper_probability"], 0.5)
        finally:
            continuous_runner.TOURNAMENT_STATE = old_state
            continuous_runner.LEDGER = old_ledger

    def test_thompson_router_is_reproducible_but_explores(self):
        candidates = [
            {
                "model": "model-a",
                "posterior_alpha": 2.0,
                "posterior_beta": 2.0,
                "posterior_keeper_probability": 0.5,
            },
            {
                "model": "model-b",
                "posterior_alpha": 2.0,
                "posterior_beta": 2.0,
                "posterior_keeper_probability": 0.5,
            },
        ]
        tracks = continuous_runner.build_tracks()[:40]
        with mock.patch.object(
            continuous_runner, "model_bandit_snapshot", return_value=candidates
        ):
            first = continuous_runner.select_research_model(tracks[0], "auto", 0)
            second = continuous_runner.select_research_model(tracks[0], "auto", 0)
            self.assertEqual(first, second)
            chosen = {
                continuous_runner.select_research_model(track, "auto", i % 3)
                for i, track in enumerate(tracks)
            }
            self.assertEqual(chosen, {"model-a", "model-b"})



    def test_opportunity_visit_schedule_preserves_majority_exploration(self):
        slots = continuous_runner.opportunity_visit_indices(8, 0.30)
        self.assertEqual(slots, {2, 5})
        self.assertLessEqual(len(slots), 8 // 2)
        self.assertEqual(
            continuous_runner.opportunity_visit_indices(8, 0.0),
            set(),
        )

    def test_track_allocator_prioritizes_money_opportunity_without_replacing_round_robin(self):
        tracks = continuous_runner.build_tracks()[:3]
        plan = {track["id"]: 10 for track in tracks}
        scores = {
            tracks[0]["id"]: 0.95,
            tracks[1]["id"]: 0.80,
            tracks[2]["id"]: 0.30,
        }
        counts = {
            track["id"]: {
                "valid": 4,
                "attempts": 4,
                "guard_passed": 3,
            }
            for track in tracks
        }

        def count_for(track):
            return counts[track["id"]]

        def meta_for(track):
            return {
                "baseline": {
                    "guard_ok": True,
                    "cagr_pct": 50.0,
                    "calmar": 2.0,
                    "evidence_grade": "A",
                    "extreme_stress": {"cagr_pct": 45.0},
                }
            }

        def score_for(track):
            return scores[track["id"]]

        def pbo_for(track):
            if track["id"] == tracks[0]["id"]:
                return {"pbo": 0.20}
            return None

        with mock.patch.object(
            continuous_runner, "is_terminal_block", return_value=False
        ), mock.patch.object(
            continuous_runner, "track_counts", side_effect=count_for
        ), mock.patch.object(
            continuous_runner, "track_meta", side_effect=meta_for
        ), mock.patch.object(
            continuous_runner, "development_selection_score", side_effect=score_for
        ), mock.patch.object(
            continuous_runner, "development_overfit", side_effect=pbo_for
        ):
            exploit = continuous_runner.next_search_track(
                tracks,
                plan,
                0,
                prefer_opportunity=True,
            )
            explore = continuous_runner.next_search_track(
                tracks,
                plan,
                0,
                prefer_opportunity=False,
            )

        # Low-PBO stronger alpha now beats a merely missing-PBO candidate.
        self.assertEqual(exploit[1]["id"], tracks[0]["id"])
        self.assertEqual(explore[1]["id"], tracks[0]["id"])

    def test_opportunity_allocator_cannot_cross_market_slot(self):
        tracks = [
            {"id": "stock_a", "target": {"market": "stock"}},
            {"id": "fx_a", "target": {"market": "forex"}},
            {"id": "stock_b", "target": {"market": "stock"}},
        ]
        plan = {t["id"]: 10 for t in tracks}
        scores = {"stock_a": 0.4, "fx_a": 0.3, "stock_b": 0.9}

        with mock.patch.object(
            continuous_runner, "is_terminal_block", return_value=False
        ), mock.patch.object(
            continuous_runner, "track_counts",
            return_value={"valid": 2, "attempts": 2, "guard_passed": 2},
        ), mock.patch.object(
            continuous_runner, "track_meta",
            return_value={
                "baseline": {
                    "guard_ok": True,
                    "cagr_pct": 40.0,
                    "calmar": 2.0,
                    "evidence_grade": "A",
                    "extreme_stress": {"cagr_pct": 35.0},
                }
            },
        ), mock.patch.object(
            continuous_runner, "development_selection_score",
            side_effect=lambda t: scores[t["id"]],
        ), mock.patch.object(
            continuous_runner, "development_overfit",
            return_value={"pbo": 0.10},
        ):
            chosen = continuous_runner.next_search_track(
                tracks,
                plan,
                0,
                prefer_opportunity=True,
                opportunity_market="forex",
            )

        self.assertEqual(chosen[1]["id"], "fx_a")

    def test_opportunity_visit_preserves_exploration_cursor(self):
        self.assertEqual(
            continuous_runner.next_cursor_after_visit(
                99,
                120,
                prefer_opportunity=True,
                scheduled_cursor_next=6,
            ),
            6,
        )
        self.assertEqual(
            continuous_runner.next_cursor_after_visit(
                99,
                120,
                prefer_opportunity=False,
                scheduled_cursor_next=6,
            ),
            100,
        )

    def test_money_opportunity_rewards_stress_surviving_growth(self):
        tracks = continuous_runner.build_tracks()[:2]
        metas = {
            tracks[0]["id"]: {
                "baseline": {
                    "guard_ok": True,
                    "cagr_pct": 80.0,
                    "calmar": 1.0,
                    "evidence_grade": "A",
                    "extreme_stress": {"cagr_pct": 0.0},
                }
            },
            tracks[1]["id"]: {
                "baseline": {
                    "guard_ok": True,
                    "cagr_pct": 70.0,
                    "calmar": 3.0,
                    "evidence_grade": "A",
                    "extreme_stress": {"cagr_pct": 65.0},
                }
            },
        }
        scores = {tracks[0]["id"]: 0.90, tracks[1]["id"]: 0.65}

        with mock.patch.object(
            continuous_runner, "track_meta", side_effect=lambda t: metas[t["id"]]
        ), mock.patch.object(
            continuous_runner, "development_selection_score",
            side_effect=lambda t: scores[t["id"]],
        ), mock.patch.object(
            continuous_runner, "development_overfit",
            return_value={"pbo": 0.10},
        ), mock.patch.object(
            continuous_runner, "track_counts",
            return_value={"valid": 8, "attempts": 9, "guard_passed": 7},
        ):
            a = continuous_runner._money_opportunity_value(tracks[0])[0]
            b = continuous_runner._money_opportunity_value(tracks[1])[0]

        self.assertGreater(b, a)

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
            'class AtlasStrategy:\n'
            '    def next(self):\n'
            '        __builtins__["open"]("hidden.csv")\n'
        )
        with self.assertRaises(ValueError):
            loop.validate_source_safety(tree)

    def test_strategy_safety_rejects_unlisted_pandas_reader_family(self):
        tree = __import__("ast").parse(
            'import pandas as pd\n'
            'class AtlasStrategy:\n'
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
        old = continuous_runner.TOURNAMENT_STATE
        try:
            with tempfile.TemporaryDirectory() as td:
                p = Path(td) / "tournament.json"
                continuous_runner.TOURNAMENT_STATE = p
                continuous_runner.save_json(
                    p,
                    {
                        "protocol": continuous_runner.PROTOCOL,
                        "provisional": True,
                        "ranking": [{
                            "provider": "nvidia",
                            "model": "should-not-run",
                            "admitted": 99,
                            "case_aggregates": {"x": {"keep_rate": 1.0}},
                        }],
                    },
                )
                track = continuous_runner.build_tracks()[0]
                self.assertEqual(
                    continuous_runner.select_research_model(track, "auto", 0),
                    continuous_runner.DEFAULT_MODEL,
                )
        finally:
            continuous_runner.TOURNAMENT_STATE = old

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
