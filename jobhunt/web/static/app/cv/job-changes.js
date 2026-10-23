/* The changes Hal made to your CV for one job: each with its kind, where
   it is, your CV's words beside the job's, why, the evidence, and Undo or Redo before you approve. The list
   comes from the application's documents (`docs.cv.changes`: id, kind, where, before, after, why, ev, on);
   the documents step owns the call that switches a change, and passes it in as `onToggle(change, on)`. */
import { html } from "../core/html.js";
import { Icon } from "../ui/icons.js";
import { withGaps } from "./sugg-card.js";

/** The list, with the sentence above it; `why` replaces it when adjusting was off for this job. */
export function JobChanges({ changes = [], onToggle, why }) {
  if (why) return html`<p class="sub" style="margin:0">${why}</p>`;
  if (!changes.length) return html`<p class="sub" style="margin:0">Hal used your main CV as it is for this job.</p>`;
  return html`<div class="stack s">
    <p class="sub" style="margin:0">Hal changed ${changes.length} things from your CV for this job. Changed parts are highlighted in the preview.</p>
    ${changes.map(c => html`<article class=${c.on ? "sugg" : "sugg done"} data-change=${c.id}>
      <div class="row tight"><span class="chip info">${c.kind}</span><span class="small sub">${c.where}</span><span class="sp"></span>
        ${c.on ? html`<button type="button" class="btn quiet small" onClick=${() => onToggle(c, false)}><${Icon} n="undo" c="s" />Undo</button>`
          : html`<span class="small sub">Undone: your CV's version is used</span><button type="button" class="btn quiet small" onClick=${() => onToggle(c, true)}>Redo</button>`}</div>
      ${c.on && html`<div class="ba"><div class="b"><small>Your CV</small>${c.before || "(not there)"}</div><div class="a"><small>For this job</small>${c.after ? withGaps(c.after) : "(left out)"}</div></div>
        ${c.why && html`<div class="small"><b style="color:var(--why)">Why:</b> ${c.why}${c.ev && html`<div class="sub">${c.ev}</div>`}</div>`}`}
    </article>`)}</div>`;
}
