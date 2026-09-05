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


def load_experiment_fold_matrix(path):
    path=Path(path)
    if not path.exists():
        return np.empty((0,0)), []
    rows=[]
    ids=[]
    with path.open(encoding="utf-8") as f:
        for line in f:
            try:
                x=json.loads(line)
            except Exception:
                continue
            if x.get("selection_eligible") is not True:
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
            rows.append(arr)
            ids.append(x.get("candidate_ast_sha256") or f"iter-{x.get('iteration')}")
    if not rows:
        return np.empty((0,0)), []
    # Use the modal fold count so variants are compared on identical slices.
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
        order=np.argsort(test_perf)
        rank=int(np.where(order==best)[0][0])+1  # 1=worst, n=best
        percentile=(rank-0.5)/n_strat
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
        "candidate_pool":"selection_eligible_backtests",
        "partition":"fixed_even_development_slices",
    }


def track_pbo(experiments_path):
    matrix,_=load_experiment_fold_matrix(experiments_path)
    return cscv_pbo(matrix)
