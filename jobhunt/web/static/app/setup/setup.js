/* STUB: the setup part */
/* The setup guide, drawn over any page. This part is being built; until then it
   shows the steps and the stub card, and Close returns to the page underneath. */
import { html, useEffect, useRef } from "../core/html.js";
import { closeSetup } from "../core/store.js";
import { StubCard } from "../ui/stub.js";
import { trapTab } from "../ui/dialog.js";

export const STUB = "setup";
const STEPS = ["Welcome", "Your CV", "About you", "What you're looking for", "Your record", "How Hal thinks",
               "Browser helper", "Email", "Phone alerts", "Updates", "All set"];

/** The guide's frame. */
export function Setup() {
  const ref = useRef(null);
  useEffect(() => { const b = ref.current && ref.current.querySelector("button"); if (b) b.focus(); }, []);
  const onKey = e => { if (e.key === "Escape") closeSetup(); else trapTab(e, ref.current); };
  return html`<div class="setup" role="dialog" aria-modal="true" aria-label="Setup guide" ref=${ref} onKeyDown=${onKey}>
    <aside><h2 class="h2">Setup guide</h2><ol class="small" style="margin:0;padding-left:20px">${STEPS.map(s => html`<li>${s}</li>`)}</ol></aside>
    <div class="sbody"><div class="sinner"><${StubCard} owner="setup" />
      <div class="row" style="margin-top:18px"><button type="button" class="btn primary" onClick=${closeSetup}>Close the guide</button></div>
    </div></div>
  </div>`;
}
