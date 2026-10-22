/* The app's state: one small store for what the shell shows, and one cache of server data keyed by API path.

   Server data is never copied into the store. Pages read it with useApi(path), and the event stream
   (core/events.js) marks paths stale so every page showing them reloads. Per-viewer conveniences (theme,
   simple menu) may live in localStorage; every access is wrapped because a private window or blocked
   site data makes it throw. */
import { useState, useEffect, useRef } from "./html.js";
import { get } from "./api.js";

const TOAST_MS = 4500;        // long enough to read a one-line message
const TOAST_UNDO_MS = 8000;   // long enough to reach the Undo button by keyboard

export const store = {
  s: { route: "home", dialog: null, toasts: [], session: null, status: null, counts: {}, needs: 0,
       theme: "system", simpleNav: false, railOpen: false, setup: { open: false, step: 0 },
       reconnecting: false, offline: false },
  ls: new Set(),
  /** Merges a patch (or the result of patch(state)) into the state and tells every listener. */
  set(patch) {
    this.s = { ...this.s, ...(typeof patch === "function" ? patch(this.s) : patch) };
    this.ls.forEach(f => f());
  },
};

function shallowEqual(a, b) {
  if (Object.is(a, b)) return true;
  if (typeof a !== "object" || typeof b !== "object" || !a || !b) return false;
  const ka = Object.keys(a), kb = Object.keys(b);
  return ka.length === kb.length && ka.every(k => Object.is(a[k], b[k]));
}

/** The selected part of the state; the component re-renders only when it changes (shallow compare). */
export function useStore(select = s => s) {
  const [, force] = useState(0);
  const last = useRef(select(store.s));
  last.current = select(store.s);
  useEffect(() => {
    const l = () => { if (!shallowEqual(select(store.s), last.current)) force(x => x + 1); };
    store.ls.add(l);
    return () => store.ls.delete(l);
  }, []);
  return last.current;
}

/* ---- server data ---- */

const cache = new Map();      // path -> { data, error, loading, subs: Set<fn>, seq }
const watchers = new Set();   // { pred, reload } from useApi's `on`

function entry(path) {
  let e = cache.get(path);
  if (!e) { e = { data: undefined, error: null, loading: false, subs: new Set(), seq: 0 }; cache.set(path, e); }
  return e;
}

function tell(e) { e.subs.forEach(f => f()); }

/** Fetches `path` again and tells every component that shows it. */
export function load(path) {
  const e = entry(path);
  const seq = ++e.seq;
  e.loading = true;
  tell(e);
  return get(path).then(
    data => { if (seq === e.seq) { e.data = data; e.error = null; e.loading = false; tell(e); } return data; },
    err => { if (seq === e.seq) { e.error = err; e.loading = false; tell(e); } throw err; });
}

/** Sets cached data at once (the optimistic updates shown instantly: Save, switches). */
export function mutate(path, update) {
  const e = entry(path);
  e.data = typeof update === "function" ? update(e.data) : update;
  tell(e);
}

/** Marks paths stale: "/jobs" matches "/jobs" and "/jobs?...", "/cv*" every path starting "/cv". */
export function invalidate(pattern) {
  const prefix = pattern.endsWith("*") ? pattern.slice(0, -1) : null;
  for (const [path, e] of cache) {
    const hit = prefix !== null ? path.startsWith(prefix) : path === pattern || path.startsWith(pattern + "?");
    if (!hit) continue;
    if (e.subs.size) load(path).catch(() => {});
    else cache.delete(path);
  }
}

/** Reloads every path on screen and forgets the rest (after the event stream says "resync"). */
export function reloadAll() { invalidate("*"); }

/** Called by core/events.js for each event, so useApi's `on` predicates can reload their path. */
export function notifyApi(ev) {
  watchers.forEach(w => { try { if (w.pred(ev)) w.reload(); } catch (err) { console.warn("useApi predicate", err); } });
}

/** Server data for an API path: { data, error, loading, reload }. `on` lists event predicates that reload it. */
export function useApi(path, { on = [] } = {}) {
  const [, force] = useState(0);
  useEffect(() => {
    if (!path) return undefined;
    const e = entry(path);
    const sub = () => force(x => x + 1);
    e.subs.add(sub);
    if (e.data === undefined && !e.loading) load(path).catch(() => {});
    const ws = on.map(pred => ({ pred, reload: () => load(path).catch(() => {}) }));
    ws.forEach(w => watchers.add(w));
    return () => { e.subs.delete(sub); ws.forEach(w => watchers.delete(w)); };
  }, [path]);
  if (!path) return { data: undefined, error: null, loading: false, reload: () => Promise.resolve() };
  const e = entry(path);
  return { data: e.data, error: e.error, loading: e.loading || (e.data === undefined && !e.error),
           reload: () => load(path) };
}

/* ---- dialogs and toasts ---- */

/** Opens a dialog by kind (dialogs/<kind>.js); the element that had focus gets it back on close. */
export const openDialog = (kind, props = {}) =>
  store.set(s => ({ dialog: { kind, ...props, opener: (s.dialog && s.dialog.opener) || document.activeElement } }));

/** Closes the open dialog and gives focus back to the element that opened it. */
export function closeDialog() {
  const opener = store.s.dialog && store.s.dialog.opener;
  store.set({ dialog: null });
  if (opener && opener.isConnected && typeof opener.focus === "function") opener.focus();
}

let toastSeq = 0;
/** Shows a short message; with `undo` (a function) it carries an Undo button. */
export function toast(text, undo) {
  const id = ++toastSeq;
  store.set(s => ({ toasts: [...s.toasts, { id, text, undo }] }));
  setTimeout(() => dismissToast(id), undo ? TOAST_UNDO_MS : TOAST_MS);
  return id;
}

/** Removes one toast. */
export function dismissToast(id) { store.set(s => ({ toasts: s.toasts.filter(t => t.id !== id) })); }

/* ---- per-viewer conveniences ---- */

const PREFS_KEY = "hal.prefs";

/** This viewer's saved conveniences ({theme, simpleNav, tabs}), or {} when storage is unavailable. */
export function readPrefs() {
  try { return JSON.parse(localStorage.getItem(PREFS_KEY) || "{}") || {}; } catch (err) { return {}; }
}

/** Saves one convenience for this viewer; silently does nothing when storage is unavailable. */
export function writePref(key, value) {
  try { localStorage.setItem(PREFS_KEY, JSON.stringify({ ...readPrefs(), [key]: value })); } catch (err) { /* private window: kept for this visit only */ }
}

/** Applies "light", "dark" or "system" to the page and remembers it for this viewer. */
export function setTheme(theme) {
  const t = ["light", "dark"].includes(theme) ? theme : "system";
  if (t === "system") delete document.documentElement.dataset.theme;
  else document.documentElement.dataset.theme = t;
  writePref("theme", t);
  store.set({ theme: t });
}

/** Switches the rail between the full menu and the simple one (five items and More). */
export function setSimpleNav(on) {
  writePref("simpleNav", !!on);
  store.set({ simpleNav: !!on });
}

/** Opens the setup guide over the current page, at a step id or index. */
export function openSetup(step = 0) { store.set({ setup: { open: true, step }, railOpen: false, dialog: null }); }

/** Closes the setup guide. */
export function closeSetup() { store.set(s => ({ setup: { ...s.setup, open: false } })); }
