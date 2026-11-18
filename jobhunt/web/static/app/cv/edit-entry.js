/* One entry of Edit your CV (a job, a qualification, a project...): its fields, then its lines, each line
   with its own Move, Lock and Delete. Every control is named for the entry and line it acts on, so a screen
   reader user hears "Delete line 2 of Ticket clerk", never a list of "Delete". `act(change)` hands each
   change to the editor (cv/edit-tab.js), which shows it at once and saves the list on "Save changes".
   On an application's CV, `marks` (from `tailor.editor`, by `model.diff`) say what Hal changed for the job
   and what the person changed after it, in words beside the part, and `onCopy` adds "Copy to my main CV" (named
   for the CV the application was built from, `copyTo`, when several are in use). */
import { html, useState } from "../core/html.js";
import { Chip, Switch } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";
import { target, blankLine } from "./doc-changes.js";

const YOUR_WORDS = "you";   // `model.YOUR_WORDS`: the person typed or reworded this line
const CARD = "card:";       // `edit.CARD`: the line came from a card of your record
const DATED = ["education", "experience", "volunteering", "research", "teaching"];
const HEADING = { education: "Qualification", experience: "Job title", volunteering: "Role", research: "Role",
  teaching: "Role", projects: "Project name", skills: "Kind of skill", certifications: "Certificate" };
const ORG = { education: "School, college or university", experience: "Employer", volunteering: "Organisation" };
const LISTED = ["skills", "languages", "interests"];

/** The Copy button's words for the CV `label` ("your Museum CV"), as the person says it: "Copy to my Museum CV". */
export const copyWords = label => "Copy to " + label.replace(/^your /, "my ");

/** The fields an entry of a section of `kind` shows: [field, words]. */
function fieldsFor(kind) {
  const out = [["heading", HEADING[kind] || "Heading"]];
  if (DATED.includes(kind)) out.push(["org", ORG[kind] || "Where"], ["start", "From"], ["end", "To"]);
  if (kind === "education") out.push(["grade", "Grade or result"]);
  if (kind === "certifications") out.push(["org", "Given by"], ["start", "Date"], ["grade", "Grade or result"]);
  if (["projects", "portfolio", "publications"].includes(kind)) out.push(["link", "Web address"]);
  if (kind === "projects") out.push(["meta", "Tools"]);
  return out;
}

/** The marks of the part at target `t`; with `fields`, also those of its own fields (not its lines'). */
export function marksAt(marks, t, fields = false) {
  return (marks || []).filter(m => m.target === t
    || (fields && m.target.startsWith(t + "/") && !m.target.slice(t.length + 1).includes(":")));
}

/** What changed at a part for this job, in words: Hal's kind of change, or the person's own edit. */
export function Marks({ list }) {
  if (!list.length) return null;
  const hal = [...new Set(list.filter(m => m.by === "hal").map(m => m.kind))];
  const yours = list.some(m => m.by !== "hal");
  return html`${hal.map(k => html`<${Chip} tone="info">Hal: ${k}<//>`)}${yours && html`<${Chip}>Your edit<//>`}`;
}

/** The class marking a part Hal or the person changed for this job. */
const markClass = list => (list.some(m => m.by !== "hal") ? " cv-mark-you" : list.length ? " cv-mark-hal" : "");

/** Focuses the element with this id once the page has drawn it. */
export function focusSoon(id) {
  requestAnimationFrame(() => { const el = document.getElementById(id); if (el) el.focus(); });
}

/** Up and down buttons for the part at `i` of `n`. */
function UpDown({ i, n, name, onMove }) {
  return html`<span class="updown"><button type="button" aria-label=${"Move " + name + " up"} disabled=${i === 0}
    onClick=${() => onMove(i - 1)}><${Icon} n="chevU" c="s" /></button><button type="button" aria-label=${"Move " + name + " down"}
    disabled=${i === n - 1} onClick=${() => onMove(i + 1)}><${Icon} n="chevD" c="s" /></button></span>`;
}

function Items({ id, label, items, onChange }) {
  const [draft, setDraft] = useState((items || []).join(", "));
  const set = v => { setDraft(v); onChange(v.split(",").map(x => x.trim()).filter(Boolean)); };
  return html`<div class="rec-field wide"><label class="lab" for=${id}>${label} <span class="small sub">(separated by commas)</span></label>
    <input class="input" id=${id} value=${draft} onInput=${e => set(e.target.value)} /></div>`;
}

function Line({ s, e, b, i, n, name, act, addId, marks }) {
  const t = target(s, e, b);
  const here = marksAt(marks, t);
  const lineName = "line " + (i + 1) + " of " + name;
  const mine = (b.sources || []).includes(YOUR_WORDS);
  const fromCard = (b.sources || []).some(x => x.startsWith(CARD));
  return html`<div class=${"cv-line" + markClass(here)}>
    <${UpDown} i=${i} n=${n} name=${lineName} onMove=${p => act({ op: "move", target: t, position: p })} />
    <div class="grow"><textarea class="input" rows="2" id=${"f-" + t} aria-label=${"Line " + (i + 1) + " of " + name} value=${b.text}
      onInput=${ev => act({ op: "replace", target: t + "/text", value: ev.target.value })}></textarea>
      ${(mine || fromCard || here.length > 0) && html`<div class="row tight"><${Marks} list=${here} />${mine
        ? html`<${Chip}>Your words<//>` : fromCard && html`<${Chip} tone="why">From your record<//>`}</div>`}</div>
    <span class="row tight"><${Switch} on=${!!b.locked} label=${"Lock " + lineName} onChange=${v => act({ op: "replace", target: t + "/locked", value: v })} /><${Icon} n="lock" c="s" /></span>
    <button type="button" class="iconbtn" aria-label=${"Delete " + lineName}
      onClick=${() => { act({ op: "delete", target: t }); focusSoon(addId); }}><${Icon} n="trash" c="s" /></button></div>`;
}

/** The card of one entry: `s` its section, `i` its place of `n`, `act` the editor's change handler; on an
    application's CV, `marks` and `onCopy(s, e)`. */
export function EntryCard({ s, e, i, n, act, addId, marks, onCopy, copyTo = "your main CV" }) {
  const t = target(s, e);
  const here = marksAt(marks, t, true);
  const name = e.heading || "the new entry";
  const lineAdd = "add-" + t;
  const addLine = () => { const b = blankLine(); act({ op: "insert", target: t, value: b }); focusSoon("f-" + target(s, e, b)); };
  return html`<div class=${"cv-entry" + markClass(here)} role="group" aria-label=${e.heading || "New entry"}>
    <div class="row cv-entry-head"><${UpDown} i=${i} n=${n} name=${name} onMove=${p => act({ op: "move", target: t, position: p })} />
      <b class="grow">${e.heading || "New entry"}</b><${Marks} list=${here} />${e.hidden && html`<${Chip} tone="warn">Hidden<//>`}
      <span class="row tight"><${Switch} on=${!e.hidden} label=${"Show " + name} onChange=${v => act({ op: v ? "show" : "hide", target: t })} /><span class="small sub">Show</span></span>
      <span class="row tight"><${Switch} on=${!!e.locked} label=${"Lock " + name} onChange=${v => act({ op: "replace", target: t + "/locked", value: v })} /><${Icon} n="lock" c="s" /></span>
      <button type="button" class="iconbtn" aria-label=${"Delete " + name} onClick=${() => { act({ op: "delete", target: t }); focusSoon(addId); }}><${Icon} n="trash" c="s" /></button></div>
    ${onCopy && html`<div class="row"><button type="button" class="btn quiet small" aria-label=${copyWords(copyTo) + ": " + name}
      onClick=${() => onCopy(s, e)}><${Icon} n="copy" c="s" />${copyWords(copyTo)}</button></div>`}
    <div class="rec-form">${fieldsFor(s.kind).map(([f, words]) => html`<div class="rec-field" key=${f}>
      <label class="lab" for=${"f-" + t + "/" + f}>${words}</label><input class="input" id=${"f-" + t + "/" + f} value=${e[f] || ""}
        onInput=${ev => act({ op: "replace", target: t + "/" + f, value: ev.target.value })} /></div>`)}
      ${(LISTED.includes(s.kind) || (e.items || []).length > 0) && html`<${Items} id=${"f-" + t + "/items"} label="Items" items=${e.items}
        onChange=${v => act({ op: "replace", target: t + "/items", value: v })} />`}
      ${e.style === "paragraph" && html`<div class="rec-field wide"><label class="lab" for=${"f-" + t + "/text"}>What it says</label>
        <textarea class="input" id=${"f-" + t + "/text"} value=${e.text} onInput=${ev => act({ op: "replace", target: t + "/text", value: ev.target.value })}></textarea></div>`}</div>
    ${(e.modules || []).length > 0 && html`<p class="small sub">${e.modules.length} modules, as they are now.</p>`}
    ${e.style !== "paragraph" && (!LISTED.includes(s.kind) || e.bullets.length > 0) && html`<div class="cv-lines">${e.bullets.map((b, k) => html`<${Line} key=${b.id} s=${s} e=${e} b=${b} i=${k}
      n=${e.bullets.length} name=${name} act=${act} addId=${lineAdd} marks=${marks} />`)}
      <button type="button" class="btn quiet small" id=${lineAdd} onClick=${addLine}><${Icon} n="plus" c="s" />Add a line</button></div>`}</div>`;
}
