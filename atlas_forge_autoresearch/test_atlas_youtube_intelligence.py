from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import atlas_youtube_intelligence as ayi


def write_feed(path: Path) -> None:
    rows = [
        {
            "idea_id": "old-qqe",
            "video_id": "abc",
            "channel_id": "chan-a",
            "channel_title": "Trading Lab",
            "published_at": "2020-06-01",
            "title": "QQE pullback setup",
            "summary": "Use QQE confirmation after a causal pullback.",
            "strategy_rules": [
                "Wait for a pullback before QQE confirmation.",
                "Do not enter on an unconfirmed continuation.",
            ],
            "markets": ["crypto"],
            "timeframes": ["1d"],
            "tags": ["qqe", "pullback"],
            "source_kind": "transcript_analysis",
            "specification_quality": 0.9,
            "claimed_metrics": {"win_rate": "91%", "return": "500%"},
        },
        {
            "idea_id": "future-leak",
            "video_id": "future",
            "channel_id": "chan-b",
            "channel_title": "Future Trader",
            "published_at": "2024-01-01",
            "title": "Later strategy",
            "summary": "A strategy published after the adaptive cutoff.",
            "strategy_rules": ["Buy a later-discovered breakout."],
            "markets": ["crypto"],
            "specification_quality": 1.0,
        },
        {
            "idea_id": "wrong-market",
            "video_id": "fx",
            "channel_id": "chan-c",
            "published_at": "2019-01-01",
            "title": "FX only",
            "summary": "A forex-specific session idea.",
            "strategy_rules": ["Trade London session pullbacks."],
            "markets": ["forex"],
            "specification_quality": 0.95,
        },
    ]
    path.write_text(
        "\n".join(json.dumps(x, sort_keys=True) for x in rows) + "\n",
        encoding="utf-8",
    )


class AtlasYouTubeIntelligenceTests(unittest.TestCase):
    def test_source_provenance_is_frozen(self):
        self.assertEqual(ayi.YOUTUBE_INTELLIGENCE_VERSION, "3.1.0")
        self.assertEqual(
            ayi.YOUTUBE_INTELLIGENCE_SOURCE_BRANCH,
            "yke-v3.1-windows-build",
        )
        self.assertEqual(
            ayi.YOUTUBE_INTELLIGENCE_SOURCE_COMMIT,
            "1f7673b00994fb321fda0b7077c5405529441691",
        )

    def test_post_cutoff_video_is_quarantined_and_never_selected(self):
        with tempfile.TemporaryDirectory() as td:
            feed = Path(td) / "feed.jsonl"
            write_feed(feed)
            brain = ayi.YouTubeAtlasBridge(
                Path(td) / "youtube.db",
                feed_path=feed,
                track_id="qqe__btc__private",
                domain="crypto:qqe:private",
                published_cutoff="2020-12-31",
            )
            try:
                summary = brain.ingest_feed()
                self.assertEqual(summary, {"imported": 3, "quarantined": 1})
                snap = brain.snapshot()
                self.assertEqual(snap["eligible"], 2)
                self.assertEqual(snap["quarantined"], 1)
                guidance, idea_id = brain.guidance(1, "external_proposal")
                self.assertEqual(idea_id, "old-qqe")
                self.assertNotIn("future-leak", guidance)
                self.assertFalse(snap["hidden_validation_access"])
                self.assertFalse(snap["final_oos_access"])
            finally:
                brain.close()

    def test_creator_claims_are_stored_but_withheld_from_prompt(self):
        with tempfile.TemporaryDirectory() as td:
            feed = Path(td) / "feed.jsonl"
            write_feed(feed)
            brain = ayi.YouTubeAtlasBridge(
                Path(td) / "youtube.db",
                feed_path=feed,
                track_id="qqe__btc__private",
                domain="crypto:qqe:private",
                published_cutoff="2020-12-31",
            )
            try:
                guidance, idea_id = brain.guidance(1, "external_proposal")
                self.assertEqual(idea_id, "old-qqe")
                self.assertIn("UNVERIFIED", guidance)
                self.assertIn("independently test", guidance)
                self.assertNotIn("91%", guidance)
                self.assertNotIn("500%", guidance)
            finally:
                brain.close()

    def test_youtube_only_supplies_when_evomind_requests_external_proposal(self):
        with tempfile.TemporaryDirectory() as td:
            feed = Path(td) / "feed.jsonl"
            write_feed(feed)
            brain = ayi.YouTubeAtlasBridge(
                Path(td) / "youtube.db",
                feed_path=feed,
                track_id="qqe__btc__private",
                domain="crypto:qqe:private",
                published_cutoff="2020-12-31",
            )
            try:
                for arm in ["evolution", "synthesis", "immigrant", "skill_transfer"]:
                    guidance, idea_id = brain.guidance(1, arm)
                    self.assertEqual(guidance, "")
                    self.assertIsNone(idea_id)
                guidance, idea_id = brain.guidance(2, "external_proposal")
                self.assertTrue(guidance)
                self.assertEqual(idea_id, "old-qqe")
            finally:
                brain.close()

    def test_outcome_persists_and_prevents_same_track_retest(self):
        with tempfile.TemporaryDirectory() as td:
            feed = Path(td) / "feed.jsonl"
            write_feed(feed)
            db = Path(td) / "youtube.db"
            brain = ayi.YouTubeAtlasBridge(
                db,
                feed_path=feed,
                track_id="qqe__btc__private",
                domain="crypto:qqe:private",
                published_cutoff="2020-12-31",
            )
            try:
                _, idea_id = brain.guidance(1, "external_proposal")
                update = brain.record_outcome(
                    idea_id=idea_id,
                    verdict="KEPT",
                    result={"guard_ok": True},
                    base_score=0.5,
                    candidate_score=0.6,
                )
                self.assertTrue(update["kept"])
                self.assertAlmostEqual(update["delta_k"], 0.1)
                self.assertEqual(brain.snapshot()["keeper_outcomes"], 1)
                guidance, next_id = brain.guidance(2, "external_proposal")
                self.assertEqual(guidance, "")
                self.assertIsNone(next_id)
            finally:
                brain.close()

    def test_environment_entrypoint_needs_no_market_or_oos_file(self):
        with tempfile.TemporaryDirectory() as td:
            feed = Path(td) / "feed.jsonl"
            write_feed(feed)
            env = {
                "AUTORESEARCH_YOUTUBE_INTELLIGENCE_ENABLED": "1",
                "AUTORESEARCH_YOUTUBE_INTELLIGENCE_DB": str(Path(td) / "youtube.db"),
                "AUTORESEARCH_YOUTUBE_INTELLIGENCE_FEED": str(feed),
                "AUTORESEARCH_YOUTUBE_PUBLISHED_CUTOFF": "2020-12-31",
                "AUTORESEARCH_TRACK_ID": "qqe__btc__private",
                "AUTORESEARCH_MARKET": "crypto",
                "AUTORESEARCH_FAMILY": "qqe",
                "AUTORESEARCH_PROFILE": "private",
            }
            with mock.patch.dict(os.environ, env, clear=False):
                guidance, idea_id = ayi.prompt_guidance(1, "external_proposal")
                self.assertTrue(guidance)
                self.assertEqual(idea_id, "old-qqe")
                result = ayi.learn_from_atlas(
                    idea_id=idea_id,
                    verdict="REJECTED",
                    result={"guard_ok": True},
                    base_score=0.5,
                    candidate_score=0.49,
                )
                self.assertEqual(result["idea_id"], "old-qqe")
                self.assertNotIn("validation_data", guidance)
                self.assertNotIn("2023", guidance)


if __name__ == "__main__":
    unittest.main()
