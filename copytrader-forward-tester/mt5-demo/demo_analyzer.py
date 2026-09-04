#!/usr/bin/env python3
from __future__ import annotations
import argparse, csv, json, math
from collections import defaultdict
from pathlib import Path

CASHFLOW_TYPES={"BALANCE","CREDIT","BONUS","CORRECTION"}

def f(x):
    try: return float(x)
    except Exception: return 0.0

def read_csv(path):
    with path.open(newline="",encoding="utf-8-sig") as h:
        return list(csv.DictReader(h))

def dedupe(rows,key="ticket"):
    out={}
    for r in rows:
        out[r.get(key) or json.dumps(r,sort_keys=True)] = r
    return list(out.values())

def max_dd(equities):
    peak=None; worst=0.0
    for e in equities:
        if peak is None or e>peak: peak=e
        if peak and peak>0: worst=max(worst,100*(peak-e)/peak)
    return worst

def analyze_account(account_path: Path):
    account=read_csv(account_path)
    if not account: raise ValueError(f"empty {account_path}")
    stem=account_path.name[:-len("_account.csv")]
    deals_path=account_path.with_name(stem+"_deals.csv")
    deals=dedupe(read_csv(deals_path)) if deals_path.exists() else []
    first,last=account[0],account[-1]
    eq=[f(r["equity"]) for r in account]
    bal0=f(first["balance"]); eq0=f(first["equity"])
    bal1=f(last["balance"]); eq1=f(last["equity"])

    cashflows=[r for r in deals if r.get("type_name") in CASHFLOW_TYPES]
    trading=[r for r in deals if r.get("type_name") in {"BUY","SELL"}]

    by_position=defaultdict(float)
    closed=set()
    for r in trading:
        pid=r.get("position_id") or r.get("ticket")
        by_position[pid]+=f(r.get("profit"))+f(r.get("commission"))+f(r.get("swap"))+f(r.get("fee"))
        if r.get("entry_name") in {"OUT","INOUT","OUT_BY"}: closed.add(pid)

    closed_pnl=[by_position[p] for p in closed]
    gp=sum(x for x in closed_pnl if x>0)
    gl=-sum(x for x in closed_pnl if x<0)
    pf=(gp/gl if gl>0 else (math.inf if gp>0 else None))
    wins=sum(x>0 for x in closed_pnl)
    losses=sum(x<0 for x in closed_pnl)

    return {
      "login":last["login"],"server":last["server"],"candidate":last["candidate"],
      "first_utc":first["utc"],"last_utc":last["utc"],
      "baseline_balance":bal0,"current_balance":bal1,
      "baseline_equity":eq0,"current_equity":eq1,
      "equity_return_pct":100*(eq1-eq0)/eq0 if eq0 else None,
      "balance_return_pct":100*(bal1-bal0)/bal0 if bal0 else None,
      "max_observed_equity_dd_pct":max_dd(eq),
      "closed_positions":len(closed),"wins":wins,"losses":losses,
      "gross_profit":gp,"gross_loss":gl,
      "profit_factor":"Infinity" if pf==math.inf else pf,
      "cashflow_event_count":len(cashflows),
      "valid_for_clean_comparison":len(cashflows)==0,
      "current_positions":int(last["positions"]),
    }

def discover(common: Path):
    return sorted(common.glob("COPYTRADER_DEMO_*_account.csv"))

def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--common-files",required=True,type=Path)
    ap.add_argument("--out",type=Path)
    args=ap.parse_args()
    rows=[analyze_account(p) for p in discover(args.common_files)]
    text=json.dumps(rows,indent=2,allow_nan=False)
    if args.out:
        args.out.parent.mkdir(parents=True,exist_ok=True); args.out.write_text(text,encoding="utf-8")
    print(text)

if __name__=="__main__": main()
