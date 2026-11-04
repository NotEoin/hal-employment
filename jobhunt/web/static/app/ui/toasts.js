/* Toasts: short messages at the bottom of the window, read out politely by screen readers; a toast made
   with an undo function carries a button, Undo unless it names another. Raise them with toast() from core/store.js. */
import { html } from "../core/html.js";
import { useStore, dismissToast } from "../core/store.js";
import { Icon } from "./icons.js";

const SHOWN = 3;   // the newest toasts on show; more covered the page they report on

/** The toast stack. */
export function Toasts() {
  const toasts = useStore(s => s.toasts).slice(-SHOWN);
  return html`<div class="toasts" aria-live="polite">${toasts.map(t => html`<div class="toast" key=${t.id}>
    <${Icon} n="check" c="s" /><span>${t.text}</span>
    ${t.undo && html`<button type="button" onClick=${() => { dismissToast(t.id); t.undo(); }}>${t.label || "Undo"}</button>`}</div>`)}</div>`;
}
