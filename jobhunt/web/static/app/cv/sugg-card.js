/* One suggested change to Hal's copy of your CV: why, what it is based on,
   Now and the suggestion side by side, the evidence, and Accept, Edit, Leave it as it is and Try another way.
   Gaps Hal left for numbers it could not find ("[how many?]") are marked, and Accept asks for them to be
   filled first, so a guess never reaches an employer. The server owns every decision; this card only asks. */
import { html, useState } from "../core/html.js";
import { post, run } from "../core/api.js";
import { openDialog, toast } from "../core/store.js";
import { Icon } from "../ui/icons.js";
import { Switch } from "../ui/controls.js";

const SUGG = "/cv/suggestions/";
export const REASONS = ["Keep my wording", "This is not true", "Too formal", "Too long", "Not relevant"];
const GAP = /(\[[^\]]+\])/;

/** Text with Hal's gaps marked. */
export function withGaps(text) {
  return String(text || "").split(GAP).map(p => (GAP.test(p) ? html`<span class="ph">${p}</span>` : p));
}

const path = (x, what) => SUGG + encodeURIComponent(x.id) + "/" + what;

/** A reviewed suggestion: what happened to it, with Undo for one that was accepted. */
function Done({ x, onChange }) {
  const accepted = x.status === "accepted" || x.status === "edited";
  const undo = async () => {
    try { await post(path(x, "undo")); toast("Undone. Saved as a new version."); onChange && onChange(); }
    catch (err) { toast(err.message); }
  };
  return html`<div class="sugg done"><div class="row"><span class=${accepted ? "chip good" : "chip"}>${accepted ? "Accepted" : "Left as it was"}</span>
    <span class="grow" style="flex:1;min-width:0">${x.title}</span>
    ${accepted && html`<button type="button" class="btn quiet small" onClick=${undo}><${Icon} n="undo" c="s" />Undo</button>`}</div></div>`;
}

/** One suggestion card; `onChange` reloads the list after a decision. */
export function SuggCard({ x, onChange }) {
  const [mode, setMode] = useState("");
  const [why, setWhy] = useState("");
  const [remember, setRemember] = useState(true);
  const [alt, setAlt] = useState(x.alt || 0);
  const [txt, setTxt] = useState(x.afters[x.alt || 0] || "");
  const [busy, setBusy] = useState(false);
  if (x.status !== "pending") return html`<${Done} x=${x} onChange=${onChange} />`;
  const after = x.afters[alt] || "";
  const reorder = x.op === "reorder";
  const act = async (fn, done) => {
    setBusy(true);
    try { const out = await fn(); done && done(out); onChange && onChange(); }
    catch (err) { toast(err.message); }
    finally { setBusy(false); }
  };
  const accept = () => act(() => post(path(x, "accept"), { alt }), out => {
    if (out && out.ask_send_copy) openDialog("sendcopy");
    else toast("Accepted. Saved as a new version; Undo is always there.");
  });
  const saveEdit = () => act(() => post(path(x, "accept"), { alt, text: txt }), () => toast("Saved your edited version"));
  const leave = () => act(() => post(path(x, "leave"), { reason: why, remember }), () =>
    toast(remember && why ? "Left as it was. Hal will remember: " + why.toLowerCase() + "." : "Left as it was"));
  const another = () => act(() => run(path(x, "another")), a => {
    const out = (a && a.result) || {};
    if (out.new) { setAlt(out.alt); setTxt(out.after); toast("Here is another way to say it"); }
    else if (x.afters.length > 1) { const n = (alt + 1) % x.afters.length; setAlt(n); setTxt(x.afters[n]); toast("Here is another way to say it"); }
    else toast(out.message || "This is the only sensible option here. Edit it instead?");
  });
  const label = x.afters.length > 1 ? "Suggestion " + (alt + 1) + " of " + x.afters.length : "Suggestion";
  return html`<article class="sugg" data-sugg=${x.id}>
    <div class="row tight"><span class="chip info">${x.kind}</span><span class="small sub">${x.where}</span></div>
    <h3 class="h3" style="font-size:17px">${x.title}</h3>
    <div class="note"><${Icon} n="info" /><div><b>Why:</b> ${x.why}${x.trigger && html`<div class="small sub" style="margin-top:4px">Based on: ${x.trigger}</div>`}</div></div>
    <div class="ba"><div class="b"><small>Now</small>${x.before || "(not there)"}</div><div class="a"><small>${label}</small>${mode === "edit"
      ? html`<textarea class="input" aria-label="Your wording" value=${txt} onInput=${e => setTxt(e.target.value)}></textarea>`
      : after ? withGaps(after) : "(left out)"}</div></div>
    ${x.evidence && x.evidence.length > 0 && html`<div class="small"><b>Evidence:</b> ${x.evidence.join("; ")}</div>`}
    ${mode === "reject" ? html`<div class="card soft"><b>Why leave it as it is?</b> <span class="small sub">Optional. Helps Hal suggest better next time.</span>
        <div class="chips" style="margin:10px 0">${REASONS.map(r => html`<button type="button" class=${why === r ? "pick on" : "pick"}
          aria-pressed=${why === r ? "true" : "false"} onClick=${() => setWhy(why === r ? "" : r)}>${r}</button>`)}</div>
        <div class="row"><${Switch} on=${remember} onChange=${setRemember} label="Remember this" /><span class="small">Remember this for next time</span><span class="sp"></span>
          <button type="button" class="btn quiet small" onClick=${() => setMode("")}>Back</button>
          <button type="button" class="btn small" disabled=${busy} onClick=${leave}>Leave it as it is</button></div></div>`
    : html`<div class="row">
        <button type="button" class="btn primary small" disabled=${busy} onClick=${accept}><${Icon} n="check" c="s" />Accept</button>
        ${reorder ? null : mode === "edit"
          ? html`<button type="button" class="btn small" disabled=${busy} onClick=${saveEdit}>Save my edit</button>`
          : html`<button type="button" class="btn small" onClick=${() => { setTxt(after); setMode("edit"); }}><${Icon} n="edit" c="s" />Edit</button>`}
        <button type="button" class="btn small" onClick=${() => setMode("reject")}>Leave it as it is</button>
        ${!reorder && html`<button type="button" class="btn quiet small" disabled=${busy} onClick=${another}><${Icon} n="refresh" c="s" />Try another way</button>`}
      </div>`}
  </article>`;
}
