/* The Suggestions tab of My CV: how many are reviewed, Accept all for the kinds
   that need no judgement, Look again, a filter by kind, the cards, and "How suggestions work" beside a
   preview of the CV. The list reloads on the `cv` and `suggestions` events (core/events.js). */
import { html, useState } from "../core/html.js";
import { post, run } from "../core/api.js";
import { toast, useApi } from "../core/store.js";
import { Icon } from "../ui/icons.js";
import { Bar } from "../ui/controls.js";
import { DocPreview } from "../ui/doc-preview.js";
import { SuggCard } from "./sugg-card.js";

const LIST = "/cv/suggestions";
// "Accept all" is offered only for kinds whose fix needs no judgement.
const ACCEPT_ALL = [["Consistency", "Accept all consistency fixes", "Accepted all consistency fixes"],
  ["Symbols", "Accept all symbol fixes", "Accepted all symbol fixes"], ["Dates", "Accept all date fixes", "Accepted all date fixes"]];

/** The Suggestions tab; `preview` replaces the CV preview beside the cards (My CV passes its own). */
export function SuggTab({ preview }) {
  const { data, error, reload } = useApi(LIST);
  const [kind, setKind] = useState("All");
  const [looking, setLooking] = useState(false);
  if (error) return html`<div class="card empty">${error.message}</div>`;
  if (!data) return html`<div class="card empty" aria-busy="true">Loading your suggestions.</div>`;
  const pending = data.pending.filter(x => kind === "All" || x.kind === kind);
  const kinds = ["All", ...data.kinds];
  const pct = data.total ? (data.reviewed / data.total) * 100 : 100;
  const lookAgain = async () => {
    setLooking(true);
    toast("Hal is reviewing your CV again, section by section");
    try { await run(LIST + "/look-again"); } catch (err) { toast(err.message); }
    finally { setLooking(false); reload(); }
  };
  const acceptAll = async (k, words) => {
    try { const out = await post(LIST + "/accept-kind", { kind: k }); toast(out.accepted ? words : "Nothing to accept"); reload(); }
    catch (err) { toast(err.message); }
  };
  const busy = looking || data.state === "looking";
  return html`
    <div class="row" style="margin-bottom:14px"><span><b>${data.reviewed} of ${data.total}</b> reviewed</span>
      <div style="width:160px"><${Bar} pct=${pct} label="Suggestions reviewed" /></div><span class="sp"></span>
      ${ACCEPT_ALL.filter(([k]) => data.pending.some(x => x.kind === k)).map(([k, words, done]) =>
        html`<button type="button" class="btn small" onClick=${() => acceptAll(k, done)}>${words}</button>`)}
      <button type="button" class="btn quiet small" disabled=${busy} onClick=${lookAgain}><${Icon} n="refresh" c="s" />Look again</button></div>
    ${kinds.length > 2 && html`<div class="chips" style="margin-bottom:16px" role="group" aria-label="Show suggestions of one kind">${kinds.map(k =>
      html`<button type="button" class=${kind === k ? "pick on" : "pick"} aria-pressed=${kind === k ? "true" : "false"} onClick=${() => setKind(k)}>${k}</button>`)}</div>`}
    <div class="grid2"><div class="stack">
      ${busy && html`<div class="note"><${Icon} n="spark" /><div>Hal is reading your CV one section at a time.</div></div>`}
      ${pending.length ? pending.map(x => html`<${SuggCard} x=${x} key=${x.id + ":" + x.alt + ":" + x.afters.length} onChange=${reload} />`)
        : html`<div class="card empty"><b>All reviewed.</b><br />Hal will suggest more when your CV, its type or your goal changes.</div>`}
      ${data.done.length > 0 && html`<h2 class="h2" style="margin-top:6px">Reviewed</h2>${data.done.map(x => html`<${SuggCard} x=${x} key=${x.id + "d"} onChange=${reload} />`)}`}</div>
      <div class="stack"><section class="card soft"><h2 class="h2">How suggestions work</h2>
        <p class="small">Hal reads your CV one section at a time against your CV type and your goal. Each suggestion names the check behind it and the evidence it used.</p>
        <p class="small">Hal never adds a number, date or qualification it cannot find in your CV or record. Where a number would help, it leaves a gap like <span class="ph">[how many?]</span> for you.</p>
        <p class="small" style="margin:0">When you leave something as it is, Hal remembers why and stops suggesting it.</p></section>
        ${preview || html`<${DocPreview} />`}</div></div>`;
}
