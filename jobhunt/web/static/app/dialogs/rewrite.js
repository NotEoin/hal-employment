/* The "Write the documents again?" dialog, shown when the application holds edits of the person's own.
   "Keep my edits" is chosen first: Hal writes everything again from the record and CV as they are now, then
   puts the person's letter, message, answers and CV edits back. "Write everything fresh" replaces them too.
   Either way Hal's changes the person undid are not carried over to the new text. */
import { html, cx, useState } from "../core/html.js";
import { post } from "../core/api.js";
import { closeDialog, toast, invalidate } from "../core/store.js";
import { DlgHead } from "../ui/dialog.js";

const CHOICES = [["keep", "Keep my edits", "Hal writes the rest again and puts your own changes back."],
                 ["fresh", "Write everything fresh", "Your own changes to the letter, message, answers and CV are replaced."]];

/** Asks for the documents to be written again; `d.app` is the application, `d.then` runs once Hal has started. */
export default function Rewrite({ d }) {
  const [choice, setChoice] = useState("keep");
  const [busy, setBusy] = useState(false);
  const a = d.app || {};
  const go_ = async () => {
    setBusy(true);
    try {
      await post("/applications/" + encodeURIComponent(a.id) + "/documents/redo", { fresh: choice === "fresh" });
      closeDialog();
      invalidate("/applications*");
      toast(choice === "fresh" ? "Hal is writing everything fresh." : "Hal is writing the documents again and keeps your edits.");
      if (d.then) d.then();
    } catch (err) { toast(err.message); setBusy(false); }
  };
  const said = CHOICES.find(c => c[0] === choice)[2];
  return html`<${DlgHead} title="Write the documents again?" sub=${[a.job && a.job.title, a.job && a.job.org].filter(Boolean).join(", ")} icon="refresh" />
    <p class="small">You have changed these documents yourself. Hal writes them again from your record and your CV as they are now.</p>
    <div class="chips" style="margin:12px 0" role="radiogroup" aria-label="Your edits">${CHOICES.map(([k, w]) => html`<button type="button" role="radio"
      aria-checked=${choice === k ? "true" : "false"} class=${cx("pick", choice === k && "on")} onClick=${() => setChoice(k)}>${w}</button>`)}</div>
    <p class="small sub" role="status">${said}</p>
    <footer><button type="button" class="btn quiet" onClick=${closeDialog}>Cancel</button>
      <button type="button" class="btn primary" disabled=${busy} onClick=${go_}>Write them again</button></footer>`;
}
