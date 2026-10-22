/* The screen shown when this window has no session: Hal's pages open only from its own shortcut (or the
   laptop launcher), which signs the window in with a link that works once. No page data is shown. */
import { html } from "../core/html.js";

/** "For your safety, Hal opens from its shortcut." */
export function NoSession() {
  return html`<main class="nosess" id="main"><div class="card">
    <div class="brand"><span class="eye" aria-hidden="true"></span><div>Hal Employment<small>Your job search assistant</small></div></div>
    <h1 class="h1" tabindex="-1">For your safety, Hal opens from its shortcut.</h1>
    <p class="sub" style="margin-top:12px">Open Hal Employment from its shortcut in your apps menu or on the desktop.</p>
    <p class="sub small">Using Hal from another computer? Run the launcher on this computer again.</p>
  </div></main>`;
}

/** Shown when Hal's server does not answer at start, with Retry. */
export function NotAnswering() {
  return html`<main class="nosess" id="main"><div class="card">
    <div class="brand"><span class="eye" aria-hidden="true"></span><div>Hal Employment<small>Your job search assistant</small></div></div>
    <h1 class="h1" tabindex="-1">Hal is not answering.</h1>
    <p class="sub" style="margin-top:12px">It may be restarting. Try again in a moment.</p>
    <button type="button" class="btn primary" onClick=${() => location.reload()}>Retry</button>
  </div></main>`;
}
