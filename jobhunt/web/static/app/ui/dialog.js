/* The dialog host: one dialog (or drawer) at a time, opened with openDialog(kind, props).

   Each kind lives in dialogs/<kind>.js and default-exports its component, which gets the props as `d`.
   A component may set `.wide` (a wider box), `.drawer` (slides in from the right, like the Why drawer)
   or `.label` (its name when it has no DlgHead). The host moves focus in, keeps Tab inside, closes on
   Esc or a click on the dimmed page, and gives focus back to the element that opened it. */
import { html, cx, useEffect, useRef, useState } from "../core/html.js";
import { useStore, closeDialog, openDialog } from "../core/store.js";
import { Icon } from "./icons.js";

export const DIALOGS = {
  notfor: () => import("../dialogs/notfor.js"), addjob: () => import("../dialogs/addjob.js"),
  discard: () => import("../dialogs/discard.js"), askchange: () => import("../dialogs/askchange.js"),
  firstapp: () => import("../dialogs/firstapp.js"), report: () => import("../dialogs/report.js"),
  addsite: () => import("../dialogs/addsite.js"), route: () => import("../dialogs/route.js"),
  sendcopy: () => import("../dialogs/sendcopy.js"), checkread: () => import("../dialogs/checkread.js"),
  prompt: () => import("../dialogs/prompt.js"), private: () => import("../dialogs/private.js"),
  secret: () => import("../dialogs/secret.js"), restart: () => import("../dialogs/restart.js"),
  update: () => import("../dialogs/update.js"), confirm: () => import("../dialogs/confirm.js"),
  why: () => import("../dialogs/why.js"), keys: () => import("../dialogs/keys.js"),
  palette: () => import("../dialogs/palette.js"),
};
const TITLE_ID = "dlg-title";
const FOCUSABLE = 'a[href], button:not([disabled]), input:not([disabled]), select:not([disabled]), '
  + 'textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

const loading = new Map();   // kind -> promise of its component
const ready = new Map();     // kind -> component, so a dialog opened again draws at once
function loadDialog(kind) {
  if (!loading.has(kind)) {
    loading.set(kind, DIALOGS[kind]().then(m => { ready.set(kind, m.default); return m.default; }));
  }
  return loading.get(kind);
}

/** The focusable elements inside `root`, in Tab order. */
export function focusables(root) {
  return [...root.querySelectorAll(FOCUSABLE)].filter(el => el.offsetParent !== null || el === document.activeElement);
}

/** Keeps Tab and Shift+Tab inside `root`. */
export function trapTab(e, root) {
  if (e.key !== "Tab" || !root) return;
  const els = focusables(root);
  if (!els.length) { e.preventDefault(); return; }
  const first = els[0], last = els[els.length - 1];
  if (!root.contains(document.activeElement)) { e.preventDefault(); first.focus(); }
  else if (e.shiftKey && document.activeElement === first) { e.preventDefault(); last.focus(); }
  else if (!e.shiftKey && document.activeElement === last) { e.preventDefault(); first.focus(); }
}

/** A dialog's title row with an icon and the Close button; its h2 names the dialog. */
export function DlgHead({ title, sub, icon }) {
  return html`<header>${icon && html`<div class="ic why"><${Icon} n=${icon} /></div>`}<div class="grow">
    <h2 class="h2" id=${TITLE_ID} style="margin:0">${title}</h2>${sub && html`<p class="sub small" style="margin:4px 0 0">${sub}</p>`}</div>
    <button type="button" class="iconbtn" aria-label="Close" onClick=${closeDialog}><${Icon} n="x" /></button></header>`;
}

/** A drawer from the right, for pages and parts that open one outside the dialog host. */
export function Drawer({ label, onClose, children }) {
  const ref = useRef(null);
  useEffect(() => {
    const opener = document.activeElement;
    const els = ref.current ? focusables(ref.current) : [];
    if (els[0]) els[0].focus();
    return () => { if (opener && opener.isConnected) opener.focus(); };
  }, []);
  const onKey = e => { if (e.key === "Escape") { e.stopPropagation(); onClose(); } else trapTab(e, ref.current); };
  return html`<div class="scrim" onClick=${onClose}></div><aside class="drawer" role="dialog" aria-modal="true" aria-label=${label}
    ref=${ref} onKeyDown=${onKey}>${children}</aside>`;
}

/** Shows the open dialog from the store. */
export function DialogHost() {
  const d = useStore(s => s.dialog);
  const [, setTick] = useState(0);
  const ref = useRef(null);
  const kind = d && d.kind;
  const C = kind ? ready.get(kind) : null;

  useEffect(() => {
    if (!kind || ready.has(kind)) return undefined;
    if (!DIALOGS[kind]) { console.error("No dialog called " + kind); closeDialog(); return undefined; }
    let live = true;
    loadDialog(kind).then(() => { if (live) setTick(t => t + 1); }, err => { console.error(err); if (live) closeDialog(); });
    return () => { live = false; };
  }, [kind]);

  useEffect(() => {
    if (!C || !ref.current) return;
    const root = ref.current;
    const first = root.querySelector("input, textarea, select") || focusables(root)[0];
    (first || root).focus();
  }, [kind, C]);

  if (!d || !C) return null;
  const named = C.label ? { "aria-label": C.label } : { "aria-labelledby": TITLE_ID };
  const onKey = e => trapTab(e, ref.current);
  if (C.drawer) {
    return html`<div class="scrim" onClick=${closeDialog}></div><aside class="drawer" role="dialog" aria-modal="true"
      ...${named} tabindex="-1" ref=${ref} onKeyDown=${onKey}><${C} d=${d} /></aside>`;
  }
  return html`<div class="scrim" onClick=${e => { if (e.target === e.currentTarget) closeDialog(); }}><div
    class=${cx("dialog", C.wide && "wide", kind === "palette" && "palette")} role="dialog" aria-modal="true" ...${named}
    tabindex="-1" ref=${ref} onKeyDown=${onKey}><${C} d=${d} /></div></div>`;
}

/** Asks a yes-or-no question in the confirm dialog; resolves true for yes, false for anything else. */
export function ask(opts) {
  return new Promise(resolve => openDialog("confirm", { ...opts, resolve }));
}
