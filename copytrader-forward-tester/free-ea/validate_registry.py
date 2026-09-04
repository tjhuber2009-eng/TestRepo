#!/usr/bin/env python3
import json
from pathlib import Path
ROOT=Path(__file__).resolve().parent
policy=json.loads((ROOT/"FREE_ONLY_POLICY.json").read_text())
registry=json.loads((ROOT/"candidates.json").read_text())
assert policy["policy"]=="FREE_EA_ONLY"
assert policy["rules"]["maximum_acquisition_cost_usd"]==0
assert not policy["rules"]["paid_signal_subscriptions_allowed"]
assert not policy["rules"]["paid_eas_allowed"]
ids=set()
for c in registry["candidates"]:
    assert c["cost_usd"]==0, f"paid candidate leaked into executable registry: {c['name']}"
    assert c["platform"]=="MT5"
    assert c["id"] not in ids
    ids.add(c["id"])
    assert c["source_url"].startswith("https://")
    assert c["priority"]>=1
print(f"FREE-ONLY REGISTRY PASS: {len(ids)} executable candidates, all cost $0")
