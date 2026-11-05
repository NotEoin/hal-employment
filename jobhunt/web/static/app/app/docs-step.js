/* The Documents step of an application: while Hal writes, its steps and Stop; then the
   CV changes (Undo and Redo per change), the letter, the answers and the message, with "Approve and fill in
   the form". The first approval shows the three-step guide (dialogs/firstapp.js) before anything opens.
   "Critique as a recruiter" (dialogs/critique.js) reads the saved documents against the advert and changes
   nothing.

   The CV and the letter are each shown beside the PDF Hal will upload, so the person sees the document
   itself and not only the words. An Undo, a Redo or saved letter edits make the PDF again, and its address
   changes with it, so the preview follows. */
import { html, cx, useState } from "../core/html.js";
import { post, put } from "../core/api.js";
import { toast, openDialog, invalidate } from "../core/store.js";
import { go } from "../core/router.js";
import { Tabs } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";
import { copyText } from "../ui/clipboard.js";
import { plural } from "../core/format.js";
import { UncheckedNote } from "../ui/unchecked-note.js";

const appPath = a => "/applications/" + encodeURIComponent(a.id);

/** Approve and fill in the form: starts the form, then calls `then`, which shows the Form step. The page
    stays on this application, so the person watches the form being filled rather than the board. */
export async function approve(a, edits = {}, then = null) {
  try {
    await post(appPath(a) + "/documents/approve", edits);
    invalidate("/applications*");
    toast("Approved. Hal is opening the form on the employer's site.");
    if (then) then();
  } catch (err) { toast(err.message); }
}

/** Change the documents: Hal stops filling and the documents can be edited and approved again. */
async function reopen(a, reload) {
  try {
    await post(appPath(a) + "/documents/reopen", {});
    toast("Hal stopped. Change the documents, then approve them again.");
    invalidate("/applications*");
    if (reload) reload();
  } catch (err) { toast(err.message); }
}

const copy = text => copyText(text);

/** The PDF Hal will upload, drawn in the page; nothing when there is no file yet. */
function Pdf({ url, label }) {
  if (!url) return html`<div class="card"><p class="small sub">The ${label.toLowerCase()} appears here once Hal has made it.</p></div>`;
  return html`<div class="stack s"><iframe class="pdfview" src=${url} title=${label}></iframe>
    <a class="btn small" href=${url} target="_blank" rel="noopener"><${Icon} n="external" c="s" />Open in a new tab</a></div>`;
}

async function stop(a) {
  try { await post("/actions/" + encodeURIComponent(a.action_id) + "/stop"); toast("Hal stopped. Nothing was sent and no form was opened."); }
  catch (err) { toast(err.message); }
}

function Writing({ a }) {
  return html`<section class="card"><div class="row"><span class="eye busy" aria-hidden="true"></span><h2 class="h2" style="margin:0">Hal is writing your documents for this job</h2></div>
    <p class="sub" style="margin-top:8px">Nothing is sent and no form is opened. When Hal finishes, the CV and letter wait here for you.</p>
    ${(a.docs.writing || []).map(s => html`<div class="li" style="align-items:center"><div class=${cx("ic", s.state === "done" ? "good" : "hal")}>${s.state === "done"
      ? html`<${Icon} n="check" />` : html`<span class="eye sm busy" aria-hidden="true"></span>`}</div><div class="grow">${s.text}</div></div>`)}
    ${a.action_id && html`<div class="row" style="margin-top:12px"><button type="button" class="btn stop" onClick=${() => stop(a)}><${Icon} n="stop" c="s" />Stop</button></div>`}</section>`;
}

/** The Documents step. */
export function DocsStep({ a, reload, onApproved }) {
  const d = a.docs;
  const [tab, setTab] = useState("cv");
  const [letter, setLetter] = useState(d.letter.text);
  const [answers, setAnswers] = useState(d.answers);
  const [message, setMessage] = useState(d.message.text);
  if (d.state === "writing") return html`<${Writing} a=${a} />`;
  if (a.step > 1) {
    return html`<section class="card"><div class="row"><div class="ic good"><${Icon} n="check" /></div><div class="grow" style="flex:1">
      <b>You approved these documents${d.approved_at ? " on " + new Date(d.approved_at).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : ""}.</b>
      <div class="small sub">Cover letter of ${d.letter.words} words.</div></div>
      ${a.column === "form" && html`<button type="button" class="btn small" onClick=${() => reopen(a, reload)}>Change the documents</button>`}
      <button type="button" class="btn small" onClick=${() => go("docs-" + (a.number || ""))}>Open in Documents</button></div></section>`;
  }
  const edits = { letter, message, answers };
  const onApprove = () => (a.first_time ? openDialog("firstapp", { app: a, edits, then: onApproved }) : approve(a, edits, onApproved));
  const toggle = async (c, on) => {
    try { await post(appPath(a) + "/documents/changes/" + encodeURIComponent(c.id), { on }); reload(); }
    catch (err) { toast(err.message); }
  };
  const save = async body => {
    try { await put(appPath(a) + "/documents", body); toast("Saved"); reload(); } catch (err) { toast(err.message); }
  };
  const changes = d.cv.changes || [];
  return html`
    <div class="approve"><div class="ic info"><${Icon} n="file" /></div><div class="grow"><b>Check what Hal made for this job, then approve it.</b>
      <div class="small">After you approve, Hal opens the employer's form and fills it in. It never presses Submit.</div></div>
      <button type="button" class="btn primary" onClick=${onApprove}>Approve and fill in the form</button>
      <button type="button" class="btn" onClick=${() => openDialog("askchange", { app: a })}>Ask for changes</button>
      <button type="button" class="btn quiet" onClick=${() => openDialog("critique", { app: a })}><${Icon} n="eye" c="s" />Critique as a recruiter</button></div>
    <${UncheckedNote} />
    <${Tabs} label="Documents" value=${tab} onChange=${setTab} tabs=${[["cv", "CV", plural(changes.filter(c => c.on).length, "change")],
      ["letter", "Cover letter", ""], ["answers", "Answers", answers.length || ""], ["message", "Message", ""]]} />
    ${tab === "cv" && html`<div class="docs-split"><div class="stack s">
      <p class="sub" style="margin:0">${changes.length ? "Hal changed " + plural(changes.length, "thing") + " from your CV for this job." : "Hal used your main CV as it is for this job."}</p>
      ${changes.map(c => html`<article class=${cx("sugg", !c.on && "done")}><div class="row tight"><span class="chip info">${c.kind}</span><span class="small sub">${c.where}</span><span class="sp"></span>
        ${c.on ? html`<button type="button" class="btn quiet small" onClick=${() => toggle(c, false)}><${Icon} n="undo" c="s" />Undo</button>`
          : html`<span class="small sub">Undone: your CV's version is used</span><button type="button" class="btn quiet small" onClick=${() => toggle(c, true)}>Redo</button>`}</div>
        ${c.on && html`<div class="ba"><div class="b"><small>Your CV</small>${c.before || "(not there)"}</div><div class="a"><small>For this job</small>${c.after || "(changed)"}</div></div>
          ${c.why && html`<div class="small"><b style="color:var(--why)">Why:</b> ${c.why}</div>`}`}</article>`)}
      </div><${Pdf} url=${d.cv.preview_url} label="CV preview" /></div>`}
    ${tab === "letter" && html`<div class="docs-split"><div class="stack s">
      <label class="small" for="letter-text"><b>Cover letter</b> <span class="sub">${letter.split(/\s+/).filter(Boolean).length} words</span></label>
      <textarea id="letter-text" class="input" style="min-height:260px" value=${letter} onInput=${e => setLetter(e.target.value)}></textarea>
      <div class="row"><button type="button" class="btn" onClick=${() => save({ letter })}><${Icon} n="edit" c="s" />Save my edits</button>
      ${letter !== d.letter.text && html`<span class="small sub">Save your edits to see them in the letter.</span>`}</div></div>
      <${Pdf} url=${d.letter.preview_url} label="Cover letter preview" /></div>`}
    ${tab === "answers" && html`<div class="card">
      <p class="small sub">${answers.length ? "The advert asks " + answers.length + " questions. Hal drafted these from your evidence. They go into the form after you approve." : "The advert asks no questions of its own."}</p>
      ${answers.map((x, k) => html`<div class="field-row"><div class="q">${x.q}</div><button type="button" class="btn quiet small" onClick=${() => copy(x.a)}><${Icon} n="copy" c="s" />Copy</button>
        <div class="ans"><textarea class="input" aria-label=${x.q} value=${x.a} onInput=${e => setAnswers(answers.map((y, i) => (i === k ? { ...y, a: e.target.value } : y)))}></textarea></div></div>`)}
      ${answers.length > 0 && html`<button type="button" class="btn" onClick=${() => save({ answers })}>Save my edits</button>`}</div>`}
    ${tab === "message" && html`<div class="card">
      <p class="small sub">A short note to the hiring manager, if you want to send one. Hal never sends it.</p>
      <textarea class="input" aria-label="Message to the hiring manager" style="min-height:150px" value=${message} onInput=${e => setMessage(e.target.value)}></textarea>
      <div class="row" style="margin-top:10px"><button type="button" class="btn" onClick=${() => copy(message)}><${Icon} n="copy" c="s" />Copy</button>
      <button type="button" class="btn quiet" onClick=${() => save({ message })}>Save my edits</button>
      ${d.message.to && html`<span class="small sub">To ${d.message.to}.</span>`}</div></div>`}`;
}
