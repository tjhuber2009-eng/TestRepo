"""Aggregate matched model-tournament results with paired-case statistics."""

import argparse
import json
import math
import statistics
from pathlib import Path

import numpy as np


def load_results(folder):
    rows=[]
    for p in sorted(Path(folder).glob("tournament_*.json")):
        try:
            rows.append(json.loads(p.read_text(encoding="utf-8")))
        except Exception:
            continue
    return rows


def finite(v):
    try:
        x=float(v)
    except Exception:
        return None
    return x if math.isfinite(x) else None


def wilson(successes, n, z=1.96):
    if n <= 0:
        return (None,None)
    p=successes/n
    denom=1+z*z/n
    center=(p+z*z/(2*n))/denom
    half=z*math.sqrt(p*(1-p)/n+z*z/(4*n*n))/denom
    return (max(0,center-half),min(1,center+half))


def bootstrap_ci(values, seed=20260905, reps=2000):
    vals=np.asarray([x for x in values if x is not None and math.isfinite(float(x))],dtype=float)
    if len(vals)==0:
        return (None,None)
    if len(vals)==1:
        x=float(vals[0]); return (x,x)
    rng=np.random.default_rng(seed)
    meds=[]
    for _ in range(reps):
        s=rng.choice(vals,size=len(vals),replace=True)
        meds.append(float(np.median(s)))
    return (float(np.quantile(meds,.025)),float(np.quantile(meds,.975)))


def case_aggregates(model_result):
    grouped={}
    for row in model_result.get("cases",[]):
        grouped.setdefault(row.get("track_id"),[]).append(row)
    out={}
    for case,rows in grouped.items():
        deltas=[finite(r.get("delta_k")) for r in rows]
        deltas=[x for x in deltas if x is not None]
        guard=[r for r in rows if r.get("guard_ok") and finite(r.get("delta_k")) is not None]
        guard_deltas=[float(r["delta_k"]) for r in guard]
        out[case]={
            "trials":len(rows),
            "mean_delta_k":float(statistics.mean(deltas)) if deltas else None,
            "median_delta_k":float(statistics.median(deltas)) if deltas else None,
            "guard_mean_delta_k":float(statistics.mean(guard_deltas)) if guard_deltas else None,
            "guard_pass_rate":sum(bool(r.get("guard_ok")) for r in rows)/len(rows) if rows else 0,
            "keep_rate":sum(bool(r.get("would_keep")) for r in rows)/len(rows) if rows else 0,
            "admission_rate":sum(bool(r.get("admitted")) for r in rows)/len(rows) if rows else 0,
        }
    return out


def aggregate(folder, state_sha=""):
    results=load_results(folder)
    available=[x for x in results if x.get("available")]
    unavailable=[x for x in results if not x.get("available")]
    case_maps={x["model"]:case_aggregates(x) for x in available}
    case_ids=sorted({c for m in case_maps.values() for c in m})

    wins={x["model"]:0.0 for x in available}
    for case in case_ids:
        candidates=[]
        for x in available:
            row=case_maps[x["model"]].get(case)
            if row and row.get("guard_mean_delta_k") is not None:
                candidates.append((float(row["guard_mean_delta_k"]),x["model"]))
        if not candidates:
            continue
        best=max(v for v,_ in candidates)
        tied=[m for v,m in candidates if abs(v-best)<1e-12]
        for m in tied:
            wins[m]+=1/len(tied)

    ranking=[]
    for x in available:
        rows=x.get("cases",[])
        attempts=len(rows)
        keep=sum(bool(r.get("would_keep")) for r in rows)
        guard=sum(bool(r.get("guard_ok")) for r in rows)
        admitted=sum(bool(r.get("admitted")) for r in rows)
        api=sum(bool(r.get("api_success")) for r in rows)
        deltas=[finite(r.get("delta_k")) for r in rows]
        deltas=[d for d in deltas if d is not None]
        per_case=[v.get("guard_mean_delta_k") for v in case_maps[x["model"]].values()]
        per_case=[v for v in per_case if v is not None]
        ci=bootstrap_ci(per_case,seed=int(abs(hash(x["model"]))%(2**32)))
        keep_ci=wilson(keep,attempts)
        ranking.append({
            "provider":x.get("provider"),
            "model":x.get("model"),
            "attempts":attempts,
            "trials_per_case":x.get("trials_per_case"),
            "api_success":api,
            "admitted":admitted,
            "guard_pass":guard,
            "would_keep":keep,
            "keep_rate":round(keep/attempts,4) if attempts else 0,
            "keep_rate_ci95":[None if v is None else round(v,4) for v in keep_ci],
            "matched_case_wins":round(wins.get(x["model"],0),3),
            "mean_delta_k":round(float(statistics.mean(deltas)),6) if deltas else None,
            "median_delta_k":round(float(statistics.median(deltas)),6) if deltas else None,
            "paired_guard_median_delta_k":round(float(statistics.median(per_case)),6) if per_case else None,
            "paired_guard_median_ci95":[None if v is None else round(v,6) for v in ci],
            "unique_proposals":(x.get("summary") or {}).get("unique_proposals",0),
            "total_seconds":(x.get("summary") or {}).get("total_seconds"),
            "case_aggregates":case_maps[x["model"]],
        })

    def n(v,default=-1e99):
        try:
            return float(v)
        except Exception:
            return default

    ranking.sort(key=lambda r:(
        r["keep_rate"],
        r["matched_case_wins"],
        r["guard_pass"]/max(r["attempts"],1),
        n(r["paired_guard_median_delta_k"]),
        r["unique_proposals"],
        -n(r["total_seconds"],1e99),
    ),reverse=True)

    payload={
        "state_sha":state_sha,
        "protocol":"nested_chronological_v3",
        "available_models":len(available),
        "unavailable_models":len(unavailable),
        "case_count":len(case_ids),
        "ranking":ranking,
        "unavailable":[{
            "provider":x.get("provider"),
            "model":x.get("model"),
            "detail":x.get("availability_detail"),
        } for x in unavailable],
        "hidden_validation_opened":False,
        "final_oos_opened":False,
        "ranking_policy":[
            "keep rate",
            "matched-case wins",
            "guard-pass rate",
            "paired median guard-passing delta K",
            "idea diversity",
            "speed",
        ],
    }
    return payload


def report(payload):
    out=[
        "# Zero-fee AI model tournament — matched repeated-trial ranking",
        "",
        f"Protocol: **{payload.get('protocol')}**",
        f"Frozen continuous-state SHA: `{payload.get('state_sha','')}`",
        f"Matched cases: **{payload.get('case_count',0)}**",
        f"Available models: **{payload.get('available_models',0)}**",
        "",
        "| Rank | Model | Keep rate | Keep 95% CI | Case wins | Guard | Paired median ΔK | ΔK 95% CI | Unique ideas |",
        "|---:|---|---:|---|---:|---:|---:|---|---:|",
    ]
    for i,r in enumerate(payload.get("ranking",[]),1):
        ci=r.get("keep_rate_ci95",[None,None])
        dci=r.get("paired_guard_median_ci95",[None,None])
        def pc(x):
            return "—" if x is None else f"{100*x:.1f}%"
        def ff(x):
            return "—" if x is None else f"{x:.6f}"
        out.append(
            f"| {i} | {r.get('model')} | {100*r.get('keep_rate',0):.1f}% | "
            f"{pc(ci[0])}–{pc(ci[1])} | {r.get('matched_case_wins')} | "
            f"{r.get('guard_pass')}/{r.get('attempts')} | "
            f"{ff(r.get('paired_guard_median_delta_k'))} | "
            f"{ff(dci[0])}–{ff(dci[1])} | {r.get('unique_proposals',0)} |"
        )
    if payload.get("unavailable"):
        out+=["","Unavailable or unconfigured:"]
        for x in payload["unavailable"]:
            out.append(f"- `{x.get('model')}` ({x.get('provider')}): {x.get('detail')}")
    out += [
        "",
        "Ranking uses repeated matched trials rather than a single lucky proposal.",
        "No model sees another model's output. Hidden validation and 2023+ OOS remain sealed.",
    ]
    return "\n".join(out)+"\n"


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--folder",default="tournament_results")
    ap.add_argument("--state-sha",default="")
    ap.add_argument("--json-out",default="tournament-summary.json")
    ap.add_argument("--report-out",default="tournament-report.md")
    args=ap.parse_args()
    payload=aggregate(args.folder,args.state_sha)
    Path(args.json_out).write_text(json.dumps(payload,indent=2,sort_keys=True)+"\n",encoding="utf-8")
    Path(args.report_out).write_text(report(payload),encoding="utf-8")


if __name__=="__main__":
    main()
