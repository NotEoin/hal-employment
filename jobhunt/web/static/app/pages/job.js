/* One job: why it matches, what it asks for and what to do next.

   Every point is listed with the starting points and the total, so the score always adds up. Opening a job
   asks Hal to check its requirements once; "This does not matter to me" takes one
   requirement out of this job's score only. A hidden job says why it is hidden and how to change that. */
import { html, useState, useEffect, useLayoutEffect } from "../core/html.js";
import { post, put } from "../core/api.js";
import { go } from "../core/router.js";
import { useApi, toast } from "../core/store.js";
import { useKeys } from "../core/keys.js";
import { shortDay } from "../core/format.js";
import { Icon } from "../ui/icons.js";
import { Head, Score, Chip, Note } from "../ui/controls.js";
import { applyJob, queueJob, saveJob, notForMe, refreshJobs, jobPath } from "../ui/job-card.js";

const STATE_WORDS = { met: "Met", part: "Partly met", miss: "Missing", off: "Does not matter to you" };
const STATE_ICON = { met: "check", part: "warn", miss: "x", off: "eye" };
const STATE_TONE = { met: "good", part: "warn", miss: "bad", off: "" };
const asked = new Set();   // jobs whose requirements Hal was asked to check in this window

/** One requirement, with the advert's words, what the person has and "This does not matter to me". */
export function Req({ r, jobId }) {
  const [st, setSt] = useState(r.s);
  // a layout effect: Preact runs a plain one up to a frame late, where it can undo the person's first action
  useLayoutEffect(() => setSt(r.s), [r.s]);
  const off = async on => {
    try {
      const res = await post(jobPath(jobId) + "/requirements/" + r.n + "/off", { off: on });
      setSt(res.state);
      refreshJobs();
      toast(on ? "Hal leaves this out of the score for this job" : "This counts again for this job");
    } catch (err) { toast(err.message); }
  };
  return html`<div class="req">
    <span class=${"st " + st} aria-hidden="true"><${Icon} n=${STATE_ICON[st] || "x"} c="s" /></span>
    <div><div class="row tight"><b>${r.text}</b><${Chip} tone=${STATE_TONE[st]}>${STATE_WORDS[st] || st}<//></div>
      ${r.quote && html`<div class="quote"><small>From the advert</small>"${r.quote}"</div>`}
      <div class="quote mine"><small>What you have</small>${r.mine || "Nothing found in your CV or record."}</div>
      ${st !== "met" && st !== "off" && html`<div class="row" style="margin-top:8px">${r.tip && html`<span class="small sub">${r.tip}</span>`}
        <button type="button" class="btn quiet small" onClick=${() => off(true)}>This does not matter to me</button></div>`}
      ${st === "off" && html`<div class="row" style="margin-top:8px"><button type="button" class="btn quiet small"
        onClick=${() => off(false)}>Count it again</button></div>`}
    </div></div>`;
}

function subLine(j) {
  const parts = [[j.org, j.place].filter(Boolean).join(", "), j.pattern, j.pay];
  if (j.closes) parts.push("Closes " + shortDay(j.closes));
  parts.push("Found on " + j.found_on);
  return parts.filter(Boolean).join(". ") + ".";
}

/** Why it is hidden, with the way to change it. */
function HiddenNote({ hidden, jobId }) {
  const fix = async () => {
    const f = hidden.fix;
    try {
      if (hidden.rule === "not_for_me") { await post(jobPath(jobId) + "/put-back"); refreshJobs(); toast("Back in your matches"); }
      else if (f.set) { await put("/settings/" + encodeURIComponent(f.set.key), { value: f.set.value }); toast("Changed. Re-check your jobs from Jobs, Hidden."); }
      else go(f.route);
    } catch (err) { toast(err.message); }
  };
  return html`<${Note} icon="eye">Hidden: ${hidden.label}.${hidden.fix && html` <button type="button" class="btn quiet small"
    onClick=${fix}>${hidden.fix.label}</button>`}<//>`;
}

/** The job page; `arg` is the job id. */
export function JobPage({ arg }) {
  const path = jobPath(arg);
  const { data, error, reload } = useApi(path, { on: [ev => ev.type === "job" && ev.id === arg] });
  const j = data && data.job;
  useEffect(() => {
    if (!data || data.reqs || asked.has(arg) || data.hidden) return;
    asked.add(arg);
    post(path + "/requirements").catch(() => { /* already being checked, or nothing to check */ });
  }, [data && data.job && data.job.id]);
  useKeys({ a: () => j && applyJob(j), q: () => j && queueJob(j), s: () => j && saveJob(j), n: () => j && notForMe(j) }, [j && j.id, j && j.saved]);
  const checkAgain = async () => {
    try { await post(path + "/check"); toast("Hal is checking this job again. Watch it in Activity."); }
    catch (err) { toast(err.message); }
  };
  const back = html`<button type="button" class="back" onClick=${() => go("jobs")}><${Icon} n="chevL" c="s" />Jobs</button>`;
  if (error) return html`${back}<${Head} title="Job" /><div class="card empty">${error.message}</div>`;
  if (!data) return html`${back}<${Head} title="Job" /><div class="card empty">Loading...</div>`;
  const pts = data.points;
  return html`
    ${back}
    <${Head} title=${j.title} sub=${subLine(j)}>
      <button type="button" class="btn primary" onClick=${() => applyJob(j)}>Apply with Hal</button>
      <button type="button" class="btn" onClick=${() => queueJob(j)}><${Icon} n="plus" c="s" />Add to queue</button>
      <button type="button" class="btn" aria-pressed=${j.saved ? "true" : "false"} onClick=${() => saveJob(j)}><${Icon} n="star" c="s" />${j.saved ? "Saved" : "Save"}</button>
      ${!data.hidden && html`<button type="button" class="btn quiet" onClick=${() => notForMe(j)}>Not for me</button>`}
    <//>
    ${(data.eligibility || data.hidden) && html`<div class="stack s" style="margin-bottom:16px">
      ${data.eligibility && html`<${Note} icon="user">${data.eligibility.line}<//>`}
      ${data.hidden && html`<${HiddenNote} hidden=${data.hidden} jobId=${j.id} />`}</div>`}
    <div class="grid2">
      <div class="stack">
        <section class="card">
          <div class="row" style="align-items:flex-start"><div class="grow" style="flex:1"><h2 class="h2">Why Hal thinks this fits</h2>
            <p class="small sub">Every job starts at ${pts.base} points. Your goal adds or takes away points.</p></div><${Score} score=${pts.total} /></div>
          <div class="pts">
            <div class="pt"><span>Starting points</span><span class="v">${pts.base}</span></div>
            ${pts.items.map(p => html`<div class="pt"><span>${p.label}</span><span class=${"v " + (p.points >= 0 ? "plus" : "minus")}>${p.points > 0 ? "+" : ""}${p.points}</span></div>`)}
            <div class="pt total"><span>Total</span><span class="v">${pts.total}</span></div>
          </div>
          <div class="row" style="margin-top:8px"><button type="button" class="btn quiet small" onClick=${() => go("settings-scoring")}><${Icon} n="sliders" c="s" />Change how jobs are scored</button></div>
        </section>
        ${data.reqs ? html`<section class="card"><h2 class="h2">What they ask for</h2>
            <p class="small sub">Hal checked each requirement against your CV and your record. Quotes are copied word for word.</p>
            ${data.reqs.map(r => html`<${Req} r=${r} jobId=${j.id} />`)}</section>`
          : html`<section class="card empty">Hal checks the requirements when you open a job. They appear here when Hal has read the advert.
            <div class="row" style="margin-top:10px;justify-content:center"><button type="button" class="btn small" onClick=${reload}>Look again</button></div></section>`}
      </div>
      <div class="stack">
        ${data.flags && data.flags.length > 0 && html`<section class="card"><h2 class="h2">Good to know</h2>${data.flags.map(f => html`<div class="li"><div class="ic info"><${Icon} n="info" /></div><div class="grow small">${f}</div></div>`)}</section>`}
        ${data.company && data.company.length > 0 && html`<section class="card"><h2 class="h2" style="margin:0">About ${j.org}</h2><ul style="margin:10px 0 6px;padding-left:20px">${data.company.map(c => html`<li class="small">${c}</li>`)}</ul></section>`}
        <section class="card"><h2 class="h2">The advert</h2><div class="advert" tabindex="0" aria-label="The advert">${j.advert || "The full advert is saved here when Hal first finds the job."}</div>
          <p class="small sub" style="margin:10px 0 0">${j.advert_saved_at ? "Saved on " + shortDay(j.advert_saved_at) + ", so you can still read it if the employer takes it down." : ""}</p>
          <div class="row" style="margin-top:10px">${j.url && html`<a class="btn small" href=${j.url} target="_blank" rel="noopener noreferrer"><${Icon} n="external" c="s" />Open the advert</a>`}
            <button type="button" class="btn quiet small" onClick=${checkAgain}><${Icon} n="refresh" c="s" />Check this job again</button></div>
        </section>
      </div>
    </div>`;
}
