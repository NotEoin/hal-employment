/* The Applications board: every application by step, Board or List, and the Closed list.
   The columns and their help words come from GET /applications, so the board and the server never
   disagree about where an application stands. */
import { html, cx, useState } from "../core/html.js";
import { useApi } from "../core/store.js";
import { go, route } from "../core/router.js";
import { Head, Seg, Empty } from "../ui/controls.js";

const REFRESH_ON = [ev => ev.type === "action" || ev.type === "activity"];

/** One card on the board. */
function Card({ a }) {
  return html`<button type="button" class="acard" onClick=${() => go(route("app", a.id))}><span class="org">${a.org}</span>
    <b>${a.title}</b><span class=${cx("chip w", a.tone)}>${a.tone === "hal" && html`<span class="eye sm busy" aria-hidden="true"></span>`}${a.next}</span>
    ${a.note && html`<span class="small sub">${a.note}</span>`}</button>`;
}

/** The Applications page. */
export function Apps() {
  const { data, error } = useApi("/applications", { on: REFRESH_ON });
  const [view, setView] = useState("Board");
  const head = html`<${Head} title="Applications" sub="Each application goes Documents, Form, Sent, Interview. Hal never presses Submit: every form waits for you.">
    <${Seg} label="View" value=${view} options=${["Board", "List"]} onChange=${setView} /><//>`;
  if (error) return html`${head}<div class="card"><p>${error.message}</p></div>`;
  if (!data) return html`${head}<div class="card"><p class="sub">Loading your applications.</p></div>`;
  const cols = data.columns || [];
  const all = cols.flatMap(c => c.items.map(a => ({ ...a, colTitle: c.title })));
  return html`${head}
    ${view === "Board" ? html`<div class="board">${cols.map(c => html`<section class="colm" aria-label=${c.title}>
        <header><h3 class="h3">${c.title}</h3><span class="count">${c.items.length}</span></header>
        <p class="small sub" style="margin:0 4px">${c.help}</p>
        ${c.items.map(a => html`<${Card} a=${a} />`)}
        ${!c.items.length && html`<p class="small sub" style="margin:8px 4px">Nothing here yet.</p>`}</section>`)}</div>`
      : html`<div class="card">${all.map(a => html`<div class="li" style="align-items:center"><div class="grow"><b>${a.title}</b>
          <div class="small sub">${a.org}. ${a.colTitle}.</div></div><span class=${cx("chip", a.tone)}>${a.next}</span>
          <button type="button" class="btn small" onClick=${() => go(route("app", a.id))}>Open</button></div>`)}
        ${!all.length && html`<${Empty}>No applications yet. Press Apply with Hal on a job to start one.<//>`}</div>`}
    <section class="card" style="margin-top:20px"><h2 class="h2">Closed</h2><p class="small sub">Kept so Hal can learn from them. Nothing here is deleted.</p>
      ${(data.closed || []).map(c => html`<div class="li"><div class="grow"><b>${c.title}</b><div class="small sub">${c.org}. ${c.why}.</div></div></div>`)}
      ${!(data.closed || []).length && html`<p class="small sub">Nothing closed yet.</p>`}</section>`;
}
