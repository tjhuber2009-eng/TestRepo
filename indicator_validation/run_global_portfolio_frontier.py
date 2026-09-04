#!/usr/bin/env python3
from pathlib import Path
import pandas as pd, numpy as np, yfinance as yf

OUT=Path("indicator_validation/output_global_portfolio_frontier"); OUT.mkdir(parents=True,exist_ok=True)
END="2026-09-03"; START="2020-09-01"; LOOKBACK=63; THRESH=0.5; COST=0.0007
CEILINGS=list(range(3,61))
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
    ema=close.ewm(span=LOOKBACK,adjust=False).mean()
    sd=close.rolling(LOOKBACK).std(ddof=0)
    z=(close-ema)/sd
    bull=(z>THRESH).fillna(False); bear=(z<-THRESH).fillna(False)
    st=np.zeros(len(close),bool); on=False
    for i in range(len(close)):
        if bull.iloc[i]: on=True
        elif bear.iloc[i]: on=False
        st[i]=on
    return pd.Series(st,index=close.index)

def panel(symbols):
    idx=None
    for s in symbols:
        d=raw[s]
        idx=d.index if idx is None else idx.intersection(d.index)
    idx=idx[idx>=pd.Timestamp(START,tz="UTC")]
    O=pd.DataFrame({s:raw[s].loc[idx,"open"] for s in symbols},index=idx)
    C=pd.DataFrame({s:raw[s].loc[idx,"close"] for s in symbols},index=idx)
    S=pd.DataFrame({s:signal(raw[s]["close"]).reindex(idx).fillna(False) for s in symbols},index=idx)
    return O,C,S

def base_weights(C,kind):
    n=len(C.columns)
    if kind=="equal":
        return pd.DataFrame(np.full(C.shape,1/n),index=C.index,columns=C.columns)
    # Causal inverse-vol weights; 60d close-to-close realized volatility.
    rv=np.log(C/C.shift(1)).rolling(60).std(ddof=0)*np.sqrt(365)
    inv=1/rv.replace(0,np.nan)
    w=inv.div(inv.sum(axis=1),axis=0).fillna(0)
    return w

def run(O,C,S,W,scalar):
    syms=list(C.columns); m=len(syms)
    shares=np.zeros(m); cash=1.0; curve=[]; turn=0.0
    o=O.to_numpy(float); c=C.to_numpy(float); sig=S.to_numpy(bool); w=W.to_numpy(float)
    for i in range(len(O)):
        if i==0: target=np.zeros(m)
        else: target=scalar*w[i-1]*sig[i-1]
        mtm_open=cash+np.dot(shares,o[i])
        desired=mtm_open*target
        current=shares*o[i]
        trade=desired-current
        fee=np.abs(trade).sum()*COST
        cash -= trade.sum()+fee
        shares += trade/o[i]
        turn += np.abs(trade).sum()
        curve.append(cash+np.dot(shares,c[i]))
    if np.any(shares):
        vals=shares*c[-1]; cash+=vals.sum()-np.abs(vals).sum()*COST; curve[-1]=cash
    e=pd.Series(curve,index=O.index)
    years=max((O.index[-1]-O.index[0]).total_seconds()/(365.25*86400),1/365.25)
    final=float(e.iloc[-1]); dd=float((e/e.cummax()-1).min()*100)
    cagr=(final**(1/years)-1)*100 if final>0 else -100
    return {"net_pct":(final-1)*100,"cagr_pct":cagr,"maxdd_pct":dd,
            "mar":cagr/abs(dd) if dd<0 else np.nan,"turnover_x":turn}

rows=[]
for pname,syms in PORTFOLIOS.items():
    O,C,S=panel(syms)
    for kind in ["equal","invvol"]:
        W=base_weights(C,kind)
        for scalar_pct in np.arange(0.25,100.0001,0.25):
            m=run(O,C,S,W,scalar_pct/100)
            rows.append({"portfolio":pname,"weighting":kind,"scalar_pct":scalar_pct,
                         "start":str(O.index[0]),"end":str(O.index[-1]),**m})
g=pd.DataFrame(rows); g.to_csv(OUT/"portfolio_candidate_grid.csv",index=False)

front=[]
for ceiling in CEILINGS:
    ok=g[g.maxdd_pct>=-ceiling]
    if len(ok):
        w=ok.sort_values(["cagr_pct","maxdd_pct"],ascending=[False,False]).iloc[0]
        front.append({"dd_ceiling_pct":ceiling,**w.to_dict()})
front=pd.DataFrame(front); front.to_csv(OUT/"global_portfolio_frontier.csv",index=False)

# Walk-forward: select on 2020-09 through 2022-12; test frozen portfolio/weighting/scalar on 2023+.
cut=pd.Timestamp("2023-01-01",tz="UTC")
train_rows=[]
for pname,syms in PORTFOLIOS.items():
    O,C,S=panel(syms); mask=O.index<cut; Ot,Ct,St=O.loc[mask],C.loc[mask],S.loc[mask]
    for kind in ["equal","invvol"]:
        W=base_weights(C,kind).loc[mask]
        for scalar_pct in np.arange(0.25,100.0001,0.25):
            m=run(Ot,Ct,St,W,scalar_pct/100)
            train_rows.append({"portfolio":pname,"weighting":kind,"scalar_pct":scalar_pct,**m})
tg=pd.DataFrame(train_rows)
wf=[]
for ceiling in CEILINGS:
    ok=tg[tg.maxdd_pct>=-ceiling]
    if not len(ok): continue
    w=ok.sort_values(["cagr_pct","maxdd_pct"],ascending=[False,False]).iloc[0]
    syms=PORTFOLIOS[w.portfolio]; O,C,S=panel(syms); mask=O.index>=cut
    Ot,Ct,St=O.loc[mask],C.loc[mask],S.loc[mask]
    W=base_weights(C,w.weighting).loc[mask]
    tm=run(Ot,Ct,St,W,float(w.scalar_pct)/100)
    wf.append({"dd_ceiling_pct":ceiling,"portfolio":w.portfolio,"weighting":w.weighting,
               "scalar_pct":w.scalar_pct,"train_cagr_pct":w.cagr_pct,"train_maxdd_pct":w.maxdd_pct,
               "test_cagr_pct":tm["cagr_pct"],"test_maxdd_pct":tm["maxdd_pct"],
               "test_net_pct":tm["net_pct"],"test_pass_ceiling":tm["maxdd_pct"]>=-ceiling})
wf=pd.DataFrame(wf); wf.to_csv(OUT/"global_portfolio_walkforward.csv",index=False)

print("GLOBAL PORTFOLIO FRONTIER")
print(front[["dd_ceiling_pct","portfolio","weighting","scalar_pct","cagr_pct","maxdd_pct","net_pct","mar"]].to_string(index=False))
print("\nWALKFORWARD")
print(wf.to_string(index=False))
