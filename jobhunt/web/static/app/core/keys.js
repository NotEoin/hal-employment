/* Keyboard shortcuts: Ctrl K (search or jump), ? (the shortcuts list) and Esc are the app's own;
   J, K, A, Q, S, N, [ and ] belong to whichever page registers them with useKeys.

   No single-key shortcut fires while the person is typing or while a dialog is open, so a letter typed
   into a box never acts on a job. */
import { useEffect } from "./html.js";
import { store, openDialog, closeDialog } from "./store.js";

/** The keys pages may claim, with what each does (the "Help and shortcuts" list). */
export const SHORTCUTS = [
  ["Ctrl K", "Search or jump anywhere"], ["?", "Show these shortcuts"], ["J and K", "Next and previous job"],
  ["A", "Apply with Hal"], ["Q", "Add the job to the queue"], ["S", "Save the job"], ["N", "Not for me"], ["[ and ]", "Previous and next application"],
  ["Esc", "Close a window"],
];
const PAGE_KEYS = new Set(["j", "k", "a", "q", "s", "n", "[", "]"]);
const pageHandlers = [];  // a stack: the page shown last wins

/** True while focus is in a box the person types into. */
export function isTyping(el = document.activeElement) {
  if (!el) return false;
  return /^(INPUT|TEXTAREA|SELECT)$/.test(el.tagName || "") || !!el.isContentEditable;
}

/** Lets a page handle its own keys while it is shown: useKeys({ j: next, k: prev, s: save }). */
export function useKeys(map, deps = []) {
  useEffect(() => {
    pageHandlers.push(map);
    return () => { const i = pageHandlers.lastIndexOf(map); if (i >= 0) pageHandlers.splice(i, 1); };
  }, deps);
}

/** The keydown handler for the whole app; returns true when it used the key. */
export function handleKey(e) {
  const s = store.s;
  if ((e.ctrlKey || e.metaKey) && !e.altKey && e.key.toLowerCase() === "k") {
    e.preventDefault();
    if (s.dialog && s.dialog.kind === "palette") closeDialog(); else openDialog("palette");
    return true;
  }
  if (e.key === "Escape") {
    if (s.dialog) { closeDialog(); return true; }
    if (s.railOpen) { store.set({ railOpen: false }); return true; }
    return false;
  }
  if (e.ctrlKey || e.metaKey || e.altKey || isTyping() || s.dialog || s.setup.open) return false;
  if (e.key === "?") { e.preventDefault(); openDialog("keys"); return true; }
  const key = e.key.length === 1 ? e.key.toLowerCase() : "";
  if (!PAGE_KEYS.has(key)) return false;
  const top = pageHandlers[pageHandlers.length - 1];
  if (top && typeof top[key] === "function") { e.preventDefault(); top[key](e); return true; }
  return false;
}

/** Installs the app-wide handler once; returns a function that removes it. */
export function installKeys() {
  window.addEventListener("keydown", handleKey);
  return () => window.removeEventListener("keydown", handleKey);
}
