/* "Not for you?": hide a job with a reason. The reason is kept, so Hal can learn from
   it and suggest a change to the goal; Hal never changes the goal by itself. The toast's Undo puts it back. */
import { html, cx, useState } from "../core/html.js";
import { post } from "../core/api.js";
import { closeDialog, toast } from "../core/store.js";
import { DlgHead } from "../ui/dialog.js";
import { refreshJobs, jobPath } from "../ui/job-card.js";

const REASONS = ["Too far", "Pay too low", "Not my kind of work", "Too senior", "Not this employer", "The advert has closed",
  "Too much effort to apply"];

/** The dialog; `d.job` is the JobCard. */
export default function NotFor({ d }) {
  const [r, setR] = useState("");
  const [busy, setBusy] = useState(false);
  const hide = async () => {
    setBusy(true);
    try {
      await post(jobPath(d.job.id) + "/not-for-me", { reason: r || null });
      closeDialog();
      refreshJobs();
      toast("Hidden" + (r ? ": " + r.toLowerCase() : "") + ". It is in Jobs, Hidden, if you change your mind.", async () => {
        try { await post(jobPath(d.job.id) + "/put-back"); refreshJobs(); toast("Back in your matches"); }
        catch (err) { toast(err.message); }
      });
    } catch (err) { setBusy(false); toast(err.message); }
  };
  return html`<${DlgHead} title="Not for you?" sub=${d.job.title + ", " + d.job.org} icon="eye" />
    <p class="small">Pick a reason, or type your own. Hal hides the job and learns from the reason; if a pattern appears, it suggests a change to your goal. It never changes your goal by itself.</p>
    <div class="chips" style="margin:12px 0" role="group" aria-label="Reasons">${REASONS.map(x => html`<button type="button"
      class=${cx("pick", r === x && "on")} aria-pressed=${r === x ? "true" : "false"} onClick=${() => setR(x)}>${x}</button>`)}</div>
    <input class="input" placeholder="Or type a reason" aria-label="Your reason" onInput=${e => setR(e.target.value)} />
    <footer><button type="button" class="btn quiet" onClick=${closeDialog}>Cancel</button>
      <button type="button" class="btn primary" disabled=${busy} onClick=${hide}>Hide this job</button></footer>`;
}
