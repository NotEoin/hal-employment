/* Messages: emails Hal sorted and alerts from Hal. Opening a message marks it read;
   Home stays the one list of what needs you. */
import { html, cx, useState } from "../core/html.js";
import { useApi, toast } from "../core/store.js";
import { post, run } from "../core/api.js";
import { go } from "../core/router.js";
import { Icon } from "../ui/icons.js";
import { Head, Empty } from "../ui/controls.js";

const FILTERS = [["all", "All"], ["unread", "Unread"], ["employers", "From employers"], ["hal", "From Hal"]];
const ICONS = { interview: "calendar", suggestion: "spark", todo: "check", alert: "warn", email: "mail", update: "refresh",
  health: "warn" };

/** Follows a message's action: a page in Hal, or a web address in a new tab. */
export function follow(action) {
  if (!action) return;
  if (action.route) go(action.route);
  else if (action.url) window.open(action.url, "_blank", "noopener");
}

/** The Messages page. */
export function Messages() {
  const [f, setF] = useState("all");
  const list = useApi("/messages?filter=" + f);
  const [checking, setChecking] = useState(false);
  const act = async (path, words) => {
    try { await post(path); if (words) toast(words); list.reload(); } catch (err) { toast(err.message); }
  };
  const open = m => { if (m.unread) act("/messages/" + encodeURIComponent(m.id) + "/read"); };
  const check = async () => {
    setChecking(true);
    toast("Hal is checking your email now");
    try { const done = await run("/mail/check"); if (done && done.state === "error") toast(done.error); list.reload(); }
    catch (err) { toast(err.message); }
    setChecking(false);
  };
  const items = (list.data && list.data.items) || [];
  return html`
    <${Head} title="Messages" sub="Emails Hal sorted from your inbox, and alerts from Hal. Anything that needs you is also on Home. Opening a message marks it read.">
      <button type="button" class="btn" onClick=${() => act("/messages/read-all", "All marked read")}>Mark all read</button>
      <button type="button" class="btn quiet" disabled=${checking} onClick=${check}><${Icon} n="refresh" c="s" />Check email</button>
    <//>
    <div class="chips" style="margin-bottom:16px">${FILTERS.map(([id, label]) => html`<button type="button"
      class=${cx("pick", f === id && "on")} aria-pressed=${f === id ? "true" : "false"} onClick=${() => setF(id)}>${label}</button>`)}</div>
    <div class="card">${list.error ? html`<${Empty}>${list.error.message}<//>` : !list.data ? html`<${Empty}>Loading<//>`
      : items.length === 0 ? html`<${Empty}>Nothing here.<//>` : items.map(m => html`<div class="li msg" style="align-items:center">
      <span class=${cx("dot", m.unread ? "blue" : "")} style=${m.unread ? "" : "opacity:0"} role=${m.unread ? "img" : null}
        aria-label=${m.unread ? "Unread" : null}></span>
      <div class=${"ic " + (m.tone || "")}><${Icon} n=${ICONS[m.kind] || "mail"} /></div>
      <button type="button" class="grow msg-open" aria-label=${"Open " + m.title} onClick=${() => open(m)}>
        <b style=${m.unread ? "" : "font-weight:500"}>${m.title}</b><div class="small sub">${m.from}. ${m.text}</div></button>
      <span class="small sub mono">${m.when}</span>
      ${m.action && html`<button type="button" class="btn small" onClick=${() => { open(m); follow(m.action); }}>${m.action.label}</button>`}
      <button type="button" class="iconbtn" aria-label="Archive" onClick=${() => act("/messages/" + encodeURIComponent(m.id) + "/archive",
        "Archived")}><${Icon} n="x" c="s" /></button>
    </div>`)}</div>`;
}
