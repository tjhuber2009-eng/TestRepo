let DATA=null;

const $=(s)=>document.querySelector(s);
const fmt=(v,d=2)=>v===null||v===undefined||Number.isNaN(Number(v))?"—":Number(v).toFixed(d);
const pct=(v,d=1)=>v===null||v===undefined||Number.isNaN(Number(v))?"—":`${Number(v).toFixed(d)}%`;
const num=(v)=>new Intl.NumberFormat().format(Number(v||0));
const esc=(s)=>String(s??"").replace(/[&<>"']/g,m=>({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;","'":"&#39;"}[m]));
const when=(s)=>s?new Date(s).toLocaleString():"—";
const finite=(v)=>{const n=Number(v);return Number.isFinite(n)?n:null;};
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
  const protocol=DATA.protocol||"—";
  const phase=DATA.phase||"unknown";
  const hiddenOpen=!!DATA.safeguards?.hidden_validation_opened;

  $("#subtitle").textContent=`${protocol} · phase ${phase} · updated ${when(p.updated_at)}`;
  $("#generatedAt").textContent=when(DATA.generated_at);
  $("#freshness").textContent=`snapshot ${when(DATA.generated_at)}`;
  $("#phaseTitle").textContent=String(phase).toUpperCase();
  $("#protocolTop").textContent=protocol;
  $("#phaseTop").textContent=String(phase).toUpperCase();
  $("#hiddenTop").textContent=hiddenOpen?"OPENED":"SEALED";
  $("#hiddenTop").className=hiddenOpen?"text-warn":"text-good";

  $("#validCandidates").textContent=num(p.total_valid_candidates);
  $("#breadthGoal").textContent=`of ${num(d.breadth_total_candidates)} breadth candidates`;
  $("#touchedTracks").textContent=num(d.touched_tracks);
  $("#runnableTracks").textContent=num(p.runnable_track_count);
  $("#validTracks").textContent=num(d.tracks_valid_ge_1);
  $("#terminalTracks").textContent=num(p.terminal_track_count);
  $("#keepersKpi").textContent=num(p.total_kept_candidates);

  const runnable=Math.max(0,Number(p.runnable_track_count||0));
  const touched=Math.max(0,Number(d.touched_tracks||0));
  const validTracks=Math.max(0,Number(d.tracks_valid_ge_1||0));
  $("#touchedShare").textContent=runnable?`${fmt(100*touched/runnable,1)}% of universe`:"0% of universe";
  $("#validShare").textContent=runnable?`${fmt(100*validTracks/runnable,1)}% candidate coverage`:"candidate coverage";

  const valid=Math.max(0,Number(p.total_valid_candidates||0));
  const passed=Math.max(0,Number(p.total_guard_passed_candidates||0));
  $("#guardRateKpi").textContent=valid?`${fmt(100*passed/valid,1)}%`:"—";

  const prog=Math.max(0,Math.min(100,Number(d.breadth_pct||0)));
  $("#progressPct").textContent=`${fmt(prog,2)}%`;
  $("#progressRing").style.background=`conic-gradient(var(--cyan) ${prog*3.6}deg,#203142 0)`;

  $("#protocolValue").textContent=protocol;
  $("#hiddenState").textContent=hiddenOpen?"OPENED":"SEALED";
  $("#hiddenDot").className=`dot ${hiddenOpen?"warn":"safe"}`;

  renderPhaseRail(phase,hiddenOpen);
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

  if(DATA.stale_state){
    const alert=$("#alertBar");
    alert.textContent=`Historical ${DATA.stale_state.progress_protocol||"prior-protocol"} state detected and intentionally hidden from the active v3 leaderboard.`;
    alert.classList.remove("hidden");
  }
}

function renderPhaseRail(phase,hiddenOpen){
  const order=["breadth","depth","elite","validation","oos"];
  const normalized=String(phase||"breadth").toLowerCase();
  let current=order.indexOf(normalized);
  if(current<0){
    if(normalized.includes("valid")) current=3;
    else if(normalized.includes("elite")) current=2;
    else if(normalized.includes("depth")) current=1;
    else current=0;
  }
  document.querySelectorAll(".phase-step").forEach((el)=>{
    const p=el.dataset.phase;
    const idx=order.indexOf(p);
    el.classList.remove("active","done");
    if(idx<current) el.classList.add("done");
    if(idx===current) el.classList.add("active");
    if(p==="validation"&&!hiddenOpen&&idx>=current) el.classList.add("sealed");
    if(p==="oos") el.classList.add("sealed");
  });
  const descriptions={
    breadth:"Wide search across every runnable family, target, and risk profile.",
    depth:"Top breadth survivors receive a larger adaptive research budget.",
    elite:"Only the strongest depth survivors receive the final adaptive budget.",
    validation:"Adaptive search is frozen. Elite champions receive one hidden pre-OOS check.",
    oos:"Final one-look 2023+ out-of-sample evaluation."
  };
  $("#phaseDescription").textContent=descriptions[order[current]]||descriptions.breadth;
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
    const linked=r.html_url?`<a href="${esc(r.html_url)}" target="_blank" rel="noopener" style="color:inherit;text-decoration:none">${esc(label)}</a>`:esc(label);
    return `<div class="workflow-row">
      <div><b>${linked}</b><small>#${esc(r.run_number||r.id)} · ${when(r.updated_at||r.created_at)}</small></div>
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
  ["profileFilter","marketFilter","eligibilityFilter","evidenceFilter","rankMetric","championSearch"].forEach(id=>{
    $("#"+id).oninput=renderChampions;
  });
  ["trackProfileFilter","trackSearch"].forEach(id=>$("#"+id).oninput=renderTracks);
}

function filteredChampions(){
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
    const av=finite(a[metric]),bv=finite(b[metric]);
    return (bv??-1e99)-(av??-1e99);
  });
  return rows;
}

function renderChampions(){
  const rows=filteredChampions();
  renderPodium(rows);
  renderScatter(rows);

  const body=$("#championsTable tbody");
  body.innerHTML=rows.slice(0,100).map((r,i)=>{
    const score=finite(r.development_score);
    const scoreClass=score!==null&&score>=0?"score-pos":"score-neg";
    const psr=r.development_psr_zero==null?"—":`${(100*Number(r.development_psr_zero)).toFixed(1)}%`;
    const qv=r.multiple_test_qvalue==null?"—":`${(100*Number(r.multiple_test_qvalue)).toFixed(1)}%`;
    const pbo=r.pbo==null?"—":`${(100*Number(r.pbo)).toFixed(1)}%`;
    const eg=String(r.evidence_grade||"—").toLowerCase();
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
      <td><span class="status-chip grade-${eg}">${esc(r.evidence_grade||"—")}</span></td>
      <td><span class="status-chip">${esc(r.data_quality_grade||"—")}</span></td>
      <td class="num">${fmt(r.development_trades_per_year,1)}</td>
      <td class="num">${pct(r.extreme_stress_return_pct,1)}</td>
      <td class="num">${num(r.valid_attempts)}</td>
    </tr>`;
  }).join("");
}

function renderPodium(rows){
  const top=rows.slice(0,3);
  const wrap=$("#podium");
  if(!top.length){
    wrap.innerHTML='<div class="empty" style="grid-column:1/-1">No protocol-v3 champions yet.</div>';
    return;
  }
  wrap.innerHTML=top.map((r,i)=>`<article class="podium-card">
    <div class="podium-rank">#${i+1} DEVELOPMENT CHAMPION</div>
    <div class="podium-name">${esc(r.family||r.track_id||"—")}</div>
    <div class="podium-meta">${esc(String(r.target||"").toUpperCase())} · ${esc(r.profile||"—")} · evidence ${esc(r.evidence_grade||"—")}</div>
    <div class="podium-metrics">
      <div><span>Robust K</span><strong>${fmt(r.development_score,5)}</strong></div>
      <div><span>CAGR</span><strong class="metric-good">${pct(r.development_cagr_pct,1)}</strong></div>
      <div><span>Max DD</span><strong>${pct(r.development_max_dd_pct,1)}</strong></div>
      <div><span>Sharpe</span><strong>${fmt(r.development_sharpe,2)}</strong></div>
    </div>
  </article>`).join("");
}

function renderScatter(rows){
  const wrap=$("#scatterPlot");
  const pts=rows.slice(0,35).map((r,i)=>({
    r,
    x:Math.abs(finite(r.development_max_dd_pct)??0),
    y:finite(r.development_cagr_pct),
    k:Math.abs(finite(r.development_score)??0),
    idx:i
  })).filter(p=>p.y!==null&&Number.isFinite(p.x));

  if(!pts.length){
    wrap.innerHTML='<div class="empty">Risk/return map will appear when v3 champions are available.</div>';
    return;
  }

  const W=760,H=260,ml=46,mr=18,mt=14,mb=32;
  const xmax=Math.max(5,...pts.map(p=>p.x))*1.08;
  let ymin=Math.min(0,...pts.map(p=>p.y));
  let ymax=Math.max(1,...pts.map(p=>p.y));
  if(ymax===ymin) ymax=ymin+1;
  const pad=(ymax-ymin)*.08;
  ymin-=pad;ymax+=pad;
  const sx=x=>ml+(W-ml-mr)*(x/xmax);
  const sy=y=>mt+(H-mt-mb)*(1-(y-ymin)/(ymax-ymin));
  const maxK=Math.max(.000001,...pts.map(p=>p.k));

  const xTicks=[0,.25,.5,.75,1].map(t=>{
    const v=xmax*t;
    const x=sx(v);
    return `<line class="grid-line" x1="${x}" y1="${mt}" x2="${x}" y2="${H-mb}"/><text class="axis-label" x="${x}" y="${H-10}" text-anchor="middle">${v.toFixed(1)}%</text>`;
  }).join("");
  const yTicks=[0,.25,.5,.75,1].map(t=>{
    const v=ymin+(ymax-ymin)*t;
    const y=sy(v);
    return `<line class="grid-line" x1="${ml}" y1="${y}" x2="${W-mr}" y2="${y}"/><text class="axis-label" x="${ml-7}" y="${y+3}" text-anchor="end">${v.toFixed(0)}%</text>`;
  }).join("");
  const dots=pts.map((p,i)=>{
    const rad=4+7*Math.sqrt(p.k/maxK);
    const name=esc(p.r.family||p.r.track_id||"");
    const cls=p.r.profile==="private"?"scatter-dot private":"scatter-dot";
    const label=i<6?`<text class="scatter-label" x="${sx(p.x)+rad+4}" y="${sy(p.y)+3}">${name.slice(0,18)}</text>`:"";
    return `<g><circle class="${cls}" cx="${sx(p.x)}" cy="${sy(p.y)}" r="${rad}"><title>${name} · CAGR ${pct(p.y,1)} · DD ${pct(-p.x,1)} · K ${fmt(p.r.development_score,5)}</title></circle>${label}</g>`;
  }).join("");

  wrap.innerHTML=`<svg viewBox="0 0 ${W} ${H}" role="img" aria-label="Champion CAGR versus drawdown scatter plot">
    ${xTicks}${yTicks}
    <line class="axis-line" x1="${ml}" y1="${H-mb}" x2="${W-mr}" y2="${H-mb}"/>
    <line class="axis-line" x1="${ml}" y1="${mt}" x2="${ml}" y2="${H-mb}"/>
    ${dots}
    <text class="axis-label" x="${(ml+W-mr)/2}" y="${H-1}" text-anchor="middle">Absolute max drawdown</text>
    <text class="axis-label" transform="translate(10,${H/2}) rotate(-90)" text-anchor="middle">Development CAGR</text>
  </svg>`;
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
        <td class="num">${r.keep_rate==null?"—":(100*Number(r.keep_rate)).toFixed(1)+"%"}</td>
        <td class="num">${fmt(r.matched_case_wins,1)}</td>
        <td class="num">${num(r.guard_pass)}/${num(r.attempts)}</td>
        <td class="num">${fmt(r.paired_guard_median_delta_k??r.median_delta_k,6)}</td>
        <td class="num">${num(r.unique_proposals)}</td>
      </tr>`).join("")}</tbody></table></div>`;
    return;
  }

  if(latest){
    const cls=stateClass(latest.status,latest.conclusion);
    state.textContent=latest.status==="completed"?(latest.conclusion||"completed"):latest.status;
    state.className=`pill ${cls}`;
  }
  if(!jobs.length){
    panel.innerHTML='<div class="empty">Tournament summary not available yet. Live contestant status will appear here while the round runs.</div>';
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
    ["Backtested",p.total_valid_candidates],
    ["Guard-passing",p.total_guard_passed_candidates],
    ["Keepers",p.total_kept_candidates],
    ["Model crashes",p.total_crashes],
    ["Parameter-only",p.total_parameter_only],
    ["Too broad",p.total_too_broad],
    ["Risk-control",p.total_risk_control_changes],
    ["Duplicates",p.total_duplicates],
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
  <div class="progress-track"><div style="width:${Math.min(100,Number(d.breadth_pct||0))}%"></div></div>
  <p>${fmt(d.breadth_pct,2)}% of breadth budget completed · successive-halving targets ${d.breadth_target}/${d.depth_target}/${d.elite_target} valid candidates.</p>`;
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
  if(!entries.length){
    $("#dataQualityPanel").innerHTML='<div class="empty">Data-quality metadata unavailable.</div>';
    return;
  }
  $("#dataQualityPanel").innerHTML=`<div class="data-quality-cards">${entries.map(([key,count])=>{
    const [grade,fidelity]=key.split("|");
    return `<article class="data-quality-card">
      <div class="grade">Grade ${esc(grade)}</div>
      <div class="count">${num(count)} tracks</div>
      <div class="fidelity">${esc(fidelity)}</div>
    </article>`;
  }).join("")}</div>
  <p style="margin-top:10px">A = checksum-verified archive · B = adjusted daily provider snapshot · C = non-contract-exact futures proxy.</p>`;
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
  $("#tracksTable tbody").innerHTML=rows.slice(0,600).map(r=>`<tr>
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
