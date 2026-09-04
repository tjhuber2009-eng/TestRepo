#!/usr/bin/env python3
from pathlib import Path
import pandas as pd, numpy as np, yfinance as yf

OUT=Path("indicator_validation/output_global_portfolio_frontier"); OUT.mkdir(parents=True,exist_ok=True)
END="2026-09-03"; START="2020-09-01"; LOOKBACK=63; THRESH=0.5; COST=0.0007
CEILINGS=list(range(3,61)); SCALARS=np.arange(0.25,100.0001,0.25)/100
PORTFOLIOS={
 "BTC":["BTC-USD"],
 "BTC_ETH":["BTC-USD","ETH-USD"],
 "CRYPTO4":["BTC-USD","ETH-USD","BNB-USD","SOL-USD"],
 "CRYPTO8":["BTC-USD","ETH-USD","BNB-USD","SOL-USD","ADA-USD","DOGE-USD","DOT-USD","AVAX-USD"],
 "BTC_QQQ_GLD":["BTC-USD","QQQ","GLD"],
 "BTC_ETH_QQQ_GLD":["BTC-USD","ETH-USD","QQQ","GLD"],
 "MIX6":["BTC-USD","ETH-USD","BNB-USD","SOL-USD","QQQ","GLD"],
 "MIX10":["BTC-USD","ETH-USD","BNB-USD","SOL-USD","ADA-USD","DOGE-USD","DOT-USD","AVAX-USD","QQQ","GLD"],
}

def dl(sym):
    x=yf.download(sym,start="2020-01-01",end=END,interval="1d",auto_adjust=False,progress=False,threads=False)
    if isinstance(x.columns,pd.MultiIndex): x.columns=x.columns.get_level_values(0)
    x=x.rename(columns=str.lower).reset_index(); x=x.rename(columns={x.columns[0]:"datetime"})
    x["datetime"]=pd.to_datetime(x["datetime"],utc=True)
    return x[["datetime","open","close"]].dropna().drop_duplicates("datetime").set_index("datetime").sort_index()
raw={s:dl(s) for s in sorted({x for v in PORTFOLIOS.values() for x in v})}

def signal(close):
    ema=close.ewm(span=LOOKBACK,adjust=False).mean(); sd=close.rolling(LOOKBACK).std(ddof=0)
    z=(close-ema)/sd; bull=(z>THRESH).fillna(False).to_numpy(); bear=(z<-THRESH).fillna(False).to_numpy()
    st=np.zeros(len(close),bool); on=False
    for i in range(len(close)):
        if bull[i]: on=True
        elif bear[i]: on=False
        st[i]=on
    return pd.Series(st,index=close.index)

def panel(symbols):
    idx=None
    for s in symbols: idx=raw[s].index if idx is None else idx.intersection(raw[s].index)
    idx=idx[idx>=pd.Timestamp(START,tz="UTC")]
    O=pd.DataFrame({s:raw[s].loc[idx,"open"] for s in symbols},index=idx)
    C=pd.DataFrame({s:raw[s].loc[idx,"close"] for s in symbols},index=idx)
    S=pd.DataFrame({s:signal(raw[s]["close"]).reindex(idx).fillna(False) for s in symbols},index=idx)
    return O,C,S

def base_weights(C,kind):
    n=len(C.columns)
    if kind=="equal": return pd.DataFrame(np.full(C.shape,1/n),index=C.index,columns=C.columns)
    rv=np.log(C/C.shift(1)).rolling(60).std(ddof=0)*np.sqrt(365)
    inv=1/rv.replace(0,np.nan)
    return inv.div(inv.sum(axis=1),axis=0).fillna(0)

def run_scalars(O,C,S,W,scalars):
    o=O.to_numpy(float); c=C.to_numpy(float); sig=S.to_numpy(bool); w=W.to_numpy(float)
    k=len(scalars); m=o.shape[1]
    shares=np.zeros((k,m)); cash=np.ones(k); peak=np.ones(k); maxdd=np.zeros(k); turnover=np.zeros(k)
    for i in range(len(O)):
        base=np.zeros(m) if i==0 else w[i-1]*sig[i-1]
        target=scalars[:,None]*base[None,:]
        mtm_open=cash+(shares*o[i]).sum(axis=1)
        desired=mtm_open[:,None]*target
        current=shares*o[i]
        trade=desired-current
        cash -= trade.sum(axis=1)+np.abs(trade).sum(axis=1)*COST
        shares += trade/o[i]
        turnover += np.abs(trade).sum(axis=1)
        eq=cash+(shares*c[i]).sum(axis=1)
        peak=np.maximum(peak,eq); maxdd=np.minimum(maxdd,eq/peak-1)
    vals=shares*c[-1]
    cash += vals.sum(axis=1)-np.abs(vals).sum(axis=1)*COST
    final=cash
    peak=np.maximum(peak,final); maxdd=np.minimum(maxdd,final/peak-1)
    years=max((O.index[-1]-O.index[0]).total_seconds()/(365.25*86400),1/365.25)
    cagr=np.where(final>0,(final**(1/years)-1)*100,-100)
    return pd.DataFrame({"scalar_pct":scalars*100,"net_pct":(final-1)*100,"cagr_pct":cagr,
                         "maxdd_pct":maxdd*100,"mar":np.where(maxdd<0,cagr/np.abs(maxdd*100),np.nan),
                         "turnover_x":turnover})

def candidate_grid_for_slice(start=None,end=None):
    rows=[]
    for pname,syms in PORTFOLIOS.items():
        O,C,S=panel(syms)
        if start is not None:
            mask=O.index>=start; O,C,S=O.loc[mask],C.loc[mask],S.loc[mask]
        if end is not None:
            mask=O.index<end; O,C,S=O.loc[mask],C.loc[mask],S.loc[mask]
        for kind in ["equal","invvol"]:
            W=base_weights(C,kind)
            z=run_scalars(O,C,S,W,SCALARS)
            z.insert(0,"weighting",kind); z.insert(0,"portfolio",pname)
            z["start"]=str(O.index[0]); z["end"]=str(O.index[-1]); rows.append(z)
    return pd.concat(rows,ignore_index=True)

def frontier(g):
    out=[]
    for ceiling in CEILINGS:
        ok=g[g.maxdd_pct>=-ceiling]
        if len(ok):
            w=ok.sort_values(["cagr_pct","maxdd_pct"],ascending=[False,False]).iloc[0]
            out.append({"dd_ceiling_pct":ceiling,**w.to_dict()})
    return pd.DataFrame(out)

g=candidate_grid_for_slice(); front=frontier(g)
g.to_csv(OUT/"portfolio_candidate_grid.csv",index=False); front.to_csv(OUT/"global_portfolio_frontier.csv",index=False)

cut=pd.Timestamp("2023-01-01",tz="UTC")
tg=candidate_grid_for_slice(end=cut); tf=frontier(tg)
wf=[]
for _,w in tf.iterrows():
    syms=PORTFOLIOS[w.portfolio]; O,C,S=panel(syms); mask=O.index>=cut; O,C,S=O.loc[mask],C.loc[mask],S.loc[mask]
    W=base_weights(C,w.weighting)
    tm=run_scalars(O,C,S,W,np.array([float(w.scalar_pct)/100])).iloc[0]
    wf.append({"dd_ceiling_pct":int(w.dd_ceiling_pct),"portfolio":w.portfolio,"weighting":w.weighting,
               "scalar_pct":w.scalar_pct,"train_cagr_pct":w.cagr_pct,"train_maxdd_pct":w.maxdd_pct,
               "test_cagr_pct":tm.cagr_pct,"test_maxdd_pct":tm.maxdd_pct,"test_net_pct":tm.net_pct,
               "test_pass_ceiling":tm.maxdd_pct>=-float(w.dd_ceiling_pct)})
wf=pd.DataFrame(wf); wf.to_csv(OUT/"global_portfolio_walkforward.csv",index=False)

print("GLOBAL PORTFOLIO FRONTIER")
print(front[["dd_ceiling_pct","portfolio","weighting","scalar_pct","cagr_pct","maxdd_pct","net_pct","mar"]].to_string(index=False))
print("\nWALKFORWARD")
print(wf.to_string(index=False))
