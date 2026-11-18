/* The Documents step of an application: while Hal writes, its steps and Stop; then the
   CV changes (Undo and Redo per change), the letter, the answers and the message, with "Approve and fill in
   the form". The first approval shows the three-step guide (dialogs/firstapp.js) before anything opens.
   "Critique as a recruiter" (dialogs/critique.js) reads the saved documents against the advert and changes
   nothing.

   The CV and the letter are each shown beside the PDF Hal will upload, so the person sees the document
   itself and not only the words. An Undo, a Redo or saved letter edits make the PDF again, and its address
   changes with it, so the preview follows.

   A CV Hal made with its change list (`docs.cv.schema` 2) is edited in place with My CV's editor in job
   mode (cv/edit-tab.js); it stays mounted while another tab shows, so unsaved changes survive a tab switch,
   and Approve waits until they are saved or discarded. An older application's CV keeps the change list
   with Undo.

   While "Ask me what the job cares about first" (`cvdocs.pick_first`) is on, the CV tab first lists the
   job's requirements (`docs.cv.pick`, from `tailor.to_pick`) to tick three to five of; one button sends
   them and Hal makes the job's CV again from them. Approve waits for that too.

   "Write the documents again" is always offered before approval; when the documents are out of date
   (`docs.out_of_date`) or the last write stopped (`docs.stopped`) it moves into a note above with the reason.
   With edits of the person's own (`docs.own_edits`) it asks first (dialogs/rewrite.js), and with changes not
   yet saved it asks for those first. When Hal made no documents (`docs.state` "failed") the step says why and
   offers the same button. */
import { html, cx, useState } from "../core/html.js";
import { post, put } from "../core/api.js";
import { toast, openDialog, invalidate } from "../core/store.js";
import { go } from "../core/router.js";
import { Tabs } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";
import { copyText } from "../ui/clipboard.js";
import { plural } from "../core/format.js";
import { UncheckedNote } from "../ui/unchecked-note.js";
import { EditTab } from "../cv/edit-tab.js";

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
const sentence = text => (text || "").replace(/\.\s*$/, "") + ".";

/** Write the documents again from the record and CV as they are now. With edits of the person's own it asks
    first (dialogs/rewrite.js); with changes on the page not yet saved it asks for those to be saved first, so
    Hal knows which edits are theirs. */
async function writeAgain(a, unsaved, reload) {
  if (unsaved) { toast("Save or discard your changes first, then write the documents again."); return; }
  if (a.docs.own_edits) { openDialog("rewrite", { app: a, then: reload }); return; }
  try {
    await post(appPath(a) + "/documents/redo", {});
    invalidate("/applications*");
    toast("Hal is writing the documents again.");
    if (reload) reload();
  } catch (err) { toast(err.message); }
}

/** The Documents step when Hal has no documents to show: why, and the button to write them again. */
function Failed({ a, reload }) {
  return html`<section class="card" role="status"><div class="row"><div class="ic warn"><${Icon} n="warn" /></div>
      <h2 class="h2" style="margin:0">Hal could not write the documents for this job</h2></div>
    <p class="sub" style="margin-top:8px">${sentence(a.docs.stopped)} Nothing was sent and no form was opened.</p>
    <div class="row"><button type="button" class="btn primary" onClick=${() => writeAgain(a, false, reload)}><${Icon} n="refresh" c="s" />Write the documents again</button></div></section>`;
}

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

const MIN_PICKS = 3, MAX_PICKS = 5;   // as `tailor.MIN_PICKS` and `tailor.MAX_PICKS`

/** What the job cares about most: the job's requirements to tick before Hal adjusts its CV. */
function Picks({ a, offered }) {
  const [ticked, setTicked] = useState([]);
  const [busy, setBusy] = useState(false);
  const flip = r => setTicked(ticked.includes(r) ? ticked.filter(x => x !== r) : [...ticked, r]);
  const ok = ticked.length >= MIN_PICKS && ticked.length <= MAX_PICKS;
  const send = async () => {
    setBusy(true);
    try {
      await post(appPath(a) + "/cv/priorities", { priorities: ticked });
      toast("Hal is adjusting your CV for what you ticked.");
      invalidate("/applications*");
    } catch (err) { toast(err.message); setBusy(false); }
  };
  return html`<section class="card picks"><fieldset style="border:0;margin:0;padding:0">
      <legend><h2 class="h2" style="margin:0">What does this job care about most?</h2></legend>
      <p class="small sub" id="picks-help">Tick ${MIN_PICKS} to ${MAX_PICKS}. Hal adjusts your CV for this job to show those first.</p>
      ${offered.map(r => html`<label class="li" style="align-items:center;cursor:pointer" key=${r}>
        <input type="checkbox" checked=${ticked.includes(r)} onChange=${() => flip(r)} /><div class="grow" style="min-width:0">${r}</div></label>`)}
    </fieldset>
    <div class="row" style="margin-top:12px"><button type="button" class="btn primary" disabled=${!ok || busy} aria-describedby="picks-help" onClick=${send}>Adjust my CV for these</button>
      <span class="small sub" role="status">${ticked.length} ticked</span></div></section>`;
}

/** The Documents step. */
export function DocsStep({ a, reload, onApproved }) {
  const d = a.docs;
  const [tab, setTab] = useState("cv");
  const [letter, setLetter] = useState(d.letter.text);
  const [answers, setAnswers] = useState(d.answers);
  const [message, setMessage] = useState(d.message.text);
  const [cvDirty, setCvDirty] = useState(false);
  if (d.state === "writing") return html`<${Writing} a=${a} />`;
  if (a.step > 1) {
    return html`<section class="card"><div class="row"><div class="ic good"><${Icon} n="check" /></div><div class="grow" style="flex:1">
      <b>You approved these documents${d.approved_at ? " on " + new Date(d.approved_at).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }) : ""}.</b>
      <div class="small sub">Cover letter of ${d.letter.words} words.${d.cv.version_n ? " Your CV for this job is saved as version " + d.cv.version_n + "." : ""}</div></div>
      ${a.column === "form" && html`<button type="button" class="btn small" onClick=${() => reopen(a, reload)}>Change the documents</button>`}
      <button type="button" class="btn small" onClick=${() => go("docs-" + (a.number || ""))}>Open in Documents</button></div></section>`;
  }
  if (d.state === "failed") return html`<${Failed} a=${a} reload=${reload} />`;
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
  const original = d.cv.sent_as === "original";
  const pick = d.cv.pick || [];
  const editable = d.cv.schema === 2 && !original && !pick.length;
  const unsaved = cvDirty || letter !== d.letter.text || message !== d.message.text
    || JSON.stringify(answers) !== JSON.stringify(d.answers);
  const again = () => writeAgain(a, unsaved, reload);
  const behind = d.out_of_date || d.stopped;
  return html`
    ${behind && html`<div class="note warn again" role="status" style="margin-bottom:12px"><${Icon} n="warn" /><div class="grow">
        <b>${d.out_of_date ? sentence(d.out_of_date) : "Hal could not write these again: " + sentence(d.stopped)}</b>
        <div class="small">${d.out_of_date ? "Write them again to use what changed. Hal asks before it replaces any edits of your own."
          : "These are the documents from before. Try again, or check them and approve them as they are."}</div></div>
      <button type="button" class="btn primary small" onClick=${again}><${Icon} n="refresh" c="s" />Write the documents again</button></div>`}
    <div class="approve"><div class="ic info"><${Icon} n="file" /></div><div class="grow"><b>Check what Hal made for this job, then approve it.</b>
      <div class="small">After you approve, Hal opens the employer's form and fills it in. It never presses Submit.</div>
      ${cvDirty && html`<div class="small" role="status">Save or discard your changes to the CV before you approve.</div>`}
      ${pick.length > 0 && html`<div class="small" role="status">Tick what this job cares about most on the CV tab before you approve.</div>`}</div>
      <button type="button" class="btn primary" disabled=${cvDirty || pick.length > 0} onClick=${onApprove}>Approve and fill in the form</button>
      <button type="button" class="btn" onClick=${() => openDialog("askchange", { app: a })}>Ask for changes</button>
      <button type="button" class="btn quiet" onClick=${() => openDialog("critique", { app: a })}><${Icon} n="eye" c="s" />Critique as a recruiter</button>
      ${!behind && html`<button type="button" class="btn quiet" onClick=${again}><${Icon} n="refresh" c="s" />Write the documents again</button>`}</div>
    <${UncheckedNote} />
    <${Tabs} label="Documents" value=${tab} onChange=${setTab} tabs=${[["cv", "CV", plural(changes.filter(c => c.on).length, "change")],
      ["letter", "Cover letter", ""], ["answers", "Answers", answers.length || ""], ["message", "Message", ""]]} />
    ${editable && html`<div class="docs-split cv-job" hidden=${tab !== "cv"}><${EditTab} job=${{ path: appPath(a) + "/cv", appPath: appPath(a),
      onSaved: reload, onDirty: setCvDirty }} /><div class="docs-pdf"><${Pdf} url=${d.cv.preview_url} label="CV preview" /></div></div>`}
    ${tab === "cv" && pick.length > 0 && html`<${Picks} a=${a} offered=${pick} />`}
    ${tab === "cv" && !editable && !pick.length && html`<div class="docs-split"><div class="stack s">
      <p class="sub" style="margin:0">${original ? "Hal sends your own CV file, unchanged, as you chose on My CV." : changes.length ? "Hal changed " + plural(changes.length, "thing") + " from your CV for this job." : "Hal used your main CV as it is for this job."}</p>
      ${changes.map(c => html`<article class=${cx("sugg", !c.on && "done")}><div class="row tight"><span class="chip info">${c.kind}</span><span class="small sub">${c.where}</span><span class="sp"></span>
        ${c.on ? html`<button type="button" class="btn quiet small" onClick=${() => toggle(c, false)}><${Icon} n="undo" c="s" />Undo</button>`
          : html`<span class="small sub">Undone: your CV's version is used</span><button type="button" class="btn quiet small" onClick=${() => toggle(c, true)}>Redo</button>`}</div>
        ${c.on && html`<div class="ba"><div class="b"><small>Your CV</small>${c.before || "(not there)"}</div><div class="a"><small>For this job</small>${c.after || "(changed)"}</div></div>
          ${c.why && html`<div class="small"><b style="color:var(--why)">Why:</b> ${c.why}</div>`}`}</article>`)}
      </div>${original && d.cv.preview_url && !/\.pdf&/i.test(d.cv.preview_url)
        ? html`<div class="card"><p class="small">Your file is not a PDF, so it cannot be shown here.</p>
            <a class="btn small" href=${d.cv.preview_url} download><${Icon} n="download" c="s" />Download your file</a></div>`
        : html`<${Pdf} url=${d.cv.preview_url} label="CV preview" />`}</div>`}
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
