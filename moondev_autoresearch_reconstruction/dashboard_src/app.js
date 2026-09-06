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
  renderDecisionSummary();
  renderV4();
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
  const v4=(DATA.workflow?.v4_runs||[])[0];
  const tournament=(DATA.workflow?.tournament_runs||[])[0];
  const rows=[
    ["Continuous research",continuous],
    ["V4 promotion",v4],
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

function renderDecisionSummary(){
  const wrap=$("#decisionCards");
  const next=$("#nextAction");
  const badge=$("#maturityBadge");
  if(!wrap||!next||!badge) return;

  const p=DATA.progress||{};
  const d=DATA.progress_derived||{};
  const priv=DATA.v4?.private||{};
  const prop=DATA.v4?.prop||{};
  const chosen=priv.portfolio?.chosen||{};
  const two=prop.programs?.ftmo_2step_2026?.refined_frontiers||{};
  const repeat=two.max_repeat_payout_efficiency||{};
  const repeatView=repeat.view||{};
  const repeatFunded=repeatView.funded||{};
  const balanced=two.balanced||{};
  const balancedView=balanced.view||{};
  const balancedFunded=balancedView.funded||{};
  const hidden=!!DATA.safeguards?.hidden_validation_opened;
  const finalOos=!!DATA.safeguards?.final_oos_opened;
  const breadth=Number(d.breadth_pct||0);

  badge.textContent=hidden||finalOos?"boundary opened":"development only";
  badge.className=`pill ${hidden||finalOos?"bad":"warn"}`;

  const repeatFamily=repeat.params?.continuous_track_id||repeat.params?.family||"—";
  const balancedFamily=balanced.params?.continuous_track_id||balanced.params?.family||"—";

  wrap.innerHTML=`
    <article class="decision-card primary">
      <span class="decision-label">PRIVATE ACCOUNT</span>
      <h3>Authoritative V4 portfolio</h3>
      <div class="decision-number">${pct(chosen.cagr_pct,2)}</div>
      <p>Development CAGR with ${pct(Math.abs(Number(chosen.max_dd_pct||0)),2)} observed max drawdown and ${pct(chosen.bootstrap_dd_q95_pct,2)} bootstrap q95 drawdown.</p>
      <small>Current concentration cap: ${pct(100*Number(priv.portfolio_authoritative_concentration_cap||0.55),0)}</small>
    </article>
    <article class="decision-card primary">
      <span class="decision-label">PROP · BEST REPEAT ECONOMICS</span>
      <h3>FTMO 2-Step · Max repeat payout</h3>
      <div class="decision-number">${pct(repeatView.repeat_expected_reward_pct,2)}</div>
      <p>Projected 12-cycle reward; ${repeatView.combined_evaluation_pass_probability==null?"—":pct(100*Number(repeatView.combined_evaluation_pass_probability),1)} evaluation pass and ${repeatFunded.survival_probability==null?"—":pct(100*Number(repeatFunded.survival_probability),1)} funded survival.</p>
      <small>${esc(repeatFamily)} · development simulation</small>
    </article>
    <article class="decision-card">
      <span class="decision-label">PROP · BALANCED</span>
      <h3>Exact transferred signal</h3>
      <div class="decision-number">${balancedView.combined_evaluation_pass_probability==null?"—":pct(100*Number(balancedView.combined_evaluation_pass_probability),1)}</div>
      <p>Evaluation-pass estimate with ${balancedFunded.survival_probability==null?"—":pct(100*Number(balancedFunded.survival_probability),1)} funded survival and ${pct(balancedView.repeat_expected_reward_pct,2)} projected 12-cycle reward.</p>
      <small>${esc(balancedFamily)}</small>
    </article>
    <article class="decision-card">
      <span class="decision-label">RESEARCH MATURITY</span>
      <h3>Still in breadth search</h3>
      <div class="decision-number">${fmt(breadth,1)}%</div>
      <p>${num(p.total_valid_candidates)} valid candidates out of ${num(d.breadth_total_candidates)} breadth slots. Hidden validation and final OOS remain sealed.</p>
      <small>PBO appears only after enough unique variants exist.</small>
    </article>`;

  next.innerHTML=`
    <strong>Next decision:</strong>
    compare every exact prop adapter in V4, keep only genuine frontier winners, and continue breadth research until the candidate evidence is mature enough for valid PBO/CSCV. <b>Do not open hidden validation yet.</b>
  `;
}

function renderV4(){
  const state=$("#v4State");
  const wrap=$("#v4Content");
  if(!state||!wrap) return;
  const v4=DATA.v4||{};
  const priv=v4.private||{};
  const prop=v4.prop||{};
  if(!v4.available){
    state.textContent="no state";
    state.className="pill neutral";
    wrap.innerHTML='<div class="empty">V4 development state is not present in this dashboard snapshot.</div>';
    return;
  }
  const hidden=!!priv.hidden_validation_opened||!!prop.hidden_validation_opened;
  const finalOos=!!priv.final_oos_opened||!!prop.final_oos_opened;
  state.textContent=hidden||finalOos?"boundary opened":"development sealed";
  state.className=`pill ${hidden||finalOos?"warn":"good"}`;

  const chosen=priv.portfolio?.chosen||null;
  const cap=Number(priv.portfolio_authoritative_concentration_cap||0.55);
  const weights=chosen?.weights||{};
  const weightRows=Object.entries(weights).sort((a,b)=>Number(b[1])-Number(a[1])).map(([name,w])=>`
    <tr><td><b>${esc(name)}</b></td><td class="num">${fmt(100*Number(w),2)}%</td></tr>`).join("");
  const concentration=priv.portfolio_concentration_sensitivity||{};
  const concentrationRows=Object.entries(concentration).sort((a,b)=>Number(a[0])-Number(b[0])).map(([k,row])=>{
    const x=row?.chosen||row||{};
    const auth=Math.abs(Number(k)-cap)<1e-9;
    return `<tr>
      <td><b>${fmt(100*Number(k),0)}%</b> ${auth?'<span class="pill good">AUTHORITATIVE</span>':""}</td>
      <td class="num">${pct(x.cagr_pct,2)}</td>
      <td class="num">${pct(x.bootstrap_median_cagr_pct,2)}</td>
      <td class="num">${pct(x.bootstrap_dd_q95_pct,2)}</td>
      <td class="num">${pct(x.max_dd_pct,2)}</td>
      <td class="num">${fmt(x.sharpe,3)}</td>
      <td class="num">${x.gross_exposure==null?"—":pct(100*Number(x.gross_exposure),1)}</td>
      <td class="num">${x.cash_weight==null?"—":pct(100*Number(x.cash_weight),1)}</td>
    </tr>`;
  }).join("");

  const privateTransfer=(priv.continuous_private_transfer?.candidates||[]).map(r=>{
    const cand=r.candidate||r;
    const gate=r.portfolio_gate_reason||r.transfer_status||"—";
    return `<tr>
      <td><b>${esc(cand.track_id||"—")}</b><div class="muted">${esc(cand.family||"")}</div></td>
      <td class="num">${pct(r.base?.cagr_pct,2)}</td>
      <td class="num">${pct(r.cost_stress?.cagr_pct,2)}</td>
      <td class="num">${pct(r.base?.max_dd_pct,2)}</td>
      <td class="num">${fmt(r.base?.sharpe,3)}</td>
      <td class="num">${cand.pbo==null?"—":pct(100*Number(cand.pbo),1)}</td>
      <td><span class="pill ${r.portfolio_eligible?"good":"warn"}">${r.portfolio_eligible?"eligible":"blocked"}</span><div class="muted">${esc(gate)}</div></td>
    </tr>`;
  }).join("");

  const viewOrder=["max_payout_efficiency","max_repeat_payout_efficiency","max_evaluation_pass","safest_funded","balanced","conservative"];
  const viewLabel={
    max_payout_efficiency:"Max first payout",
    max_repeat_payout_efficiency:"Max repeat payout",
    max_evaluation_pass:"Max evaluation pass",
    safest_funded:"Safest funded",
    balanced:"Balanced",
    conservative:"Conservative",
  };
  const isNonAuthoritative=(params)=>{
    if(!params) return false;
    return params.transfer_exactness==="signal_only_proxy"||params.source_stop_transferred===false;
  };
  const programBlock=(id,label)=>{
    const p=prop.programs?.[id]||{};
    const front=p.refined_frontiers||{};
    const rows=viewOrder.map(view=>{
      const r=front[view]||{};
      const params=r.params||{};
      const x=r.view||{};
      const funded=x.funded||{};
      const nonauth=isNonAuthoritative(params);
      const family=params.family||"—";
      const source=params.continuous_track_id||"";
      const exposures=[x.challenge_exposure_scale,x.verification_exposure_scale,x.funded_exposure_scale]
        .map(v=>v==null?"—":fmt(v,2)).join(" / ");
      return `<tr>
        <td><b>${esc(viewLabel[view])}</b></td>
        <td><b>${esc(family)}</b>${source?`<div class="muted">${esc(source)}</div>`:""}${nonauth?'<span class="pill bad">NON-AUTH PROXY</span>':""}</td>
        <td class="num">${exposures}</td>
        <td class="num">${x.combined_evaluation_pass_probability==null?"—":pct(100*Number(x.combined_evaluation_pass_probability),1)}</td>
        <td class="num">${fmt(x.expected_evaluation_days_if_passed,1)}</td>
        <td class="num">${fmt(x.payout_efficiency_score,6)}</td>
        <td class="num">${fmt(x.repeat_payout_efficiency_score,6)}</td>
        <td class="num">${pct(x.repeat_expected_reward_pct,2)}</td>
        <td class="num">${funded.survival_probability==null?"—":pct(100*Number(funded.survival_probability),1)}</td>
        <td class="num">${funded.daily_loss_breach_probability==null?"—":pct(100*Number(funded.daily_loss_breach_probability),1)}</td>
        <td class="num">${funded.max_loss_breach_probability==null?"—":pct(100*Number(funded.max_loss_breach_probability),1)}</td>
      </tr>`;
    }).join("");
    const horizons=p.horizon_sensitivity||{};
    const hrows=viewOrder.map(view=>{
      const h=horizons[view]?.horizons||{};
      const cell=(n)=>{
        const x=h[String(n)]?.view||{};
        const funded=x.funded||{};
        return `<td>
          <div><b>Pass</b> ${x.combined_evaluation_pass_probability==null?"—":pct(100*Number(x.combined_evaluation_pass_probability),1)}</div>
          <div><b>Repeat</b> ${fmt(x.repeat_payout_efficiency_score,5)}</div>
          <div><b>Reward</b> ${pct(x.repeat_expected_reward_pct,1)}</div>
          <div><b>Survival</b> ${funded.survival_probability==null?"—":pct(100*Number(funded.survival_probability),1)}</div>
        </td>`;
      };
      return `<tr><td><b>${esc(viewLabel[view])}</b></td>${cell(252)}${cell(365)}${cell(504)}</tr>`;
    }).join("");
    return `<details class="detail-panel">
      <summary><span><b>${esc(label)}</b><small>All six frontiers + horizon sensitivity</small></span><span class="detail-hint">Show details</span></summary>
      <article class="glass-card detail-body">
      <div class="table-wrap"><table>
        <thead><tr><th>Frontier</th><th>Family / source</th><th class="num">C / V / F</th><th class="num">Pass</th><th class="num">Eval days</th><th class="num">First eff.</th><th class="num">Repeat eff.</th><th class="num">12-cycle reward</th><th class="num">Survival</th><th class="num">Daily breach</th><th class="num">Max breach</th></tr></thead>
        <tbody>${rows}</tbody>
      </table></div>
      <h4 style="margin:18px 0 8px">252 / 365 / 504-day sensitivity</h4>
      <div class="table-wrap"><table>
        <thead><tr><th>Frontier</th><th>252 days</th><th>365 days</th><th>504 days</th></tr></thead>
        <tbody>${hrows}</tbody>
      </table></div>
      </article>
    </details>`;
  };

  const adapterRows=(prop.continuous_prop_transfer?.candidates||[]).map(c=>{
    const p=c.transfer_params||{};
    const nonauth=isNonAuthoritative(p);
    const status=c.transfer_status==="adapter_required"?"adapter_required":nonauth?"non-authoritative proxy":c.transfer_status||"—";
    const cls=status==="supported"?"good":status==="adapter_required"?"warn":"bad";
    return `<tr>
      <td><b>${esc(c.track_id||"—")}</b></td><td>${esc(c.family||"—")}</td>
      <td>${esc(c.adapter||"—")}</td><td>${esc(p.transfer_exactness||c.exactness||"—")}</td>
      <td>${p.source_stop_transferred===undefined?"—":esc(p.source_stop_transferred)}</td>
      <td><span class="pill ${cls}">${esc(status)}</span></td>
    </tr>`;
  }).join("");

  const privateHtml=chosen?`<div class="kpi-grid">
      <article class="kpi-card accent-kpi"><span>Private CAGR</span><strong>${pct(chosen.cagr_pct,2)}</strong><small>55% authoritative cap</small></article>
      <article class="kpi-card"><span>Bootstrap median CAGR</span><strong>${pct(chosen.bootstrap_median_cagr_pct,2)}</strong><small>paired block bootstrap</small></article>
      <article class="kpi-card"><span>Observed max DD</span><strong>${pct(chosen.max_dd_pct,2)}</strong><small>cap 32%</small></article>
      <article class="kpi-card"><span>Bootstrap q95 DD</span><strong>${pct(chosen.bootstrap_dd_q95_pct,2)}</strong><small>stress drawdown</small></article>
      <article class="kpi-card"><span>Sharpe</span><strong>${fmt(chosen.sharpe,3)}</strong><small>development only</small></article>
      <article class="kpi-card"><span>Gross / cash</span><strong>${pct(100*Number(chosen.gross_exposure||0),1)} / ${pct(100*Number(chosen.cash_weight||0),1)}</strong><small>portfolio exposure</small></article>
    </div>
    <div class="dashboard-grid" style="margin-top:16px">
      <article class="glass-card"><div class="card-title-row"><div><span class="kicker">PRIVATE ACCOUNT</span><h3>Authoritative weights</h3></div></div>
        <div class="table-wrap"><table><thead><tr><th>Strategy</th><th class="num">Weight</th></tr></thead><tbody>${weightRows}</tbody></table></div>
      </article>
      <article class="glass-card"><div class="card-title-row"><div><span class="kicker">ROBUSTNESS</span><h3>Concentration sensitivity</h3></div></div>
        <div class="table-wrap"><table><thead><tr><th>Cap</th><th class="num">CAGR</th><th class="num">Boot CAGR</th><th class="num">q95 DD</th><th class="num">DD</th><th class="num">Sharpe</th><th class="num">Gross</th><th class="num">Cash</th></tr></thead><tbody>${concentrationRows}</tbody></table></div>
      </article>
    </div>`:'<div class="empty">Private V4 portfolio state unavailable.</div>';

  const repeat1=prop.programs?.ftmo_1step_2026?.refined_frontiers?.max_repeat_payout_efficiency||{};
  const repeat2=prop.programs?.ftmo_2step_2026?.refined_frontiers?.max_repeat_payout_efficiency||{};
  const balanced2=prop.programs?.ftmo_2step_2026?.refined_frontiers?.balanced||{};
  const summaryCard=(label,row,tag)=>{
    const x=row.view||{};
    const funded=x.funded||{};
    const source=row.params?.continuous_track_id||row.params?.family||"—";
    return `<article class="frontier-card">
      <span class="decision-label">${esc(tag)}</span>
      <h3>${esc(label)}</h3>
      <div class="frontier-metrics">
        <div><span>Pass</span><strong>${x.combined_evaluation_pass_probability==null?"—":pct(100*Number(x.combined_evaluation_pass_probability),1)}</strong></div>
        <div><span>12-cycle reward</span><strong>${pct(x.repeat_expected_reward_pct,2)}</strong></div>
        <div><span>Survival</span><strong>${funded.survival_probability==null?"—":pct(100*Number(funded.survival_probability),1)}</strong></div>
      </div>
      <small>${esc(source)}</small>
    </article>`;
  };

  wrap.innerHTML=`
    <div class="v4-explainer">
      <b>How to read this section:</b> “Pass” is the simulated probability of completing the evaluation, “12-cycle reward” is the repeated-payout research projection, and “survival” is the simulated funded-account survival rate. These are development estimates, not live proof.
    </div>
    <div class="frontier-grid">
      ${summaryCard("FTMO 2-Step","" && repeat2,"Best repeat economics")}
    </div>
  `;

  // Avoid nested-template coercion by append via insertAdjacentHTML.
  wrap.innerHTML=`
    <div class="v4-explainer">
      <b>How to read this section:</b> “Pass” = simulated evaluation completion; “12-cycle reward” = repeated-payout projection; “Survival” = simulated funded-account survival. Development estimates only.
    </div>
    <div class="frontier-grid">
      ${summaryCard("FTMO 2-Step · Max repeat payout",repeat2,"BEST REPEAT ECONOMICS")}
      ${summaryCard("FTMO 1-Step · Max repeat payout",repeat1,"SIMPLER PROGRAM")}
      ${summaryCard("FTMO 2-Step · Balanced",balanced2,"BALANCED / EXACT TRANSFER")}
    </div>
    ${privateHtml}
    <details class="detail-panel">
      <summary><span><b>Private promotion queue</b><small>Why high-return continuous candidates are still blocked</small></span><span class="detail-hint">Show details</span></summary>
      <article class="glass-card detail-body">
        <div class="table-wrap"><table><thead><tr><th>Track</th><th class="num">V4 CAGR</th><th class="num">3× cost CAGR</th><th class="num">DD</th><th class="num">Sharpe</th><th class="num">PBO</th><th>Gate</th></tr></thead><tbody>${privateTransfer||'<tr><td colspan="7">No candidates</td></tr>'}</tbody></table></div>
      </article>
    </details>
    ${programBlock("ftmo_1step_2026","FTMO 1-Step")}
    ${programBlock("ftmo_2step_2026","FTMO 2-Step")}
    <details class="detail-panel">
      <summary><span><b>Continuous → prop adapter audit</b><small>Exact adapters, blocked proxies, and transfer status</small></span><span class="detail-hint">Show details</span></summary>
      <article class="glass-card detail-body">
        <div class="table-wrap"><table><thead><tr><th>Track</th><th>Family</th><th>Adapter</th><th>Exactness</th><th>Source stop</th><th>Status</th></tr></thead><tbody>${adapterRows||'<tr><td colspan="6">No transferred candidates</td></tr>'}</tbody></table></div>
      </article>
    </details>`;

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
