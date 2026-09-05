#!/usr/bin/env python3
from pathlib import Path
import pandas as pd, numpy as np, yfinance as yf

OUT=Path("indicator_validation/output_unbiased_frontier"); OUT.mkdir(parents=True,exist_ok=True)
END="2026-09-03"; START="2020-09-01"; LOOKBACK=63; THRESH=0.5; COST=0.0007
CEILINGS=list(range(3,61)); SCALARS=np.arange(0.25,100.0001,0.25)/100
C12=["BTC-USD","ETH-USD","BNB-USD","SOL-USD","XRP-USD","ADA-USD","DOGE-USD","LTC-USD","BCH-USD","LINK-USD","DOT-USD","AVAX-USD"]
C8=["BTC-USD","ETH-USD","BNB-USD","SOL-USD","ADA-USD","DOGE-USD","DOT-USD","AVAX-USD"]
T11=["SPY","QQQ","IWM","GLD","SLV","IEF","TLT","DBC","VNQ","EFA","EEM"]
PORTS={
 "SELECTED_MIX10":("selected",C8+["QQQ","GLD"]),
 "ALL12":("all_tested",C12),
 "ALL12_QQQ_GLD":("all_tested",C12+["QQQ","GLD"]),
 "ALL12_GROWTH4":("all_tested",C12+["SPY","QQQ","IWM","GLD"]),
 "ALL12_BROAD":("all_tested",C12+T11),
}
ALL=sorted({x for _,v in PORTS.values() for x in v})

def dl(sym):
    x=yf.download(sym,start="2020-01-01",end=END,interval="1d",auto_adjust=False,progress=False,threads=False)
    if isinstance(x.columns,pd.MultiIndex): x.columns=x.columns.get_level_values(0)
    x=x.rename(columns=str.lower).reset_index(); x=x.rename(columns={x.columns[0]:"datetime"})
    x["datetime"]=pd.to_datetime(x["datetime"],utc=True)
    return x[["datetime","open","close"]].dropna().drop_duplicates("datetime").set_index("datetime").sort_index()
raw={s:dl(s) for s in ALL}

def signal(close):
    ema=close.ewm(span=LOOKBACK,adjust=False).mean(); sd=close.rolling(LOOKBACK).std(ddof=0); z=(close-ema)/sd
    bull=(z>THRESH).fillna(False).to_numpy(); bear=(z<-THRESH).fillna(False).to_numpy()
    st=np.zeros(len(close),bool); on=False
    for i in range(len(close)):
        if bull[i]: on=True
        elif bear[i]: on=False
        st[i]=on
    return pd.Series(st,index=close.index)

def panel(syms):
    idx=None
    for s in syms: idx=raw[s].index if idx is None else idx.intersection(raw[s].index)
    idx=idx[idx>=pd.Timestamp(START,tz="UTC")]
    O=pd.DataFrame({s:raw[s].loc[idx,"open"] for s in syms},index=idx)
    C=pd.DataFrame({s:raw[s].loc[idx,"close"] for s in syms},index=idx)
    S=pd.DataFrame({s:signal(raw[s]["close"]).reindex(idx).fillna(False) for s in syms},index=idx)
    return O,C,S

def targets(C,S,kind):
    n=len(C.columns)
    if "invvol" in kind:
        rv=np.log(C/C.shift(1)).rolling(60).std(ddof=0)*np.sqrt(365); inv=1/rv.replace(0,np.nan)
        W=inv.div(inv.sum(axis=1),axis=0).fillna(0)
    else:
        W=pd.DataFrame(np.full(C.shape,1/n),index=C.index,columns=C.columns)
    T=W*S.astype(float)
    if kind.startswith("active_"):
        T=T.div(T.sum(axis=1).replace(0,np.nan),axis=0).fillna(0)
    return T

def run_scalars(O,C,T):
    o=O.to_numpy(float); c=C.to_numpy(float); t=T.to_numpy(float); k=len(SCALARS); m=o.shape[1]
    shares=np.zeros((k,m)); cash=np.ones(k); peak=np.ones(k); maxdd=np.zeros(k)
    for i in range(len(O)):
        base=np.zeros(m) if i==0 else t[i-1]; target=SCALARS[:,None]*base
        mtm=cash+(shares*o[i]).sum(1); desired=mtm[:,None]*target; current=shares*o[i]; trade=desired-current
        cash-=trade.sum(1)+np.abs(trade).sum(1)*COST; shares+=trade/o[i]
        eq=cash+(shares*c[i]).sum(1); peak=np.maximum(peak,eq); maxdd=np.minimum(maxdd,eq/peak-1)
    vals=shares*c[-1]; cash+=vals.sum(1)-np.abs(vals).sum(1)*COST
    final=cash; peak=np.maximum(peak,final); maxdd=np.minimum(maxdd,final/peak-1)
    years=(O.index[-1]-O.index[0]).total_seconds()/(365.25*86400)
    cagr=np.where(final>0,(final**(1/years)-1)*100,-100)
    return pd.DataFrame({"scalar_pct":SCALARS*100,"cagr_pct":cagr,"maxdd_pct":maxdd*100,"net_pct":(final-1)*100})

KINDS=["static_equal","static_invvol","active_equal","active_invvol"]
def grid(end=None):
    rows=[]
    for name,(bias,syms) in PORTS.items():
        O,C,S=panel(syms)
        if end is not None:
            mask=O.index<end; O,C,S=O.loc[mask],C.loc[mask],S.loc[mask]
        for kind in KINDS:
            z=run_scalars(O,C,targets(C,S,kind)); z.insert(0,"allocation",kind); z.insert(0,"bias_class",bias); z.insert(0,"portfolio",name); rows.append(z)
    return pd.concat(rows,ignore_index=True)

def frontier(g,bias_filter=None):
    if bias_filter: g=g[g.bias_class==bias_filter]
    out=[]
    for ce in CEILINGS:
        ok=g[g.maxdd_pct>=-ce]
        if len(ok):
            w=ok.sort_values(["cagr_pct","maxdd_pct"],ascending=[False,False]).iloc[0]
            out.append({"dd_ceiling_pct":ce,**w.to_dict()})
    return pd.DataFrame(out)

g=grid(); fall=frontier(g); funb=frontier(g,"all_tested")
g.to_csv(OUT/"candidate_grid.csv",index=False); fall.to_csv(OUT/"frontier_all_including_selected.csv",index=False); funb.to_csv(OUT/"frontier_all_tested_universe.csv",index=False)
print("UNBIASED ALL-TESTED FRONTIER")
print(funb[["dd_ceiling_pct","portfolio","allocation","scalar_pct","cagr_pct","maxdd_pct","net_pct"]].to_string(index=False))
print("\nCOMPARISON SELECTED VS UNBIASED")
cmp=fall[["dd_ceiling_pct","portfolio","cagr_pct","maxdd_pct"]].merge(funb[["dd_ceiling_pct","portfolio","cagr_pct","maxdd_pct"]],on="dd_ceiling_pct",suffixes=("_best_any","_unbiased"))
print(cmp.to_string(index=False))
