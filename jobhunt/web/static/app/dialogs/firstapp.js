/* The "Your first application with Hal" guide, shown once before the first form opens.
   "Got it, open the form" approves the documents; the server remembers the guide was seen. */
import { html } from "../core/html.js";
import { closeDialog } from "../core/store.js";
import { DlgHead } from "../ui/dialog.js";
import { approve } from "../app/docs-step.js";

/** The guide; `d.app` and `d.edits` are the application and the documents as the person left them. */
export default function FirstApp({ d }) {
  return html`<${DlgHead} title="Your first application with Hal" sub="This shows once. It is also in Help." icon="help" />
    <ol style="margin:0;padding-left:22px;display:grid;gap:12px">
      <li><b>Hal opens the job site in a new browser window.</b> Leave it open. If the site asks you to sign in or tick "I am not a robot", do that; Hal waits for you.</li>
      <li><b>Hal fills in the form</b> and outlines each answer: green when it is sure, amber when you should check.</li>
      <li><b>You check the answers</b> here or on the site, then <b>press Submit on the site yourself</b>. Hal never presses it.</li>
    </ol>
    <footer><button type="button" class="btn primary" onClick=${() => { closeDialog(); if (d.app) approve(d.app, d.edits || {}); }}>Got it, open the form</button></footer>`;
}
