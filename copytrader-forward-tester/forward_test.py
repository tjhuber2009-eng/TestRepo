#!/usr/bin/env python3
from __future__ import annotations

import argparse
import csv
import hashlib
import html as html_lib
import json
import math
import re
import sqlite3
import time
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Optional

ROOT = Path(__file__).resolve().parent
TOL = 0.02
GENESIS = "0" * 64

@dataclass(slots=True)
class SignalSnapshot:
    candidate_id: str
    name: str
    source_url: str
    observed_at: str
    page_sha256: str
    currency: Optional[str] = None
    growth_pct: Optional[float] = None
    profit: Optional[float] = None
    equity: Optional[float] = None
    balance: Optional[float] = None
    initial_deposit: Optional[float] = None
    deposits: Optional[float] = None
    withdrawals: Optional[float] = None
    trades: Optional[int] = None
    wins: Optional[int] = None
    losses: Optional[int] = None
    gross_profit: Optional[float] = None
    gross_loss: Optional[float] = None
    profit_factor: Optional[float] = None
    algo_pct: Optional[float] = None
    equity_dd_pct: Optional[float] = None
    balance_dd_pct: Optional[float] = None
    latest_trade_text: Optional[str] = None
    parse_warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

@dataclass(slots=True)
class Classification:
    candidate_id: str
    classification: str
    accepted: bool
    reason: str
    activated: bool
    forward_trades: int = 0

class _VisibleText(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []

    def handle_data(self, data: str):
        data = data.strip()
        if data:
            self.parts.append(data)

def now_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")

def html_to_text(page: str) -> str:
    parser = _VisibleText()
    parser.feed(page)
    return "\n".join(parser.parts)

def clean_number(raw: str) -> float:
    raw = raw.replace("\xa0", " ").replace("−", "-").strip()
    raw = re.sub(r"\s+", "", raw).replace(",", "")
    return float(raw)

def first(text: str, pattern: str, flags: int = re.I | re.S):
    m = re.search(pattern, text, flags)
    return m.group(1).strip() if m else None

def money_after(text: str, label: str):
    pat = rf"(?:^|\n){re.escape(label)}\s*\n\s*([+\-−]?[\d\s,.]+)\s+([A-Z]{{3}})(?:\s|\n|$)"
    m = re.search(pat, text, re.I)
    if not m:
        return None, None
    return clean_number(m.group(1)), m.group(2).upper()

def pct_after(text: str, label: str):
    v = first(text, rf"(?:^|\n){re.escape(label)}\s*\n\s*([+\-−]?[\d\s,.]+)%")
    return clean_number(v) if v is not None else None

def int_after(text: str, label: str):
    v = first(text, rf"(?:^|\n){re.escape(label)}\s*\n\s*([\d\s,]+)")
    return int(clean_number(v)) if v is not None else None

def parse_signal_page(candidate: dict, page: str, observed_at: str | None = None) -> SignalSnapshot:
    text = html_to_text(page)
    observed_at = observed_at or now_utc()
    page_sha = hashlib.sha256(page.encode("utf-8", errors="replace")).hexdigest()

    profit, c1 = money_after(text, "Profit:")
    equity, c2 = money_after(text, "Equity:")
    balance, c3 = money_after(text, "Balance:")
    initial, c4 = money_after(text, "Initial Deposit:")
    withdrawals, c5 = money_after(text, "Withdrawals:")
    deposits, c6 = money_after(text, "Deposits:")
    gross_profit, c7 = money_after(text, "Gross Profit:")
    gross_loss, c8 = money_after(text, "Gross Loss:")
    currencies = [c for c in (c1,c2,c3,c4,c5,c6,c7,c8) if c]
    currency = currencies[0] if currencies else None
    warnings: list[str] = []
    if currencies and any(c != currency for c in currencies):
        warnings.append("currency_inconsistency_on_page")

    pf = first(text, r"(?:^|\n)Profit Factor:\s*\n\s*([\d\s,.]+)")
    algo = first(text, r"(?:^|\n)Algo trading:\s*\n\s*([\d\s,.]+)%")
    eqdd = first(text, r"(?:^|\n)By Equity:\s*\n\s*([\d\s,.]+)%")
    baldd = first(text, r"(?:^|\n)By Balance:\s*\n\s*([\d\s,.]+)%")
    latest = first(text, r"(?:^|\n)Latest trade:\s*\n\s*([^\n]+)")
    trades = int_after(text, "Trades:")

    if equity is None:
        warnings.append("missing_required_equity")
    if balance is None:
        warnings.append("missing_required_balance")
    if trades is None:
        warnings.append("missing_required_trades")

    return SignalSnapshot(
        candidate_id=str(candidate["candidate_id"]),
        name=candidate["name"],
        source_url=candidate["source_url"],
        observed_at=observed_at,
        page_sha256=page_sha,
        currency=currency,
        growth_pct=pct_after(text, "Growth:"),
        profit=profit,
        equity=equity,
        balance=balance,
        initial_deposit=initial,
        deposits=deposits,
        withdrawals=withdrawals,
        trades=trades,
        wins=int_after(text, "Profit Trades:"),
        losses=int_after(text, "Loss Trades:"),
        gross_profit=gross_profit,
        gross_loss=gross_loss,
        profit_factor=clean_number(pf) if pf is not None else None,
        algo_pct=clean_number(algo) if algo is not None else None,
        equity_dd_pct=clean_number(eqdd) if eqdd is not None else None,
        balance_dd_pct=clean_number(baldd) if baldd is not None else None,
        latest_trade_text=latest,
        parse_warnings=warnings,
    )

def fetch_html(url: str, timeout: int = 30, retries: int = 3) -> str:
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/151 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Cache-Control": "no-cache",
        "Pragma": "no-cache",
    }
    last: Exception | None = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as response:
                raw = response.read()
                encoding = response.headers.get_content_charset() or "utf-8"
                return raw.decode(encoding, errors="replace")
        except (urllib.error.URLError, TimeoutError, OSError) as exc:
            last = exc
            if attempt + 1 < retries:
                time.sleep(2 * (attempt + 1))
    raise RuntimeError(f"failed to fetch {url}: {last}")

SCHEMA = """
PRAGMA journal_mode=WAL;
CREATE TABLE IF NOT EXISTS observations (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  candidate_id TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  page_sha256 TEXT NOT NULL,
  snapshot_json TEXT NOT NULL,
  classification TEXT NOT NULL,
  accepted INTEGER NOT NULL,
  reason TEXT NOT NULL,
  prev_hash TEXT NOT NULL,
  row_hash TEXT NOT NULL UNIQUE
);
CREATE INDEX IF NOT EXISTS idx_obs_candidate ON observations(candidate_id, observed_at, id);
"""

def canonical(obj) -> str:
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)

class Ledger:
    def __init__(self, path: Path):
        path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(path)
        self.db.row_factory = sqlite3.Row
        self.db.executescript(SCHEMA)

    def head(self) -> str:
        row = self.db.execute("SELECT row_hash FROM observations ORDER BY id DESC LIMIT 1").fetchone()
        return row[0] if row else GENESIS

    def append(self, snapshot: SignalSnapshot, c: Classification) -> str:
        prev = self.head()
        snap_json = canonical(snapshot.to_dict())
        persisted = {
            "candidate_id": snapshot.candidate_id,
            "observed_at": snapshot.observed_at,
            "page_sha256": snapshot.page_sha256,
            "snapshot_json": snap_json,
            "classification": c.classification,
            "accepted": int(c.accepted),
            "reason": c.reason,
            "prev_hash": prev,
        }
        row_hash = hashlib.sha256(canonical(persisted).encode()).hexdigest()
        self.db.execute(
            "INSERT INTO observations(candidate_id,observed_at,page_sha256,snapshot_json,classification,accepted,reason,prev_hash,row_hash) VALUES(?,?,?,?,?,?,?,?,?)",
            (snapshot.candidate_id, snapshot.observed_at, snapshot.page_sha256, snap_json,
             c.classification, int(c.accepted), c.reason, prev, row_hash),
        )
        self.db.commit()
        return row_hash

    def rows(self, candidate_id: str | None = None):
        if candidate_id:
            return self.db.execute("SELECT * FROM observations WHERE candidate_id=? ORDER BY id", (candidate_id,)).fetchall()
        return self.db.execute("SELECT * FROM observations ORDER BY id").fetchall()

    def last_accepted(self, candidate_id: str):
        row = self.db.execute("SELECT snapshot_json FROM observations WHERE candidate_id=? AND accepted=1 ORDER BY id DESC LIMIT 1", (candidate_id,)).fetchone()
        return SignalSnapshot(**json.loads(row[0])) if row else None

    def activated(self, candidate_id: str) -> bool:
        row = self.db.execute("SELECT 1 FROM observations WHERE candidate_id=? AND classification='FORWARD_ACTIVATION' LIMIT 1", (candidate_id,)).fetchone()
        return bool(row)

    def verify_chain(self):
        prev = GENESIS
        problems = []
        for row in self.rows():
            persisted = {
                "candidate_id": row["candidate_id"], "observed_at": row["observed_at"],
                "page_sha256": row["page_sha256"], "snapshot_json": row["snapshot_json"],
                "classification": row["classification"], "accepted": int(row["accepted"]),
                "reason": row["reason"], "prev_hash": row["prev_hash"],
            }
            expected = hashlib.sha256(canonical(persisted).encode()).hexdigest()
            if row["prev_hash"] != prev:
                problems.append(f"row {row['id']}: prev_hash mismatch")
            if row["row_hash"] != expected:
                problems.append(f"row {row['id']}: row_hash mismatch")
            prev = row["row_hash"]
        return problems

    def close(self):
        self.db.close()

def regressed(current, previous, tolerance=TOL):
    return current is not None and previous is not None and current < previous - tolerance

def classify(snapshot: SignalSnapshot, baseline: dict, last: SignalSnapshot | None, activated: bool) -> Classification:
    fatal = [w for w in snapshot.parse_warnings if w.startswith("missing_required_") or w == "currency_inconsistency_on_page"]
    if fatal:
        return Classification(snapshot.candidate_id, "ANOMALY", False, ";".join(fatal), activated)
    if snapshot.currency and baseline.get("currency") and snapshot.currency != baseline["currency"]:
        return Classification(snapshot.candidate_id, "ANOMALY", False, "currency_changed", activated)

    btr = int(baseline["trades"])
    if snapshot.trades is None or snapshot.trades < btr:
        return Classification(snapshot.candidate_id, "ANOMALY", False, "trade_count_below_frozen_baseline", activated)
    if last and snapshot.trades < (last.trades or 0):
        return Classification(snapshot.candidate_id, "ANOMALY", False, "trade_count_regressed_from_last_accepted", activated)

    ref = last.to_dict() if last else baseline
    for label, cur, prev in [
        ("deposits", snapshot.deposits, ref.get("deposits")),
        ("withdrawals", snapshot.withdrawals, ref.get("withdrawals")),
        ("gross_profit", snapshot.gross_profit, ref.get("gross_profit")),
    ]:
        if regressed(cur, prev):
            return Classification(snapshot.candidate_id, "ANOMALY", False, f"{label}_regressed", activated)

    if snapshot.gross_loss is not None and ref.get("gross_loss") is not None:
        if abs(snapshot.gross_loss) + TOL < abs(float(ref["gross_loss"])):
            return Classification(snapshot.candidate_id, "ANOMALY", False, "gross_loss_magnitude_regressed", activated)

    if not activated:
        if snapshot.trades == btr:
            return Classification(snapshot.candidate_id, "NO_OUTCOME", False, "no_new_post_baseline_trade", False)
        return Classification(snapshot.candidate_id, "FORWARD_ACTIVATION", True, "first_consistent_post_baseline_trade_count_increase", True, snapshot.trades-btr)

    return Classification(snapshot.candidate_id, "FORWARD", True, "consistent_post_activation_observation", True, snapshot.trades-btr)

def parse_dt(value: str) -> datetime:
    d = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

def maturity(days: float, trades: int, observations: int) -> float:
    time_evidence = 1.0 - math.exp(-max(0.0, days) / 30.0)
    trade_evidence = 1.0 - math.exp(-max(0, trades) / 100.0)
    obs_evidence = 1.0 - math.exp(-max(0, observations) / 20.0)
    return max(0.0, min(1.0, .50*time_evidence + .35*trade_evidence + .15*obs_evidence))

def result_for(ledger: Ledger, baseline: dict, rules: dict) -> dict:
    cid = str(baseline["candidate_id"])
    rows = ledger.rows(cid)
    accepted = [r for r in rows if r["accepted"]]
    anomalies = sum(1 for r in rows if r["classification"] == "ANOMALY")
    if not accepted:
        return {
            "candidate_id":cid, "name":baseline["name"], "status":"AWAITING_FORWARD_TRADE",
            "baseline_at":baseline["observed_at"], "last_observed_at":rows[-1]["observed_at"] if rows else None,
            "forward_trades":0, "forward_wins":None, "forward_losses":None, "forward_win_rate_pct":None,
            "forward_pnl":None, "forward_realized_pnl":None, "forward_return_pct":None,
            "forward_gross_profit":None, "forward_gross_loss":None, "forward_profit_factor":None,
            "max_forward_equity_dd_pct":0.0, "elapsed_days":0.0, "maturity":0.0,
            "accepted_observations":0, "anomalies":anomalies, "decision_ready":False,
            "decision_reasons":["no accepted post-baseline observation"],
        }

    snaps = [SignalSnapshot(**json.loads(r["snapshot_json"])) for r in accepted]
    last = snaps[-1]
    b_equity = float(baseline["equity"])
    b_balance = float(baseline["balance"])
    b_dep = float(baseline.get("deposits",0) or 0)
    b_wd = float(baseline.get("withdrawals",0) or 0)
    dep_delta = max(0.0, (last.deposits or 0)-b_dep)
    wd_delta = max(0.0, (last.withdrawals or 0)-b_wd)
    pnl = last.equity-b_equity-dep_delta+wd_delta if last.equity is not None else None
    realized = last.balance-b_balance-dep_delta+wd_delta if last.balance is not None else None
    ret = 100*pnl/b_equity if pnl is not None and b_equity > 0 else None
    ftr = max(0,(last.trades or baseline["trades"])-int(baseline["trades"]))
    fw = max(0,last.wins-int(baseline["wins"])) if last.wins is not None and baseline.get("wins") is not None else None
    fl = max(0,last.losses-int(baseline["losses"])) if last.losses is not None and baseline.get("losses") is not None else None
    wr = 100*fw/ftr if fw is not None and ftr else None
    fgp = max(0,last.gross_profit-float(baseline["gross_profit"])) if last.gross_profit is not None else None
    fgl = max(0,abs(last.gross_loss)-abs(float(baseline["gross_loss"]))) if last.gross_loss is not None else None
    fpf = (fgp/fgl if fgl and fgl > 0 else (float("inf") if fgp and fgp > 0 else None)) if fgp is not None and fgl is not None else None

    peak = b_equity
    maxdd = 0.0
    for snap in snaps:
        if snap.equity is None:
            continue
        adj = snap.equity-max(0,(snap.deposits or 0)-b_dep)+max(0,(snap.withdrawals or 0)-b_wd)
        peak = max(peak, adj)
        if peak > 0:
            maxdd = max(maxdd, 100*(peak-adj)/peak)

    days = max(0,(parse_dt(last.observed_at)-parse_dt(baseline["observed_at"])).total_seconds()/86400)
    mat = maturity(days,ftr,len(accepted))
    req = rules["decision_ready"]
    reasons = []
    if mat < float(req["min_maturity"]): reasons.append(f"maturity {mat:.3f} < {req['min_maturity']}")
    if ret is None or ret <= 0: reasons.append("forward return is not positive")
    dd_limit = min(float(req["absolute_dd_cap_pct"]), max(float(req["minimum_dd_allowance_pct"]), float(req["baseline_dd_multiplier"])*float(baseline["baseline_equity_dd_pct"])))
    if maxdd > dd_limit: reasons.append(f"forward DD {maxdd:.2f}% > {dd_limit:.2f}%")
    if ftr >= int(req["pf_gate_min_trades"]) and (fpf is None or fpf < float(req["min_pf_after_trade_gate"])):
        reasons.append("forward PF below gate")

    return {
        "candidate_id":cid, "name":baseline["name"], "status":"ACTIVE",
        "baseline_at":baseline["observed_at"], "last_observed_at":last.observed_at,
        "forward_trades":ftr, "forward_wins":fw, "forward_losses":fl, "forward_win_rate_pct":wr,
        "forward_pnl":pnl, "forward_realized_pnl":realized, "forward_return_pct":ret,
        "forward_gross_profit":fgp, "forward_gross_loss":fgl, "forward_profit_factor":fpf,
        "max_forward_equity_dd_pct":maxdd, "elapsed_days":days, "maturity":mat,
        "accepted_observations":len(accepted), "anomalies":anomalies,
        "decision_ready":not reasons, "decision_reasons":reasons,
    }

def load_json(path: Path):
    return json.loads(path.read_text(encoding="utf-8"))

def configs():
    candidates = load_json(ROOT/"config/candidates.json")["candidates"]
    baselines = {str(x["candidate_id"]):x for x in load_json(ROOT/"config/baselines.json")["candidates"]}
    rules = load_json(ROOT/"config/decision_rules.json")
    return candidates, baselines, rules

def write_reports(results: list[dict], events: list[dict]):
    out = ROOT/"reports"
    out.mkdir(parents=True,exist_ok=True)
    serializable = []
    for row in results:
        r = dict(row)
        if r.get("forward_profit_factor") == float("inf"):
            r["forward_profit_factor"] = "Infinity"
        serializable.append(r)
    (out/"status.json").write_text(json.dumps(serializable,indent=2,allow_nan=False),encoding="utf-8")
    (out/"latest_run.json").write_text(json.dumps({"observed_at":now_utc(),"events":events},indent=2),encoding="utf-8")

    fields = list(results[0].keys()) if results else []
    with (out/"status.csv").open("w",newline="",encoding="utf-8") as f:
        writer = csv.DictWriter(f,fieldnames=fields)
        writer.writeheader()
        for row in results:
            r = dict(row)
            r["decision_reasons"] = " | ".join(r["decision_reasons"])
            writer.writerow(r)

    trs = []
    for r in results:
        pf = r["forward_profit_factor"]
        pf_s = "—" if pf is None else ("∞" if pf == float("inf") else f"{pf:.2f}")
        ret_s = "—" if r["forward_return_pct"] is None else f"{r['forward_return_pct']:+.3f}%"
        trs.append("<tr>"+"".join([
            f"<td>{html_lib.escape(r['name'])}</td>", f"<td>{r['status']}</td>",
            f"<td>{r['forward_trades']}</td>", f"<td>{ret_s}</td>", f"<td>{pf_s}</td>",
            f"<td>{r['max_forward_equity_dd_pct']:.3f}%</td>", f"<td>{r['maturity']:.3f}</td>",
            f"<td>{'YES' if r['decision_ready'] else 'NO'}</td>"
        ])+"</tr>")
    doc = """<!doctype html><meta charset="utf-8"><title>CopyTrader Forward Test</title>
<style>body{font-family:Segoe UI,Arial,sans-serif;max-width:1100px;margin:36px auto;padding:0 20px}table{border-collapse:collapse;width:100%}th,td{border-bottom:1px solid #ddd;padding:10px;text-align:right}th:first-child,td:first-child{text-align:left}.note{color:#555}</style>
<h1>CopyTrader Prospective Forward Test</h1><p class="note">Only internally consistent post-baseline observations are admitted. No historical rebaselining.</p>
<table><thead><tr><th>Candidate</th><th>Status</th><th>New trades</th><th>Forward return</th><th>Forward PF</th><th>Forward max DD</th><th>Maturity</th><th>Decision ready</th></tr></thead><tbody>""" + "".join(trs) + "</tbody></table>"
    (out/"status.html").write_text(doc,encoding="utf-8")

def run_once():
    candidates, baselines, rules = configs()
    ledger = Ledger(ROOT/"data/forward.sqlite3")
    events = []
    try:
        for candidate in candidates:
            observed = now_utc()
            try:
                page = fetch_html(candidate["source_url"])
                snap = parse_signal_page(candidate,page,observed)
                baseline = baselines[str(candidate["candidate_id"])]
                last = ledger.last_accepted(str(candidate["candidate_id"]))
                cl = classify(snap,baseline,last,ledger.activated(str(candidate["candidate_id"])))
                row_hash = ledger.append(snap,cl)
                events.append({"candidate":candidate["name"],"classification":cl.classification,"reason":cl.reason,"row_hash":row_hash})
            except Exception as exc:
                events.append({"candidate":candidate["name"],"classification":"FETCH_ERROR","reason":str(exc)})
        results = [result_for(ledger,baselines[str(c["candidate_id"])],rules) for c in candidates]
        write_reports(results,events)
        print(json.dumps({"events":events,"results":results},indent=2,default=str))
        if events and all(e["classification"]=="FETCH_ERROR" for e in events):
            raise SystemExit(2)
    finally:
        ledger.close()

def status():
    candidates, baselines, rules = configs()
    ledger = Ledger(ROOT/"data/forward.sqlite3")
    try:
        results = [result_for(ledger,baselines[str(c["candidate_id"])],rules) for c in candidates]
        write_reports(results,[])
        print(json.dumps(results,indent=2,default=str))
    finally:
        ledger.close()

def verify():
    problems = []
    lock = load_json(ROOT/"config/BASELINE_LOCK.json")
    for rel, expected in lock["files"].items():
        path = ROOT/rel
        if not path.exists():
            problems.append(f"missing {rel}")
        elif hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            problems.append(f"hash mismatch {rel}")
    db = ROOT/"data/forward.sqlite3"
    if db.exists():
        ledger = Ledger(db)
        problems.extend(ledger.verify_chain())
        ledger.close()
    print(json.dumps({"ok":not problems,"problems":problems},indent=2))
    if problems:
        raise SystemExit(1)

def self_test():
    c = {"candidate_id":"x","name":"X","source_url":"https://example.invalid"}
    page = """
    <div>Profit:</div><div>100.00 USD</div><div>Equity:</div><div>1100.00 USD</div>
    <div>Balance:</div><div>1100.00 USD</div><div>Initial Deposit:</div><div>1000.00 USD</div>
    <div>Withdrawals:</div><div>0.00 USD</div><div>Deposits:</div><div>0.00 USD</div>
    <div>Trades:</div><div>11</div><div>Profit Trades:</div><div>7</div><div>Loss Trades:</div><div>4</div>
    <div>Gross Profit:</div><div>200.00 USD</div><div>Gross Loss:</div><div>-100.00 USD</div>
    <div>Profit Factor:</div><div>2.00</div><div>Algo trading:</div><div>100%</div>
    <div>By Equity:</div><div>5.00%</div><div>By Balance:</div><div>4.00%</div>
    <div>Latest trade:</div><div>1 hour ago</div><div>Growth:</div><div>10%</div>
    """
    snap = parse_signal_page(c,page,"2026-09-04T00:00:00Z")
    assert snap.trades == 11 and snap.equity == 1100 and snap.gross_loss == -100
    baseline = {"candidate_id":"x","name":"X","observed_at":"2026-09-03T00:00:00Z","currency":"USD","equity":1000.0,"balance":1000.0,"deposits":0.0,"withdrawals":0.0,"trades":10,"wins":6,"losses":4,"gross_profit":150.0,"gross_loss":-100.0,"baseline_equity_dd_pct":5.0}
    cl = classify(snap,baseline,None,False)
    assert cl.classification == "FORWARD_ACTIVATION" and cl.accepted
    no = SignalSnapshot(**{**snap.to_dict(),"trades":10})
    cl2 = classify(no,baseline,None,False)
    assert cl2.classification == "NO_OUTCOME" and not cl2.accepted
    print("self-test: PASS")

def main():
    parser = argparse.ArgumentParser(description="GitHub-native prospective MQL5 forward tester")
    parser.add_argument("command",choices=["run","status","verify","self-test"])
    args = parser.parse_args()
    {"run":run_once,"status":status,"verify":verify,"self-test":self_test}[args.command]()

if __name__ == "__main__":
    main()
