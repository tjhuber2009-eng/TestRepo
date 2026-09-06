from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import atlas_evomind as ae


def strong_result(cagr=35.0, sharpe=1.8, pf=3.0):
    return {
        "guard_ok": True,
        "cagr_pct": cagr,
        "sharpe": sharpe,
        "pf": pf,
        "bootstrap_mean_positive_pvalue": 0.04,
        "evidence_grade": "A",
        "paired_vs_baseline": {
            "improved_fold_fraction": 0.75,
            "median_fold_delta_k": 0.03,
            "comparable_folds": 8,
        },
    }


class AtlasEvoMindTests(unittest.TestCase):
    def test_release_provenance_and_safe_defaults_are_frozen(self):
        manifest = json.loads(
            (
                Path(__file__).resolve().parent
                / "vendor" / "evomind" / "VENDORED.json"
            ).read_text()
        )
        self.assertEqual(ae.EVOMIND_VERSION, "0.10.0")
        self.assertEqual(
            manifest["source_sha256"], ae.EVOMIND_V010_SOURCE_SHA256
        )
        self.assertEqual(
            manifest["wheel_sha256"], ae.EVOMIND_V010_WHEEL_SHA256
        )
        self.assertEqual(
            manifest["release_sha256"], ae.EVOMIND_V010_RELEASE_SHA256
        )
        self.assertEqual(
            manifest["upstream_component_hashes"]["meta_search_py"],
            ae.EVOMIND_V010_META_SEARCH_SHA256,
        )
        self.assertEqual(
            ae.EVOMIND_SAFE_DEFAULTS,
            {
                "adaptive_portfolio": False,
                "compute_cost_penalty": 0.0,
                "islands": 1,
            },
        )
        self.assertFalse(
            manifest["atlas_integration"]["may_open_hidden_validation"]
        )
        self.assertFalse(
            manifest["atlas_integration"]["may_open_final_oos"]
        )
        self.assertFalse(
            manifest["atlas_integration"]["may_keep_or_promote_strategies"]
        )

    def test_memory_persists_and_transfers_advisory_concepts(self):
        with tempfile.TemporaryDirectory() as td:
            db = Path(td) / "evomind.db"
            first = ae.EvoMindAtlasBrain(
                db,
                domain="crypto:qqe_proxy:private",
                track_id="qqe_proxy__btc__private",
                seed_material="first",
            )
            try:
                guidance, arm = first.guidance(1)
                self.assertIn(arm, ae.ProposalPortfolioBandit.ARMS)
                self.assertIn("advisory only", guidance)
                learned = first.learn(
                    arm=arm,
                    verdict="KEPT",
                    description=(
                        "Use a causal momentum regime confirmation for "
                        "QQE entries"
                    ),
                    result=strong_result(),
                    base_score=0.50,
                    candidate_score=0.62,
                    evidence_id="abc123",
                    family="qqe_proxy",
                    market="crypto",
                )
                self.assertEqual(learned["reward"], 1.0)
                self.assertGreater(learned["concept_score"], 0.50)
            finally:
                first.close()

            second = ae.EvoMindAtlasBrain(
                db,
                domain="stock:connors_double7:private",
                track_id="connors_double7__bac__private",
                seed_material="second",
            )
            try:
                transfer = second.transferable_concepts()
                self.assertTrue(transfer)
                self.assertEqual(
                    transfer[0].domain, "crypto:qqe_proxy:private"
                )
                self.assertIn("momentum", transfer[0].tags)
                snapshot = second.snapshot()
                self.assertFalse(snapshot["hidden_validation_access"])
                self.assertFalse(snapshot["final_oos_access"])
            finally:
                second.close()

    def test_repeated_failed_mechanism_becomes_avoid_signal(self):
        with tempfile.TemporaryDirectory() as td:
            brain = ae.EvoMindAtlasBrain(
                Path(td) / "evomind.db",
                domain="crypto:test:private",
                track_id="test__btc__private",
                seed_material="negative",
            )
            try:
                for i in range(2):
                    brain.learn(
                        arm="immigrant",
                        verdict="CRASH",
                        description="Add a breakout confirmation layer",
                        result=None,
                        base_score=0.4,
                        candidate_score=float("nan"),
                        evidence_id=f"bad{i}",
                        family="test",
                        market="crypto",
                    )
                guidance, _ = brain.guidance(3)
                self.assertIn("Repeated weak mechanisms", guidance)
                self.assertIn("breakout", guidance)
            finally:
                brain.close()

    def test_environment_entrypoint_never_needs_market_or_oos_paths(self):
        with tempfile.TemporaryDirectory() as td:
            env = {
                "AUTORESEARCH_EVOMIND_ENABLED": "1",
                "AUTORESEARCH_EVOMIND_DB": str(Path(td) / "evomind.db"),
                "AUTORESEARCH_TRACK_ID": "family__asset__private",
                "AUTORESEARCH_PROTOCOL": "nested_chronological_v3",
                "AUTORESEARCH_MARKET": "crypto",
                "AUTORESEARCH_FAMILY": "family",
                "AUTORESEARCH_PROFILE": "private",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                guidance, arm = ae.prompt_guidance(1)
                self.assertTrue(guidance)
                self.assertIsNotNone(arm)
                update = ae.learn_from_atlas(
                    iteration=1,
                    arm=arm,
                    verdict="REJECTED",
                    description="Try a causal pullback entry confirmation",
                    result=strong_result(cagr=12.0, sharpe=1.0, pf=1.5),
                    base_score=0.50,
                    candidate_score=0.49,
                    evidence_id="candidate",
                )
                self.assertIsNotNone(update)
                self.assertTrue(Path(env["AUTORESEARCH_EVOMIND_DB"]).exists())
                self.assertNotIn("validation_data", guidance)
                self.assertNotIn("2023", guidance)


if __name__ == "__main__":
    unittest.main()
