/* The Adjust for each job card on My CV: No, A little or More, the setting `cvdocs.tailor`, saved through the
   settings API so Activity and Undo work as for any setting. The words say what applications do
   (`cvstudio/tailor.py`): A little reorders and rewords the profile line, More also rewords up to three lines.
   "Ask me what the job cares about first" (`cvdocs.pick_first`) is not offered because no application asks
   for those picks yet (2026-11-13). While the original file is sent the card says adjusting is off and why,
   because the server forces it to No then. */
import { html } from "../core/html.js";
import { put } from "../core/api.js";
import { toast, useApi } from "../core/store.js";
import { Seg, Note } from "../ui/controls.js";

const CARD = "/cv/adjust";
const LEVELS = [["no", "No"], ["little", "A little"], ["more", "More"]];
const HELP = {
  no: "No: employers get the same CV for every job.",
  little: "A little: Hal puts the projects and skill groups that fit the job first, and may reword your profile to suit it.",
  more: "More: as A little, and Hal may also reword up to three lines of your jobs and projects.",
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
    <p class="small" style="margin:12px 0 0">${HELP[data.setting] || ""}</p></section>`;
}
