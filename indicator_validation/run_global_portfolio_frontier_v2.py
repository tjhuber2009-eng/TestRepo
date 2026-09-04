#!/usr/bin/env python3
from pathlib import Path
import pandas as pd, numpy as np, yfinance as yf

OUT=Path("indicator_validation/output_global_portfolio_frontier_v2"); OUT.mkdir(parents=True,exist_ok=True)
END="2026-09-03"; START="2020-09-01"; LOOKBACK=63; THRESH=0.5; COST=0.0007
CEILINGS=list(range(3,61)); SCALARS=np.arange(0.25,100.0001,0.25)/100
C8=["BTC-USD","ETH-USD","BNB-USD","SOL-USD","ADA-USD","DOGE-USD","DOT-USD","AVAX-USD"]
T11=["SPY","QQQ","IWM","GLD","SLV","IEF","TLT","DBC","VNQ","EFA","EEM"]
PORTFOLIOS={
 "CRYPTO8":C8,
 "MIX10":C8+["QQQ","GLD"],
 "MIX12":C8+["SPY","QQQ","IWM","GLD"],
 "MIX13":C8+["SPY","QQQ","IWM","GLD","SLV"],
 "MIX15":C8+["SPY","QQQ","IWM","GLD","SLV","IEF","DBC"],
 "BROAD19":C8+T11,
 "TRAD11":T11,
 "BTC_ETH_TRAD":["BTC-USD","ETH-USD"]+T11,
}
ALL=sorted({x for v in PORTFOLIOS.values() for x in v})

def dl(sym):
    x=yf.download(sym,start="2020-01-01",end=END,interval="1d",auto_adjust=False,progress=False,threads=False)
    if isinstance(x.columns,pd.MultiIndex): x.columns=x.columns.get_level_values(0)
    x=x.rename(columns=str.lower).reset_index(); x=x.rename(columns={x.columns[0]:"datetime"})
    x["datetime"]=pd.to_datetime(x["datetime"],utc=True)
    return x[["datetime","open","close"]].dropna().drop_duplicates("datetime").set_index("datetime").sort_index()
raw={s:dl(s) for s in ALL}

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

def raw_weights(C,kind):
    n=len(C.columns)
    if "invvol" in kind:
        rv=np.log(C/C.shift(1)).rolling(60).std(ddof=0)*np.sqrt(365)
        inv=1/rv.replace(0,np.nan); W=inv.div(inv.sum(axis=1),axis=0).fillna(0)
    else:
        W=pd.DataFrame(np.full(C.shape,1/n),index=C.index,columns=C.columns)
    return W

def target_matrix(C,S,kind):
    W=raw_weights(C,kind)
    T=W*S.astype(float)
    if kind.startswith("active_"):
        sums=T.sum(axis=1).replace(0,np.nan)
        T=T.div(sums,axis=0).fillna(0)
    return T

def run_scalars(O,C,T,scalars):
    o=O.to_numpy(float); c=C.to_numpy(float); t=T.to_numpy(float)
    k=len(scalars); m=o.shape[1]
    shares=np.zeros((k,m)); cash=np.ones(k); peak=np.ones(k); maxdd=np.zeros(k); turnover=np.zeros(k)
    for i in range(len(O)):
        base=np.zeros(m) if i==0 else t[i-1]
        target=scalars[:,None]*base[None,:]
        mtm_open=cash+(shares*o[i]).sum(axis=1)
        desired=mtm_open[:,None]*target; current=shares*o[i]; trade=desired-current
        cash-=trade.sum(axis=1)+np.abs(trade).sum(axis=1)*COST; shares+=trade/o[i]; turnover+=np.abs(trade).sum(axis=1)
        eq=cash+(shares*c[i]).sum(axis=1); peak=np.maximum(peak,eq); maxdd=np.minimum(maxdd,eq/peak-1)
    vals=shares*c[-1]; cash+=vals.sum(axis=1)-np.abs(vals).sum(axis=1)*COST
    final=cash; peak=np.maximum(peak,final); maxdd=np.minimum(maxdd,final/peak-1)
    years=max((O.index[-1]-O.index[0]).total_seconds()/(365.25*86400),1/365.25)
    cagr=np.where(final>0,(final**(1/years)-1)*100,-100)
    return pd.DataFrame({"scalar_pct":scalars*100,"net_pct":(final-1)*100,"cagr_pct":cagr,
                         "maxdd_pct":maxdd*100,"mar":np.where(maxdd<0,cagr/np.abs(maxdd*100),np.nan),
                         "turnover_x":turnover})

KINDS=["static_equal","static_invvol","active_equal","active_invvol"]

def grid_slice(start=None,end=None):
    rows=[]
    for pname,syms in PORTFOLIOS.items():
        O,C,S=panel(syms)
        if start is not None:
            mask=O.index>=start; O,C,S=O.loc[mask],C.loc[mask],S.loc[mask]
        if end is not None:
            mask=O.index<end; O,C,S=O.loc[mask],C.loc[mask],S.loc[mask]
        for kind in KINDS:
            T=target_matrix(C,S,kind)
            z=run_scalars(O,C,T,SCALARS); z.insert(0,"allocation",kind); z.insert(0,"portfolio",pname)
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

g=grid_slice(); f=frontier(g)
g.to_csv(OUT/"candidate_grid_v2.csv",index=False); f.to_csv(OUT/"global_frontier_v2.csv",index=False)
cut=pd.Timestamp("2023-01-01",tz="UTC"); tg=grid_slice(end=cut); tf=frontier(tg); wf=[]
for _,w in tf.iterrows():
    syms=PORTFOLIOS[w.portfolio]; O,C,S=panel(syms); mask=O.index>=cut; O,C,S=O.loc[mask],C.loc[mask],S.loc[mask]
    T=target_matrix(C,S,w.allocation); tm=run_scalars(O,C,T,np.array([float(w.scalar_pct)/100])).iloc[0]
    wf.append({"dd_ceiling_pct":int(w.dd_ceiling_pct),"portfolio":w.portfolio,"allocation":w.allocation,
               "scalar_pct":w.scalar_pct,"train_cagr_pct":w.cagr_pct,"train_maxdd_pct":w.maxdd_pct,
               "test_cagr_pct":tm.cagr_pct,"test_maxdd_pct":tm.maxdd_pct,"test_net_pct":tm.net_pct,
               "test_pass_ceiling":tm.maxdd_pct>=-float(w.dd_ceiling_pct)})
wf=pd.DataFrame(wf); wf.to_csv(OUT/"walkforward_v2.csv",index=False)
print("GLOBAL V2 FRONTIER")
print(f[["dd_ceiling_pct","portfolio","allocation","scalar_pct","cagr_pct","maxdd_pct","net_pct","mar"]].to_string(index=False))
print("\nWALKFORWARD V2")
print(wf.to_string(index=False))
