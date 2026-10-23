/* The Adjust for each job card on My CV: No, A little or More, and "Ask me what
   the job cares about first". Both are settings (`cvdocs.tailor`, `cvdocs.pick_first`), saved through the
   settings API so Activity and Undo work as for any setting. While the original file is sent the card says
   adjusting is off and why, because the server forces it to No then. */
import { html } from "../core/html.js";
import { put } from "../core/api.js";
import { toast, useApi } from "../core/store.js";
import { Seg, Switch, Note } from "../ui/controls.js";

const CARD = "/cv/adjust";
const LEVELS = [["no", "No"], ["little", "A little"], ["more", "More"]];
const HELP = {
  no: "No: employers get the same CV for every job.",
  little: "A little: Hal reorders sections and skills and adjusts your profile line. Your wording stays.",
  more: "More: Hal may also rewrite bullets to match the job, using only your evidence.",
};

/** The card. */
export function TailorSetting() {
  const { data, error, reload } = useApi(CARD);
  if (error) return html`<section class="card"><h2 class="h2">Adjust for each job</h2><p class="small">${error.message}</p></section>`;
  if (!data) return html`<section class="card" aria-busy="true"><h2 class="h2">Adjust for each job</h2></section>`;
  const save = async (key, value) => {
    try { await put("/settings/" + key, { value }); reload(); } catch (err) { toast(err.message); }
  };
  return html`<section class="card"><h2 class="h2">Adjust for each job</h2>
    <p class="small sub">How much Hal may change your CV for one job. Every change is shown to you before anything is sent.</p>
    ${data.forced_no && html`<${Note} tone="lock">${data.forced_no}</${Note}>`}
    <${Seg} label="Adjust for each job" value=${data.setting} options=${LEVELS} onChange=${v => save("cvdocs.tailor", v)} />
    <p class="small" style="margin:12px 0 0">${HELP[data.setting] || ""}</p>
    <div class="row" style="margin-top:12px"><${Switch} on=${!!data.pick_first} label="Ask me what the job cares about first"
      onChange=${v => save("cvdocs.pick_first", v)} /><span class="small">Before Hal adjusts it, ask me to pick the 3 to 5 things the job cares about most</span></div></section>`;
}
