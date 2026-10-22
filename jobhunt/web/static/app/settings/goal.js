/* What you're looking for, in Settings: the earlier versions of the goal, with Restore.

   The goal's own rows (kinds of role, studying now, avoid lists and the rest) are ordinary settings drawn
   by the Settings page; this panel adds the history every goal change keeps. Restoring writes the old
   values back as a new version, so a restore can itself be undone. */
import { html, useState } from "../core/html.js";
import { post } from "../core/api.js";
import { useApi, toast } from "../core/store.js";
import { dayWords, clock } from "../core/format.js";

const SHOWN = 5;   // the newest few; "Show all" opens the rest

/** The goal's earlier versions with Restore. */
export function GoalPanel() {
  const r = useApi("/goal/versions");
  const [all, setAll] = useState(false);
  if (r.error || !r.data) return null;
  const list = r.data;
  const restore = async v => {
    try { const res = await post("/goal/versions/" + encodeURIComponent(v.id) + "/restore"); toast("Your goal is back as it was. This is version " + res.version + "."); }
    catch (e) { toast(e.message); }
  };
  return html`<div class="setting" data-key="goal.history" id="goal-history"><div class="full">
    <div class="lab">Earlier versions of your goal</div>
    ${!list.length && html`<div class="small sub">Each change you make to your goal is kept here.</div>`}
    ${(all ? list : list.slice(0, SHOWN)).map((v, i) => html`<div class="li sc-version"><div class="grow">
        <b>${i === 0 ? "Now: " : ""}${v.summary}</b><div class="small sub">${dayWords(v.at)}, ${clock(v.at)}</div></div>
      ${i > 0 && html`<button type="button" class="btn small" onClick=${() => restore(v)}>Restore</button>`}</div>`)}
    ${list.length > SHOWN && !all && html`<button type="button" class="btn quiet small" onClick=${() => setAll(true)}>Show all ${list.length}</button>`}
  </div></div>`;
}
