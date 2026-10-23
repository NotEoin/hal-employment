/* The control for one setting, for every kind the registry knows.

   SettingCtl draws the control and calls onChange(value) with what the person chose; it never talks to the
   server itself, so the Settings page and the setup guide can save, show errors and offer Restart or
   Re-check in one place. Typed values (money, times, hours, tags) go to the server as typed: the server
   parses them and answers with its own words ("Hal reads this as 24,000 pounds a year"), so the page and
   the command line can never read a value differently. Text boxes save when the person leaves them or
   presses Enter, not on every key, so one change is one history row. */
import { html, cx, useState, useEffect } from "../core/html.js";
import { go } from "../core/router.js";
import { openDialog } from "../core/store.js";
import { Switch, Seg } from "./controls.js";
import { Icon } from "./icons.js";
import { copyText } from "./clipboard.js";

export const STORED_KINDS = new Set(["text", "email", "phone", "url", "int", "bool", "choice", "seg", "multi", "tags",
  "ordered_tags", "textlist", "money", "range", "times", "hours", "date", "path"]);
const INPUT_TYPES = { email: "email", phone: "tel", url: "url", date: "date" };
const SHORT_KINDS = new Set(["int", "phone", "date"]);
// Words for display rows whose value is fixed; the schema sends no value for them.
const LOCKED_WORDS = { "you.country": "United Kingdom", "applying.submit": "Never" };
const LINK_WORDS = { "sites.where": "Open Where Hal looks", "cvdocs.route": "Open My CV", "applying.notes": "Open the notes",
  "goal.history": "Show earlier versions", "updates.whats_new": "Open What's new" };
const SECRET_WORDS = { saved: "Saved", "not saved": "Not saved", locked: "Locked" };

/** The words a text box shows for a stored value (lists one a line, or joined as the kind expects). */
export function asText(kind, value) {
  if (value == null) return "";
  if (Array.isArray(value)) {
    if (kind === "textlist") return value.join("\n");
    return value.join(kind === "times" ? " and " : kind === "hours" ? " to " : ", ");
  }
  return String(value);
}

function TextBox({ it, value, onChange, disabled }) {
  const shown = asText(it.kind, value);
  const [draft, setDraft] = useState(shown);
  useEffect(() => setDraft(shown), [shown]);
  const commit = () => {
    if (draft === shown) return;
    onChange(it.kind === "int" && draft.trim() !== "" && !Number.isNaN(Number(draft)) ? Number(draft) : draft);
  };
  if (it.kind === "textlist") {
    return html`<textarea class="input sc-wide" rows="4" aria-label=${it.label} disabled=${disabled} value=${draft}
      onInput=${e => setDraft(e.target.value)} onBlur=${commit}></textarea>`;
  }
  const type = it.kind === "int" ? "number" : INPUT_TYPES[it.kind] || "text";
  return html`<input class=${cx("input", SHORT_KINDS.has(it.kind) ? "sc-short" : "sc-wide")} type=${type} aria-label=${it.label}
    min=${it.min == null ? undefined : it.min} max=${it.max == null ? undefined : it.max} disabled=${disabled} value=${draft}
    onInput=${e => setDraft(e.target.value)} onBlur=${commit}
    onKeyDown=${e => { if (e.key === "Enter") { e.preventDefault(); commit(); } }} />`;
}

function Tags({ it, value, onChange, disabled }) {
  const list = Array.isArray(value) ? value : [];
  const [draft, setDraft] = useState("");
  const add = () => {
    const words = draft.split(",").map(w => w.trim()).filter(w => w && !list.includes(w));
    setDraft("");
    if (words.length) onChange([...list, ...words]);
  };
  const move = (i, by) => { const n = [...list]; const [x] = n.splice(i, 1); n.splice(i + by, 0, x); onChange(n); };
  const ordered = it.kind === "ordered_tags";
  return html`<div class="sc-tags">${list.map((t, i) => html`<span class="tag">${ordered && html`<span class="sc-n">${i + 1}</span>`}${t}
      ${ordered && i > 0 && html`<button type="button" aria-label=${"Move " + t + " up"} disabled=${disabled} onClick=${() => move(i, -1)}><${Icon} n="chevL" c="s" /></button>`}
      <button type="button" aria-label=${"Remove " + t} disabled=${disabled} onClick=${() => onChange(list.filter(x => x !== t))}><${Icon} n="x" c="s" /></button></span>`)}
    <input class="input sc-add" aria-label=${"Add to " + it.label} placeholder="Add" disabled=${disabled} value=${draft}
      onInput=${e => setDraft(e.target.value)} onBlur=${add}
      onKeyDown=${e => { if (e.key === "Enter") { e.preventDefault(); add(); } }} /></div>`;
}

function Multi({ it, value, onChange, disabled }) {
  const list = Array.isArray(value) ? value : [];
  const toggle = v => onChange(list.includes(v) ? list.filter(x => x !== v) : [...list, v]);
  return html`<fieldset class="sc-multi" disabled=${disabled}><legend class="sc-sr">${it.label}</legend>${it.choices.map(([v, words]) => html`<label class="row tight">
    <input type="checkbox" checked=${list.includes(v)} onChange=${() => toggle(v)} />${words}</label>`)}</fieldset>`;
}

function Range({ it, value, onChange, disabled }) {
  const [live, setLive] = useState(value);
  useEffect(() => setLive(value), [value]);
  const n = Number(live);
  return html`<input type="range" min=${it.min} max=${it.max} value=${live} aria-label=${it.label} disabled=${disabled}
    onInput=${e => setLive(+e.target.value)} onChange=${e => onChange(+e.target.value)} />
    <span class="mono sc-num">${n > 0 && it.unit === "points" ? "+" : ""}${n} ${it.unit}</span>`;
}

function Choice({ it, value, onChange, disabled, extra }) {
  if (!it.choices.length) return html`<${TextBox} it=${it} value=${value} onChange=${onChange} disabled=${disabled} />`;
  const found = extra.found;
  const unsupported = extra.unsupported || new Set();
  const words = ([v, w]) => (found && found.has(v) && !unsupported.has(v) ? w + " (found)" : w);
  const current = it.choices.findIndex(([v]) => v === value);
  return html`<select class="input sc-select" aria-label=${it.label} disabled=${disabled} value=${String(current)}
    onChange=${e => onChange(it.choices[+e.target.value][0])}>${current < 0 && html`<option value="-1">Choose one</option>`}
    ${it.choices.map((c, i) => html`<option value=${String(i)} disabled=${unsupported.has(c[0])}>${words(c)}</option>`)}</select>`;
}

function TwoStep({ it, onPress, disabled }) {
  const [sure, setSure] = useState(false);
  if (!sure) {
    return html`<button type="button" class="btn small danger" disabled=${disabled} onClick=${() => setSure(true)}>Delete everything</button>`;
  }
  return html`<span class="small">This cannot be undone.</span>
    <button type="button" class="btn small" onClick=${() => setSure(false)}>Keep my data</button>
    <button type="button" class="btn small danger" onClick=${() => { setSure(false); onPress(it); }}>Yes, delete</button>`;
}

/** The control for one schema item. `extra` carries what a row needs beyond its value: secret states, the
    browsers found, the words of an info row, a button's words and what pressing it does. */
export function SettingCtl({ it, value, onChange, disabled, extra = {} }) {
  const off = !!(disabled || it.disabled_why);
  switch (it.kind) {
    case "bool": return html`<${Switch} on=${!!value} label=${it.label} disabled=${off} onChange=${onChange} />`;
    case "seg": return html`<${Seg} label=${it.label} value=${value} options=${it.choices} onChange=${v => { if (!off) onChange(v); }} />`;
    case "choice": return html`<${Choice} it=${it} value=${value} onChange=${onChange} disabled=${off} extra=${extra} />`;
    case "multi": return html`<${Multi} it=${it} value=${value} onChange=${onChange} disabled=${off} />`;
    case "tags": case "ordered_tags": return html`<${Tags} it=${it} value=${value} onChange=${onChange} disabled=${off} />`;
    case "range": return html`<${Range} it=${it} value=${value} onChange=${onChange} disabled=${off} />`;
    case "secret": {
      const state = (extra.secrets || {})[it.key] || "not saved";
      return html`<span class=${cx("chip", state === "saved" && "good", state === "locked" && "warn")}><${Icon} n="lock" c="s" />${SECRET_WORDS[state] || state}</span>
        <button type="button" class="btn small" disabled=${off} aria-label=${"Change " + it.label}
          onClick=${() => openDialog("secret", { name: it.key, label: it.label, state })}>Change</button>`;
    }
    case "private": return html`<span class=${cx("chip", extra.privateSaved && "good")}><${Icon} n="lock" c="s" />${extra.privateSaved ? "Saved" : "Not saved"}</span>
      <button type="button" class="btn small" disabled=${off} onClick=${() => openDialog("private")}>Change answers</button>`;
    case "locked": return html`<span class="chip"><${Icon} n="lock" c="s" />${LOCKED_WORDS[it.key] || "Fixed"}</span>`;
    case "link": return html`<button type="button" class="btn small" onClick=${() => go(it.to)}>${LINK_WORDS[it.key] || "Open"}</button>`;
    case "button": return html`<button type="button" class="btn small" disabled=${off || !!extra.busy}
      onClick=${() => extra.press && extra.press(it)}>${extra.words || it.label}</button>`;
    case "danger": return html`<${TwoStep} it=${it} disabled=${off} onPress=${extra.press || (() => {})} />`;
    case "info": return extra.words ? html`<span class="small sc-info">${extra.words}</span>${extra.copy && html`<button type="button"
      class="btn small" aria-label=${"Copy " + it.label} onClick=${() => copyText(extra.words, extra.copy)}><${Icon} n="copy" c="s" />Copy</button>`}` : null;
    case "list": return html`<ul class="small sc-list">${(extra.list || []).map(x => html`<li>${x}</li>`)}</ul>`;
    default:
      return STORED_KINDS.has(it.kind) ? html`<${TextBox} it=${it} value=${value} onChange=${onChange} disabled=${off} />` : null;
  }
}
