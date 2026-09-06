"""Finite free-source discovery lane for AUTORESEARCH Phase 3.

Runs independently of Phase 1 and Phase 2. It collects public hypotheses only,
does not alter the strategy registry, does not backtest hidden validation, and
does not touch 2023+ data.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
import argparse
import json

from free_source_pipeline import harvest

HERE=Path(__file__).resolve().parent
STATE=HERE/"phase3_state"
DISCOVERIES=STATE/"discoveries.json"
QUEUE=STATE/"candidate_queue.json"
PROGRESS=STATE/"progress.json"
CURSOR=STATE/"cursor.json"
LANE="phase3_free_discovery"
PROTOCOL="nested_chronological_v3"

QUERY_BATCHES=[
    [
        "time series momentum trend following trading strategy",
        "cross sectional momentum trading strategy",
        "short term reversal trading strategy",
        "volatility managed momentum strategy",
        "carry strategy futures currencies",
        "defensive quality low volatility systematic strategy",
    ],
    [
        "post earnings announcement drift trading strategy",
        "earnings surprise drift systematic strategy",
        "turn of month trading anomaly",
        "weekday seasonality trading anomaly",
        "overnight intraday reversal strategy",
        "opening range breakout stocks in play",
    ],
    [
        "pairs trading cointegration strategy",
        "statistical arbitrage residual reversal",
        "Kalman pairs trading strategy",
        "variance risk premium trading strategy",
        "volatility risk premium options strategy",
        "cross asset tactical allocation strategy",
    ],
    [
        "crypto perpetual funding basis strategy",
        "crypto liquidation mean reversion strategy",
        "crypto trend following strategy",
        "bitcoin systematic momentum strategy",
        "algorithmic trading strategy profit factor drawdown",
        "reproducible trading strategy github backtest",
    ],
]


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def read_json(path, default):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except Exception:
        return default


def write_json(path,obj):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,indent=2,sort_keys=True,ensure_ascii=False)+"\n",encoding="utf-8")


def quality(row):
    source=row.get("source_type","")
    source_rank={"crossref":5,"openalex":5,"arxiv":4,"github":3,"reddit":1}.get(source,0)
    text=(row.get("title","")+" "+row.get("snippet","")).lower()
    evidence_terms=sum(
        1 for term in (
            "out-of-sample","out of sample","transaction cost","sharpe",
            "profit factor","drawdown","replication","paper","journal",
            "momentum","reversal","carry","earnings","pairs","arbitrage",
        )
        if term in text
    )
    return source_rank*100+evidence_terms


def dedupe_key(row):
    return (row.get("url") or row.get("title") or "").strip().lower()


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--batches-per-run",type=int,default=1)
    args=ap.parse_args()
    STATE.mkdir(parents=True,exist_ok=True)

    prior=read_json(DISCOVERIES,{"candidates":[]})
    by_key={dedupe_key(x):x for x in prior.get("candidates",[]) if dedupe_key(x)}
    cursor=read_json(CURSOR,{"next_batch":0})
    batch=int(cursor.get("next_batch",0))
    completed=[]
    failures=[]

    for _ in range(max(1,args.batches_per_run)):
        if batch>=len(QUERY_BATCHES):
            break
        queries=QUERY_BATCHES[batch]
        try:
            rows=harvest(queries)
            for row in rows:
                x=row.to_dict()
                x["discovered_at"]=now()
                x["batch"]=batch
                k=dedupe_key(x)
                if k and k not in by_key:
                    by_key[k]=x
            completed.append(batch)
        except Exception as exc:
            failures.append({"batch":batch,"error":f"{type(exc).__name__}: {str(exc)[:800]}"})
        batch+=1

    rows=list(by_key.values())
    rows.sort(key=lambda x:(quality(x),x.get("title","")),reverse=True)
    write_json(DISCOVERIES,{
        "lane":LANE,
        "protocol":PROTOCOL,
        "policy":"free public discovery only; hypotheses are not trading evidence",
        "updated_at":now(),
        "candidate_count":len(rows),
        "candidates":rows,
    })
    queue=[]
    seen_title=set()
    for row in rows:
        title=" ".join(row.get("title","").lower().split())
        if not title or title in seen_title:
            continue
        seen_title.add(title)
        queue.append({
            **row,
            "quality_score":quality(row),
            "intake_status":"needs_rule_reconstruction",
            "hidden_validation_opened":False,
            "final_oos_opened":False,
        })
        if len(queue)>=300:
            break
    write_json(QUEUE,{
        "lane":LANE,
        "updated_at":now(),
        "policy":"rank for reconstruction; never promote source-reported performance directly",
        "count":len(queue),
        "candidates":queue,
    })
    write_json(CURSOR,{"next_batch":batch,"batch_count":len(QUERY_BATCHES),"updated_at":now()})
    progress={
        "updated_at":now(),
        "lane":LANE,
        "protocol":PROTOCOL,
        "stage":"discovery_complete" if batch>=len(QUERY_BATCHES) else "discovering",
        "batch_count":len(QUERY_BATCHES),
        "next_batch":batch,
        "batches_completed_this_run":completed,
        "failures_this_run":failures,
        "unique_discoveries":len(rows),
        "reconstruction_queue_count":len(queue),
        "discovery_complete":batch>=len(QUERY_BATCHES),
        "phase1_registry_mutated":False,
        "hidden_validation_opened":False,
        "final_oos_opened":False,
        "next_stage":"rule_reconstruction_and_dedup" if batch>=len(QUERY_BATCHES) else "continue_discovery",
    }
    write_json(PROGRESS,progress)
    print(json.dumps(progress,indent=2,sort_keys=True))


if __name__=="__main__":
    main()
