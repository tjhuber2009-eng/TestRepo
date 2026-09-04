#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import importlib.util
import json
import math
from datetime import datetime
from pathlib import Path

HERE=Path(__file__).resolve().parent
DEMO_ANALYZER=HERE.parent/"mt5-demo"/"demo_analyzer.py"
spec=importlib.util.spec_from_file_location("demo_analyzer",DEMO_ANALYZER)
demo=importlib.util.module_from_spec(spec)
spec.loader.exec_module(demo)

def parse_time(text: str) -> datetime | None:
    for fmt in ("%Y.%m.%d %H:%M:%S","%Y-%m-%d %H:%M:%S","%Y-%m-%d"):
        try:
            return datetime.strptime(text,fmt)
        except Exception:
            pass
    return None

def pf_number(value):
    if value == "Infinity":
        return 5.0
    try:
        return float(value)
    except Exception:
        return None

def score_result(row: dict) -> dict:
    start=parse_time(row.get("first_utc",""))
    end=parse_time(row.get("last_utc",""))
    days=max(0.0,(end-start).total_seconds()/86400.0) if start and end else 0.0
    trades=int(row.get("closed_positions") or 0)
    ret=float(row.get("equity_return_pct") or 0.0)
    dd=float(row.get("max_observed_equity_dd_pct") or 0.0)
    pf=pf_number(row.get("profit_factor"))

    trade_ev=1.0-math.exp(-trades/50.0)
    time_ev=1.0-math.exp(-days/30.0)
    confidence=0.55*trade_ev+0.45*time_ev

    ret_component=math.tanh(ret/25.0)
    pf_component=0.0 if pf is None else math.tanh(max(0.0,pf-1.0))
    rdd=ret/max(dd,1.0)
    rdd_component=math.tanh(rdd/5.0)
    score=100.0*(0.45*ret_component+0.25*pf_component+0.20*rdd_component+0.10*confidence)

    clean=bool(row.get("valid_for_clean_comparison"))
    status="ACTIVE"
    if not clean:
        status="CONTAMINATED"
        score=None
    elif trades == 0:
        status="AWAITING_CLOSED_TRADES"
    elif confidence < 0.25:
        status="LOW_EVIDENCE"

    return {
        **row,
        "elapsed_days":round(days,4),
        "return_over_dd":None if dd <= 0 else ret/dd,
        "evidence_confidence":round(confidence,6),
        "prospective_score":None if score is None else round(score,6),
        "tournament_status":status,
    }

def rank(common_files: Path) -> list[dict]:
    rows=[score_result(demo.analyze_account(p)) for p in demo.discover(common_files)]
    rows.sort(key=lambda r:(
        r["prospective_score"] is not None,
        r["prospective_score"] if r["prospective_score"] is not None else -1e9,
        r.get("equity_return_pct") or -1e9,
    ),reverse=True)
    for i,row in enumerate(rows,1):
        row["rank"]=i
    return rows

def write_outputs(rows: list[dict], out_dir: Path):
    out_dir.mkdir(parents=True,exist_ok=True)
    (out_dir/"ranking.json").write_text(json.dumps(rows,indent=2,allow_nan=False),encoding="utf-8")
    if rows:
        fields=["rank","candidate","login","server","tournament_status","equity_return_pct","profit_factor",
                "max_observed_equity_dd_pct","return_over_dd","closed_positions","wins","losses",
                "evidence_confidence","prospective_score","first_utc","last_utc","cashflow_event_count"]
        with (out_dir/"ranking.csv").open("w",newline="",encoding="utf-8") as h:
            w=csv.DictWriter(h,fieldnames=fields,extrasaction="ignore")
            w.writeheader(); w.writerows(rows)

    lines=["# Free-EA Prospective Tournament","","Small samples remain admitted; evidence affects confidence, not eligibility.","",
           "| Rank | Candidate | Return | PF | Max DD | Return/DD | Trades | Confidence | Score | Status |",
           "|---:|---|---:|---:|---:|---:|---:|---:|---:|---|"]
    for r in rows:
        pf=r.get("profit_factor")
        rr=r.get("return_over_dd")
        lines.append(
            f"| {r['rank']} | {r['candidate']} | {float(r.get('equity_return_pct') or 0):+.2f}% | "
            f"{pf if pf is not None else '—'} | {float(r.get('max_observed_equity_dd_pct') or 0):.2f}% | "
            f"{'—' if rr is None else f'{rr:.2f}'} | {r.get('closed_positions',0)} | "
            f"{r['evidence_confidence']:.3f} | {'—' if r['prospective_score'] is None else f'{r["prospective_score"]:.2f}'} | "
            f"{r['tournament_status']} |"
        )
    (out_dir/"ranking.md").write_text("\n".join(lines)+"\n",encoding="utf-8")

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--common-files",required=True,type=Path)
    ap.add_argument("--out-dir",type=Path,default=HERE/"reports")
    args=ap.parse_args()
    rows=rank(args.common_files)
    write_outputs(rows,args.out_dir)
    print(json.dumps(rows,indent=2,allow_nan=False))

if __name__=="__main__":
    main()
