/* Check your record, the cards: one fact with "Yes, that's right", "Fix it" and "Not me", a group of skills
   from one file with a tick box each, and a "Which is right?" conflict.

   Every choice goes to the profile routes (`jobhunt/profile/routes.py`), which publish a `profile` event; the
   page then reads the queue again and shows the next card, so nothing is kept here between visits. A skills
   card keeps the ticked skills and hides the unticked ones, because a skill left as it is would only come
   back in a smaller group. */
import { html, useState } from "../core/html.js";
import { post, put } from "../core/api.js";
import { toast, invalidate } from "../core/store.js";
import { Icon } from "../ui/icons.js";
import { ItemForm, basicKey } from "./fields.js";
import { Sources, Fields, Conflict } from "./items.js";

// What each kind of fact is called above its card.
export const KIND_NAME = { job: "A job", education: "Education", certificate: "A certificate", achievement: "An achievement",
  project: "A project", link: "A link", skill: "A skill" };

/** Runs one change, says `said` and reads the profile again; true when it worked, else the error is shown. */
export async function act(fn, said) {
  try { await fn(); toast(said); invalidate("/profile*"); return true; }
  catch (err) { toast(err.message); return false; }
}

/** The source to show for each item of a card: the one the server ranked best, else the first. */
const bestOf = (card, k) => {
  const ev = card.items[k].evidence;
  return ev.find(e => e.id === card.best[k]) || ev[0];
};

/** A card's heading; the page focuses it when the card replaces the one just answered. */
function Heading({ id, sub, children }) {
  return sub ? html`<h3 class="h3 rec-check-h" id=${id}>${children}</h3>`
    : html`<h2 class="h2 rec-check-h" id=${id} tabindex="-1">${children}</h2>`;
}

/** One fact: what it says, the quote it came from, and the three choices. */
export function ItemCheck({ card }) {
  const item = card.items[0];
  const [fixing, setFixing] = useState(false);
  const [busy, setBusy] = useState(false);
  const id = encodeURIComponent(item.id);
  const basic = item.kind === "basic";
  const choose = async (fn, said) => { setBusy(true); const ok = await act(fn, said); setBusy(false); return ok; };
  const save = async fields => {
    if (await choose(() => put("/profile/items/" + id, { fields }), "Saved. Hal uses your words.")) setFixing(false);
  };
  const best = bestOf(card, 0);
  return html`<section class="card rec-check" data-item=${item.id} aria-labelledby=${"chk-" + item.id}>
    <div class="eyebrow">${basic ? basicKey(item.fields.key) : KIND_NAME[item.kind]}</div>
    <${Heading} id=${"chk-" + item.id}>${basic ? item.fields.value : item.title}<//>
    ${!basic && (item.sub || item.when) && html`<p class="sub rec-line">${[item.sub, item.when].filter(Boolean).join(", ")}</p>`}
    ${fixing ? html`<${ItemForm} kind=${item.kind} item=${item} uid=${"chk" + item.id} onSave=${save} onCancel=${() => setFixing(false)} />`
      : html`${!basic && html`<${Fields} item=${item} />`}
        ${best && html`<${Sources} evidence=${[best]} />`}
        <p class="small sub rec-line">Is this right about you?</p>
        <div class="rec-check-btns">
          <button type="button" class="btn primary" disabled=${busy}
            onClick=${() => choose(() => post("/profile/items/" + id + "/confirm"), "Thanks. Hal can use this now.")}>
            <${Icon} n="check" c="s" />Yes, that's right</button>
          <button type="button" class="btn" disabled=${busy} onClick=${() => setFixing(true)}><${Icon} n="edit" c="s" />Fix it</button>
          <button type="button" class="btn" disabled=${busy}
            onClick=${() => choose(() => post("/profile/items/" + id + "/hide"), "Hidden. Hal will not use it.")}>
            <${Icon} n="eye" c="s" />Not me</button></div>`}
  </section>`;
}

const FILE_PATH = "/api/v2/record/file/";
const fileName = rel => rel.split("/").pop().replace(/\.md$/, "");

/** A group of skills from one file, each ticked, with "Keep these" and "None of these". */
export function SkillsCheck({ card, sub = false }) {
  const [off, setOff] = useState(() => new Set());
  const [busy, setBusy] = useState(false);
  const ids = card.items.map(i => i.id);
  const kept = ids.filter(i => !off.has(i));
  const dropped = ids.filter(i => off.has(i));
  const flip = i => setOff(s => { const n = new Set(s); if (n.has(i)) n.delete(i); else n.add(i); return n; });
  const keep = async () => {
    setBusy(true);
    await act(async () => {
      if (kept.length) await post("/profile/confirm-many", { ids: kept });
      if (dropped.length) await post("/profile/hide-many", { ids: dropped });
    }, kept.length ? "Kept. Hal can use these skills now." : "Hidden. Hal will not use them.");
    setBusy(false);
  };
  const none = async () => {
    setBusy(true);
    await act(() => post("/profile/hide-many", { ids }), "Hidden. Hal will not use them.");
    setBusy(false);
  };
  const hid = "chk-" + card.key;
  const where = card.source_rel ? fileName(card.source_rel) : "your files";
  return html`<section class="card rec-check" data-group=${card.key} aria-labelledby=${hid}>
    <div class="eyebrow">Skills</div>
    <${Heading} id=${hid} sub=${sub}>Skills from ${where}<//>
    ${card.file_id && html`<p class="small sub rec-line">Hal found these in <a href=${FILE_PATH + card.file_id} target="_blank" rel="noopener">${where}</a>. Untick any that are not yours.</p>`}
    <ul class="rec-check-skills">${card.items.map((it, k) => {
      const best = bestOf(card, k);
      return html`<li key=${it.id}><label class="rec-check-skill"><input type="checkbox" checked=${!off.has(it.id)} onChange=${() => flip(it.id)} />
        <span><b>${it.title}</b>${best && best.quote && html`<span class="small sub"> "${best.quote}"</span>`}</span></label></li>`;
    })}</ul>
    <div class="rec-check-btns">
      <button type="button" class="btn primary" disabled=${busy} onClick=${keep}><${Icon} n="check" c="s" />${kept.length === ids.length ? "Keep these" : "Keep the ticked ones"}</button>
      <button type="button" class="btn" disabled=${busy} onClick=${none}>None of these</button></div>
  </section>`;
}

/** The skills only course material names, kept apart: Hal never writes with them unless the person says they
    used one in their own work. */
export function StudiedSkills({ items }) {
  const [busy, setBusy] = useState(false);
  const claim = async it => {
    setBusy(true);
    await act(() => post("/profile/items/" + encodeURIComponent(it.id) + "/confirm"), "Thanks. Hal can use " + it.title + " now.");
    setBusy(false);
  };
  return html`<details class="rec-hidden rec-check-studied">
    <summary>Studied in your course (${items.length} ${items.length === 1 ? "skill" : "skills"})</summary>
    <p class="small sub rec-line">Hal found these only in your course material, such as lecture notes and slides. They show what you studied, so Hal leaves them off your CV and out of your letters. If you have used one in your own work, press <b>I have used this</b>.</p>
    <ul class="rec-check-studied-list">${items.map(it => html`<li key=${it.id}>
      <span>${it.title}</span>
      <button type="button" class="btn small" disabled=${busy} aria-label=${"I have used this: " + it.title} onClick=${() => claim(it)}>I have used this</button></li>`)}</ul>
  </details>`;
}

/** One open conflict, asked as "Which is right?" with the choices it takes. */
export function ConflictCheck({ c }) {
  const about = (c.items || []).map(i => i.title).join(" and ");
  return html`<section class="card rec-check" data-conflict-card=${c.id} aria-labelledby=${"chk-c" + c.id}>
    <div class="eyebrow">${about || "Your record"}</div>
    <${Heading} id=${"chk-c" + c.id}>Which is right?<//>
    <${Conflict} c=${c} /></section>`;
}
