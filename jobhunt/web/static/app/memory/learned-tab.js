/* What Hal has learned from your choices: each item says what it is, where it came from and
   when, with a switch and Delete. Switching off or deleting puts back what the item changed. */
import { html } from "../core/html.js";
import { useApi, toast, invalidate } from "../core/store.js";
import { put, del } from "../core/api.js";
import { Icon } from "../ui/icons.js";
import { Empty, Note, Switch } from "../ui/controls.js";
import { MEMORY_EVENTS } from "./waiting-tab.js";

function refresh() {
  invalidate("/memory*");
  invalidate("/home");
}

async function setOn(l, on) {
  try {
    await put("/memory/learned/" + encodeURIComponent(l.id), { on });
    refresh();
    toast(on ? "Hal will use this again" : "Hal will ignore this until you switch it back on");
  } catch (err) { toast(err.message); }
}

async function forget(l) {
  try { await del("/memory/learned/" + encodeURIComponent(l.id)); refresh(); toast("Deleted"); }
  catch (err) { toast(err.message); }
}

/** The "Learned" tab. */
export function LearnedTab() {
  const { data, error } = useApi("/memory/learned", { on: MEMORY_EVENTS });
  if (error) return html`<${Note} icon="warn" tone="warn">${error.message}<//>`;
  if (!data) return html`<${Empty}>Loading<//>`;
  if (!data.length) return html`<div class="card empty">Nothing learned yet. When you say yes to a suggestion, it shows here with where it came from.</div>`;
  return html`<div class="card">${data.map(l => html`<div class="li mem-item" key=${l.id}><div class="ic why"><${Icon} n="bulb" /></div>
    <div class="grow"><b>${l.what}</b><div class="small sub">From: ${l.from}. ${l.when}.</div></div>
    <span class="chip">${l.area}</span>
    <${Switch} on=${l.on} label=${"Use: " + l.what} onChange=${v => setOn(l, v)} />
    <button type="button" class="iconbtn" aria-label=${"Delete: " + l.what} onClick=${() => forget(l)}><${Icon} n="trash" c="s" /></button></div>`)}</div>`;
}
