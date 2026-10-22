/* The "Saved passwords and keys" dialog: paste a new key or password, or forget the saved one.

   The old value is never shown; the box starts empty. A locked keyring on Linux answers 409, and the dialog
   then offers to unlock it, which shows the keyring's own prompt on Hal's computer. */
import { html, useState } from "../core/html.js";
import { put, del, post } from "../core/api.js";
import { closeDialog, toast, invalidate } from "../core/store.js";
import { DlgHead } from "../ui/dialog.js";

/** Change or forget one saved key or password; `d` has its name and label. */
export default function SecretDlg({ d }) {
  const [value, setValue] = useState("");
  const [err, setErr] = useState("");
  const [locked, setLocked] = useState(d.state === "locked");
  const done = words => { invalidate("/settings"); closeDialog(); toast(words); };
  const save = async () => {
    if (!value.trim()) { setErr("Paste the new one first."); return; }
    try { const r = await put("/secrets/" + encodeURIComponent(d.name), { value }); done("Saved in " + r.store.name + "."); }
    catch (e) { setErr(e.message); if (e.code === "conflict" || /keyring/i.test(e.message)) setLocked(true); }
  };
  const forget = async () => {
    try { await del("/secrets/" + encodeURIComponent(d.name)); done("Forgotten."); } catch (e) { setErr(e.message); }
  };
  const unlock = async () => {
    try { const r = await post("/secrets/retry"); setLocked(!!r.locked); setErr(r.locked ? "Your keyring is still locked." : ""); }
    catch (e) { setErr(e.message); }
  };
  return html`<${DlgHead} title=${d.label} icon="lock" />
    <p class="small">Paste a new one. The old one is replaced and never shown.</p>
    <input class="input sc-wide" type="password" autocomplete="off" aria-label=${"New " + d.label} value=${value}
      onInput=${e => setValue(e.target.value)} onKeyDown=${e => { if (e.key === "Enter") save(); }} />
    ${err && html`<p class="small sc-err" role="alert">${err}</p>`}
    ${locked && html`<p class="small">Unlock your keyring to save it there.
      <button type="button" class="btn small" onClick=${unlock}>Unlock and try again</button></p>`}
    <footer>${d.state === "saved" && html`<button type="button" class="btn quiet" onClick=${forget}>Forget it</button>`}
      <button type="button" class="btn quiet" onClick=${closeDialog}>Cancel</button>
      <button type="button" class="btn primary" onClick=${save}>Save</button></footer>`;
}
