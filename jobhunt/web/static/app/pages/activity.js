/* Activity: everything Hal did, newest first, by day. Each line opens to say why, what it
   used, which AI, how long it took and what changed; Undo where the change can be undone. Earlier days load
   with "Show earlier". */
import { html, cx, useState } from "../core/html.js";
import { useApi, toast } from "../core/store.js";
import { post, get } from "../core/api.js";
import { Icon } from "../ui/icons.js";
import { Head, Empty } from "../ui/controls.js";
import { whyRows } from "../ui/why.js";

const AREAS = [["", "All"], ["find", "Finding jobs"], ["documents", "Documents"], ["applying", "Applying"],
  ["email", "Email"], ["learning", "Learning"], ["settings", "Settings"], ["cv", "CV"]];

/** One Activity line, with "Why and what" and Undo. */
export function ActItem({ a, onUndone }) {
  const [open, setOpen] = useState(false);
  const undo = async () => {
    try { await post("/activity/" + a.id + "/undo"); toast("Undone. This is logged too."); if (onUndone) onUndone(); }
    catch (err) { toast(err.message); }
  };
  return html`<div class="act"><time>${a.t}</time><div class=${"ic " + (a.tone || "")}>${a.running
      ? html`<span class="eye sm busy"></span>` : html`<${Icon} n=${a.icon} />`}</div>
    <div class="txt"><div class="row tight"><span>${a.text}</span>${a.running && html`<span class="chip hal">Working</span>`}</div>
      <div class="row tight" style="margin-top:4px"><span class="chip">${a.area_label}</span>${a.why && html`<button type="button"
        class="why" aria-expanded=${open ? "true" : "false"} onClick=${() => setOpen(!open)}>${open ? "Hide" : "Why and what"}</button>`}${a.undo
        && html`<button type="button" class="btn quiet small" onClick=${undo}><${Icon} n="undo" c="s" />Undo</button>`}</div></div>
    ${open && html`<div class="detail"><dl class="kv">${whyRows(a).map(([k, v]) => html`<dt>${k}</dt><dd>${v}</dd>`)}</dl>
      ${a.remote && html`<p class="small sub" style="margin:8px 0 0">Started from another computer.</p>`}</div>`}</div>`;
}

function merge(days, more) {
  const out = days.map(d => ({ ...d, items: [...d.items] }));
  for (const d of more) {
    const last = out[out.length - 1];
    if (last && last.day === d.day) last.items.push(...d.items); else out.push(d);
  }
  return out;
}

/** The Activity page. */
export function Activity() {
  const [area, setArea] = useState("");
  const path = "/activity" + (area ? "?area=" + area : "");
  const page = useApi(path);
  const [extra, setExtra] = useState({ path: "", days: [], more: undefined });
  const mine = extra.path === path ? extra : { path, days: [], more: undefined };
  const days = page.data ? merge(page.data.days, mine.days) : [];
  const more = mine.more !== undefined ? mine.more : page.data && page.data.more;
  const earlier = async () => {
    try {
      const next = await get(path + (area ? "&" : "?") + "before=" + more);
      setExtra({ path, days: [...mine.days, ...next.days], more: next.more });
    } catch (err) { toast(err.message); }
  };
  return html`
    <${Head} title="Activity" sub="Everything Hal did, newest first. Open any line to see why, what it used and what changed." />
    <div class="chips" style="margin-bottom:6px">${AREAS.map(([id, label]) => html`<button type="button"
      class=${cx("pick", area === id && "on")} aria-pressed=${area === id ? "true" : "false"} onClick=${() => setArea(id)}>${label}</button>`)}</div>
    ${page.error ? html`<${Empty}>${page.error.message}<//>` : page.loading && !page.data ? html`<${Empty}>Loading<//>`
      : days.length === 0 ? html`<${Empty}>Nothing here yet.<//>`
      : days.map(d => html`<h2 class="h2 day">${d.day}</h2><div>${d.items.map(a => html`<${ActItem} a=${a} onUndone=${page.reload} />`)}</div>`)}
    ${more && html`<div class="row" style="margin-top:14px"><button type="button" class="btn" onClick=${earlier}>Show earlier</button></div>`}`;
}
