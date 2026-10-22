/* The "Restart Hal" dialog, offered after a change that works only once Hal restarts (such as the port).

   Hal restarts after replying; work in progress is marked and can carry on afterwards. */
import { html, useState } from "../core/html.js";
import { post } from "../core/api.js";
import { closeDialog, toast } from "../core/store.js";
import { DlgHead } from "../ui/dialog.js";

/** Restart now, or later. */
export default function RestartDlg() {
  const [err, setErr] = useState("");
  const restart = async () => {
    try { await post("/system/restart"); closeDialog(); toast("Hal is restarting. This window reconnects by itself."); }
    catch (e) { setErr(e.message); }
  };
  return html`<${DlgHead} title="Restart Hal" icon="refresh" />
    <p>Hal restarts in a few seconds. Anything it was doing is kept and can carry on afterwards.</p>
    ${err && html`<p class="small sc-err" role="alert">${err}</p>`}
    <footer><button type="button" class="btn quiet" onClick=${closeDialog}>Later</button>
      <button type="button" class="btn primary" onClick=${restart}>Restart now</button></footer>`;
}
