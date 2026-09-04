import json
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]

class LaunchPackTests(unittest.TestCase):
    def test_manifest_free_and_demo_only(self):
        m=json.loads((ROOT/"FREE_EA_START_MANIFEST.json").read_text())
        self.assertTrue(m["account_policy"]["demo_only"])
        for c in m["launch_order"]:
            self.assertEqual(c["cost_usd"],0)

    def test_recovery_forbidden(self):
        pins=json.loads((ROOT/"UPSTREAM_PINS.json").read_text())
        apex=next(x for x in pins["upstreams"] if x["id"]=="APEX_BREAKOUT_UPSTREAM")
        self.assertTrue(any("Recovery" in x for x in apex["forbidden_files"]))
        self.assertFalse(any("Recovery" in x for x in apex["allowed_files"]))

    def test_ema_quarantined(self):
        m=json.loads((ROOT/"FREE_EA_START_MANIFEST.json").read_text())
        ema=next(x for x in m["launch_order"] if x["id"]=="EMA_GOLD_TRADER")
        self.assertEqual(ema["status"],"QUARANTINED")

    def test_installer_never_copies_recovery(self):
        text=(ROOT/"install_free_eas.ps1").read_text(encoding="utf-8")
        forbidden='Copy-Item (Join-Path $tmp "MQL5\\Experts\\ApexBreakoutRecovery.mq5")'
        self.assertNotIn(forbidden,text)
        self.assertIn("ApexBreakoutRecovery.mq5",text)

    def test_registry_has_only_free_candidates(self):
        r=json.loads((ROOT/"candidates.json").read_text())
        self.assertGreaterEqual(len(r["candidates"]),8)
        for c in r["candidates"]:
            self.assertEqual(c["cost_usd"],0)

    def test_safe_scalper_source_and_market_are_separate(self):
        r=json.loads((ROOT/"candidates.json").read_text())
        ids={c["id"] for c in r["candidates"]}
        self.assertIn("SAFE_SCALPER_CODEBASE_V120",ids)
        self.assertIn("SAFE_SCALPER_MARKET_V344",ids)

if __name__=="__main__":
    unittest.main()
