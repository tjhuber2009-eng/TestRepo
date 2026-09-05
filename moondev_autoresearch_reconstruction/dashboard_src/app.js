let DATA=null;

const $=(s)=>document.querySelector(s);
const fmt=(v,d=2)=>v===null||v===undefined||Number.isNaN(Number(v))?"—":Number(v).toFixed(d);
const pct=(v,d=1)=>v===null||v===undefined?"—":`${Number(v).toFixed(d)}%`;
const num=(v)=>new Intl.NumberFormat().format(Number(v||0));
const esc=(s)=>String(s??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[m]));
const when=(s)=>s?new Date(s).toLocaleString():"—";
const stateClass=(status,conclusion)=>{
  const x=(conclusion||status||"").toLowerCase();
  if(["success","completed","validation_pass"].includes(x)) return "good";
  if(["failure","cancelled","timed_out","validation_fail"].includes(x)) return "bad";
  if(["in_progress","queued","pending"].includes(x)) return "warn";
  return "neutral";
};

async function load(){
  const alert=$("#alertBar");
  try{
    if(window.__AUTORESEARCH_DATA__){
      DATA=window.__AUTORESEARCH_DATA__;
    }else{
      const r=await fetch("data.json",{cache:"no-store"});
      if(!r.ok) throw new Error(`HTTP ${r.status}`);
      DATA=await r.json();
    }
    render();
    alert.classList.add("hidden");
  }catch(e){
    alert.textContent=`Dashboard data could not be loaded: ${e.message}. Use dashboard.html for a self-contained local copy.`;
    alert.classList.remove("hidden");
  }
}

function render(){
  const p=DATA.progress||{};
  const d=DATA.progress_derived||{};
  $("#subtitle").textContent=`${DATA.protocol||"—"} · phase ${DATA.phase||"—"} · updated ${when(p.updated_at)}`;
  $("#generatedAt").textContent=when(DATA.generated_at);
  $("#freshness").textContent=`snapshot ${when(DATA.generated_at)}`;
  $("#phaseTitle").textContent=(DATA.phase||"unknown").toUpperCase();
  $("#validCandidates").textContent=num(p.total_valid_candidates);
  $("#breadthGoal").textContent=`of ${num(d.breadth_total_candidates)} breadth candidates`;
  $("#touchedTracks").textContent=num(d.touched_tracks);
  $("#runnableTracks").textContent=num(p.runnable_track_count);
  $("#validTracks").textContent=num(d.tracks_valid_ge_1);
  $("#terminalTracks").textContent=num(p.terminal_track_count);

  const prog=Math.max(0,Math.min(100,Number(d.breadth_pct||0)));
  $("#progressPct").textContent=`${fmt(prog,2)}%`;
  $("#progressFill").style.width=`${prog}%`;
  $("#progressRing").style.background=`conic-gradient(var(--accent) ${prog*3.6}deg,#203142 0)`;

  $("#protocolValue").textContent=DATA.protocol||"—";
  const hiddenOpen=!!DATA.safeguards?.hidden_validation_opened;
  $("#hiddenState").textContent=hiddenOpen?"OPENED":"SEALED";
  $("#hiddenDot").className=`dot ${hiddenOpen?"warn":"safe"}`;

  renderWorkflows();
  setupFilters();
  renderChampions();
  renderTournament();
  renderOutcomes();
  renderCoverage();
  renderActivity();
  renderModelPerformance();
  renderDataQuality();
  renderTracks();
}

function renderWorkflows(){
  const wrap=$("#workflowSummary");
  const continuous=(DATA.workflow?.continuous_runs||[])[0];
  const tournament=(DATA.workflow?.tournament_runs||[])[0];
  const rows=[
    ["Continuous research",continuous],
    ["Model tournament",tournament],
  ];
  wrap.innerHTML=rows.map(([label,r])=>{
    if(!r) return `<div class="workflow-row"><div><b>${label}</b><small>No run metadata</small></div><span class="pill neutral">—</span></div>`;
    const cls=stateClass(r.status,r.conclusion);
    const text=r.status==="completed"?(r.conclusion||"completed"):r.status;
    return `<div class="workflow-row">
      <div><b>${label}</b><small>#${esc(r.run_number||r.id)} · ${when(r.updated_at||r.created_at)}</small></div>
      <span class="pill ${cls}">${esc(text)}</span>
    </div>`;
  }).join("");
}

function setupFilters(){
  const markets=[...new Set((DATA.leaderboard||[]).map(r=>r.market).filter(Boolean))].sort();
  const mf=$("#marketFilter");
  const existing=[...mf.options].map(o=>o.value);
  markets.forEach(m=>{
    if(existing.includes(m)) return;
    const o=document.createElement("option");
    o.value=m;o.textContent=m;
    mf.appendChild(o);
  });
  ["profileFilter","marketFilter","eligibilityFilter","evidenceFilter","rankMetric","championSearch"].forEach(id=>$("#"+id).oninput=renderChampions);
  ["trackProfileFilter","trackSearch"].forEach(id=>$("#"+id).oninput=renderTracks);
}

function renderChampions(){
  const profile=$("#profileFilter").value;
  const market=$("#marketFilter").value;
  const eligibility=$("#eligibilityFilter").value;
  const evidence=$("#evidenceFilter").value;
  const metric=$("#rankMetric").value;
  const q=$("#championSearch").value.trim().toLowerCase();
  const gradeRank={A:1,B:2,C:3,D:4};
  let rows=[...(DATA.leaderboard||[])];
  rows=rows.filter(r=>
    (profile==="all"||r.profile===profile)&&
    (market==="all"||r.market===market)&&
    (eligibility==="all"||r.development_guard_ok!==false)&&
    (evidence==="all"||(gradeRank[r.evidence_grade]||99)<=(gradeRank[evidence]||99))&&
    (!q||`${r.family} ${r.target} ${r.track_id}`.toLowerCase().includes(q))
  );
  rows.sort((a,b)=>{
    const av=Number(a[metric]),bv=Number(b[metric]);
    const aa=Number.isFinite(av)?av:-1e99;
    const bb=Number.isFinite(bv)?bv:-1e99;
    return bb-aa;
  });
  const body=$("#championsTable tbody");
  body.innerHTML=rows.slice(0,100).map((r,i)=>{
    const score=Number(r.development_score);
    const scoreClass=Number.isFinite(score)&&score>=0?"score-pos":"score-neg";
    const psr=r.development_psr_zero==null?"—":`${(100*Number(r.development_psr_zero)).toFixed(1)}%`;
    const qv=r.multiple_test_qvalue==null?"—":`${(100*Number(r.multiple_test_qvalue)).toFixed(1)}%`;
    const pbo=r.pbo==null?"—":`${(100*Number(r.pbo)).toFixed(1)}%`;
    return `<tr>
      <td>${i+1}</td>
      <td><b>${esc(r.family)}</b><div class="muted">${esc(r.exactness||"")}</div></td>
      <td>${esc(String(r.target||"").toUpperCase())}</td>
      <td><span class="profile-chip ${esc(r.profile)}">${esc(r.profile)}</span></td>
      <td class="num ${scoreClass}">${fmt(r.development_score,6)}</td>
      <td class="num score-pos">${pct(r.development_cagr_pct,1)}</td>
      <td class="num">${pct(r.excess_cagr_vs_buyhold_pct,1)}</td>
      <td class="num">${pct(r.benchmark_cagr_pct,1)}</td>
      <td class="num">${fmt(r.development_years,1)}</td>
      <td class="num">${fmt(r.development_sharpe,3)}</td>
      <td class="num">${fmt(r.development_pf,2)}</td>
      <td class="num">${pct(r.development_max_dd_pct,2)}</td>
      <td class="num">${psr}</td>
      <td class="num">${qv}</td>
      <td class="num">${pbo}</td>
      <td><span class="status-chip">${esc(r.evidence_grade||"—")}</span></td>
      <td><span class="status-chip">${esc(r.data_quality_grade||"—")}</span></td>
      <td class="num">${fmt(r.development_trades_per_year,1)}</td>
      <td class="num">${pct(r.extreme_stress_return_pct,1)}</td>
      <td class="num">${num(r.valid_attempts)}</td>
    </tr>`;
  }).join("");
}

function renderTournament(){
  const panel=$("#tournamentPanel");
  const state=$("#tournamentState");
  const summary=DATA.tournament;
  const jobs=DATA.workflow?.tournament_jobs||[];
  const latest=(DATA.workflow?.tournament_runs||[])[0];

  if(summary?.ranking?.length){
    state.textContent="round complete";
    state.className="pill good";
    panel.innerHTML=`<div class="table-wrap tournament-table"><table>
      <thead><tr><th>#</th><th>Model</th><th class="num">Keep rate</th><th class="num">Wins</th><th class="num">Guard</th><th class="num">Paired ΔK</th><th class="num">Ideas</th></tr></thead>
      <tbody>${summary.ranking.map((r,i)=>`<tr>
        <td>${i+1}</td><td><b>${esc(r.model)}</b><div class="muted">${esc(r.provider||"")}</div></td>
        <td class="num">${r.keep_rate==null?"—":(100*Number(r.keep_rate)).toFixed(1)+"%"}</td><td class="num">${fmt(r.matched_case_wins,1)}</td>
        <td class="num">${num(r.guard_pass)}/${num(r.attempts)}</td><td class="num">${fmt(r.paired_guard_median_delta_k??r.median_delta_k,6)}</td><td class="num">${num(r.unique_proposals)}</td>
      </tr>`).join("")}</tbody></table></div>`;
    return;
  }

  if(latest){
    const cls=stateClass(latest.status,latest.conclusion);
    state.textContent=latest.status==="completed"?(latest.conclusion||"completed"):latest.status;
    state.className=`pill ${cls}`;
  }
  if(!jobs.length){
    panel.innerHTML='<div class="empty">Tournament summary not available yet. Workflow status will appear here while Round 1 runs.</div>';
    return;
  }
  panel.innerHTML=`<div class="tournament-jobs">${jobs.map(j=>{
    const cls=stateClass(j.status,j.conclusion);
    const label=j.name.replace(/^model \(/,"").replace(/\)$/,"");
    const stateText=j.status==="completed"?(j.conclusion||"completed"):j.status;
    return `<div class="tournament-job"><div><b>${esc(label)}</b><small>${esc(j.current_step||"")}</small></div><span class="pill ${cls}">${esc(stateText)}</span></div>`;
  }).join("")}</div>`;
}

function renderOutcomes(){
  const p=DATA.progress||{};
  const rows=[
    ["Valid backtests",p.total_valid_candidates],
    ["Model crashes",p.total_crashes],
    ["Parameter-only",p.total_parameter_only],
    ["Too broad",p.total_too_broad],
    ["Risk-control change",p.total_risk_control_changes],
    ["Semantic duplicate",p.total_duplicates],
  ];
  const max=Math.max(1,...rows.map(r=>Number(r[1]||0)));
  $("#outcomeBars").innerHTML=rows.map(([label,v])=>`<div class="outcome-row">
    <label>${label}</label>
    <div class="bar"><span style="width:${100*Number(v||0)/max}%"></span></div>
    <b>${num(v)}</b>
  </div>`).join("");
}

function renderCoverage(){
  const d=DATA.progress_derived||{};
  const cells=[
    ["Touched",d.touched_tracks,"tracks"],
    ["≥1 valid",d.tracks_valid_ge_1,"tracks"],
    ["≥2 valid",d.tracks_valid_ge_2,"tracks"],
    ["≥5 valid",d.tracks_valid_ge_5,"tracks"],
    ["Breadth complete",d.tracks_valid_ge_10,"tracks"],
  ];
  $("#coveragePanel").innerHTML=`<div class="coverage-grid">${cells.map(([label,v,s])=>`<div class="coverage-cell"><span>${label}</span><strong>${num(v)}</strong><small>${s}</small></div>`).join("")}</div>
  <div class="progress-track" style="margin-top:16px"><div style="width:${Math.min(100,Number(d.breadth_pct||0))}%"></div></div>
  <p>${fmt(d.breadth_pct,2)}% of breadth candidate budget completed · targets ${d.breadth_target}/${d.depth_target}/${d.elite_target} valid candidates.</p>`;
}

function renderActivity(){
  const rows=[...(DATA.recent_cycles||[])].reverse().slice(0,30);
  const wrap=$("#activityList");
  if(!rows.length){wrap.innerHTML='<div class="empty">No cycle history in this snapshot.</div>';return;}
  wrap.innerHTML=rows.map(r=>`<div class="activity">
    <time>${when(r.ts)}</time>
    <div><b>${esc(r.track_id||r.status||"cycle")}</b><span>${esc(r.status||"")}</span></div>
    <span>${r.score!==undefined&&r.score!==null?`K ${fmt(r.score,4)}`:""}</span>
  </div>`).join("");
}

function renderModelPerformance(){
  const rows=DATA.progress?.model_performance||[];
  const panel=$("#modelPerformancePanel");
  if(!rows.length){
    panel.innerHTML='<div class="empty">No protocol-v3 model attempts recorded yet.</div>';
    return;
  }
  panel.innerHTML=`<div class="table-wrap"><table>
    <thead><tr><th>Model</th><th class="num">Attempts</th><th class="num">Admission</th><th class="num">Keeper</th><th class="num">Crash</th><th class="num">Mean ΔK</th><th class="num">Ideas</th></tr></thead>
    <tbody>${rows.map(r=>`<tr>
      <td><b>${esc(r.model)}</b></td>
      <td class="num">${num(r.attempts)}</td>
      <td class="num">${pct(100*Number(r.admission_rate||0),1)}</td>
      <td class="num">${pct(100*Number(r.keeper_rate||0),1)}</td>
      <td class="num">${pct(100*Number(r.crash_rate||0),1)}</td>
      <td class="num">${fmt(r.mean_delta_k,6)}</td>
      <td class="num">${num(r.unique_ideas)}</td>
    </tr>`).join("")}</tbody>
  </table></div>`;
}

function renderDataQuality(){
  const rows=DATA.tracks||[];
  const by={};
  rows.forEach(r=>{
    const key=`${r.data_quality_grade||"—"}|${r.instrument_fidelity||"—"}`;
    by[key]=(by[key]||0)+1;
  });
  const entries=Object.entries(by).sort();
  $("#dataQualityPanel").innerHTML=entries.length
    ? `<div class="coverage-grid">${entries.map(([key,count])=>{
        const [grade,fidelity]=key.split("|");
        return `<div class="coverage-cell"><span>Grade ${esc(grade)}</span><strong>${num(count)}</strong><small>${esc(fidelity)}</small></div>`;
      }).join("")}</div>
      <p>A = checksum-verified archive; B = adjusted daily provider snapshot; C = non-contract-exact futures proxy.</p>`
    : '<div class="empty">Data-quality metadata unavailable.</div>';
}

function renderTracks(){
  const profile=$("#trackProfileFilter").value;
  const q=$("#trackSearch").value.trim().toLowerCase();
  let rows=[...(DATA.tracks||[])];
  rows=rows.filter(r=>
    (profile==="all"||r.profile===profile)&&
    (!q||`${r.track_id} ${r.family} ${r.target} ${r.profile}`.toLowerCase().includes(q))
  );
  rows.sort((a,b)=>{
    const av=Number(a.valid_attempts||0),bv=Number(b.valid_attempts||0);
    if(bv!==av)return bv-av;
    return Number(b.development_score??-1e99)-Number(a.development_score??-1e99);
  });
  $("#tracksTable tbody").innerHTML=rows.slice(0,514).map(r=>`<tr>
    <td><b>${esc(r.track_id)}</b></td>
    <td><span class="status-chip">${esc(r.status||"")}</span></td>
    <td class="num">${fmt(r.development_score,5)}</td>
    <td class="num">${pct(r.development_cagr_pct,1)}</td>
    <td class="num">${fmt(r.development_years,1)}</td>
    <td class="num">${num(r.attempts)}</td>
    <td class="num">${num(r.valid_attempts)}</td>
    <td>${r.depth_selected?"YES":"—"}</td>
    <td>${r.elite_selected?"YES":"—"}</td>
  </tr>`).join("");
}

$("#refreshBtn").addEventListener("click",load);
load();
