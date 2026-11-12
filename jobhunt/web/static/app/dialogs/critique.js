/* The "Critique as a recruiter" dialog: Hal reads one application's CV and cover letter against the advert
   (POST /applications/{id}/documents/critique starts docs.critique) and shows the three changes that would
   help most, then for each document its grades with why, and comments on the lines they quote. It is Hal's
   opinion and changes nothing; the documents are read as last saved. */
import { html, useEffect, useState } from "../core/html.js";
import { run } from "../core/api.js";
import { closeDialog } from "../core/store.js";
import { DlgHead } from "../ui/dialog.js";
import { Icon } from "../ui/icons.js";

const GRADE_TONE = { 1: "bad", 2: "warn", 3: "line", 4: "good", 5: "good" };

function Grade({ g }) {
  return html`<div class="li" style="padding:8px 2px" data-grade=${g.criterion}>
    <span class=${"chip " + (GRADE_TONE[g.grade] || "line")} aria-label=${g.grade + " out of 5"}>${g.grade} of 5</span>
    <div class="grow"><b style="font-weight:600">${g.criterion}</b>${g.why && html`<div class="small">${g.why}</div>`}
      ${g.fix && html`<div class="small sub">${g.needs_fact ? "You know this one: " : "Try: "}${g.fix}</div>`}</div></div>`;
}

function Line({ x }) {
  return html`<li class="small" style="margin:6px 0"><q>${x.quote}</q> ${x.comment}</li>`;
}

function Doc({ d }) {
  return html`<section class="stack s" data-doc=${d.document} style="margin-top:14px">
    <h3 class="h3" style="margin:0">${d.title}</h3>
    ${d.summary && html`<p style="margin:0">${d.summary}</p>`}
    ${d.grades.map(g => html`<${Grade} key=${g.criterion} g=${g} />`)}
    ${d.lines.length > 0 && html`<div><b class="small">Line by line</b><ul style="margin:4px 0 0;padding-left:18px">
      ${d.lines.map((x, i) => html`<${Line} key=${i} x=${x} />`)}</ul></div>`}</section>`;
}

/** The dialog; `d.app` is the application. */
export default function Critique({ d }) {
  const [out, setOut] = useState(null);
  const [error, setError] = useState("");
  useEffect(() => {
    let live = true;
    run("/applications/" + encodeURIComponent(d.app.id) + "/documents/critique", {})
      .then(done => { if (live) { if (done && done.state === "done" && done.result) setOut(done.result); else setError((done && done.error) || "Hal could not finish its read just now."); } })
      .catch(err => { if (live) setError(err.message); });
    return () => { live = false; };
  }, [d.app.id]);
  const head = html`<${DlgHead} title="Critique as a recruiter" sub="Hal reads your CV and cover letter against the advert, as a hiring manager would. It is Hal's opinion, and nothing is changed." icon="eye" />`;
  const close = html`<footer><button type="button" class="btn primary" onClick=${closeDialog}>Close</button></footer>`;
  if (error) return html`${head}<p role="alert">${error}</p>${close}`;
  if (!out) return html`${head}<div class="row" role="status"><span class="eye sm busy" aria-hidden="true"></span><span class="small sub">Reading your documents as a recruiter would. This can take a minute or two.</span></div>`;
  return html`${head}
    ${out.note && html`<p class="small sub" role="status">${out.note}</p>`}
    ${out.top_fixes.length > 0 && html`<section class="card soft" data-top-fixes><b>The ${out.top_fixes.length === 1 ? "change" : out.top_fixes.length + " changes"} that would help most</b>
      <ol style="margin:6px 0 0;padding-left:20px">${out.top_fixes.map((f, i) => html`<li key=${i}>${f}</li>`)}</ol></section>`}
    ${out.screen && out.screen.why && html`<p class="small" style="margin-top:12px"><${Icon} n="clock" c="s" /> <b>In the first six seconds:</b> ${out.screen.read_on ? "a recruiter reads on. " : "a recruiter may stop here. "}${out.screen.why}</p>`}
    ${out.requirements.length > 0 && html`<section style="margin-top:12px"><h3 class="h3" style="margin:0">What the advert asks, and where your CV shows it</h3>
      <ul style="margin:4px 0 0;padding-left:18px">${out.requirements.map((r, i) => html`<li key=${i} class="small">${r.requirement}: ${r.evidence ? html`<q>${r.evidence}</q>` : html`<b>not shown yet</b>`}</li>`)}</ul></section>`}
    ${out.documents.map(doc => html`<${Doc} key=${doc.document} d=${doc} />`)}
    ${close}`;
}
Critique.wide = true;
