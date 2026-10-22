/* A small picture of the employer's form as the person sees it: Hal's answers outlined
   green or amber, and Hal's panel in the corner. Drawn from the application's own answers. */
import { html, cx } from "../core/html.js";

const SHOWN = 4;  // a few answers, the ones to check first

/** The picture of the form. */
export function HelperView({ a }) {
  const f = (a && a.form) || { fields: [], site: "", words: "" };
  const fields = [...f.fields].sort((x, y) => (x.ok ? 1 : 0) - (y.ok ? 1 : 0)).slice(0, SHOWN).reverse();
  return html`<div class="browser" role="img" aria-label="The employer's form in your browser, with Hal's answers outlined and Hal's panel in the corner">
    <div class="bbar"><i></i><i></i><i></i><span>${f.site}</span></div>
    <div class="pg"><h4>${a ? a.job.title : ""}: application</h4>
      ${fields.map(x => html`<div class=${cx("ff", !x.ok && "amber")}><label>${x.q}</label>${x.a}</div>`)}
      <div class="halpanel"><div style="display:flex;gap:6px;align-items:center"><span class="eye sm"></span><b>Hal</b></div>
        <div style="margin-top:6px">${f.filled} of ${f.total} filled.${f.to_check ? " Check the " + f.to_check + " in amber, then press Submit yourself." : " Press Submit yourself."}</div></div>
    </div></div>`;
}
