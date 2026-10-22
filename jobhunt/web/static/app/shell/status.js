/* The status line over every page: what Hal is doing now, with Why? and Stop, or when it works next.
   This file draws the line from /status and the `status` event; the runner
   owns what /status says.

   Actions run by the command line or a scheduled search live in another process, so their changes reach
   this page only as `step` and `activity` rows that the server's poller publishes, never as `status`
   events. Those events reload /status (at most once per RELOAD_GAP_MS), and while Hal is busy the line
   reloads every BUSY_RELOAD_MS in case such an action ended without a new row. */
import { html, cx } from "../core/html.js";
import { useStore, openDialog, toast, store } from "../core/store.js";
import { post, get } from "../core/api.js";
import { go } from "../core/router.js";
import { retryNow, on } from "../core/events.js";
import { clock, plural } from "../core/format.js";
import { Icon } from "../ui/icons.js";

const RELOAD_GAP_MS = 1000;
const BUSY_RELOAD_MS = 30000;
let following = false;
let lastLoad = 0;
let queued = null;

/** Fetches /status into the store; a server without it yet leaves the line idle. The first call also
    starts following the events that can change it. */
export async function loadStatus() {
  if (!following) follow();
  lastLoad = Date.now();
  try { store.set({ status: await get("/status") }); } catch (err) { /* idle line until the runner reports */ }
}

function reloadSoon() {
  if (queued) return;
  const wait = Math.max(0, RELOAD_GAP_MS - (Date.now() - lastLoad));
  queued = setTimeout(() => { queued = null; loadStatus(); }, wait);
}

function follow() {
  following = true;
  on(ev => {
    if (ev.type === "action") reloadSoon();
    else if (ev.type === "step" || ev.type === "activity") {
      const st = store.s.status || {};
      if (!st.busy || (ev.action_id && ev.action_id !== st.action_id) || ev.type === "activity") reloadSoon();
    }
  });
  setInterval(() => { if (store.s.status && store.s.status.busy) loadStatus(); }, BUSY_RELOAD_MS);
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
