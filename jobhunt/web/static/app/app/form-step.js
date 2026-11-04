/* The Form step of an application: what Hal filled, each answer outlined fine or to
   check with its source and "Why this answer?", the notes about the job site Hal used, and the buttons that
   act on the form. The words come from the server (`form.words`, each field's `why` and `notes_used`), so
   they are the same in Hal's window and with the browser helper. A private answer's "Why this answer?" is
   the one fixed sentence below whatever the server sends, so nothing more about it can reach the page. */
import { html, cx, useState } from "../core/html.js";
import { post } from "../core/api.js";
import { go } from "../core/router.js";
import { toast, openDialog, invalidate } from "../core/store.js";
import { Icon } from "../ui/icons.js";
import { ShowFormButton } from "../ui/open-buttons.js";
import { HelperView } from "./helper-view.js";

const SRC_LOOK = { profile: ["info", "user"], cv: ["info", "file"], record: ["info", "book"], saved: ["why", "bookmark"],
                   note: ["why", "globe"], hal: ["hal", "spark"], private: ["", "lock"], you: ["good", "user"] };
export const PRIVATE_WHY = "From your private answers. These are never shown to the AI or saved in logs.";
const ORIGIN_WORDS = { you: "You told Hal.", builtin: "Built into Hal.", learned: "Hal learned this from you.",
                       hal: "Hal suggested this." };

// When Hal could not get to the form, the person gets there in Hal's window and Hal starts from that page.
const STUCK = "No answers yet. If Hal is stuck before the form, open the form yourself in Hal's browser window "
  + "(press Apply on the site, close any pop-up, sign in if it asks), then press \"Fill in from this page\".";
const HERE_BUTTON = act => html`<button type="button" class="btn" onClick=${() => act("/form/fill", { here: true },
  "Hal is filling in the page your tab shows")}>Fill in from this page</button>`;

const sentence = t => (/[.!?]$/.test(t) ? t : t + ".");

/** The notes Hal used on this form: the form's own list, then each field's, once each. */
export function notesUsed(form) {
  const seen = new Set();
  const out = [];
  for (const n of [...(form.notes_used || []), ...form.fields.flatMap(x => x.notes_used || [])]) {
    const key = n.id || n.text;
    if (!n.text || seen.has(key)) continue;
    seen.add(key);
    out.push(n);
  }
  return out;
}

/** "Why this answer?" for one field, with the trace line of each note used on it. */
function WhyLine({ x }) {
  if (x.src === "private") return html`<div class="whyline">${PRIVATE_WHY}</div>`;
  const traces = (x.notes_used || []).map(n => n.what).filter(w => w && w !== x.why);
  return html`<div class="whyline">${x.why}${traces.map(t => html`<div class="small" style="margin-top:4px">${t}</div>`)}</div>`;
}

/** The Form step. `onReopen` shows the Documents step once "Change the documents" has taken them back. */
export function FormStep({ a, reload, onReopen }) {
  const f = a.form;
  const [open, setOpen] = useState(null);
  const [edits, setEdits] = useState({});
  const path = "/applications/" + encodeURIComponent(a.id);
  const act = async (rest, body, said) => {
    try { await post(path + rest, body); toast(said); invalidate("/applications*"); if (reload) reload(); }
    catch (err) { toast(err.message); }
  };
  const reopen = async () => {
    try {
      await post(path + "/documents/reopen", {});
      toast("Hal stopped. Change the documents, then approve them again.");
      invalidate("/applications*");
      if (onReopen) onReopen();
    } catch (err) { toast(err.message); }
  };
  if (!f && a.step >= 2) {
    return html`<section class="card"><div class="row"><span class="eye busy" aria-hidden="true"></span><h2 class="h2" style="margin:0">Hal is opening the form on the employer's site</h2></div>
      <p class="sub" style="margin-top:8px">When Hal has filled it in, its answers appear here to check.</p>
      <div class="row" style="margin-top:12px">${HERE_BUTTON(act)}<button type="button" class="btn quiet" onClick=${reopen}>Change the documents</button></div></section>`;
  }
  if (!f) return html`<section class="card empty">The form starts after you approve the documents. Hal will open it in your browser and fill it in.</section>`;
  const fields = [...f.fields].sort((x, y) => (x.ok ? 1 : 0) - (y.ok ? 1 : 0));
  const changed = Object.entries(edits).map(([hid, value]) => ({ hid, value }));
  const warn = f.to_check > 0;
  const used = notesUsed(f);
  return html`
    <div class="approve" style=${warn ? "background:var(--warn-soft)" : ""}><div class=${cx("ic", warn ? "warn" : "info")}><${Icon} n=${warn ? "warn" : "info"} /></div>
      <div class="grow"><b>${f.words || f.message}</b>${f.handed_over && html`<div class="small">Hal stops before Submit. You press Submit on the employer's site.</div>`}</div>
      <${ShowFormButton} appId=${a.id} />
      <button type="button" class="btn" onClick=${() => openDialog("report", { site: f.site, platform: f.platform, job_id: a.id })}><${Icon} n="flag" c="s" />Something wrong here?</button></div>
    <div class="grid2">
      <section class="card">
        <div class="row" style="margin-bottom:6px"><h2 class="h2" style="margin:0">Answers</h2><span class="sp"></span>
          <span class="chip good">${f.fields.filter(x => x.ok).length} fine</span><span class="chip warn">${f.to_check} to check</span></div>
        ${!f.fields.length && html`<p class="small sub">${STUCK}</p>`}
        ${fields.map((x, k) => { const look = SRC_LOOK[x.src] || SRC_LOOK.hal; return html`<div class="field-row" data-hid=${x.hid}>
          <div class="q">${x.q}</div>
          <span class=${cx("flag", x.ok ? "ok" : "check")}><${Icon} n=${x.ok ? "check" : "warn"} c="s" />${x.ok ? "Fine" : "Check this"}</span>
          <div class="ans"><input class="input" aria-label=${x.q} value=${x.hid in edits ? edits[x.hid] : x.a} disabled=${x.src === "private"}
            onInput=${e => setEdits({ ...edits, [x.hid]: e.target.value })} /></div>
          <div class="row tight" style="grid-column:1/-1"><span class=${cx("chip", look[0])}><${Icon} n=${look[1]} c="s" />${x.src_label}</span>
            ${x.src !== "note" && x.src !== "private" && (x.notes_used || []).length > 0 && html`<span class="chip why"><${Icon} n="globe" c="s" />Site note</span>`}
            <button type="button" class="why" aria-expanded=${open === k ? "true" : "false"} onClick=${() => setOpen(open === k ? null : k)}>Why this answer?</button></div>
          ${x.problem && html`<div class="small" style="grid-column:1/-1;color:var(--bad)">${x.problem}</div>`}
          ${open === k && html`<${WhyLine} x=${x} />`}</div>`; })}
        <div class="row" style="margin-top:14px">
          <button type="button" class="btn" disabled=${!changed.length} onClick=${() => act("/form/edits", { edits: changed }, "Hal is putting your changes into the form")}>Put my changes in the form</button>
          ${!f.last_page && html`<button type="button" class="btn" onClick=${() => act("/form/next", {}, "Hal is carrying on to the next page")}>Carry on to the next page</button>`}
          <button type="button" class="btn quiet" onClick=${() => act("/form/fill", { fresh: true }, "Hal is filling the form again from the start")}>Fill it in again</button>
          ${HERE_BUTTON(act)}
          <button type="button" class="btn quiet" onClick=${reopen}>Change the documents</button>
          <button type="button" class="btn quiet" onClick=${() => act("/form/draft-rest", {}, "Hal is answering the rest")}>Ask Hal to answer the rest</button>
          <button type="button" class="btn quiet" onClick=${() => act("/sent", {}, "Moved to Sent. Hal will watch your email for a reply.")}>I sent it myself</button></div>
      </section>
      <div class="stack">
        <section><h2 class="h2">What you see on the site</h2><${HelperView} a=${a} />
          <p class="small sub" style="margin-top:8px">Green: Hal is sure. Amber: please check. The helper's panel sits in the corner of the employer's page.</p></section>
        ${used.length > 0 && html`<section class="card soft" data-notes-used><h3 class="h3">Notes Hal used on this site</h3>
          ${used.map(n => html`<div class="li"><div class="ic why"><${Icon} n="globe" /></div><div class="grow small">${sentence(n.text)}
            ${ORIGIN_WORDS[n.origin] && html` <span class="sub">${ORIGIN_WORDS[n.origin]}</span>`}</div></div>`)}
          <button type="button" class="btn quiet small" onClick=${() => go("memory-sites")}>See all notes for this site</button></section>`}
      </div>
    </div>`;
}
