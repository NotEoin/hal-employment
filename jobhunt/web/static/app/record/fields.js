/* Your record, the fields of each kind of profile item: what they are called on screen, how they are shown,
   and the form that edits them or adds a new item by hand.

   The kinds and fields are the server's (`jobhunt/profile/store.py` KINDS); a field the server does not know
   would be refused, so this list follows it. Dates are typed as words ("May 2024", "Sept 2023", "2021") and
   the server works them out, so the form shows a stored "2024-05" as "May 2024". Lists are one item a line,
   which reads the same for a duty, a tool or a method. */
import { html, useState } from "../core/html.js";

const MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October",
  "November", "December"];
const DATE_HINT = "For example May 2024";

// [field, words, type]; type is text, long, date, flag, list, hours or modules
export const KIND_FIELDS = {
  job: [["title", "Job title", "text"], ["employer", "Employer", "text"], ["location", "Place", "text"],
    ["start", "Started", "date"], ["end", "Ended", "date"], ["current", "I still do this job", "flag"],
    ["hours", "Hours", "hours"], ["duties", "What you did", "list"], ["achievements", "What you achieved", "list"]],
  education: [["qualification", "Qualification", "text"], ["subject", "Subject", "text"],
    ["institution", "School, college or university", "text"], ["start", "Started", "date"], ["end", "Finished", "date"],
    ["current", "I still study here", "flag"], ["grade", "Grade or result", "text"], ["modules", "Modules and marks", "modules"]],
  skill: [["name", "Skill", "text"], ["level", "How good you are", "text"], ["category", "Kind of skill", "text"]],
  project: [["name", "Name", "text"], ["summary", "What it was", "long"], ["role", "Your part in it", "text"],
    ["tools", "Tools, methods or materials", "list"], ["link", "Web address", "text"], ["period", "When", "text"]],
  achievement: [["title", "Achievement", "text"], ["date", "Date", "date"], ["issuer", "Given by", "text"],
    ["detail", "What it was for", "long"]],
  certificate: [["name", "Certificate", "text"], ["issuer", "Given by", "text"], ["date", "Date", "date"],
    ["expires", "Runs out", "date"], ["grade", "Grade or result", "text"]],
  basic: [["key", "What it is", "text"], ["value", "Your answer", "text"]],
  link: [["label", "Name", "text"], ["url", "Web address", "text"]],
};

/** What each kind is called in "Add a ..." and in a card's buttons. */
export const KIND_WORDS = { job: "job", education: "qualification", skill: "skill", project: "project",
  achievement: "achievement", certificate: "certificate", basic: "detail about you", link: "link" };

const HOURS = [["", "Not said"], ["full_time", "Full time"], ["part_time", "Part time"]];
const BASIC_KEYS = { name: "Name", email: "Email", phone: "Phone", location: "Where you live" };

/** "2024-05" as "May 2024"; anything else as it is. */
export function dateWords(v) {
  const m = /^((?:19|20)\d\d)-(\d\d)$/.exec(v || "");
  const month = m ? Number(m[2]) : 0;
  return month >= 1 && month <= MONTHS.length ? MONTHS[month - 1] + " " + m[1] : v || "";
}

/** The words for a basic's key ("phone" is "Phone"). */
export const basicKey = k => BASIC_KEYS[k] || k;

/** One field's value in words, or "" when it is empty. */
export function shown(type, v) {
  if (v == null || v === "" || v === false) return "";
  if (type === "date") return dateWords(v);
  if (type === "flag") return "Yes";
  if (type === "hours") return (HOURS.find(h => h[0] === v) || [v, v])[1];
  if (type === "list") return v.length ? v : "";
  if (type === "modules") return v.length ? v : "";
  return String(v);
}

function formValues(kind, fields) {
  const out = {};
  for (const [f, , type] of KIND_FIELDS[kind]) {
    const v = fields[f];
    if (type === "flag") out[f] = !!v;
    else if (type === "list") out[f] = (v || []).join("\n");
    else if (type === "modules") out[f] = (v || []).map(m => ({ name: m.name || "", mark: m.mark || "" }));
    else if (type === "date") out[f] = dateWords(v);
    else out[f] = v == null ? "" : String(v);
  }
  return out;
}

/** The values of the form as the server takes them; `keepEmpty` sends "" and [] so a cleared field is cleared. */
function toFields(kind, values, keepEmpty) {
  const out = {};
  for (const [f, , type] of KIND_FIELDS[kind]) {
    let v = values[f];
    if (type === "list") v = v.split("\n").map(s => s.trim()).filter(Boolean);
    else if (type === "modules") v = v.filter(m => m.name.trim()).map(m => ({ name: m.name.trim(), mark: m.mark.trim() }));
    else if (type !== "flag") v = v.trim();
    const empty = v === "" || v === false || (Array.isArray(v) && !v.length);
    if (!empty || keepEmpty) out[f] = v;
  }
  return out;
}

function Modules({ id, value, onChange }) {
  const set = (i, patch) => onChange(value.map((m, k) => (k === i ? { ...m, ...patch } : m)));
  return html`<div class="stack s" id=${id}>
    ${value.map((m, i) => html`<div class="rec-module" key=${i}>
      <input class="input" value=${m.name} aria-label=${"Module " + (i + 1) + " name"} onInput=${e => set(i, { name: e.target.value })} />
      <input class="input" value=${m.mark} aria-label=${"Module " + (i + 1) + " mark"} onInput=${e => set(i, { mark: e.target.value })} />
      <button type="button" class="btn quiet small" aria-label=${"Remove module " + (i + 1)}
        onClick=${() => onChange(value.filter((_, k) => k !== i))}>Remove</button></div>`)}
    <div><button type="button" class="btn quiet small" onClick=${() => onChange([...value, { name: "", mark: "" }])}>Add a module</button></div></div>`;
}

function Field({ uid, f, words, type, value, onChange }) {
  const id = uid + "-" + f;
  if (type === "flag") {
    return html`<label class="rec-flag" for=${id}><input type="checkbox" id=${id} checked=${value}
      onChange=${e => onChange(e.target.checked)} />${words}</label>`;
  }
  const lab = html`<label class="lab" for=${id}>${words}${type === "list" ? html` <span class="small sub">(one a line)</span>` : ""}</label>`;
  if (type === "modules") return html`<div class="rec-field wide"><span class="lab" id=${id + "-l"}>${words}</span>
    <div role="group" aria-labelledby=${id + "-l"}><${Modules} id=${id} value=${value} onChange=${onChange} /></div></div>`;
  if (type === "hours") return html`<div class="rec-field">${lab}<select class="input" id=${id} value=${value}
    onChange=${e => onChange(e.target.value)}>${HOURS.map(([v, w]) => html`<option value=${v}>${w}</option>`)}</select></div>`;
  if (type === "list" || type === "long") return html`<div class="rec-field wide">${lab}<textarea class="input" id=${id}
    value=${value} onInput=${e => onChange(e.target.value)}></textarea></div>`;
  return html`<div class="rec-field">${lab}<input class="input" id=${id} value=${value}
    placeholder=${type === "date" ? DATE_HINT : ""} onInput=${e => onChange(e.target.value)} /></div>`;
}

/** The form for one item: `item` to edit, or none to add a new one. `onSave(fields)` gets what the server takes. */
export function ItemForm({ kind, item, uid, onSave, onCancel }) {
  const [values, setValues] = useState(() => formValues(kind, (item && item.fields) || {}));
  const [busy, setBusy] = useState(false);
  const save = async e => {
    e.preventDefault();
    setBusy(true);
    try { await onSave(toFields(kind, values, !!item)); } finally { setBusy(false); }
  };
  return html`<form class="rec-form" onSubmit=${save}>
    ${KIND_FIELDS[kind].map(([f, words, type]) => html`<${Field} key=${f} uid=${uid} f=${f} words=${words} type=${type}
      value=${values[f]} onChange=${v => setValues(s => ({ ...s, [f]: v }))} />`)}
    <div class="row rec-btns"><button type="submit" class="btn primary small" disabled=${busy}>Save</button>
      <button type="button" class="btn quiet small" onClick=${onCancel}>Cancel</button></div></form>`;
}
