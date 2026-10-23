/* The Checks tab of My CV: how job-site scanners read the CV, with fixed published
   weights; the checks of the CV type, each failing one linked to its suggestion; a recruiter's read, which
   is the AI's opinion and part of no score; and the CV against one job, where "Does not matter" leaves a
   word out of that job's match. */
import { html, cx, useState } from "../core/html.js";
import { post, run } from "../core/api.js";
import { useApi, toast } from "../core/store.js";
import { go } from "../core/router.js";
import { Empty } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";

const PCT = 100;

function Scanner({ s, onFix }) {
  const fixes = s.parts.filter(p => p.fix_id);
  return html`<section class="card"><div class="row"><div class="ring" style=${"--v:" + s.score}><b>${s.score}</b></div>
      <div style="flex:1;min-width:0"><h2 class="h2" style="margin:0 0 4px">How job-site scanners read it</h2>
      <p class="small sub" style="margin:0">Fixed checks with published weights. The same CV always gets the same score. No AI is used for this number.</p></div></div>
    <div style="margin-top:14px">${s.parts.map(p => html`<div style="margin:10px 0" key=${p.id}><div class="row small"><span>${p.name}</span><span class="sp"></span>
      <span class="mono">${p.got} of ${p.max}</span></div>
      <div class=${cx("bar", p.got < p.max && "warn")} role="img" aria-label=${p.name + ": " + p.got + " of " + p.max}><i style=${"width:" + (p.got / p.max * PCT) + "%"}></i></div>
      ${p.got < p.max && !p.fix_id && html`<p class="small sub" style="margin:4px 0 0">${p.note}</p>`}</div>`)}</div>
    ${fixes.map(p => html`<p class="small sub" key=${p.id}>"${p.name}": ${p.note} <button class="btn quiet small" onClick=${() => onFix(p)}>Fix it</button></p>`)}</section>`;
}

function Recruiter({ r, typeName, onAsk, busy }) {
  return html`<section class="card"><h2 class="h2">A recruiter's read</h2>
    <p class="small sub">Hal's opinion as a recruiter for your CV type, ${typeName}. It is not part of any score.</p>
    ${r ? html`<p>"${r.text}"</p>` : html`<p class="small">Hal has not read this version as a recruiter yet.</p>`}
    <div class="row">${r && r.ai_words && html`<span class="chip">${r.ai_words}</span>`}
      <button class="btn quiet small" disabled=${busy} onClick=${onAsk}>${busy ? "Reading your CV" : r ? "Ask again" : "Ask for a recruiter's read"}</button></div></section>`;
}

function TypeChecks({ t }) {
  return html`<section class="card"><h2 class="h2">Checks for your CV type</h2><p class="small sub">${t.name}.</p>
    ${t.items.map(i => html`<div class="li" key=${i.id} style="padding:9px 2px;align-items:center">
      <span class=${cx("flag", i.ok ? "ok" : "check")} role="img" aria-label=${i.ok ? "Passes" : "Needs a look"}><${Icon} n=${i.ok ? "check" : "warn"} c="s" /></span>
      <div class="grow small">${i.name}${!i.ok && i.note && html`<div class="sub">${i.note}</div>`}</div>
      ${!i.ok && i.suggestion_id && html`<button class="btn quiet small" onClick=${() => go("cv-sugg")}>See suggestion</button>`}</div>`)}</section>`;
}

function Job({ jobs, job, value, onPick, onOff }) {
  if (!jobs.length) {
    return html`<section class="card"><h2 class="h2">Against a job</h2><p class="small sub" style="margin:0">Save a job to see what your CV has evidence for.</p></section>`;
  }
  return html`<section class="card"><h2 class="h2">Against a job</h2>
    <select class="input" aria-label="Job" value=${value} onChange=${e => onPick(e.target.value)}>
      <option value="">Choose a job</option>${jobs.map(j => html`<option value=${j.id}>${j.title}, ${j.company}</option>`)}</select>
    ${job && html`<p class="small" style="margin:12px 0 6px"><b>You have evidence for</b></p>
      <div class="chips">${job.have.length ? job.have.map(k => html`<span class="chip good">${k}</span>`) : html`<span class="small sub">Nothing from this advert yet.</span>`}</div>
      <p class="small" style="margin:12px 0 6px"><b>No evidence yet</b></p>
      ${job.missing.length ? job.missing.map(m => html`<div class="li" style="padding:8px 2px;align-items:center"><span class="chip warn">${m.k}</span>
        <span class="grow small sub">${m.note}</span><button class="btn quiet small" onClick=${() => onOff(m.k)}>Does not matter</button></div>`)
        : html`<p class="small sub" style="margin:0">Your CV shows everything Hal found in this advert.</p>`}`}</section>`;
}

/** The Checks tab. */
export function ChecksTab() {
  const [jobId, setJobId] = useState("");
  const [busy, setBusy] = useState(false);
  const { data, error, reload } = useApi("/cv/checks" + (jobId ? "?job_id=" + encodeURIComponent(jobId) : ""));
  if (error) return html`<${Empty}>${error.message}<//>`;
  if (!data) return html`<p class="sub">Checking your CV...</p>`;
  const fix = async p => {
    try { await post("/cv/checks/fix/" + encodeURIComponent(p.fix_id)); toast("Fixed in a new version. Your earlier version is kept."); reload(); }
    catch (err) { toast(err.message); }
  };
  const ask = async () => {
    setBusy(true);
    try {
      const done = await run("/cv/checks/recruiter", {});
      const out = (done && done.result) || {};
      if (out.ok === false) toast(out.why || "Hal could not read your CV as a recruiter just now.");
      reload();
    } catch (err) { toast(err.message); } finally { setBusy(false); }
  };
  const off = async term => {
    try { await post("/cv/keywords/off", { job_id: jobId, term }); toast("Hal will leave this out of the match"); reload(); }
    catch (err) { toast(err.message); }
  };
  return html`<div class="grid2 even">
    <div class="stack"><${Scanner} s=${data.scanner} onFix=${fix} /><${Recruiter} r=${data.recruiter} typeName=${data.type.name} onAsk=${ask} busy=${busy} /></div>
    <div class="stack"><${TypeChecks} t=${data.type} /><${Job} jobs=${data.jobs} job=${data.job} value=${jobId} onPick=${setJobId} onOff=${off} /></div>
  </div>`;
}
