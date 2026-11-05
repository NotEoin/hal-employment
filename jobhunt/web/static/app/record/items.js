/* Your record, the profile tabs: one card per item with its fields, its state, the files it came from and the
   person's choices (Confirm, Edit, Hide, Delete), and the Inconsistencies tab.

   Every change is the server's (`jobhunt/profile/routes.py`), which publishes a `profile` event; the event
   makes /profile* stale (core/events.js), so every tab showing the item reads it again. A conflict is shown
   once, on the first item it names, with only the choices the server says it takes, because a gap between
   two jobs has no "right value" to pick. Hidden items stay listed, folded away at the end, so a person can
   bring one back. */
import { html, useState } from "../core/html.js";
import { post, put, del } from "../core/api.js";
import { useApi, toast, invalidate } from "../core/store.js";
import { Chip, Empty } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";
import { KIND_FIELDS, KIND_WORDS, ItemForm, basicKey, shown } from "./fields.js";

const STATE_TONE = { hal: "warn", confirmed: "good", edited: "info", hidden: "line" };
const FILE_PATH = "/api/v2/record/file/";

function refresh() { invalidate("/profile*"); }

async function act(fn, said) {
  try { const out = await fn(); toast(said); refresh(); return out; }
  catch (err) { toast(err.message); return null; }
}

const fileName = rel => rel.split("/").pop().replace(/\.md$/, "");

/** "From:" the files an item came from, each with its quote and a mark when the quote is not in the file. */
export function Sources({ evidence }) {
  if (!evidence.length) return null;
  return html`<div class="rec-from"><div class="small sub"><b>From:</b></div><ul class="rec-sources">
    ${evidence.map(e => html`<li key=${e.id}>
      <a href=${FILE_PATH + e.file_id} target="_blank" rel="noopener">${fileName(e.source_rel)}</a>
      ${!e.verified && html` <${Chip} tone="warn">Quote not found in the file<//>`}
      ${e.quote && html`<div class="quote">"${e.quote}"</div>`}</li>`)}</ul></div>`;
}

function Value({ type, v }) {
  const s = shown(type, v);
  if (type === "list") return html`<ul class="rec-list">${s.map((x, i) => html`<li key=${i}>${x}</li>`)}</ul>`;
  if (type === "modules") return html`<ul class="rec-list">${s.map((m, i) => html`<li key=${i}>${m.name}${m.mark ? ": " + m.mark : ""}</li>`)}</ul>`;
  return s;
}

/** An item's fields as a list of words and values; nothing when it has none. */
export function Fields({ item }) {
  const rows = KIND_FIELDS[item.kind].filter(([f, , type]) => shown(type, item.fields[f]) !== "");
  if (!rows.length) return null;
  return html`<dl class="kv rec-kv">${rows.map(([f, words, type]) => html`<dt>${words}</dt><dd>
    ${item.kind === "basic" && f === "key" ? basicKey(item.fields[f]) : html`<${Value} type=${type} v=${item.fields[f]} />`}</dd>`)}</dl>`;
}

const keepAllWords = c => (c.kind === "gap" ? "The gap is right" : c.kind === "overlap" ? "Both are right" : "Keep it as it is");

/** One conflict: why it is one, each value with the files that give it, and the choices it takes. */
export function Conflict({ c, names = {} }) {
  const [busy, setBusy] = useState(false);
  const resolve = async (body, said) => {
    setBusy(true);
    await act(() => post("/profile/conflicts/" + encodeURIComponent(c.id) + "/resolve", body), said);
    setBusy(false);
  };
  const items = c.items || c.item_ids.map(id => names[id]).filter(Boolean);
  const open = c.state === "open";
  return html`<div class="rec-conflict" data-conflict=${c.id}>
    <p class="rec-why"><${Icon} n="warn" c="s" /> <span>${c.why}</span></p>
    ${c.values.length > 0 && html`<ul class="rec-values">${c.values.map(v => html`<li key=${v.value}>
      <b>${v.words || v.value}</b>${v.sources.map(s => html`<div class="small sub">in ${fileName(s.source_rel)}${s.quote ? html`: "${s.quote}"` : ""}</div>`)}
      ${open && c.choices.includes("value") && html`<div><button type="button" class="btn small" disabled=${busy}
        onClick=${() => resolve({ value: v.value }, "Saved. Hal uses " + (v.words || v.value) + ".")}>Use ${v.words || v.value}</button></div>`}</li>`)}</ul>`}
    ${open && html`<div class="row">
      ${c.choices.includes("keep_item") && items.map(it => html`<button type="button" class="btn small" disabled=${busy}
        onClick=${() => resolve({ keep_item: it.id }, "Kept " + it.title + ". Hal hid the other.")}>Keep ${it.title}${it.when ? " (" + it.when + ")" : ""}</button>`)}
      ${c.choices.includes("keep_all") && html`<button type="button" class="btn small" disabled=${busy}
        onClick=${() => resolve({ keep_all: true }, "Saved. Hal leaves it as it is.")}>${keepAllWords(c)}</button>`}</div>`}
    ${!open && c.resolution && html`<p class="small sub">${c.resolution.value != null ? "You chose " + c.resolution.value + "."
      : c.resolution.keep_item != null ? "You kept one and Hal hid the other." : "You said it is right as it is."}</p>`}</div>`;
}

/** One item's card: title, state, fields or the form, sources, conflicts and buttons. */
function ItemCard({ item, conflicts, names }) {
  const [editing, setEditing] = useState(false);
  const id = encodeURIComponent(item.id);
  const what = item.title || KIND_WORDS[item.kind];
  const save = async fields => {
    const out = await act(() => put("/profile/items/" + id, { fields }), "Saved your changes.");
    if (out) setEditing(false);
  };
  const hidden = item.state === "hidden";
  return html`<article class="card rec-item" data-item=${item.id} aria-label=${what}>
    <div class="rec-head"><div class="grow">
        <h3 class="h3">${item.kind === "basic" ? basicKey(item.title) : item.title}</h3>
        ${(item.sub || item.when) && html`<div class="small sub">${[item.kind === "basic" ? "" : item.sub, item.when].filter(Boolean).join(", ")}</div>`}</div>
      <${Chip} tone=${STATE_TONE[item.state]}>${item.state_words}<//></div>
    ${editing ? html`<${ItemForm} kind=${item.kind} item=${item} uid=${"it" + item.id} onSave=${save} onCancel=${() => setEditing(false)} />`
      : html`<${Fields} item=${item} />`}
    <${Sources} evidence=${item.evidence} />
    ${conflicts.map(c => html`<${Conflict} key=${c.id} c=${c} names=${names} />`)}
    ${!editing && html`<div class="row rec-acts">
      ${(item.state === "hal" || hidden) && html`<button type="button" class="btn good small" aria-label=${(hidden ? "Show again: " : "Confirm: ") + what}
        onClick=${() => act(() => post("/profile/items/" + id + "/confirm"), hidden ? "Hal uses it again." : "Marked as checked.")}>
        <${Icon} n="check" c="s" />${hidden ? "Show again" : "Confirm"}</button>`}
      <button type="button" class="btn small" aria-label=${"Edit: " + what} onClick=${() => setEditing(true)}><${Icon} n="edit" c="s" />Edit</button>
      ${!hidden && html`<button type="button" class="btn quiet small" aria-label=${"Hide: " + what}
        onClick=${() => act(() => post("/profile/items/" + id + "/hide"), "Hidden. Hal leaves it out of your documents.")}>
        <${Icon} n="eye" c="s" />Hide</button>`}
      ${item.can_delete && html`<button type="button" class="btn danger small" aria-label=${"Delete: " + what}
        onClick=${() => act(() => del("/profile/items/" + id), "Deleted.")}><${Icon} n="trash" c="s" />Delete</button>`}</div>`}
  </article>`;
}

function AddItem({ kind }) {
  const [open, setOpen] = useState(false);
  const words = KIND_WORDS[kind];
  const save = async fields => {
    const out = await act(() => post("/profile/items", { kind, fields }), "Added the " + words + ".");
    if (out) setOpen(false);
  };
  if (!open) return html`<div><button type="button" class="btn small" onClick=${() => setOpen(true)}><${Icon} n="plus" c="s" />Add a ${words}</button></div>`;
  return html`<section class="card rec-item" aria-label=${"Add a " + words}><h3 class="h3">Add a ${words}</h3>
    <${ItemForm} kind=${kind} uid=${"new-" + kind} onSave=${save} onCancel=${() => setOpen(false)} /></section>`;
}

/** Every item of one kind, its conflicts, "Add a ..." and the hidden items folded away. */
export function KindSection({ kind, heading }) {
  const { data, error } = useApi("/profile/" + kind);
  if (error) return html`<p class="sub">${error.message}</p>`;
  if (!data) return html`<p class="sub">Loading</p>`;
  const names = Object.fromEntries(data.items.map(i => [i.id, i]));
  const shownIds = new Set(data.items.filter(i => i.state !== "hidden").map(i => i.id));
  const onItem = it => data.conflicts.filter(c => c.item_ids.find(x => shownIds.has(x)) === it.id);
  const loose = data.conflicts.filter(c => !c.item_ids.some(x => shownIds.has(x)));
  const visible = data.items.filter(i => i.state !== "hidden");
  const hidden = data.items.filter(i => i.state === "hidden");
  return html`<section class="stack s rec-kind" data-kind=${kind} aria-label=${data.label}>
    ${heading && html`<h2 class="h2">${data.label}</h2>`}
    ${loose.map(c => html`<div class="card"><${Conflict} key=${c.id} c=${c} names=${names} /></div>`)}
    ${visible.length ? visible.map(it => html`<${ItemCard} key=${it.id + ":" + it.updated} item=${it} conflicts=${onItem(it)} names=${names} />`)
      : html`<${Empty}>Nothing here yet. Add your files on the Overview tab and Hal fills this in, or add one yourself.<//>`}
    <${AddItem} kind=${kind} />
    ${hidden.length > 0 && html`<details class="rec-hidden"><summary>Hidden (${hidden.length})</summary>
      <div class="stack s">${hidden.map(it => html`<${ItemCard} key=${it.id + ":" + it.updated} item=${it} conflicts=${[]} names=${names} />`)}</div></details>`}
  </section>`;
}

/** The Inconsistencies tab: every open conflict with its choices, and the settled ones folded away. */
export function ConflictsTab({ profile }) {
  const open = profile.conflicts || [];
  const done = profile.resolved || [];
  return html`<div class="stack s">
    <p class="small sub">Where your files disagree, or your dates do not add up. Choose what is right; Hal uses your choice from then on.</p>
    ${open.length ? open.map(c => html`<section class="card" key=${c.id} aria-label=${"About " + (c.items || []).map(i => i.title).join(" and ")}>
        <h2 class="h3">${(c.items || []).map(i => i.title).join(" and ") || "Your profile"}</h2><${Conflict} c=${c} /></section>`)
      : html`<${Empty}>Nothing to sort out. Your files agree with each other.<//>`}
    ${done.length > 0 && html`<details class="rec-hidden"><summary>Settled (${done.length})</summary>
      <div class="stack s">${done.map(c => html`<section class="card" key=${c.id}>
        <h2 class="h3">${(c.items || []).map(i => i.title).join(" and ") || "Your profile"}</h2><${Conflict} c=${c} /></section>`)}</div></details>`}
  </div>`;
}
