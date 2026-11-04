/* One job in a list, and the four things a person does with a job from anywhere:
   Apply with Hal, Add to queue, Save and Not for me. Home's new matches and the Jobs page draw the same card.

   After each press the jobs lists are reloaded at once rather than waiting for the server's event, so the
   card moves (Saved, Hidden, Applications) as soon as the server has answered. */
import { html, cx } from "../core/html.js";
import { post } from "../core/api.js";
import { go, route } from "../core/router.js";
import { openDialog, toast, invalidate } from "../core/store.js";
import { Icon } from "./icons.js";
import { Score, PointChip } from "./controls.js";

/** Reloads every jobs list and job page shown, and Home's matches. */
export function refreshJobs() { invalidate("/jobs*"); invalidate("/home"); }

/** The API path of one job ("/jobs/greenhouse%3A1"). */
export const jobPath = id => "/jobs/" + encodeURIComponent(id);

/** Starts the job's documents; the job moves from Matches to Getting ready at once. */
async function startApplication(j) {
  const r = await post(jobPath(j.id) + "/apply");
  refreshJobs();
  invalidate("/applications*");
  return r && r.application_id ? route("app", r.application_id) : "apps";
}

/** Apply with Hal: opens the application. Documents come first; no form is opened until they are approved. */
export async function applyJob(j) {
  try {
    const to = await startApplication(j);
    toast("Added to Applications. Hal starts with your CV and letter; it opens no form until you approve them.");
    go(to);
  } catch (err) { toast(err.message); }
}

/** Add to queue: Hal writes the documents while the person stays to choose more, then approves them together. */
export async function queueJob(j) {
  try {
    const to = await startApplication(j);
    toast("Added to Getting ready. Hal is writing the documents for " + j.title + ".", () => go(to), "Open it");
  } catch (err) { toast(err.message); }
}

/** Save or unsave; a saved job with a closing date gets a reminder 2 days before it closes. */
export async function saveJob(j) {
  const saved = !j.saved;
  try {
    const r = await post(jobPath(j.id) + "/save", { saved });
    refreshJobs();
    toast(!saved ? "Removed from Saved" : r && r.remind_on ? "Saved. Hal will remind you 2 days before it closes."
      : "Saved. The advert does not say when it closes, so check it now and then.");
  } catch (err) { toast(err.message); }
}

/** Opens "Not for you?" for a job. */
export function notForMe(j) { openDialog("notfor", { job: j }); }

/** Kinds of role worth a chip on the card; an ordinary graduate job needs none. */
const KIND_CHIP = new Set(["internship", "placement", "insight", "apprenticeship", "graduate_scheme"]);

/** The card: score, title, facts, the biggest points and the three buttons. `sel` marks the J/K choice. */
export function JobCard({ j, compact, sel, why }) {
  const open = () => go(route("job", j.id));
  const top = (j.top_points || []).slice(0, compact ? 2 : 3);
  return html`<article class=${cx("job", sel && "sel")} data-job=${j.id}>
    <${Score} score=${j.score} />
    <div class="body">
      <button type="button" class="title" onClick=${open}>${j.title}</button>
      <div class="facts"><span>${j.org}</span>${j.place && html`<span><${Icon} n="pin" c="s" />${j.place}</span>`}
        <span>${j.pay}</span>${j.closes_in && html`<span><${Icon} n="clock" c="s" />Closes in ${j.closes_in}</span>`}
        ${KIND_CHIP.has(j.kind) && html`<span class="chip">${j.kind_label}</span>`}
        ${j.is_new && html`<span class="chip info">New</span>`}</div>
      ${j.eligibility_line && html`<div class="small sub elig">${j.eligibility_line}</div>`}
      ${why && html`<div class="small sub elig">Hidden: ${why}</div>`}
      <div class="chips">${top.map(p => html`<${PointChip} p=${p} />`)}</div>
      ${!compact && html`<div class="row" style="margin-top:10px">${j.reqs
        ? html`<span class="small sub">${j.reqs.met} of ${j.reqs.total} requirements met</span>`
        : html`<span class="small sub">Requirements are checked when you open it</span>`}<button type="button" class="why"
          onClick=${open}>Why this score?</button></div>`}
    </div>
    <div class="acts">
      <button type="button" class="btn primary small" onClick=${() => applyJob(j)}>Apply with Hal</button>
      <button type="button" class="btn small" onClick=${() => queueJob(j)}><${Icon} n="plus" c="s" />Add to queue</button>
      <button type="button" class="btn small" aria-pressed=${j.saved ? "true" : "false"} onClick=${() => saveJob(j)}><${Icon}
        n="star" c="s" />${j.saved ? "Saved" : "Save"}</button>
      <button type="button" class="btn quiet small" onClick=${() => notForMe(j)}>Not for me</button>
    </div>
  </article>`;
}
