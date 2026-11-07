/* Check your record, "Your best experiences": the jobs, courses, projects, achievements and certificates Hal
   would tell an employer first (`check.best` on the server), each with why, where it was done (a plain
   choice the person can change), and Confirm, Edit and Move to archive. The archived ones are folded away
   below, each with Move up a tier.

   Every choice goes to the profile routes, which publish a `profile` event, so the list is read again and a
   moved card leaves it at once. Moving a card sets the person's own tier, which Hal never changes. */
import { html, useState } from "../core/html.js";
import { post, put } from "../core/api.js";
import { toast } from "../core/store.js";
import { Icon } from "../ui/icons.js";
import { ItemForm } from "./fields.js";
import { KIND_NAME, act } from "./check.js";

const CHECKED = ["confirmed", "edited"];
// Where an experience was done, as `profile.contexts` keeps it, in the person's words.
const CONTEXTS = [["degree", "Part of my degree"], ["team", "University team project"], ["work", "At work"],
  ["personal", "In my own time"]];

const tierPath = item => "/profile/items/" + encodeURIComponent(item.id) + "/tier";

/** Where the experience was done: one plain choice, which Hal keeps to when it writes and never changes. */
function WhereDone({ item, busy, choose }) {
  const id = "best-ctx-" + item.id;
  const pick = value => choose(() => put("/profile/items/" + encodeURIComponent(item.id) + "/context", { context: value }),
    "Saved. Hal will describe it this way.");
  return html`<div class="rec-best-where"><label class="small" for=${id}>Where was this done?</label>
    <select class="input" id=${id} value=${item.context || ""} disabled=${busy} onChange=${e => pick(e.target.value)}>
      ${!item.context && html`<option value="">Not sure yet</option>`}
      ${CONTEXTS.map(([v, w]) => html`<option value=${v}>${w}</option>`)}</select></div>`;
}

/** One best experience: what it is, why Hal puts it here, and the three choices. */
function BestCard({ item }) {
  const [editing, setEditing] = useState(false);
  const [busy, setBusy] = useState(false);
  const id = encodeURIComponent(item.id);
  const choose = async (fn, said) => { setBusy(true); const ok = await act(fn, said); setBusy(false); return ok; };
  const save = async fields => {
    if (await choose(() => put("/profile/items/" + id, { fields }), "Saved. Hal uses your words.")) setEditing(false);
  };
  const hid = "best-" + item.id;
  return html`<li class="rec-best-item" data-best=${item.id} aria-labelledby=${hid}>
    <div class="eyebrow">${KIND_NAME[item.kind]}, ${item.tier_words.toLowerCase()}</div>
    <h3 class="h3 rec-line" id=${hid}>${item.title}</h3>
    ${(item.sub || item.when) && html`<p class="sub rec-line">${[item.sub, item.when].filter(Boolean).join(", ")}</p>`}
    ${item.tier_why && html`<p class="small rec-line rec-best-why">${item.tier_why}</p>`}
    <${WhereDone} item=${item} busy=${busy} choose=${choose} />
    ${editing ? html`<${ItemForm} kind=${item.kind} item=${item} uid=${"best" + item.id} onSave=${save} onCancel=${() => setEditing(false)} />`
      : html`<div class="rec-check-btns">
        ${CHECKED.includes(item.state) ? html`<span class="small rec-best-done"><${Icon} n="check" c="s" />${item.state_words}</span>`
          : html`<button type="button" class="btn primary" disabled=${busy}
            onClick=${() => choose(() => post("/profile/items/" + id + "/confirm"), "Thanks. Hal can use this now.")}>
            <${Icon} n="check" c="s" />Confirm</button>`}
        <button type="button" class="btn" disabled=${busy} onClick=${() => setEditing(true)}><${Icon} n="edit" c="s" />Edit</button>
        <button type="button" class="btn" disabled=${busy}
          onClick=${() => choose(() => put(tierPath(item), { tier: "archive" }), "Moved to the archive. Hal will not write about it.")}>
          Move to archive</button></div>`}
  </li>`;
}

/** The archived experiences, folded away, each with Move up a tier. */
function Archived({ items }) {
  const [busy, setBusy] = useState(false);
  const up = async item => {
    setBusy(true);
    await act(() => put(tierPath(item), { tier: "supporting" }), "Moved up. Hal can use it when a job asks for it.");
    setBusy(false);
  };
  return html`<details class="rec-hidden rec-best-archive">
    <summary>Archived (${items.length})</summary>
    <p class="small sub rec-line">Hal never uses these when it writes. Move one up if an employer should hear about it.</p>
    <ul class="rec-best-archived">${items.map(it => html`<li key=${it.id}>
      <span><b>${it.title}</b>${it.tier_why && html`<span class="small sub"> ${it.tier_why}</span>`}</span>
      <button type="button" class="btn small" disabled=${busy} aria-label=${"Move up a tier: " + it.title} onClick=${() => up(it)}>Move up a tier</button></li>`)}</ul>
  </details>`;
}

/** "Your best experiences": the ranked cards, the archive folded away, and a button to sort them again. */
export function BestExperiences({ best, archived }) {
  const [busy, setBusy] = useState(false);
  const sortAgain = async () => {
    setBusy(true);
    try { await post("/profile/tiers"); toast("Hal is sorting your experiences. This list updates when it is done."); }
    catch (err) { toast(err.message); }
    setBusy(false);
  };
  return html`<section class="card rec-best" aria-labelledby="best-h">
    <div class="rec-head"><h2 class="h2 grow" id="best-h">Your best experiences</h2>
      <button type="button" class="btn small" disabled=${busy} onClick=${sortAgain}>Sort them again</button></div>
    <p class="sub rec-line">These are what Hal would tell an employer about first. Check each one: confirm it, fix it, or move it to the archive if it is not worth mentioning.</p>
    ${best.length ? html`<ol class="rec-best-list">${best.map(it => html`<${BestCard} key=${it.id} item=${it} />`)}</ol>`
      : html`<p class="small sub rec-line">Nothing here yet. Hal sorts your experiences after it reads your files.</p>`}
    ${archived.length > 0 && html`<${Archived} items=${archived} />`}
  </section>`;
}
