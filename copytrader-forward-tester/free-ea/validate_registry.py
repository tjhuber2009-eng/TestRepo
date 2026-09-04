#!/usr/bin/env python3
import json
from pathlib import Path

ROOT=Path(__file__).resolve().parent
policy=json.loads((ROOT/"FREE_ONLY_POLICY.json").read_text())
prov=json.loads((ROOT/"PROVENANCE_POLICY.json").read_text())
registry=json.loads((ROOT/"candidates.json").read_text())

assert policy["policy"]=="FREE_EA_ONLY"
assert policy["rules"]["maximum_acquisition_cost_usd"]==0
assert not policy["rules"]["paid_signal_subscriptions_allowed"]
assert not policy["rules"]["paid_eas_allowed"]

ids=set()
ready=0
for c in registry["candidates"]:
    assert c["cost_usd"]==0, f"paid candidate leaked into registry: {c['name']}"
    assert c["platform"]=="MT5"
    assert c["id"] not in ids
    ids.add(c["id"])
    assert c["source_url"].startswith("https://")
    status=c["launch_status"]
    st=c["source_type"]

    if status=="READY":
        ready += 1
        assert st in {"mql5_codebase_free_source","licensed_public_source"}, f"unlicensed READY source: {c['id']}"
    if status=="MANUAL_MARKET_INSTALL":
        assert st=="mql5_market_free", f"manual Market install not official-free: {c['id']}"
    if st in {"public_github_no_license","third_party_mirror","third_party_mirror_identity_conflict"}:
        assert status not in {"READY","MANUAL_MARKET_INSTALL"}, f"provenance violation: {c['id']}"

assert ready >= 2
print(f"FREE/PROVENANCE REGISTRY PASS: {len(ids)} candidates, {ready} auto-ready; no paid or unlicensed READY candidates")
