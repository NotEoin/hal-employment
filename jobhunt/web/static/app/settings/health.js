/* Is Hal working?, in Settings.

   Each row is one check from GET /health with its Fix button, or "Fine". A Fix either opens a page or starts
   an action whose progress shows in the status line. "Get help" makes a help file with names, contact
   details, answers and private answers taken out; the words are shown here before the person saves it. */
import { html, cx, useState } from "../core/html.js";
import { post, run } from "../core/api.js";
import { useApi, toast } from "../core/store.js";
import { go } from "../core/router.js";

const HELP_WORDS = "Makes a help file to send to whoever set Hal up for you: recent activity, versions, these checks and the notes for the site that went wrong. Names, contact details, answers and private answers are taken out, and you see the file before it is saved.";

function CheckRow({ c, reload }) {
  const [busy, setBusy] = useState(false);
  const fix = async () => {
    setBusy(true);
    try {
      // run() follows a started action to its end; a Fix that names a page answers with its route instead
      let r = await run("/health/" + encodeURIComponent(c.id) + "/fix", {});
      if (r && r.post) r = await run(r.post, {});   // the core's checks name a route to press instead of a kind
      if (r && r.route) go(r.route);
      else if (r && r.state && r.state !== "done") toast(r.error || "That did not work. Hal kept the details in Activity.");
    } catch (e) { toast(e.message); } finally { setBusy(false); reload(); }
  };
  return html`<div class="setting" data-check=${c.id}><div class="sc-text"><div class="lab row tight"><span class=${cx("dot", !c.ok && "warn")}></span>${c.name}</div>
    <div class="small sub">${c.text}</div></div><div class="ctl">${c.fix
      ? html`<button type="button" class="btn small primary" disabled=${busy} onClick=${fix}>${c.fix.label}</button>`
      : c.ok ? html`<span class="chip good">Fine</span>` : html`<span class="chip warn">Check this</span>`}</div></div>`;
}

/** The checks and Get help. */
export function HealthPanel() {
  const h = useApi("/health");
  const [file, setFile] = useState(null);
  const [making, setMaking] = useState(false);
  const make = async () => {
    setMaking(true);
    try { setFile(await post("/help-file")); toast("Made the help file. Read it below before you send it."); }
    catch (e) { toast(e.message); } finally { setMaking(false); }
  };
  const checks = (h.data && h.data.checks) || [];
  return html`${h.loading && !h.data && html`<p class="small sub">Hal is checking itself.</p>`}
    ${h.error && html`<p class="small sub">${h.error.message}</p>`}
    ${h.data && !checks.length && html`<p class="small sub">No checks have reported yet.</p>`}
    ${checks.map(c => html`<${CheckRow} key=${c.id} c=${c} reload=${() => h.reload().catch(() => {})} />`)}
    <div class="setting"><div class="sc-text"><div class="lab">Get help</div><div class="small sub">${HELP_WORDS}</div></div>
      <div class="ctl"><button type="button" class="btn small" disabled=${making} onClick=${make}>Make a help file</button></div>
      ${file && html`<div class="full"><pre class="sc-pre" tabindex="0" aria-label="The help file">${file.preview_text}</pre>
        <a class="btn small" href=${"/api/v2/files/" + encodeURIComponent(file.file_id) + "?download=1"}>Save the help file</a></div>`}</div>`;
}
