/* The card every not-yet-built part of the app shows (stub convention). Stub files call
   stubPage() or stubDialog() so they all look and read the same until the part is built. */
import { html } from "../core/html.js";
import { store, closeDialog } from "../core/store.js";
import { Icon } from "./icons.js";
import { Head } from "./controls.js";
import { DlgHead } from "./dialog.js";

/** "This part is being built.", with where the old screens are for developers. */
export function StubCard({ owner }) {
  const dev = !!(store.s.session && store.s.session.developer);
  return html`<div class="card stub" data-stub=${owner}><div class="ic why"><${Icon} n="spark" /></div><div>
    <p style="margin:0"><b>This part is being built.</b></p>
    ${dev && html`<p class="small sub" style="margin:6px 0 0">Your old screens are under Advanced, Show the classic app.</p>`}
  </div></div>`;
}

/** A page component that shows its title and the stub card. */
export function stubPage(title, owner) {
  const Page = () => html`<${Head} title=${title} /><${StubCard} owner=${owner} />`;
  Page.displayName = title;
  return Page;
}

/** A dialog component that shows its title, the stub card and Close. */
export function stubDialog(title, owner) {
  const Dlg = () => html`<${DlgHead} title=${title} /><${StubCard} owner=${owner} />
    <footer><button type="button" class="btn" onClick=${closeDialog}>Close</button></footer>`;
  Dlg.displayName = title;
  return Dlg;
}

/** A component that shows only the stub card, for parts drawn inside a page. */
export function stubPart(owner) {
  return () => html`<${StubCard} owner=${owner} />`;
}
