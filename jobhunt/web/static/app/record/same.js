/* Check your record, "Same as...": the person says a project or job card is the same piece of work as another
   card, hidden or not, and picks which card's name to keep. The other card is merged into it on the server
   (`POST /profile/items/{id}/same-as`, `profile.duplicates.same_as`): its files move to the kept card, and
   "You merged these" lists the merge with Undo. Cards the person hid that come from a shown card's own files
   are offered as maybe the same (`HiddenTwins`), and merged only when they say so.

   One project was two cards, one with its repository's notes and one resting only on a list of projects, and
   hiding either lost its files (2026-11-08), so merging is the person's choice here rather than Hide. The
   choices load only when the person opens the picker, as most cards are never merged. */
import { html, useState } from "../core/html.js";
import { post } from "../core/api.js";
import { useApi } from "../core/store.js";
import { act } from "./check.js";

// The kinds of card the person merges here: the experiences a letter tells as one piece of work each.
export const SAME_KINDS = ["project", "job"];
const SAID = "Merged. Hal keeps one card with the files of both. You can undo it under You merged these.";

/** Merges card `gone` into card `kept` as the person said. */
const merge = (gone, kept) => post("/profile/items/" + encodeURIComponent(gone.id) + "/same-as", { into: kept.id });

/** The picker: which card is the same as this one, and which name to keep. */
function Picker({ item, onDone }) {
  const { data, error } = useApi("/profile/" + item.kind);
  const [otherId, setOtherId] = useState("");
  const [keep, setKeep] = useState("this");
  const [busy, setBusy] = useState(false);
  const others = data ? data.items.filter(i => i.id !== item.id && i.merged_into == null) : [];
  const other = others.find(i => String(i.id) === otherId);
  const uid = "same-" + item.id;
  const go = async () => {
    setBusy(true);
    const ok = await act(() => (keep === "this" ? merge(other, item) : merge(item, other)), SAID);
    setBusy(false);
    if (ok) onDone();
  };
  if (error) return html`<p class="small rec-line">${error.message}</p>`;
  if (!data) return html`<p class="small sub rec-line">Loading your cards</p>`;
  if (!others.length) return html`<p class="small sub rec-line">There is no other card of this kind to pick.</p>`;
  return html`<div class="rec-same" data-same=${item.id}>
    <label class="small" for=${uid}>Which card is the same as this one?</label>
    <select class="input" id=${uid} value=${otherId} disabled=${busy} onChange=${e => setOtherId(e.target.value)}>
      <option value="">Pick a card</option>
      ${others.map(i => html`<option value=${String(i.id)}>${i.title}${i.state === "hidden" ? " (hidden)" : ""}</option>`)}
    </select>
    ${other && html`<fieldset class="rec-same-keep"><legend class="small">Which name should Hal keep?</legend>
      ${[["this", item.title], ["other", other.title]].map(([v, words]) => html`<label class="rec-same-name">
        <input type="radio" name=${uid + "-keep"} value=${v} checked=${keep === v} onChange=${() => setKeep(v)} />${words}</label>`)}
      </fieldset>
      <p class="small sub rec-line">Hal moves the files of the other card to the one you keep. Nothing is lost, and you can undo it.</p>`}
    <div class="rec-check-btns">
      <button type="button" class="btn primary" disabled=${busy || !other} onClick=${go}>Merge them</button>
      <button type="button" class="btn" disabled=${busy} onClick=${onDone}>Cancel</button></div>
  </div>`;
}

/** "Same as..." on a project or job card: a button that opens the picker; nothing for other kinds. */
export function SameAs({ item, busy = false }) {
  const [open, setOpen] = useState(false);
  if (!SAME_KINDS.includes(item.kind)) return null;
  return open ? html`<${Picker} item=${item} onDone=${() => setOpen(false)} />`
    : html`<button type="button" class="btn" disabled=${busy} onClick=${() => setOpen(true)}>Same as...</button>`;
}

/** The cards the person hid that come only from a shown card's own files, each with "Same as" that card,
    which merges the hidden card into it and keeps the shown card's name. */
export function HiddenTwins({ twins }) {
  const [busy, setBusy] = useState(false);
  const rows = twins.flatMap(t => t.hidden.map(h => ({ shown: t.item, hid: h })));
  const same = async r => { setBusy(true); await act(() => merge(r.hid, r.shown), SAID); setBusy(false); };
  return html`<details class="rec-hidden rec-check-twins">
    <summary>Hidden cards from the same files (${rows.length})</summary>
    <p class="small sub rec-line">You hid these, but they come from the same files as a card you can see. If one is the same piece of work, press <b>Same as</b> and Hal keeps one card with the files of both. If not, leave it hidden.</p>
    <ul class="rec-check-merged-list">${rows.map(r => html`<li key=${r.hid.id + "-" + r.shown.id}>
      <span><b>${r.hid.title}</b><span class="small sub"> comes from the same files as ${r.shown.title}</span></span>
      <button type="button" class="btn small" disabled=${busy} aria-label=${"Same as " + r.shown.title + ": " + r.hid.title}
        onClick=${() => same(r)}>Same as ${r.shown.title}</button></li>`)}</ul>
  </details>`;
}
