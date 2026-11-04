/* The event client: one stream per browser, shared by every tab of Hal.

   The tab holding the "hal-events-leader" Web Lock opens the EventSource and passes each event to the
   other tabs over a BroadcastChannel, so the server's four-streams-per-window cap is never reached by
   opening tabs. Each event marks the cached API paths it affects as stale (core/store.js). Without Web
   Locks every tab opens its own stream. */
import { store, invalidate, reloadAll, notifyApi, toast } from "./store.js";

const STREAM_URL = "/api/v2/events";
const TYPES = ["status", "action", "step", "activity", "job", "application", "message", "alert", "setting", "cv",
               "suggestions", "health", "helper", "update", "toast", "notify"];
const BACKOFF_S = [3, 6, 12, 30];  // then 30 s for good
const OFFLINE_AFTER_MS = 20000;    // the status line gives up "Reconnecting" for "not answering" after this

const handlers = new Set();
let bc = null;
let started = false;

/** Listens to every event; returns a function that stops listening. */
export function on(fn) { handlers.add(fn); return () => handlers.delete(fn); }

const FINAL = ["done", "error", "stopped", "interrupted"];   // core/runner.py FINAL

/** The API paths each event type makes stale. */
export function stalePaths(ev) {
  const id = ev.id != null ? encodeURIComponent(ev.id) : null;
  switch (ev.type) {
    // A finished action may have changed jobs or applications without its own event (a re-check after a
    // goal change, a search, documents drafted), so the lists that show them are fetched again.
    case "action": return FINAL.includes(ev.state) ? ["/jobs*", "/applications*", "/documents*", "/home"] : [];
    case "job": return ["/jobs", id && "/jobs/" + id, "/home"].filter(Boolean);
    case "application": return ["/applications", id && "/applications/" + id, "/home", "/documents*"].filter(Boolean);
    case "activity": return ["/activity*", "/home"];
    case "message": return ["/messages*", "/home"];
    case "cv": case "suggestions": return ["/cv*", "/home"];
    case "setting": return ["/settings*", "/goal*", "/home", "/session"];
    case "health": return ["/health*", "/status"];
    case "helper": return ["/helpers*", "/health*"];
    default: return [];
  }
}

function showPopUp(title, body) {
  const hidden = document.visibilityState === "hidden";
  const remote = !!(store.s.session && store.s.session.remote);
  if ((hidden || remote) && typeof Notification !== "undefined" && Notification.permission === "granted") {
    try { new Notification(title, { body: body || "" }); } catch (err) { /* some browsers allow pop-ups only from a service worker */ }
  }
}

function deliver(ev) {
  if (ev.type === "connection") {
    store.set({ reconnecting: !ev.ok && !ev.offline, offline: !!ev.offline });
  } else if (ev.type === "resync") {
    reloadAll();
  } else if (ev.type === "status") {
    if (ev.text !== undefined) { const { type, ...status } = ev; store.set({ status }); }
    else invalidate("/status");
  } else if (ev.type === "toast") {
    toast(ev.text);
  } else if (ev.type === "alert" || ev.type === "notify") {
    toast(ev.title || ev.text || "");
    showPopUp(ev.title || "Hal Employment", ev.body);
  }
  stalePaths(ev).forEach(invalidate);
  notifyApi(ev);
  handlers.forEach(h => { try { h(ev); } catch (err) { console.warn("event handler", err); } });
}

function share(ev) {
  deliver(ev);
  if (bc) { try { bc.postMessage(ev); } catch (err) { /* the other tabs reload on their next resync */ } }
}

function connect(attempt = 0, downSince = null) {
  const es = new EventSource(STREAM_URL);
  let offlineTimer = null;
  const parse = (type, e) => { try { return { type, ...JSON.parse(e.data || "{}") }; } catch (err) { return { type }; } };
  es.onopen = () => {
    attempt = 0;
    downSince = null;
    clearTimeout(offlineTimer);
    share({ type: "connection", ok: true });
  };
  for (const t of TYPES) es.addEventListener(t, e => share(parse(t, e)));
  es.addEventListener("resync", () => share({ type: "resync" }));
  es.addEventListener("bye", () => { es.close(); });
  es.onerror = () => {
    es.close();
    const since = downSince || Date.now();
    const waited = Date.now() - since;
    share({ type: "connection", ok: false, offline: waited >= OFFLINE_AFTER_MS });
    if (waited < OFFLINE_AFTER_MS) {
      offlineTimer = setTimeout(() => share({ type: "connection", ok: false, offline: true }), OFFLINE_AFTER_MS - waited);
    }
    const delay = BACKOFF_S[Math.min(attempt, BACKOFF_S.length - 1)] * 1000;
    setTimeout(() => { clearTimeout(offlineTimer); connect(attempt + 1, since); }, delay);
  };
  return es;
}

/** Opens the shared stream once a session exists; safe to call more than once. */
export function startEvents() {
  if (started) return;
  started = true;
  if (typeof BroadcastChannel !== "undefined") {
    bc = new BroadcastChannel("hal-events");
    bc.onmessage = e => deliver(e.data);
  }
  if (navigator.locks && bc) {
    navigator.locks.request("hal-events-leader", () => new Promise(() => { connect(); }));  // held until this tab closes
  } else {
    connect();
  }
}

/** The status line's Retry: a fresh page load reconnects and refetches everything at once. */
export function retryNow() { location.reload(); }
