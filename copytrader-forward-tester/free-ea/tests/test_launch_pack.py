from pathlib import Path
import json

ROOT=Path(__file__).resolve().parents[1]

def test_manifest_free_and_demo_only():
    m=json.loads((ROOT/"FREE_EA_START_MANIFEST.json").read_text())
    assert m["account_policy"]["demo_only"] is True
    for c in m["launch_order"]:
        assert c["cost_usd"] == 0

def test_recovery_forbidden():
    pins=json.loads((ROOT/"UPSTREAM_PINS.json").read_text())
    apex=next(x for x in pins["upstreams"] if x["id"]=="APEX_BREAKOUT_UPSTREAM")
    assert any("Recovery" in x for x in apex["forbidden_files"])
    assert not any("Recovery" in x for x in apex["allowed_files"])

def test_ema_quarantined():
    m=json.loads((ROOT/"FREE_EA_START_MANIFEST.json").read_text())
    ema=next(x for x in m["launch_order"] if x["id"]=="EMA_GOLD_TRADER")
    assert ema["status"]=="QUARANTINED"

def test_installer_never_copies_recovery():
    text=(ROOT/"install_free_eas.ps1").read_text(encoding="utf-8")
    assert 'Copy-Item (Join-Path $tmp "MQL5\\Experts\\ApexBreakoutRecovery.mq5")' not in text
    assert 'ApexBreakoutRecovery.mq5' in text  # fail-closed presence check
