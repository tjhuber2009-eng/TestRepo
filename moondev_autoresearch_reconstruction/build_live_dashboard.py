"""Build a stable private GitHub live dashboard (Markdown issue body).

Reads the same frozen project state used by the HTML dashboard. It does not
open hidden validation or 2023+ OOS. Intended for issue #20, updated after
research/tournament cycles.
"""
import json
import math
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
STATE = HERE / "continuous_state"
TOURNAMENT = HERE / "tournament_state"
RUNTIME = HERE / "dashboard_runtime"
V4_STATE = HERE / "v4_state"
OUT = HERE / "live-dashboard.md"
ACTIVE_PROTOCOL = "nested_chronological_v3"

def load(path, default=None):
    p=Path(path)
    if not p.exists():
        return default
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return default

def f(v, digits=3):
    try:
        x=float(v)
        if not math.isfinite(x):
            return "—"
        return f"{x:.{digits}f}"
    except Exception:
        return "—"

def pct(v, digits=1):
    try:
        x=float(v)
        if not math.isfinite(x):
            return "—"
        return f"{x:.{digits}f}%"
    except Exception:
        return "—"

def development_period(track_id, total_return_pct):
    meta=load(STATE/"tracks"/str(track_id)/"state_meta.json",{}) or {}
    baseline=meta.get("baseline") or {}
    start=baseline.get("start")
    end=baseline.get("end") or baseline.get("adaptive_development_end")
    try:
        s=datetime.fromisoformat(str(start).replace("Z","+00:00"))
        e=datetime.fromisoformat(str(end).replace("Z","+00:00"))
        years=(e-s).total_seconds()/(365.2425*86400.0)
        multiple=1.0+float(total_return_pct)/100.0
        cagr=(multiple**(1.0/years)-1.0)*100.0 if years>0 and multiple>0 else None
        return start,end,years,cagr
    except Exception:
        return start,end,None,None

def status_badge(status, conclusion=None):
    x=(conclusion or status or "").lower()
    if x in {"success","completed"}:
        return "🟢"
    if x in {"in_progress","queued","pending"}:
        return "🟡"
    if x in {"failure","cancelled","timed_out"}:
        return "🔴"
    return "⚪"

def latest_run(path):
    x=load(path,{}) or {}
    rows=x.get("workflow_runs",[])
    return rows[0] if rows else None

def bar(value, total, width=24):
    if total <= 0:
        return "░"*width
    ratio=max(0.0,min(1.0,value/total))
    fill=int(round(ratio*width))
    return "█"*fill + "░"*(width-fill)

progress=load(STATE/"progress.json",{}) or {}
board=load(STATE/"leaderboard_latest.json",{}) or {}
tour=load(TOURNAMENT/"tournament-summary.json",None)
continuous=latest_run(RUNTIME/"continuous_runs.json")
tournament_run=latest_run(RUNTIME/"tournament_runs.json")
jobs=(load(RUNTIME/"tournament_jobs.json",{}) or {}).get("jobs",[])
v4_private=load(V4_STATE/"development-bootstrap.json",{}) or {}
v4_prop=load(V4_STATE/"prop-intraday-bootstrap.json",{}) or {}

stale_state=None
state_is_active=(
    progress.get("protocol")==ACTIVE_PROTOCOL
    and board.get("protocol")==ACTIVE_PROTOCOL
)
if not state_is_active:
    stale_state={
        "progress_protocol":progress.get("protocol"),
        "leaderboard_protocol":board.get("protocol"),
        "leaderboard_count":len(board.get("rows",[]) or []),
        "updated_at":progress.get("updated_at"),
    }
    progress={
        "protocol":ACTIVE_PROTOCOL,
        "phase":"initializing",
        "rows":[],
        "runnable_track_count":0,
        "total_valid_candidates":0,
        "terminal_track_count":0,
        "validation_pass_count":0,
        "validation_fail_count":0,
        "breadth_target":10,
        "depth_target":30,
        "elite_target":60,
    }
    board={"protocol":ACTIVE_PROTOCOL,"rows":[]}
if tour and tour.get("protocol")!=ACTIVE_PROTOCOL:
    tour=None

rows=progress.get("rows",[])
runnable=int(progress.get("runnable_track_count",len(rows)) or 0)
valid=int(progress.get("total_valid_candidates",0) or 0)
breadth_target=int(progress.get("breadth_target",10) or 10)
breadth_total=runnable*breadth_target
breadth_pct=(100*valid/breadth_total) if breadth_total else 0
touched=sum(int(r.get("attempts",0) or 0)>0 for r in rows)
v1=sum(int(r.get("valid_attempts",0) or 0)>=1 for r in rows)
v2=sum(int(r.get("valid_attempts",0) or 0)>=2 for r in rows)
v10=sum(int(r.get("valid_attempts",0) or 0)>=10 for r in rows)
hidden_pass=int(progress.get("validation_pass_count",0) or 0)
hidden_fail=int(progress.get("validation_fail_count",0) or 0)
hidden_open=(hidden_pass+hidden_fail)>0 or bool(v4_private.get("hidden_validation_opened")) or bool(v4_prop.get("hidden_validation_opened"))
final_oos_open=bool(v4_private.get("final_oos_opened")) or bool(v4_prop.get("final_oos_opened"))

stamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

out=[
"# AUTORESEARCH Live Dashboard",
"",
f"> **Auto-updated:** {stamp}  ",
f"> **Protocol:** `{ACTIVE_PROTOCOL}`  ",
f"> **Phase:** **{str(progress.get('phase','—')).upper()}**",
"",
*(
    [
        "> ⚠️ **Protocol-stale persistent state detected.** The saved state is "
        f"`{stale_state.get('progress_protocol')}`; its {stale_state.get('leaderboard_count',0)} "
        "leaderboard rows are historical only and are intentionally hidden until a v3 cycle initializes fresh state.",
        "",
    ] if stale_state else []
),
"| Live status | Value |",
"|---|---|",
f"| Research phase | **{str(progress.get('phase','—')).upper()}** |",
f"| Breadth progress | **{breadth_pct:.2f}%** |",
f"| Hidden validation | **{'SEALED' if not hidden_open else 'OPEN'}** |",
"| 2023+ final OOS | **" + ("OPEN" if final_oos_open else "SEALED") + "** |",
"",
"## Search progress",
"",
"| Metric | Current |",
"|---|---:|",
f"| Breadth completion | **{breadth_pct:.2f}%** |",
f"| Valid candidates | **{valid:,} / {breadth_total:,}** |",
f"| Runnable tracks | **{runnable:,}** |",
f"| Tracks touched | **{touched:,}** |",
f"| Tracks with ≥1 valid | **{v1:,}** |",
f"| Tracks with ≥2 valid | **{v2:,}** |",
f"| Breadth-complete tracks | **{v10:,}** |",
f"| Terminal tracks | **{int(progress.get('terminal_track_count',0) or 0):,}** |",
"",
"## Research integrity",
"",
f"- {'🟢' if not hidden_open else '🟡'} Hidden pre-OOS validation: **{'SEALED' if not hidden_open else f'OPEN — {hidden_pass} pass / {hidden_fail} fail'}**",
f"- {'🟡' if final_oos_open else '🟢'} Final 2023+ OOS: **{'OPEN' if final_oos_open else 'SEALED'}**",
"- 🟢 Cost stress: **2×**",
"- 🟢 Prop max DD: **10%**",
"- 🟢 Private max DD: **32%**",
"",
"## AUTORESEARCH V4 — private account",
"",
*((
    lambda chosen: [
        "| Metric | Current |",
        "|---|---:|",
        f"| Authoritative concentration cap | **{pct(100*float(v4_private.get('portfolio_authoritative_concentration_cap',0.55)),0)}** |",
        f"| Portfolio CAGR | **{pct(chosen.get('cagr_pct'),2)}** |",
        f"| Bootstrap median CAGR | **{pct(chosen.get('bootstrap_median_cagr_pct'),2)}** |",
        f"| Observed max DD | **{pct(chosen.get('max_dd_pct'),2)}** |",
        f"| Bootstrap q95 DD | **{pct(chosen.get('bootstrap_dd_q95_pct'),2)}** |",
        f"| Sharpe | **{f(chosen.get('sharpe'),3)}** |",
        f"| Gross exposure | **{pct(100*float(chosen.get('gross_exposure',0)),1)}** |",
        f"| Cash | **{pct(100*float(chosen.get('cash_weight',0)),1)}** |",
        "",
        "### Private portfolio weights",
        "",
        "| Strategy | Weight |",
        "|---|---:|",
        *[
            f"| {name} | {pct(100*float(weight),2)} |"
            for name,weight in sorted(
                (chosen.get("weights") or {}).items(),
                key=lambda kv: float(kv[1]),
                reverse=True,
            )
        ],
        "",
        "### Private concentration sensitivity",
        "",
        "| Cap | CAGR | Bootstrap median CAGR | q95 DD | Observed DD | Sharpe |",
        "|---:|---:|---:|---:|---:|---:|",
        *[
            f"| {pct(100*float(cap),0)}{' **AUTH**' if abs(float(cap)-float(v4_private.get('portfolio_authoritative_concentration_cap',0.55)))<1e-9 else ''} | "
            f"{pct((row.get('chosen') or row).get('cagr_pct'),2)} | "
            f"{pct((row.get('chosen') or row).get('bootstrap_median_cagr_pct'),2)} | "
            f"{pct((row.get('chosen') or row).get('bootstrap_dd_q95_pct'),2)} | "
            f"{pct((row.get('chosen') or row).get('max_dd_pct'),2)} | "
            f"{f((row.get('chosen') or row).get('sharpe'),3)} |"
            for cap,row in sorted(
                (v4_private.get("portfolio_concentration_sensitivity") or {}).items(),
                key=lambda kv: float(kv[0]),
            )
        ],
    ]
)( (v4_private.get("portfolio") or {}).get("chosen") )
if (v4_private.get("portfolio") or {}).get("chosen")
else ["V4 private portfolio state is not available in this snapshot."])),
"",
"### Continuous private promotion queue",
"",
"| Track | V4 CAGR | 3× cost CAGR | DD | Sharpe | PBO | Gate |",
"|---|---:|---:|---:|---:|---:|---|",
*[
    f"| {(r.get('candidate') or {}).get('track_id','—')} | "
    f"{pct((r.get('base') or {}).get('cagr_pct'),2)} | "
    f"{pct((r.get('cost_stress') or {}).get('cagr_pct'),2)} | "
    f"{pct((r.get('base') or {}).get('max_dd_pct'),2)} | "
    f"{f((r.get('base') or {}).get('sharpe'),3)} | "
    f"{pct(None if (r.get('candidate') or {}).get('pbo') is None else 100*float((r.get('candidate') or {}).get('pbo')),1)} | "
    f"{r.get('portfolio_gate_reason') or '—'} |"
    for r in (v4_private.get("continuous_private_transfer") or {}).get("candidates",[])
],
"",
"## AUTORESEARCH V4 — prop-firm frontiers",
"",
*sum([
    (
        lambda pid,label,p: [
            f"### {label}",
            "",
            "| Frontier | Family / source | C/V/F | Eval pass | Eval days | First eff. | Repeat eff. | 12-cycle reward | Funded survival | Daily breach | Max breach |",
            "|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|",
            *[
                (
                    lambda r,params,x,funded,nonauth: (
                        f"| {view.replace('_',' ')} | "
                        f"{params.get('family','—')}"
                        f"{' / '+str(params.get('continuous_track_id')) if params.get('continuous_track_id') else ''}"
                        f"{' **NON-AUTH PROXY**' if nonauth else ''} | "
                        f"{f(x.get('challenge_exposure_scale'),2)} / {f(x.get('verification_exposure_scale'),2)} / {f(x.get('funded_exposure_scale'),2)} | "
                        f"{pct(None if x.get('combined_evaluation_pass_probability') is None else 100*float(x.get('combined_evaluation_pass_probability')),1)} | "
                        f"{f(x.get('expected_evaluation_days_if_passed'),1)} | "
                        f"{f(x.get('payout_efficiency_score'),6)} | "
                        f"{f(x.get('repeat_payout_efficiency_score'),6)} | "
                        f"{pct(x.get('repeat_expected_reward_pct'),2)} | "
                        f"{pct(None if funded.get('survival_probability') is None else 100*float(funded.get('survival_probability')),1)} | "
                        f"{pct(None if funded.get('daily_loss_breach_probability') is None else 100*float(funded.get('daily_loss_breach_probability')),1)} | "
                        f"{pct(None if funded.get('max_loss_breach_probability') is None else 100*float(funded.get('max_loss_breach_probability')),1)} |"
                    )
                )(
                    (p.get("refined_frontiers") or {}).get(view) or {},
                    ((p.get("refined_frontiers") or {}).get(view) or {}).get("params") or {},
                    ((p.get("refined_frontiers") or {}).get(view) or {}).get("view") or {},
                    ((((p.get("refined_frontiers") or {}).get(view) or {}).get("view") or {}).get("funded") or {}),
                    (
                        (((p.get("refined_frontiers") or {}).get(view) or {}).get("params") or {}).get("transfer_exactness")=="signal_only_proxy"
                        or (((p.get("refined_frontiers") or {}).get(view) or {}).get("params") or {}).get("source_stop_transferred") is False
                    ),
                )
                for view in ["max_payout_efficiency","max_repeat_payout_efficiency","max_evaluation_pass","safest_funded","balanced","conservative"]
            ],
            "",
            "#### 252 / 365 / 504-day sensitivity",
            "",
            "| Frontier | 252 days | 365 days | 504 days |",
            "|---|---|---|---|",
            *[
                (
                    lambda hs: f"| {view.replace('_',' ')} | " + " | ".join(
                        (
                            lambda x,fd: (
                                f"pass {pct(None if x.get('combined_evaluation_pass_probability') is None else 100*float(x.get('combined_evaluation_pass_probability')),1)}; "
                                f"repeat {f(x.get('repeat_payout_efficiency_score'),5)}; "
                                f"reward {pct(x.get('repeat_expected_reward_pct'),1)}; "
                                f"survival {pct(None if fd.get('survival_probability') is None else 100*float(fd.get('survival_probability')),1)}"
                            )
                        )(
                            (hs.get(str(h)) or {}).get("view") or {},
                            (((hs.get(str(h)) or {}).get("view") or {}).get("funded") or {}),
                        )
                        for h in (252,365,504)
                    ) + " |"
                )((((p.get("horizon_sensitivity") or {}).get(view) or {}).get("horizons") or {}))
                for view in ["max_payout_efficiency","max_repeat_payout_efficiency","max_evaluation_pass","safest_funded","balanced","conservative"]
            ],
            "",
        ]
    )(pid,label,(v4_prop.get("programs") or {}).get(pid) or {})
    for pid,label in [("ftmo_1step_2026","FTMO 1-Step"),("ftmo_2step_2026","FTMO 2-Step")]
],[]),
"### Continuous → prop adapter audit",
"",
"| Track | Family | Adapter | Exactness | Source stop | Status |",
"|---|---|---|---|---|---|",
*[
    (
        lambda p,nonauth: f"| {r.get('track_id','—')} | {r.get('family','—')} | {r.get('adapter') or '—'} | "
        f"{p.get('transfer_exactness') or r.get('exactness') or '—'} | "
        f"{p.get('source_stop_transferred','—')} | "
        f"{'NON-AUTH PROXY' if nonauth else r.get('transfer_status','—')} |"
    )(
        r.get("transfer_params") or {},
        (
            (r.get("transfer_params") or {}).get("transfer_exactness")=="signal_only_proxy"
            or (r.get("transfer_params") or {}).get("source_stop_transferred") is False
        ),
    )
    for r in (v4_prop.get("continuous_prop_transfer") or {}).get("candidates",[])
],
"",
"## Current development champions",
"",
"| # | Family | Target | Profile | Eligible | Robust K | CAGR | Excess vs B&H | B&H CAGR | Years | Sharpe | PF | DD | PSR | FDR q | PBO | Evidence | Data | Trades/yr |",
"|---:|---|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---|---|---:|",
]
for i,r in enumerate((board.get("rows",[]) or [])[:15],1):
    _start,_end,_years,_cagr=development_period(
        r.get("track_id"),
        r.get("development_return_pct"),
    )
    _cagr = r.get("development_cagr_pct") if r.get("development_cagr_pct") is not None else _cagr
    _psr = r.get("development_psr_zero")
    _q = r.get("multiple_test_qvalue")
    _pbo = r.get("pbo")
    out.append(
        f"| {i} | {r.get('family','—')} | {str(r.get('target','—')).upper()} | "
        f"{r.get('profile','—')} | {'YES' if r.get('development_guard_ok') is not False else 'NO'} | "
        f"{f(r.get('development_score'),6)} | {pct(_cagr,1)} | {pct(r.get('excess_cagr_vs_buyhold_pct'),1)} | "
        f"{pct(r.get('benchmark_cagr_pct'),1)} | {f(_years,1)} | "
        f"{f(r.get('development_sharpe'),3)} | {f(r.get('development_pf'),2)} | "
        f"{pct(r.get('development_max_dd_pct'),2)} | "
        f"{pct(None if _psr is None else 100*float(_psr),1)} | "
        f"{pct(None if _q is None else 100*float(_q),1)} | "
        f"{pct(None if _pbo is None else 100*float(_pbo),1)} | "
        f"{r.get('evidence_grade') or '—'} | {r.get('data_quality_grade') or '—'} | "
        f"{f(r.get('development_trades_per_year'),1)} |"
    )

# A separate equal-time return view prevents cumulative-return duration bias.
_cagr_rows=[]
for r in (board.get("rows",[]) or []):
    if r.get("development_guard_ok") is False:
        continue
    v=r.get("development_cagr_pct")
    if v is None:
        _,_,_,v=development_period(r.get("track_id"),r.get("development_return_pct"))
    try:
        vv=float(v)
    except Exception:
        continue
    if math.isfinite(vv):
        _cagr_rows.append((vv,r))
_cagr_rows.sort(key=lambda x:x[0],reverse=True)
out += [
"",
"## Equal-time return leaderboard (CAGR)",
"",
"| # | Track | Profile | CAGR | Robust K | DD | Years | Evidence |",
"|---:|---|---|---:|---:|---:|---:|---|",
]
for i,(cg,r) in enumerate(_cagr_rows[:12],1):
    _,_,yrs,_=development_period(r.get("track_id"),r.get("development_return_pct"))
    out.append(
        f"| {i} | {r.get('family')} / {str(r.get('target','')).upper()} | "
        f"{r.get('profile')} | {pct(cg,1)} | {f(r.get('development_score'),6)} | "
        f"{pct(r.get('development_max_dd_pct'),2)} | {f(yrs,1)} | "
        f"{r.get('evidence_grade') or '—'} |"
    )

out += [
"",
"## Project health",
"",
f"- Active protocol: **{ACTIVE_PROTOCOL}**",
"- Prior protocol v2 state is preserved on branch **continuous-autoresearch-v2-archive-20260905**.",
"- Candidate selection now uses duration-normalized K, 2×/3× cost stress, PSR, block-bootstrap diagnostics and CSCV PBO when enough candidates exist.",
"- Multiple-testing FDR q-values are reported across current champions rather than silently treating 514 searches as independent.",
"",
"## Search-quality diagnostics",
"",
f"- Backtested candidates: **{valid:,}**",
f"- Guard-passing candidates: **{int(progress.get('total_guard_passed_candidates',0) or 0):,}**",
f"- Keepers promoted: **{int(progress.get('total_kept_candidates',0) or 0):,}**",
f"- Model crashes: **{int(progress.get('total_crashes',0) or 0):,}**",
f"- Parameter-only mutations rejected: **{int(progress.get('total_parameter_only',0) or 0):,}**",
f"- Too-broad rewrites rejected: **{int(progress.get('total_too_broad',0) or 0):,}**",
f"- Risk/sizing-control changes rejected: **{int(progress.get('total_risk_control_changes',0) or 0):,}**",
f"- Semantic duplicates rejected: **{int(progress.get('total_duplicates',0) or 0):,}**",
"",
"## Research-agent performance",
"",
]
_model_rows=progress.get("model_performance",[]) or []
if _model_rows:
    out += [
        "| Model | Attempts | Admission | Keeper | Crash | Mean ΔK | Unique ideas |",
        "|---|---:|---:|---:|---:|---:|---:|",
    ]
    for r in _model_rows:
        out.append(
            f"| {r.get('model')} | {int(r.get('attempts',0) or 0)} | "
            f"{pct(100*float(r.get('admission_rate',0) or 0),1)} | "
            f"{pct(100*float(r.get('keeper_rate',0) or 0),1)} | "
            f"{pct(100*float(r.get('crash_rate',0) or 0),1)} | "
            f"{f(r.get('mean_delta_k'),6)} | {int(r.get('unique_ideas',0) or 0)} |"
        )
else:
    out.append("No protocol-v3 model attempts recorded yet.")

out += [
"",
"## Data fidelity",
"",
"- **A:** checksum-verified Binance archive / exact spot instrument.",
"- **B:** Yahoo adjusted daily stock/ETF snapshot; no archive checksum.",
"- **C:** Yahoo continuous futures proxy; **not contract-exact**.",
"",
"## Automation status",
"",
]
if continuous:
    s=continuous.get("status")
    c=continuous.get("conclusion")
    out.append(
        f"- {status_badge(s,c)} **Continuous AUTORESEARCH** — "
        f"{c if s=='completed' else s} · run "
        f"[#{continuous.get('run_number',continuous.get('id'))}]({continuous.get('html_url')})"
    )
else:
    out.append("- ⚪ Continuous AUTORESEARCH — no run metadata")

if tournament_run:
    s=tournament_run.get("status")
    c=tournament_run.get("conclusion")
    out.append(
        f"- {status_badge(s,c)} **AI model tournament** — "
        f"{c if s=='completed' else s} · run "
        f"[#{tournament_run.get('run_number',tournament_run.get('id'))}]({tournament_run.get('html_url')})"
    )
else:
    out.append("- ⚪ AI model tournament — no run metadata")

if tour and tour.get("ranking"):
    out += [
        "",
        "## Zero-fee AI tournament ranking",
        "",
        "| # | Model | Provider | Keep-worthy | Case wins | Guard pass | Median ΔK |",
        "|---:|---|---|---:|---:|---:|---:|",
    ]
    for i,r in enumerate(tour.get("ranking",[])[:10],1):
        out.append(
            f"| {i} | {r.get('model','—')} | {r.get('provider','—')} | "
            f"{int(r.get('would_keep',0) or 0)} | {int(r.get('matched_case_wins',0) or 0)} | "
            f"{int(r.get('guard_pass',0) or 0)} | {f(r.get('median_delta_k'),6)} |"
        )
elif jobs:
    out += ["","## AI tournament — live contestant status",""]
    for j in jobs:
        name=j.get("name","")
        if not name.startswith("model ("):
            continue
        s=j.get("status")
        c=j.get("conclusion")
        out.append(f"- {status_badge(s,c)} {name.replace('model (','').rstrip(')')} — **{c if s=='completed' else s}**")
else:
    out += ["","## AI tournament","","No tournament result is available yet."]

out += [
"",
"---",
"",
"**Return comparison:** CAGR is the primary return percentage. It annualizes each development result geometrically, so a 3.4-year crypto backtest and an 11-year equity backtest are shown on the same per-year basis. Cumulative return and years are retained for context.",
"",
"**Interpretation:** all leaderboard numbers above are development-period results unless hidden validation is explicitly shown as open. They are research results, not production proof.",
"",
"**Stable dashboard URL:** this issue. Refresh the page for the latest completed workflow state.",
]

OUT.write_text("\n".join(out)+"\n",encoding="utf-8")
print(OUT)
