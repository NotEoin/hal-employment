/* The "Update Hal" dialog: What's new, then the update itself, then its result.

   Pressing Update Hal starts the updater, which stops Hal, updates and starts it again, so this window
   loses Hal for about a minute. The dialog says so, then asks GET /updates every few seconds until Hal
   answers with a result newer than the press, and shows that result in plain words. "Go back to the
   previous version" opens it in the waiting state (`d.goingBack`). */
import { html, useState, useEffect } from "../core/html.js";
import { get, post } from "../core/api.js";
import { closeDialog, reloadAll } from "../core/store.js";
import { isRemote, host } from "../core/remote.js";
import { DlgHead } from "../ui/dialog.js";

const POLL_MS = 3000;
const GIVE_UP_MS = 5 * 60 * 1000;      // uv sync may take minutes on a slow connection
const ENDED = new Set(["updated", "failed", "went_back", "refused", "not_owner", "waiting", "sign_in", "up_to_date"]);

/** Update Hal, with What's new first. */
export default function UpdateDlg({ d }) {
  const u = d.u || {};
  const [phase, setPhase] = useState(d.goingBack ? "waiting" : "ask");
  const [err, setErr] = useState("");
  const [result, setResult] = useState(null);
  const [since] = useState(() => new Date().toISOString().slice(0, 19));

  useEffect(() => {
    if (phase !== "waiting") return undefined;
    const started = Date.now();
    const timer = setInterval(async () => {
      if (Date.now() - started > GIVE_UP_MS) { clearInterval(timer); setPhase("slow"); return; }
      try {
        const now = await get("/updates");
        const last = now.last_result;
        if (last && last.at && last.at.slice(0, 19) >= since && ENDED.has(last.state)) {
          clearInterval(timer); setResult(last); setPhase("done"); reloadAll();
        }
      } catch (e) { /* Hal is restarting; it answers again in a moment */ }
    }, POLL_MS);
    return () => clearInterval(timer);
  }, [phase]);

  const start = async () => {
    try { await post("/updates/apply"); setPhase("waiting"); } catch (e) { setErr(e.message); }
  };
  const where = isRemote() ? "Hal is restarting on " + host() + ". " : "";
  return html`<${DlgHead} title="Update Hal" icon="download" />
    ${phase === "ask" && html`
      ${u.whats_new && u.whats_new.length > 0 && html`<p><b>What's new</b></p><ul class="small">${u.whats_new.map(x => html`<li>${x}</li>`)}</ul>`}
      <p class="small sub">Hal stops for about a minute while it updates, then starts again by itself. Anything it was doing carries on afterwards.</p>
      ${err && html`<p class="small sc-err" role="alert">${err}</p>`}
      <footer><button type="button" class="btn quiet" onClick=${closeDialog}>Later</button>
        <button type="button" class="btn primary" onClick=${start}>Update Hal</button></footer>`}
    ${phase === "waiting" && html`<p role="status">${where}Hal is updating. This page comes back in about a minute.</p>
      <footer><button type="button" class="btn quiet" onClick=${closeDialog}>Close</button></footer>`}
    ${phase === "slow" && html`<p role="status">This is taking longer than usual. Hal says how it went in Settings, Updates once it is back.</p>
      <footer><button type="button" class="btn" onClick=${closeDialog}>Close</button></footer>`}
    ${phase === "done" && result && html`<p role="status">${result.words}</p>
      ${result.details && html`<details class="small"><summary>Details</summary><p class="sub">${/* details */ result.details}</p></details>`}
      <footer><button type="button" class="btn primary" onClick=${closeDialog}>Close</button></footer>`}`;
}
