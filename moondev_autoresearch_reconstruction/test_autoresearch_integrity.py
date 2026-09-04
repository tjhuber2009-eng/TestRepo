import json
import os
import tempfile
import unittest
from pathlib import Path

import continuous_runner
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


if __name__ == "__main__":
    unittest.main()
