(async function(){
"use strict";
const el=id=>document.getElementById(id);
const esc=value=>String(value??"").replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;",'"':"&quot;","'":"&#39;"})[c]);
const labels={REVIEW:"Needs a person",SAME_ENTITY:"Same entity",DIFFERENT_ENTITY:"Different entity"};
const tags=values=>values.map(x=>`<span class="tag">${esc(x)}</span>`).join("");
const unique=a=>[...new Set(a)];
const nameOf=c=>c.screened_name || c.candidates?.[0]?.trace.customer.names?.[0]?.[0] || c.listed_name || c.hit_id;
let cases=[], filtered=[], chosen, panel="", storageKey="", actions={}, priorFocus;
const byAlert=new Map(), byId=new Map();
const announce=text=>{el("announce").textContent=text};
const action=c=>actions[c.hit_id]?.action;
const btn=(op,label,key="",cls="")=>`<button class="${cls}" data-op="${op}">${label}${key?` <kbd>${key}</kbd>`:""}</button>`;
const entry=(title,body,meta="")=>`<div class="entry"><small>${esc(meta)}</small><b>${esc(title)}</b><p>${esc(body)}</p></div>`;
function applyFilter(){
 const q=el("search").value.trim().toLowerCase(), f=el("filter").value;
 filtered=cases.filter(c=>(!f || (f==="multiple"?c.candidates.length>1:f==="pending"?!action(c):c.decision===f)) && (!q || c.search.includes(q)));
 if(!filtered.includes(chosen)) chosen=filtered[0];
 renderQueue(); renderCase();
}
function renderQueue(){
 el("total").textContent=filtered.length;
 el("queue").innerHTML=filtered.length?filtered.map(c=>`<button class="${c.decision}" data-hit="${esc(c.hit_id)}" aria-current="${chosen===c}" aria-label="${esc(nameOf(c))}, ${labels[c.decision]}, ${esc(c.hit_id)}"><span class="dot"></span><span class="qname">${esc(nameOf(c))}</span><span class="qmeta">${esc(c.hit_id)}${action(c)?` · ${esc(action(c))}`:""}</span></button>`).join(""):'<p class="empty">No matching hits.</p>';
}
function select(c,focus=false){
 if(!c)return;
 if(!filtered.includes(c)){el("filter").value="";el("search").value="";chosen=c;applyFilter()}
 const old=chosen; chosen=c;
 const rows=el("queue").children;
 if(filtered.includes(old))rows[filtered.indexOf(old)]?.setAttribute("aria-current","false");
 const row=rows[filtered.indexOf(c)];row?.setAttribute("aria-current","true");
 if(row)el("queue").scrollTop=Math.max(0,el("queue").scrollTop+Math.min(row.getBoundingClientRect().top-el("queue").getBoundingClientRect().top,0)+Math.max(row.getBoundingClientRect().bottom-el("queue").getBoundingClientRect().bottom,0));
 renderCase();if(focus)el("case").focus({preventScroll:true});
 announce(`${nameOf(c)}. ${labels[c.decision]}.`);
}
function move(delta){if(filtered.length)select(filtered[(filtered.indexOf(chosen)+delta+filtered.length)%filtered.length],true)}
function cards(c){
 return c.candidates.map((a,i)=>`<article class="card ${a.decision}"><small>Candidate ${i+1} · ${esc(a.records.join(", ")||"Screened alert")}</small><h3>${esc(unique(a.trace.customer.names.map(x=>x[0]).filter(Boolean)).join(" / ")||"Name unavailable")}</h3><b class="statusword">${labels[a.decision]}</b><p>${esc(a.reason)}</p></article>`).join("");
}
function renderCase(){
 const c=chosen;if(!c){el("case").innerHTML="<p>No case selected.</p>";return}
 const n=filtered.indexOf(c), siblings=byAlert.get(c.alert_id);
 const cleared=siblings.filter(x=>x.decision==="DIFFERENT_ENTITY"&&action(x)!=="Escalated").length;
 const status=cleared===siblings.length?"Cleared":"Open";
 const runs=c.runs||[],date=c.candidates[0]?.trace.screened_at||"Date unavailable";
 const agreement=runs.length?`${c.agreement_count} of ${runs.length} agree`:"No five-run result";
 el("case").innerHTML=`<div class="sheet"><div class="line topbar"><span>Case file</span><div class="pager"><small>${n+1} / ${filtered.length}</small>${btn("previous","Previous")}${btn("next","Next")}</div></div>
 <div class="intro ${c.decision}"><div class="overline">${esc(c.category||"Screening hit")} &nbsp; / &nbsp; ${esc(c.hit_id)}</div><p class="name">${esc(nameOf(c))}</p><h1>${labels[c.decision]}.</h1><div class="code">${esc(c.reason_code)}</div></div>
 <section class="why"><h2>Why this verdict</h2><p>${esc(c.reason)}</p>${btn("why","Inspect rule path","E","textbtn")}</section>
 ${c.candidates.length>1?`<section><h2>Possible customers</h2><div class="candidategrid">${cards(c)}</div></section>`:""}
 <div class="cards"><div class="card"><div class="overline">Alert status</div><h3>${cleared} of ${siblings.length} hits cleared</h3><small>${esc(c.alert_id)} · ${status}</small><details class="siblings"><summary>Related hits</summary>${siblings.map(x=>`<button data-related="${esc(x.hit_id)}">${esc(x.hit_id)} · ${labels[x.decision]}</button>`).join("")}</details></div>
 <div class="card"><div class="overline">Reproducibility</div><h3>${agreement}</h3><div class="runstrip">${runs.map(r=>`<span class="trial ${r.decision}" aria-label="Run ${r.run}: ${labels[r.decision]}">${r.run}</span>`).join("")}</div>${btn("runs","Inspect runs","T","textbtn")}</div></div>
 <section class="evidence"><div class="line"><h2>Decisive evidence</h2><small>Screened ${esc(date)}</small></div><div class="tags">${tags(c.evidence)}</div></section>
 <div class="controls">${btn("accept","Accept verdict","Enter","primary")}${btn("escalate","Send to review","R")}${action(c)?btn("undo","Undo action"):""}<span class="notice">${action(c)?esc(action(c))+" on this device":""}</span></div></div>`;
}
function act(value){
 if(!chosen)return;const id=chosen.hit_id;
 if(value)actions[id]={action:value,at:new Date().toISOString()};else delete actions[id];
 try{localStorage.setItem(storageKey,JSON.stringify(actions))}catch{announce("Browser storage unavailable. Action retained for this session.")}
 applyFilter();announce(`${id}: ${value||"Action removed"}`);
}
function openPanel(kind){
 if(!chosen&&kind!=="help")return;
 if(el("drawer").open&&panel===kind){el("drawer").close();return}
 panel=kind; const c=chosen;
 el("dialogTitle").textContent=({why:"Rule path",runs:"Five-run record",help:"Keyboard shortcuts"})[kind];
 let html="";
 if(kind==="help")html='<div class="keygrid">'+[["J / K","Next / previous hit"],["E","Inspect the rule path and evidence"],["T","Inspect individual runs"],["Enter","Accept the current verdict"],["R","Send the current hit to review"],["/","Search the queue"],["Esc","Close this panel"]].map(([k,v])=>`<kbd>${k}</kbd><span>${v}</span>`).join("")+'</div>';
 if(kind==="why"){
  for(const [i,a] of c.candidates.entries()){
   html+=`<h3>Candidate ${i+1} · ${esc(a.records.join(", ")||"screened alert")}</h3><p>${esc(a.reason)}</p>`;
   html+=(a.rule_path||[]).map(r=>entry(r.code,r.detail,r.status)).join("")||"<p>Rule details unavailable for this export.</p>";
   const reads=a.trace.model_reads||[];
   if(reads.length)html+="<h3>Model-read evidence</h3>"+reads.map(r=>entry(r.field,Object.values(r.facts||{}).map(x=>x.quote).join(" · ")||"No verified quote.",r.source+" · "+r.status)).join("");
  }
  const rejected=c.rejected_facts||[];
  html+="<h3>Excluded evidence</h3>"+(rejected.length?rejected.map(r=>entry(r.source,r.quote,r.reason)).join(""):"<p>No excluded web evidence recorded for this hit.</p>");
 }
 if(kind==="runs"){
  html=(c.runs||[]).map(r=>entry(`Run ${r.run} · ${labels[r.decision]}`,r.reason||"",r.reason_code)).join("")||"<p>No five-run result in this export.</p>";
  if(c.unstable){html+="<h3>Disagreement</h3><p>"+esc(c.instability_explanation)+"</p>";
   for(const r of c.runs)for(const [field,diff] of Object.entries(r.differences_from_run_1||{}))html+=entry(field,JSON.stringify(diff),`Run ${r.run} compared with run 1`)}
 }
 el("detail").innerHTML=html;
 if(!el("drawer").open){priorFocus=document.activeElement;el("drawer").showModal()}
 el("drawer").scrollTop=0;el("close").focus();
}
el("drawer").addEventListener("close",()=>{panel="";if(priorFocus?.isConnected)priorFocus.focus({preventScroll:true})});
el("close").onclick=()=>el("drawer").close();el("help").onclick=()=>openPanel("help");
el("search").oninput=applyFilter;el("filter").onchange=applyFilter;
document.addEventListener("click",e=>{
 const b=e.target.closest("button");if(!b)return;
 if(b.dataset.hit||b.dataset.related){select(byId.get(b.dataset.hit||b.dataset.related),true);return}
 const ops={previous:()=>move(-1),next:()=>move(1),accept:()=>act("Accepted"),escalate:()=>act("Escalated"),undo:()=>act(null),why:()=>openPanel("why"),runs:()=>openPanel("runs")};ops[b.dataset.op]?.();
});
document.addEventListener("keydown",e=>{
 if(e.metaKey||e.ctrlKey||e.altKey||e.repeat)return;
 if(["INPUT","SELECT","TEXTAREA"].includes(e.target.tagName)){if(e.key==="Escape"){e.target.blur();el("case").focus()}return}
 const k=e.key.toLowerCase();if(el("drawer").open){if(["e","t","?"].includes(k)){e.preventDefault();openPanel(({e:"why",t:"runs","?":"help"})[k])}return}
 const ops={j:()=>move(1),k:()=>move(-1),arrowdown:()=>move(1),arrowup:()=>move(-1),e:()=>openPanel("why"),t:()=>openPanel("runs"),r:()=>act("Escalated"),"/":()=>el("search").focus(),"?":()=>openPanel("help")};
 if(k==="enter"&&!e.target.closest("button,summary,a"))ops.enter=()=>act("Accepted");
 if(ops[k]){e.preventDefault();ops[k]()}
});
try{
 async function load(path){const r=await fetch(path);if(!r.ok)throw Error(`Could not load ${path} (${r.status})`);return (await r.text()).trim().split(/\r?\n/).filter(Boolean).map(JSON.parse)}
 const [verdicts,traces]=await Promise.all([load("/data/verdicts"),load("/data/traces")]);const index=new Map(traces.map(t=>[t.hit_id,t]));
 if(index.size!==traces.length||index.size!==verdicts.length)throw Error("Verdict and trace counts differ");
 for(const v of verdicts){
  const t=index.get(v.hit_id);
  if(byId.has(v.hit_id)||!t||!Object.hasOwn(labels,v.decision)||v.decision!==t.decision||v.reason_code!==t.code||v.reason!==t.reason||!t.candidates?.length)throw Error(`Verdict and trace disagree for ${v.hit_id}`);
  const c={...t,...v};c.search=[nameOf(c),c.listed_name,c.hit_id,c.alert_id,c.customer_record_id,...c.candidates.flatMap(a=>a.records)].join(" ").toLowerCase();
  cases.push(c);byId.set(c.hit_id,c);if(!byAlert.has(c.alert_id))byAlert.set(c.alert_id,[]);byAlert.get(c.alert_id).push(c);
 }
 const fingerprint=await crypto.subtle.digest("SHA-256",new TextEncoder().encode(JSON.stringify(verdicts)));
 storageKey="alibi-actions-"+Array.from(new Uint8Array(fingerprint),v=>v.toString(16).padStart(2,"0")).join("");
 try{actions=JSON.parse(localStorage.getItem(storageKey)||"{}")||{}}catch{}
 cases.sort((a,b)=> Number(b.decision==="REVIEW")-Number(a.decision==="REVIEW") || String(a.candidates[0].trace.screened_at).localeCompare(String(b.candidates[0].trace.screened_at)) || a.hit_id.localeCompare(b.hit_id));
 applyFilter();
}catch(err){el("case").textContent="Could not open the case file. "+err.message;el("total").textContent="—"}
})();
