/* Suggestions waiting for your yes or no. Each card says why Hal suggests it, the evidence
   (jobs open from their line) and what would change. Nothing changes until "Yes, do this". */
import { html } from "../core/html.js";
import { useApi, toast, invalidate } from "../core/store.js";
import { post } from "../core/api.js";
import { go } from "../core/router.js";
import { Icon } from "../ui/icons.js";
import { Empty, Note } from "../ui/controls.js";

/** Events after which the memory lists may have changed. */
export const MEMORY_EVENTS = [ev => ev.type === "suggestions" || ev.type === "setting"];

const SAID = {
  yes: "Done. You can undo this in Settings or here under Learned.",
  later: "Hal will ask again next week",
  never: "Hal will not suggest this again",
};

function refresh() {
  invalidate("/memory*");
  invalidate("/home");
}

async function decide(s, decision) {
  try {
    const done = await post("/memory/waiting/" + encodeURIComponent(s.id), { decision });
    refresh();
    const undo = decision === "yes" ? async () => {
      try { await post("/activity/" + done.activity_id + "/undo"); refresh(); toast("Undone. This is logged too."); }
      catch (err) { toast(err.message); }
    } : undefined;
    toast(SAID[decision], undo);
  } catch (err) { toast(err.message); }
}

function Evidence({ e }) {
  return e.route
    ? html`<li><a href=${"#" + e.route} onClick=${ev => { ev.preventDefault(); go(e.route); }}>${e.label}</a></li>`
    : html`<li>${e.label}</li>`;
}

/** One suggestion with its three answers. */
function Card({ s }) {
  return html`<article class="sugg mem-sugg"><div class="row tight"><span class="ic why"><${Icon} n="spark" /></span>
      <h3 class="h3 mem-title">${s.title}</h3></div>
    <p class="small mem-line"><b>Why:</b> ${s.why}</p>
    <ul class="small sub mem-evidence">${s.evidence.map(e => html`<${Evidence} e=${e} />`)}</ul>
    <p class="small mem-line"><b>What would change:</b> ${s.effect}</p>
    <div class="row"><button type="button" class="btn primary small" onClick=${() => decide(s, "yes")}>Yes, do this</button>
      <button type="button" class="btn small" onClick=${() => decide(s, "later")}>Not now</button>
      <button type="button" class="btn quiet small" onClick=${() => decide(s, "never")}>Never suggest this</button></div></article>`;
}

/** The "Waiting for you" tab. */
export function WaitingTab() {
  const { data, error } = useApi("/memory/waiting", { on: MEMORY_EVENTS });
  if (error) return html`<${Note} icon="warn" tone="warn">${error.message}<//>`;
  if (!data) return html`<${Empty}>Loading<//>`;
  return html`<div class="cols wide mem-cols">${data.length ? data.map(s => html`<${Card} s=${s} key=${s.id} />`)
    : html`<div class="card empty">Nothing waiting. Hal suggests changes when it sees a pattern.</div>`}</div>`;
}
