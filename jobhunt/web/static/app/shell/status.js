/* The status line over every page: what Hal is doing now, with Why? and Stop, or when it works next.
   This file draws the line from /status and the `status` event; the runner
   owns what /status says. */
import { html, cx } from "../core/html.js";
import { useStore, openDialog, toast, store } from "../core/store.js";
import { post, get } from "../core/api.js";
import { go } from "../core/router.js";
import { retryNow } from "../core/events.js";
import { clock, plural } from "../core/format.js";
import { Icon } from "../ui/icons.js";

/** Fetches /status into the store; a server without it yet leaves the line idle. */
export async function loadStatus() {
  try { store.set({ status: await get("/status") }); } catch (err) { /* idle line until the runner reports */ }
}

function nextWords(next) {
  if (!next || !next.length) return "";
  return next.slice(0, 2).map(n => "Next " + n.what + " at " + (/^\d\d:\d\d$/.test(n.at) ? n.at : clock(n.at)) + ".").join(" ");
}

function Steps({ n, total }) {
  if (!total) return null;
  return html` Step ${n} of ${total} <span class="stepsm" aria-hidden="true">${Array.from({ length: total },
    (_, i) => html`<i class=${i < n ? "on" : ""}></i>`)}</span>`;
}

async function stop(id) {
  try { await post("/actions/" + encodeURIComponent(id) + "/stop"); toast("Hal stopped. The work so far is kept."); }
  catch (err) { toast(err.message); }
}

async function carryOn() {
  try { await post("/status/pause", { paused: false }); toast("Hal carried on"); } catch (err) { toast(err.message); }
}

/** The line itself; role="status" so changes are read out politely. */
export function Status() {
  const s = useStore(st => ({ status: st.status, reconnecting: st.reconnecting, offline: st.offline, session: st.session }));
  const st = s.status || {};
  const remote = !!(s.session && s.session.remote);
  let body;
  if (s.offline) {
    body = html`<div class="what"><span class="eye sm" aria-hidden="true"></span><span><b>Hal is not answering.</b>
      ${remote ? " Is the desktop asleep?" : " It may be restarting."}</span></div>
      <button type="button" class="btn small" onClick=${retryNow}><${Icon} n="refresh" c="s" /> Retry</button>`;
  } else if (s.reconnecting) {
    body = html`<div class="what"><span class="eye sm" aria-hidden="true"></span><span><b>Reconnecting to Hal...</b></span></div>`;
  } else if (st.busy) {
    body = html`<div class="what"><span class="eye sm busy" aria-hidden="true"></span><span><b>${st.text}</b><${Steps}
      n=${st.step_n} total=${st.step_total} /></span></div>
      ${st.why_activity_id && html`<button type="button" class="why" onClick=${() => openDialog("why", { id: st.why_activity_id })}><${Icon}
        n="info" c="s" /> Why?</button>`}
      ${st.action_id && html`<button type="button" class="btn small stop" onClick=${() => stop(st.action_id)}><${Icon} n="stop" c="s" /> Stop</button>`}`;
  } else if (st.paused) {
    body = html`<div class="what"><span class="eye sm" aria-hidden="true"></span><span><b>Hal is paused.</b> ${st.text || ""}</span></div>
      <button type="button" class="btn small" onClick=${carryOn}><${Icon} n="play" c="s" /> Carry on</button>`;
  } else {
    body = html`<div class="what"><span class="eye sm" aria-hidden="true"></span><span><b>Hal is resting.</b> ${st.text || nextWords(st.next)}</span></div>`;
  }
  const problems = st.problems || 0;
  return html`<div class="status" role="status" aria-live="polite">${body}
    ${problems > 0 && html`<button type="button" class="health" onClick=${() => go("settings-health")}><span
      class=${cx("dot", "warn")}></span>${plural(problems, "thing")} to fix</button>`}
  </div>`;
}
