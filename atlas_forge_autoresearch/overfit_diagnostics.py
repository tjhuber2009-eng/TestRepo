"""Development-only backtest-overfitting diagnostics.

Implements a compact CSCV-style Probability of Backtest Overfitting (PBO)
diagnostic using per-candidate chronological fold scores already recorded by
loop.py. Hidden validation and final OOS are never accessed.
"""

import itertools
import json
import math
from pathlib import Path

import numpy as np


def load_experiment_fold_matrix(path, baseline_path=None):
    path=Path(path)
    rows=[]
    ids=[]
    seen=set()
    seen_sources=set()
    baseline=None
    if baseline_path is not None:
        bp=Path(baseline_path)
        if bp.exists():
            try:
                b=json.loads(bp.read_text(encoding="utf-8"))
                vals=[float(x["raw_k"]) for x in b.get("cscv_slices",[])]
                arr=np.asarray(vals,dtype=float)
                if len(arr)>=4 and np.all(np.isfinite(arr)):
                    baseline={
                        "arr":arr,
                        "id":str(b.get("strategy_sha256") or "baseline"),
                        "harness_sha256":b.get("harness_sha256"),
                        "program_sha256":b.get("program_sha256"),
                    }
                    rows.append(arr)
                    ids.append(baseline["id"])
                    seen.add(baseline["id"])
                    seen_sources.add(baseline["id"])
            except Exception:
                baseline=None
    if path.exists():
        with path.open(encoding="utf-8") as f:
            for line in f:
                try:
                    x=json.loads(line)
                except Exception:
                    continue
                if x.get("selection_eligible") is not True:
                    continue
                if baseline is not None:
                    if baseline["harness_sha256"] and x.get("harness_sha256") != baseline["harness_sha256"]:
                        continue
                    if baseline["program_sha256"] and x.get("program_sha256") != baseline["program_sha256"]:
                        continue
                vals=x.get("cscv_slice_k")
                if not isinstance(vals,list) or len(vals)<4:
                    continue
                try:
                    arr=np.asarray([float(v) for v in vals],dtype=float)
                except Exception:
                    continue
                if not np.all(np.isfinite(arr)):
                    continue
                source_ident=x.get("candidate_source_sha256")
                ident=str(
                    x.get("candidate_ast_sha256")
                    or source_ident
                    or f"iter-{x.get('iteration')}"
                )
                if ident in seen or (source_ident and source_ident in seen_sources):
                    continue
                seen.add(ident)
                if source_ident:
                    seen_sources.add(source_ident)
                rows.append(arr)
                ids.append(ident)
    if not rows:
        return np.empty((0,0)), []
    if baseline is not None:
        width=len(baseline["arr"])
    else:
        counts={}
        for r in rows:
            counts[len(r)]=counts.get(len(r),0)+1
        width=max(counts,key=lambda k:(counts[k],k))
    keep=[(r,i) for r,i in zip(rows,ids) if len(r)==width]
    if not keep:
        return np.empty((0,0)), []
    return np.vstack([r for r,_ in keep]), [i for _,i in keep]


def cscv_pbo(matrix, max_splits=252):
    """Return PBO using combinations of fold halves.

    For each split, select the candidate with best mean score on the training
    folds. Rank that same candidate on the complementary folds. PBO is the
    fraction of selections landing below the median OOS rank.
    """
    x=np.asarray(matrix,dtype=float)
    if x.ndim!=2:
        return None
    n_strat,n_folds=x.shape
    if n_strat<5 or n_folds<4:
        return None
    if n_folds % 2:
        return None
    half=n_folds//2
    if half<2:
        return None

    combos=list(itertools.combinations(range(n_folds),half))
    # Complement pairs are symmetric; cap deterministically for runtime.
    if len(combos)>max_splits:
        idx=np.linspace(0,len(combos)-1,max_splits,dtype=int)
        combos=[combos[i] for i in idx]

    logits=[]
    below=0
    valid=0
    all_idx=set(range(n_folds))
    for train_tuple in combos:
        train=np.asarray(train_tuple,dtype=int)
        test=np.asarray(sorted(all_idx-set(train_tuple)),dtype=int)
        if len(test)==0:
            continue
        train_perf=np.mean(x[:,train],axis=1)
        best=int(np.argmax(train_perf))
        test_perf=np.mean(x[:,test],axis=1)
        selected=float(test_perf[best])
        less=int(np.sum(test_perf < selected))
        equal=int(np.sum(test_perf == selected))
        # Mid-rank ties so candidate array order cannot bias PBO.
        percentile=(less + 0.5*equal)/n_strat
        percentile=min(max(percentile,1e-9),1-1e-9)
        logits.append(math.log(percentile/(1-percentile)))
        below += percentile < 0.5
        valid += 1

    if valid==0:
        return None
    arr=np.asarray(logits,dtype=float)
    return {
        "pbo":round(below/valid,6),
        "cscv_splits":int(valid),
        "median_oos_logit":round(float(np.median(arr)),6),
        "candidate_count":int(n_strat),
        "fold_count":int(n_folds),
        "candidate_pool":"frozen_baseline_plus_unique_selection_eligible_backtests",
        "partition":"fixed_even_development_slices",
    }


def track_pbo(experiments_path):
    experiments_path=Path(experiments_path)
    matrix,_=load_experiment_fold_matrix(
        experiments_path,
        baseline_path=experiments_path.parent/"baseline.json",
    )
    return cscv_pbo(matrix)
