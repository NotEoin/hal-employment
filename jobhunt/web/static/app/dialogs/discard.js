/* The "Discard this application?" dialog. Each reason is kept apart, so Hal can learn
   from them; "Too much effort" is remembered against the site. */
import { html, cx, useState } from "../core/html.js";
import { post } from "../core/api.js";
import { closeDialog, toast, invalidate } from "../core/store.js";
import { go } from "../core/router.js";
import { DlgHead } from "../ui/dialog.js";
import { Icon } from "../ui/icons.js";

export const REASONS = [["advert_closed", "The advert has closed or the page is missing"], ["too_much_effort", "Too much effort to apply"],
                        ["not_interested", "Not interested any more"], ["problem", "I found a problem with the job"]];

/** The dialog; `d.job` is {id, title, org}. */
export default function Discard({ d }) {
  const [r, setR] = useState("");
  const [words, setWords] = useState("");
  const job = d.job || {};
  const go_ = async () => {
    try {
      await post("/applications/" + encodeURIComponent(job.id) + "/discard", { reason: r || "skipped_for_now", words });
      closeDialog();
      invalidate("/applications*");
      toast("Discarded. Hal stopped and closed its tab.");
      go("apps");
    } catch (err) { toast(err.message); }
  };
  return html`<${DlgHead} title="Discard this application?" sub=${[job.title, job.org].filter(Boolean).join(", ")} icon="trash" />
    <p class="small">Hal stops straight away and closes its tab. The documents stay in Documents.</p>
    <div class="chips" style="margin:12px 0" role="radiogroup" aria-label="Why">${REASONS.map(([k, w]) => html`<button type="button" role="radio"
      aria-checked=${r === k ? "true" : "false"} class=${cx("pick", r === k && "on")} onClick=${() => setR(k)}>${w}</button>`)}</div>
    ${r === "too_much_effort" && html`<div class="note"><${Icon} n="info" /><div>Hal will remember this site was too much effort, and warn you before you apply there again.</div></div>`}
    ${r === "problem" && html`<input class="input" aria-label="What is the problem?" placeholder="What is the problem? (optional)" value=${words} onInput=${e => setWords(e.target.value)} />`}
    <footer><button type="button" class="btn quiet" onClick=${closeDialog}>Keep it</button><button type="button" class="btn danger" onClick=${go_}>Discard</button></footer>`;
}
