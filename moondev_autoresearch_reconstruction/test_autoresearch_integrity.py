import json
import os
import tempfile
import unittest
from pathlib import Path

import pandas as pd

import continuous_runner
import loop
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

    def test_protocol_is_nested_v2(self):
        self.assertEqual(continuous_runner.PROTOCOL, "nested_chronological_v2")
        text = (HERE / "robust_harness.py").read_text(encoding="utf-8")
        self.assertIn('PROTOCOL = "nested_chronological_v2"', text)

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
        source = seed_factory.render_strategy(
            family="connors_double7",
            bars_per_year=252,
            vol_target=0.08,
            f_max=0.5,
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


if __name__ == "__main__":
    unittest.main()
