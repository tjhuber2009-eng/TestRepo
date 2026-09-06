"""Concurrent Phase-2 baseline screener for prior strategy work.

State and strategy definitions are isolated from continuous_state and the frozen
514-track Phase-1 registry. This lane uses development data only and never calls
the hidden-validation mode of robust_harness.py.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
import argparse
import json
import math
import os
import subprocess
import sys

import pandas as pd

from phase2_seed_factory import runnable_families, generate

HERE=Path(__file__).resolve().parent
STATE=HERE/"phase2_state"
RESULTS=STATE/"results.jsonl"
PROGRESS=STATE/"progress.json"
CURSOR=STATE/"cursor.json"
TARGET_QUALITY=STATE/"target_quality.json"
CONFIG=HERE/"continuous_config.json"
PROTOCOL="nested_chronological_v3"
LANE="phase2_prior_work"
PHASE2_SOURCE_OVERRIDES={
    "es":{"source":"yahoo_futures_proxy","symbol":"ES=F"},
    "nq":{"source":"yahoo_futures_proxy","symbol":"NQ=F"},
    "gold":{"source":"yahoo_futures_proxy","symbol":"GC=F"},
}


def now():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def load_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save_json(path,obj):
    path=Path(path); path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(obj,indent=2,sort_keys=True,allow_nan=False)+"\n",encoding="utf-8")


def slug(*parts):
    return "__".join(str(x).replace("/","_") for x in parts)


def build_tracks():
    cfg=load_json(CONFIG)
    targets=[]
    for raw in cfg["targets"]:
        if not raw.get("enabled"):
            continue
        target=dict(raw)
        target.update(PHASE2_SOURCE_OVERRIDES.get(target["id"],{}))
        if target["id"] in PHASE2_SOURCE_OVERRIDES:
            target["data_quality_note"]=(
                "Yahoo continuous futures proxy with audited settlement-envelope normalization; "
                "not contract-exact"
            )
        targets.append(target)
    profiles=cfg["profiles"]
    out=[]
    for family in runnable_families():
        for target in sorted(targets,key=lambda x:x["id"]):
            for profile_name in ("private","prop"):
                out.append({
                    "id":slug(family,target["id"],profile_name),
                    "family":family,
                    "target":target,
                    "profile_name":profile_name,
                    "profile":profiles[profile_name],
                })
    return out


def read_results():
    out={}
    quality=load_json(TARGET_QUALITY) if TARGET_QUALITY.exists() else {}
    blocked_targets=set()
    for key,row in quality.items():
        if not isinstance(row,dict) or row.get("status")!="blocked":
            continue
        override=PHASE2_SOURCE_OVERRIDES.get(key)
        if override and (
            row.get("source")!=override["source"]
            or row.get("symbol")!=override["symbol"]
        ):
            continue
        blocked_targets.add(key)
    if not RESULTS.exists(): return out
    for line in RESULTS.read_text(encoding="utf-8").splitlines():
        try: row=json.loads(line)
        except Exception: continue
        override=PHASE2_SOURCE_OVERRIDES.get(row.get("target"))
        if override and (
            row.get("data_source")!=override["source"]
            or row.get("data_symbol")!=override["symbol"]
        ):
            continue
        if row.get("status")=="error" and row.get("target") in blocked_targets:
            row=dict(row)
            row["status"]="data_blocked"
            row["block_reason"]=quality[row["target"]].get("reason")
            row.pop("error",None)
        # Retry tracks that were recorded only because of a fixed runner bug.
        # Keeping these stale rows would permanently suppress valid screening.
        if (
            row.get("status") == "error"
            and "NameError: name 'read_json' is not defined" in str(row.get("error") or "")
        ):
            continue
        out[row.get("track_id")]=row
    return out


def append_result(row):
    STATE.mkdir(parents=True,exist_ok=True)
    with RESULTS.open("a",encoding="utf-8") as f:
        f.write(json.dumps(row,sort_keys=True,allow_nan=False)+"\n")


class DataQualificationBlocked(RuntimeError):
    pass


def development_end(target):
    start=datetime.strptime(target.get("validation_start","2021-01-01"),"%Y-%m-%d")
    return (start-timedelta(days=1)).strftime("%Y-%m-%d")


def qualify_data(target, path):
    quality=load_json(TARGET_QUALITY) if TARGET_QUALITY.exists() else {}
    prior=quality.get(target["id"])
    if prior and (
        prior.get("source")==target.get("source")
        and prior.get("symbol")==target.get("symbol")
    ):
        if prior.get("status")=="blocked":
            raise DataQualificationBlocked(prior.get("reason","target data blocked"))
        return prior

    df=pd.read_csv(path)
    rename={col:col.title() for col in df.columns if col.lower() in {"open","high","low","close"}}
    df=df.rename(columns=rename)
    need=["Open","High","Low","Close"]
    missing=[x for x in need if x not in df.columns]
    if missing:
        row={
            "status":"blocked",
            "reason":f"missing OHLC columns: {missing}",
            "checked_at":now(),
            "source":target.get("source"),
            "symbol":target.get("symbol"),
        }
    else:
        for col in need:
            df[col]=pd.to_numeric(df[col],errors="coerce")
        df=df.dropna(subset=need)
        bad=(df["High"]<df[["Open","Close","Low"]].max(axis=1)) | (df["Low"]>df[["Open","Close","High"]].min(axis=1))
        nbad=int(bad.sum())
        if nbad:
            row={
                "status":"blocked",
                "reason":f"data integrity failure: {nbad} malformed OHLC rows; do not silently clean",
                "checked_at":now(),
                "data_quality_grade":target.get("data_quality_grade"),
                "instrument_fidelity":target.get("instrument_fidelity"),
                "source":target.get("source"),
                "symbol":target.get("symbol"),
            }
        else:
            row={
                "status":"qualified",
                "reason":"OHLC integrity passed",
                "checked_at":now(),
                "data_quality_grade":target.get("data_quality_grade"),
                "instrument_fidelity":target.get("instrument_fidelity"),
                "source":target.get("source"),
                "symbol":target.get("symbol"),
            }
    quality[target["id"]]=row
    save_json(TARGET_QUALITY,quality)
    if row["status"]=="blocked":
        raise DataQualificationBlocked(row["reason"])
    return row


def prepare_data(track):
    t=track["target"]; data=HERE/"data"/f"{t['id']}_1d.csv"
    manifest=HERE/"data"/f"{t['id']}_1d.manifest.json"
    wanted_end=development_end(t)
    if data.exists() and data.stat().st_size>1000 and manifest.exists():
        try:
            m=load_json(manifest)
            if (
                m.get("requested_start")==t["start"]
                and m.get("requested_end")==wanted_end
                and m.get("source")==t["source"]
                and m.get("symbol")==t["symbol"]
            ):
                qualify_data(t, data)
                return data
        except Exception:
            pass
    subprocess.run([
        sys.executable,"prepare_market_data.py",
        "--source",t["source"],"--symbol",t["symbol"],"--id",t["id"],
        "--start",t["start"],"--end",wanted_end,
    ],cwd=HERE,check=True)
    qualify_data(t,data)
    return data


def safe_number(v):
    if isinstance(v,bool) or v is None: return v
    if isinstance(v,(int,str)): return v
    try:
        x=float(v)
        return x if math.isfinite(x) else None
    except Exception:
        return v


def screen_track(track):
    t=track["target"]; p=track["profile"]
    prepare_data(track)
    generate(
        track["family"],HERE/"strategy.py",int(t["bars_per_year"]),
        float(p["starting_vol_target"]),float(p["f_max"]),
    )
    for name in ("baseline.json","last_run.json","validation_run.json","lookahead_audit.json"):
        q=HERE/name
        if q.exists(): q.unlink()
    env=dict(os.environ)
    env.update({
        "AUTORESEARCH_SYMBOL":str(t["symbol"]),
        "AUTORESEARCH_MARKET":str(t["market"]),
        "AUTORESEARCH_DATA_FILE":f"data/{t['id']}_1d.csv",
        "AUTORESEARCH_COMMISSION":str(t["commission"]),
        "AUTORESEARCH_MARGIN":str(t["margin"]),
        "AUTORESEARCH_BARS_PER_YEAR":str(t["bars_per_year"]),
        "AUTORESEARCH_PROFILE":track["profile_name"],
        "AUTORESEARCH_MAX_DD_PCT":str(p["max_dd_pct"]),
        "AUTORESEARCH_MIN_TRADES":"8",
        "AUTORESEARCH_MIN_ACTIVE_FOLDS":"3",
        "AUTORESEARCH_MIN_FOLD_BARS":"100",
        "AUTORESEARCH_VOL_BAND":"0.50",
        "AUTORESEARCH_COST_STRESS_MULT":"2.0",
        "AUTORESEARCH_EXTREME_COST_STRESS_MULT":"3.0",
        "AUTORESEARCH_BOOTSTRAP_REPS":"200",
        "AUTORESEARCH_IS_START":str(t["start"]),
        "AUTORESEARCH_VALIDATION_START":str(t["validation_start"]),
        "AUTORESEARCH_VALIDATION_END":str(t["validation_end"]),
        "AUTORESEARCH_SOURCE_SHA":os.environ.get("AUTORESEARCH_SOURCE_SHA",""),
    })
    proc=subprocess.run(
        [sys.executable,"robust_harness.py","--is"],
        cwd=HERE,env=env,text=True,capture_output=True,
    )
    if proc.returncode:
        detail=(proc.stdout+"\n"+proc.stderr).strip()[-2400:]
        raise RuntimeError(detail or f"robust_harness exit {proc.returncode}")
    x=load_json(HERE/"last_run.json")
    lookahead=None
    if bool(x.get("guard_ok")):
        subprocess.run([sys.executable,"robust_harness.py","--lookahead-audit"],cwd=HERE,env=env,check=True,stdout=subprocess.DEVNULL)
        lookahead=load_json(HERE/"lookahead_audit.json")
    return {
        "ts":now(),
        "lane":LANE,
        "protocol":PROTOCOL,
        "stage":"development_baseline_screen",
        "track_id":track["id"],
        "family":track["family"],
        "target":t["id"],
        "symbol":t["symbol"],
        "market":t["market"],
        "profile":track["profile_name"],
        "source_logic":"prior_indicator_tournament_exact_signal_logic",
        "data_source":t["source"],
        "data_symbol":t["symbol"],
        "execution_translation":"next_bar_open_with_v3_volatility_risk_profile",
        "hidden_validation_opened":False,
        "final_oos_opened":False,
        "guard_ok":bool(x.get("guard_ok")),
        "lookahead_pass":None if lookahead is None else bool(lookahead.get("passed")),
        "score":safe_number(x.get("score")),
        "cagr_pct":safe_number(x.get("cagr_pct")),
        "return_pct":safe_number(x.get("return_pct")),
        "sharpe":safe_number(x.get("sharpe")),
        "pf":safe_number(x.get("pf")),
        "max_dd_pct":safe_number(x.get("max_dd_pct")),
        "trades":safe_number(x.get("trades")),
        "evidence_grade":x.get("evidence_grade"),
        "stress_return_pct":safe_number((x.get("stress") or {}).get("return_pct")),
        "extreme_stress_return_pct":safe_number((x.get("extreme_stress") or {}).get("return_pct")),
        "guard_reason":x.get("guard_reason"),
    }


def write_progress(tracks,results):
    done=len(results)
    ok=sum(1 for x in results.values() if x.get("status")=="ok")
    guards=sum(1 for x in results.values() if x.get("guard_ok"))
    lookahead=sum(1 for x in results.values() if x.get("lookahead_pass") is True)
    errors=sum(1 for x in results.values() if x.get("status")=="error")
    data_blocked=sum(1 for x in results.values() if x.get("status")=="data_blocked")
    ranked=[
        x for x in results.values()
        if x.get("status")=="ok" and x.get("guard_ok") and x.get("score") is not None
    ]
    ranked.sort(key=lambda x:float(x["score"]),reverse=True)
    payload={
        "updated_at":now(),
        "lane":LANE,
        "protocol":PROTOCOL,
        "stage":"baseline_screening" if done<len(tracks) else "baseline_screening_complete",
        "track_count":len(tracks),
        "screened_count":done,
        "success_count":ok,
        "error_count":errors,
        "data_blocked_count":data_blocked,
        "guard_pass_count":guards,
        "lookahead_pass_count":lookahead,
        "completion_pct":round(100.0*done/max(len(tracks),1),2),
        "all_tracks_screened":done>=len(tracks),
        "hidden_validation_opened":False,
        "final_oos_opened":False,
        "phase1_registry_mutated":False,
        "next_stage":"adaptive_followup_on_survivors" if done>=len(tracks) else "continue_baseline_screening",
        "top_guard_passers":ranked[:20],
    }
    save_json(PROGRESS,payload)
    return payload


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument("--max-tracks",type=int,default=6)
    args=ap.parse_args()
    STATE.mkdir(parents=True,exist_ok=True)
    tracks=build_tracks()
    results=read_results()
    start=0
    if CURSOR.exists():
        try: start=int(load_json(CURSOR).get("next_index",0))%max(len(tracks),1)
        except Exception: start=0
    processed=0
    idx=start
    visited=0
    while processed<args.max_tracks and visited<len(tracks):
        track=tracks[idx]
        if track["id"] not in results:
            try:
                row=screen_track(track); row["status"]="ok"
            except DataQualificationBlocked as exc:
                row={
                    "ts":now(),"lane":LANE,"protocol":PROTOCOL,
                    "stage":"development_baseline_screen","track_id":track["id"],
                    "family":track["family"],"target":track["target"]["id"],
                    "data_source":track["target"]["source"],"data_symbol":track["target"]["symbol"],
                    "profile":track["profile_name"],"status":"data_blocked",
                    "block_reason":str(exc)[:1000],
                    "hidden_validation_opened":False,"final_oos_opened":False,
                }
            except Exception as exc:
                row={
                    "ts":now(),"lane":LANE,"protocol":PROTOCOL,
                    "stage":"development_baseline_screen","track_id":track["id"],
                    "family":track["family"],"target":track["target"]["id"],
                    "data_source":track["target"]["source"],"data_symbol":track["target"]["symbol"],
                    "profile":track["profile_name"],"status":"error",
                    "error":f"{type(exc).__name__}: {str(exc)[:1800]}",
                    "hidden_validation_opened":False,"final_oos_opened":False,
                }
            append_result(row); results[track["id"]]=row; processed+=1
        idx=(idx+1)%len(tracks); visited+=1
    save_json(CURSOR,{"next_index":idx,"track_count":len(tracks),"updated_at":now()})
    progress=write_progress(tracks,results)
    print(json.dumps(progress,indent=2,sort_keys=True))


if __name__=="__main__":
    main()
