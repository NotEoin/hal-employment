/* The "Why?" button and the Why drawer: what Hal did, why, what it used, which AI, how
   long it took and what changed. The drawer takes an ActivityItem already on screen, or loads one by id (the
   status line passes only an id). */
import { html } from "../core/html.js";
import { openDialog, useApi } from "../core/store.js";
import { go } from "../core/router.js";
import { Icon } from "./icons.js";
import { DlgHead } from "./dialog.js";

/** The rows of an ActivityItem worth showing, as [label, words]. */
export function whyRows(a) {
  const used = (a.used || []).map(u => u.label).filter(Boolean).join("; ");
  return [["Why", a.why], ["Used", used], ["AI", a.ai_words], ["Took", a.took], ["Changed", a.changed]]
    .filter(r => r[1]);
}

/** "Why?": opens the Why drawer for one Activity line (`item`) or an Activity id (`id`). */
export function WhyButton({ item, id, label = "Why?" }) {
  return html`<button type="button" class="why" onClick=${() => openDialog("why", item ? { item } : { id })}>${label}</button>`;
}

/** The drawer's content: the line, then its why and what. */
export function WhyDrawer({ item, id }) {
  const one = useApi(item ? null : "/activity/" + encodeURIComponent(id));
  const a = item || one.data;
  return html`<${DlgHead} title="Why Hal did this" icon="info" />
    ${!a ? html`<p class="sub">${one.error ? one.error.message : "Loading"}</p>` : html`<p><b>${a.text}</b></p>
      <dl class="kv" style="margin-top:12px">${whyRows(a).map(([k, v]) => html`<dt>${k}</dt><dd>${v}</dd>`)}</dl>
      ${a.remote && html`<p class="small sub">Started from another computer.</p>`}`}
    <div class="row" style="margin-top:18px"><button type="button" class="btn" onClick=${() => go("activity")}><${Icon}
      n="pulse" c="s" />See all activity</button></div>`;
}
