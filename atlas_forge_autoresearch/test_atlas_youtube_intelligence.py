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

    def test_selection_rechecks_cutoff_even_if_shared_db_flag_is_stale(self):
        with tempfile.TemporaryDirectory() as td:
            feed = Path(td) / "feed.jsonl"
            write_feed(feed)
            db = Path(td) / "youtube.db"
            newer = ayi.YouTubeAtlasBridge(
                db,
                feed_path=feed,
                track_id="newer__btc__private",
                domain="crypto:newer:private",
                published_cutoff="2025-12-31",
            )
            try:
                newer.ingest_feed()
                row = newer.conn.execute(
                    "SELECT eligible FROM ideas WHERE idea_id='future-leak'"
                ).fetchone()
                self.assertEqual(int(row["eligible"]), 1)
            finally:
                newer.close()

            older = ayi.YouTubeAtlasBridge(
                db,
                feed_path=None,
                track_id="older__btc__private",
                domain="crypto:older:private",
                published_cutoff="2020-12-31",
            )
            try:
                idea = older.choose(1, "external_proposal")
                self.assertIsNotNone(idea)
                self.assertEqual(idea.idea_id, "old-qqe")
                self.assertLessEqual(idea.published_at[:10], "2020-12-31")
            finally:
                older.close()

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

    def test_descriptive_market_labels_match_lane(self):
        with tempfile.TemporaryDirectory() as td:
            eur = ayi.YouTubeAtlasBridge(
                Path(td) / "eur.db",
                feed_path=None,
                track_id="sentinel63__eurusd__private",
                domain="forex:sentinel63:private",
                published_cutoff="2021-12-31",
                symbol="EURUSD",
                timeframe="1D",
            )
            gbp = ayi.YouTubeAtlasBridge(
                Path(td) / "gbp.db",
                feed_path=None,
                track_id="sentinel63__gbpusd__private",
                domain="forex:sentinel63:private",
                published_cutoff="2021-12-31",
                symbol="GBPUSD",
                timeframe="1D",
            )
            try:
                self.assertFalse(
                    eur._market_compatible(("Forex (GBP/USD demonstrated)",))
                )
                self.assertTrue(
                    gbp._market_compatible(("Forex (GBP/USD demonstrated)",))
                )
                self.assertTrue(
                    eur._market_compatible(("Forex",))
                )
                self.assertFalse(
                    eur._market_compatible(("Equities", "Stock",))
                )
            finally:
                eur.close()
                gbp.close()

    def test_index_aliases_route_to_matching_underlying(self):
        with tempfile.TemporaryDirectory() as td:
            spy = ayi.YouTubeAtlasBridge(
                Path(td) / "spy.db",
                feed_path=None,
                track_id="youtube__spy__private",
                domain="stock:sentinel63:private",
                published_cutoff="2021-12-31",
                symbol="SPY",
                timeframe="1D",
            )
            qqq = ayi.YouTubeAtlasBridge(
                Path(td) / "qqq.db",
                feed_path=None,
                track_id="youtube__qqq__private",
                domain="stock:sentinel63:private",
                published_cutoff="2021-12-31",
                symbol="QQQ",
                timeframe="1D",
            )
            try:
                markets = ("SPX500", "Indices")
                self.assertTrue(spy._market_compatible(markets))
                self.assertFalse(qqq._market_compatible(markets))
            finally:
                spy.close()
                qqq.close()

    def test_gold_reproduction_does_not_fall_through_to_generic_forex(self):
        with tempfile.TemporaryDirectory() as td:
            gold = ayi.YouTubeAtlasBridge(
                Path(td) / "gold.db",
                feed_path=None,
                track_id="youtube__xauusd__private",
                domain="forex:sentinel63:private",
                published_cutoff="2021-12-31",
                symbol="XAUUSD",
                timeframe="1D",
            )
            eur = ayi.YouTubeAtlasBridge(
                Path(td) / "eur.db",
                feed_path=None,
                track_id="youtube__eurusd__private",
                domain="forex:sentinel63:private",
                published_cutoff="2021-12-31",
                symbol="EURUSD",
                timeframe="1D",
            )
            try:
                markets = ("Gold (XAU/USD)", "Forex")
                self.assertTrue(gold._market_compatible(markets))
                self.assertFalse(eur._market_compatible(markets))
            finally:
                gold.close()
                eur.close()

    def test_transfer_stage_can_probe_related_market_after_reproduction(self):
        with tempfile.TemporaryDirectory() as td:
            brain = ayi.YouTubeAtlasBridge(
                Path(td) / "youtube.db",
                feed_path=None,
                track_id="youtube__eurusd__private",
                domain="forex:sentinel63:private",
                published_cutoff="2021-12-31",
                symbol="EURUSD",
                timeframe="1D",
                routing_stage="transfer",
            )
            try:
                self.assertTrue(
                    brain._market_compatible(("Forex (GBP/USD demonstrated)",))
                )
            finally:
                brain.close()

    def test_timeframe_mismatch_and_multitimeframe_are_deferred(self):
        with tempfile.TemporaryDirectory() as td:
            brain = ayi.YouTubeAtlasBridge(
                Path(td) / "youtube.db",
                feed_path=None,
                track_id="youtube__eurusd__private",
                domain="forex:sentinel63:private",
                published_cutoff="2021-12-31",
                symbol="EURUSD",
                timeframe="1D",
            )
            try:
                self.assertTrue(brain._timeframe_compatible(("Daily",)))
                self.assertFalse(brain._timeframe_compatible(("4H",)))
                self.assertFalse(
                    brain._timeframe_compatible(("Daily", "4-Hour"))
                )
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
                "AUTORESEARCH_SYMBOL": "BTCUSDT",
                "AUTORESEARCH_TIMEFRAME": "1D",
                "AUTORESEARCH_YOUTUBE_ROUTING_STAGE": "reproduction",
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
