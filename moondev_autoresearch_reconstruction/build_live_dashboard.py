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
OUT = HERE / "live-dashboard.md"

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
hidden_open=(hidden_pass+hidden_fail)>0

stamp=datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")

out=[
"# AUTORESEARCH Live Dashboard",
"",
f"> **Auto-updated:** {stamp}  ",
f"> **Protocol:** `{progress.get('protocol','—')}`  ",
f"> **Phase:** **{str(progress.get('phase','—')).upper()}**",
"",
"| Live status | Value |",
"|---|---|",
f"| Research phase | **{str(progress.get('phase','—')).upper()}** |",
f"| Breadth progress | **{breadth_pct:.2f}%** |",
f"| Hidden validation | **{'SEALED' if not hidden_open else 'OPEN'}** |",
"| 2023+ final OOS | **SEALED** |",
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
"- 🟢 Final 2023+ OOS: **SEALED**",
"- 🟢 Cost stress: **2×**",
"- 🟢 Prop max DD: **10%**",
"- 🟢 Private max DD: **32%**",
"",
"## Current development champions",
"",
"| # | Family | Target | Profile | Robust K | CAGR | Cum. Return | Years | Sharpe | PF | DD | Valid |",
"|---:|---|---|---|---:|---:|---:|---:|---:|---:|---:|---:|",
]
for i,r in enumerate((board.get("rows",[]) or [])[:15],1):
    _start,_end,_years,_cagr=development_period(
        r.get("track_id"),
        r.get("development_return_pct"),
    )
    out.append(
        f"| {i} | {r.get('family','—')} | {str(r.get('target','—')).upper()} | "
        f"{r.get('profile','—')} | {f(r.get('development_score'),6)} | "
        f"{pct(_cagr,1)} | {pct(r.get('development_return_pct'),1)} | "
        f"{f(_years,1)} | {f(r.get('development_sharpe'),3)} | "
        f"{f(r.get('development_pf'),2)} | {pct(r.get('development_max_dd_pct'),2)} | "
        f"{int(r.get('valid_attempts',0) or 0)} |"
    )

out += [
"",
"## Search-quality diagnostics",
"",
f"- Valid backtests: **{valid:,}**",
f"- Model crashes: **{int(progress.get('total_crashes',0) or 0):,}**",
f"- Parameter-only mutations rejected: **{int(progress.get('total_parameter_only',0) or 0):,}**",
f"- Too-broad rewrites rejected: **{int(progress.get('total_too_broad',0) or 0):,}**",
f"- Risk/sizing-control changes rejected: **{int(progress.get('total_risk_control_changes',0) or 0):,}**",
f"- Semantic duplicates rejected: **{int(progress.get('total_duplicates',0) or 0):,}**",
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
