import json
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class LaunchPackTests(unittest.TestCase):
    def test_manifest_is_demo_only_and_free(self):
        m=json.loads((ROOT/"FREE_EA_START_MANIFEST.json").read_text())
        self.assertTrue(m["account_policy"]["demo_only"])
        for group in ("first_wave","second_wave"):
            for c in m[group]:
                self.assertEqual(c["cost_usd"],0)

    def test_auto_ready_sources_have_valid_provenance(self):
        r=json.loads((ROOT/"candidates.json").read_text())
        for c in r["candidates"]:
            if c["launch_status"]=="READY":
                self.assertIn(c["source_type"],{"mql5_codebase_free_source","licensed_public_source"})

    def test_no_license_github_is_not_executable(self):
        r=json.loads((ROOT/"candidates.json").read_text())
        for c in r["candidates"]:
            if c["source_type"]=="public_github_no_license":
                self.assertNotIn(c["launch_status"],{"READY","MANUAL_MARKET_INSTALL"})

    def test_third_party_mirrors_are_not_executable(self):
        r=json.loads((ROOT/"candidates.json").read_text())
        for c in r["candidates"]:
            if "third_party_mirror" in c["source_type"]:
                self.assertNotIn(c["launch_status"],{"READY","MANUAL_MARKET_INSTALL"})

    def test_fvg_is_mit_pinned_and_ready(self):
        pins=json.loads((ROOT/"UPSTREAM_PINS.json").read_text())
        p=next(x for x in pins["upstreams"] if x["id"]=="FVG_GOLD_UPSTREAM")
        self.assertEqual(p["declared_license"],"MIT")
        r=json.loads((ROOT/"candidates.json").read_text())
        c=next(x for x in r["candidates"] if x["id"]=="FVG_GOLD_V200")
        self.assertEqual(c["launch_status"],"READY")

    def test_apex_no_longer_auto_installed(self):
        text=(ROOT/"install_free_eas.ps1").read_text(encoding="utf-8")
        self.assertNotIn("sbrakni/MQL5-trading-bot-claude-experiment.git",text)
        self.assertNotIn('Copy-Item (Join-Path $tmp "MQL5\\Experts\\ApexBreakout.mq5")',text)

    def test_gold_reaper_relabel_quarantined(self):
        r=json.loads((ROOT/"candidates.json").read_text())
        c=next(x for x in r["candidates"] if x["id"]=="GOLD_PROP_REAPER_RELABEL")
        self.assertEqual(c["launch_status"],"QUARANTINED")
        self.assertIn("algocheck_martingale_confirmed",c["risk_flags"])

    def test_search_marked_complete_to_diminishing_returns(self):
        c=json.loads((ROOT/"SEARCH_COVERAGE_2026-09-04.json").read_text())
        self.assertEqual(c["status"],"COMPLETE_TO_DIMINISHING_RETURNS")
        self.assertIn("all 41",c["coverage"]["mql5_codebase_mt5_experts"])

if __name__=="__main__":
    unittest.main()
