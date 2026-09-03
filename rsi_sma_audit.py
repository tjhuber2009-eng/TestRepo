import os
import math
import numpy as np
import pandas as pd
import requests

from dual_signal_audit import fetch_bitstamp, run_engine, annualized_metrics, buy_hold_metrics

CLAIM_START = "2010-10-07"
CLAIM_END = "2022-08-30"
PUBLICATION = "2022-08-30"
CURRENT_END = "2026-09-02"
HIST_URL = "https://raw.githubusercontent.com/nileshiq/Bitcoin-Historical-Prices-Activity-2010-2024-/main/bitcoin_2010-07-27_2024-04-25.csv"


def fetch_early_ohlc():
    r = requests.get(HIST_URL, timeout=60, headers={"User-Agent":"independent-rsi-sma-audit/1.0"})
    r.raise_for_status()
    fn="audit_output_rsi/early_btc_2010_2024.csv"
    with open(fn,"wb") as f: f.write(r.content)
    d=pd.read_csv(fn)
    d.columns=[c.strip().lstrip("\ufeff") for c in d.columns]
    d["Date"]=pd.to_datetime(d["Start"])
    for c in ["Open","High","Low","Close","Volume","Market Cap"]:
        d[c]=pd.to_numeric(d[c],errors="coerce")
    d=d.set_index("Date").sort_index()
    return d[["Open","High","Low","Close","Volume","Market Cap"]].dropna(subset=["Open","Close"])


def pine_rma(src, length):
    x=src.astype(float).to_numpy()
    out=np.full(len(x),np.nan)
    # Pine ta.rma seeds with SMA(length) once length non-na source observations exist.
    valid=[]
    prev=np.nan
    alpha=1.0/length
    for i,v in enumerate(x):
        if not np.isfinite(v):
            continue
        valid.append(v)
        if not np.isfinite(prev):
            if len(valid)>=length:
                prev=float(np.mean(valid[-length:]))
                out[i]=prev
        else:
            prev=alpha*v+(1-alpha)*prev
            out[i]=prev
    return pd.Series(out,index=src.index)


def pine_rsi(close,length):
    ch=close.diff()
    up=ch.clip(lower=0)
    down=(-ch).clip(lower=0)
    au=pine_rma(up,length)
    ad=pine_rma(down,length)
    rs=au/ad
    rsi=100-(100/(1+rs))
    rsi[(ad==0)&(au>0)]=100
    rsi[(ad==0)&(au==0)]=50
    return rsi


def make_rsi_sma_signal(close,rsi_len=50,sma_len=25):
    rsi=pine_rsi(close,rsi_len)
    sma=rsi.rolling(sma_len,min_periods=sma_len).mean()
    buy=(rsi>sma)&(rsi.shift(1)<=sma.shift(1))
    sell=(rsi<sma)&(rsi.shift(1)>=sma.shift(1))
    state=np.zeros(len(close),dtype=np.int8)
    pos=0
    for i in range(len(close)):
        if bool(buy.iloc[i]): pos=1
        elif bool(sell.iloc[i]): pos=0
        state[i]=pos
    return pd.Series(state,index=close.index),rsi,sma,buy,sell


def metric_row(source,df,eng,window,start,end,rsi_len,sma_len,mode,cost_name,cost):
    m=annualized_metrics(eng.equity,eng.position,eng.trades,start,end)
    if not m: return None
    return {"source":source,"window":window,"rsi_len":rsi_len,"sma_len":sma_len,"mode":mode,
            "cost_case":cost_name,"cost_side_bps":cost*10000,"final_dollars_from_1000":1000*(1+m["total_return_pct"]/100),**m}


def capacity_checks(df,eng):
    items=[]
    if eng.trades is None or not len(eng.trades): return pd.DataFrame()
    for _,t in eng.trades.iterrows():
        for side,date,eq in [("entry",pd.Timestamp(t.entry_date),float(t.entry_equity_pre_fee)),
                             ("exit",pd.Timestamp(t.exit_date),float(t.exit_equity_post_fee))]:
            if date not in df.index: continue
            row=df.loc[date]
            notional=eq*1000.0
            vol=float(row.get("Volume",np.nan)); cap=float(row.get("Market Cap",np.nan))
            items.append({"date":date,"side":side,"notional_usd":notional,"daily_volume":vol,"market_cap":cap,
                          "pct_daily_volume":100*notional/vol if np.isfinite(vol) and vol>0 else np.nan,
                          "pct_market_cap":100*notional/cap if np.isfinite(cap) and cap>0 else np.nan})
    return pd.DataFrame(items)


def main():
    os.makedirs("audit_output_rsi",exist_ok=True)
    early=fetch_early_ohlc()
    print("EARLY_ROWS",len(early),"RANGE",early.index.min(),early.index.max())
    print("EARLY_2010_SAMPLE")
    print(early.loc["2010-10-01":"2010-10-12"].to_string())

    costs={"zero":0.0,"realistic_15bps_side":0.0015,"half_pct_side":0.005}
    rows=[]
    sig,rsi,sma,buy,sell=make_rsi_sma_signal(early.Close,50,25)
    pd.DataFrame({"Close":early.Close,"RSI50":rsi,"SMA25_RSI":sma,"Buy":buy,"Sell":sell,"Signal":sig}).to_csv("audit_output_rsi/default_signal.csv")
    default_eng=None
    for mode in ["next_open","next_close","same_close"]:
        for cname,cost in costs.items():
            eng=run_engine(early,sig,cost,mode)
            if mode=="next_open" and cname=="zero":
                default_eng=eng
                eng.trades.to_csv("audit_output_rsi/default_trades.csv",index=False)
            for wname,ws,we in [
                ("claim_full",CLAIM_START,CLAIM_END),
                ("2013_to_claim","2013-01-01",CLAIM_END),
                ("2014_to_claim","2014-01-01",CLAIM_END),
                ("2018_to_claim","2018-01-01",CLAIM_END),
            ]:
                rr=metric_row("early_index_mirror",early,eng,wname,ws,we,50,25,mode,cname,cost)
                if rr: rows.append(rr)

    bh=[]
    for wname,ws,we in [("claim_full",CLAIM_START,CLAIM_END),("2013_to_claim","2013-01-01",CLAIM_END),("2014_to_claim","2014-01-01",CLAIM_END),("2018_to_claim","2018-01-01",CLAIM_END)]:
        m=buy_hold_metrics(early,ws,we)
        bh.append({"source":"early_index_mirror","window":wname,**m,"final_dollars_from_1000":1000*(1+m["total_return_pct"]/100)})

    cap=capacity_checks(early,default_eng)
    cap.to_csv("audit_output_rsi/capacity_checks.csv",index=False)

    # Independent exchange-only feed: Bitstamp, including untouched post-publication period.
    bit=fetch_bitstamp()
    bsig,brsi,bsma,bbuy,bsell=make_rsi_sma_signal(bit.Close,50,25)
    for cname,cost in costs.items():
        eng=run_engine(bit,bsig,cost,"next_open")
        for wname,ws,we in [
            ("bitstamp_2013_to_claim","2013-04-01",CLAIM_END),
            ("post_publication",PUBLICATION,CURRENT_END),
            ("2023_current","2023-01-01",CURRENT_END),
            ("2024_current","2024-01-01",CURRENT_END),
        ]:
            rr=metric_row("bitstamp",bit,eng,wname,ws,we,50,25,"next_open",cname,cost)
            if rr: rows.append(rr)
    for wname,ws,we in [("bitstamp_2013_to_claim","2013-04-01",CLAIM_END),("post_publication",PUBLICATION,CURRENT_END),("2023_current","2023-01-01",CURRENT_END),("2024_current","2024-01-01",CURRENT_END)]:
        m=buy_hold_metrics(bit,ws,we); bh.append({"source":"bitstamp","window":wname,**m,"final_dollars_from_1000":1000*(1+m["total_return_pct"]/100)})

    out=pd.DataFrame(rows)
    out.to_csv("audit_output_rsi/results.csv",index=False)
    pd.DataFrame(bh).to_csv("audit_output_rsi/buy_hold.csv",index=False)

    # Modest in-sample neighborhood test, not an optimizer claim: 49 combinations.
    sens=[]
    for rl in [20,30,40,50,60,70,80]:
        for sl in [10,15,20,25,30,35,40]:
            s,*_=make_rsi_sma_signal(early.Close,rl,sl)
            e=run_engine(early,s,0.0,"next_open")
            m=annualized_metrics(e.equity,e.position,e.trades,CLAIM_START,CLAIM_END)
            sens.append({"rsi_len":rl,"sma_len":sl,**m})
    sens=pd.DataFrame(sens).sort_values("cagr_pct",ascending=False)
    sens.to_csv("audit_output_rsi/parameter_neighborhood.csv",index=False)

    print("\n=== RSI/SMA DEFAULT HEADLINES ===")
    focus=out[((out.source=="early_index_mirror")&(out.window=="claim_full")) | ((out.source=="bitstamp")&(out.window.isin(["post_publication","2023_current"])))]
    print(focus[["source","window","mode","cost_case","final_dollars_from_1000","total_return_pct","cagr_pct","max_dd_pct","sharpe","profit_factor","closed_trades","exposure_pct"]].to_string(index=False,float_format=lambda x:f"{x:.3f}"))
    print("\n=== BUY HOLD ===")
    print(pd.DataFrame(bh).to_string(index=False,float_format=lambda x:f"{x:.3f}"))
    print("\n=== PARAMETER NEIGHBORHOOD TOP 15 ===")
    print(sens[["rsi_len","sma_len","cagr_pct","total_return_pct","max_dd_pct","closed_trades"]].head(15).to_string(index=False,float_format=lambda x:f"{x:.3f}"))
    rank=sens.reset_index(drop=True)
    ix=rank.index[(rank.rsi_len==50)&(rank.sma_len==25)][0]
    d=rank.loc[ix]
    print("DEFAULT_RANK",ix+1,"OF",len(rank),"CAGR",d.cagr_pct,"TOTAL",d.total_return_pct)
    if len(cap):
        print("\n=== CAPACITY FLAGS (default, zero cost, next-open) ===")
        for threshold in [1,10,100]:
            x=cap[cap.pct_daily_volume>=threshold].head(1)
            if len(x): print(f"FIRST_NOTIONAL_GE_{threshold}PCT_DAILY_VOLUME",x.to_dict("records")[0])
        for threshold in [0.1,1,5]:
            x=cap[cap.pct_market_cap>=threshold].head(1)
            if len(x): print(f"FIRST_NOTIONAL_GE_{threshold}PCT_MARKET_CAP",x.to_dict("records")[0])

if __name__=="__main__":
    main()
