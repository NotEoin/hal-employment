/* The "Send Hal's copy from now on?" dialog, opened by the first accepted suggestion
   while the person still sends their original file. */
import { html } from "../core/html.js";
import { put } from "../core/api.js";
import { closeDialog, toast, invalidate } from "../core/store.js";
import { DlgHead } from "../ui/dialog.js";

/** The dialog. */
export default function SendCopy() {
  const yes = async () => {
    try { await put("/cv/send", { send: "hal_copy" }); invalidate("/cv*"); closeDialog(); toast("Employers now get Hal's copy, with this change"); }
    catch (err) { toast(err.message); }
  };
  return html`<${DlgHead} title="Send Hal's copy from now on?" icon="file" />
    <p>You are sending your original file, so this change goes into Hal's copy of your CV. Hal's copy uses the look you chose in Type and look.</p>
    <p class="small sub">You can switch back at any time. Your original file is never changed.</p>
    <footer><button type="button" class="btn" onClick=${() => { closeDialog(); toast("Kept your original. The change waits in Hal's copy."); }}>Keep sending my original</button>
      <button type="button" class="btn primary" onClick=${yes}>Yes, send Hal's copy</button></footer>`;
}
