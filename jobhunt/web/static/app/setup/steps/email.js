/* Setup guide step 8: Email, optional.

   Gmail only for now. Hal cannot read a Google account setting, so the first line opens Google's
   2-Step Verification page with a picture of what "on" looks like, instead of checking for the person.
   Test signs in to Gmail with the pasted app password and saves it only when Gmail accepts it
   (POST /email/test); the password never comes back to the page. */
import { html, cx, useState, useEffect } from "../../core/html.js";
import { get, post } from "../../core/api.js";
import { Icon } from "../../ui/icons.js";

const TWO_STEP = "https://myaccount.google.com/signinoptions/two-step-verification";
const APP_PASSWORDS = "https://myaccount.google.com/apppasswords";

/** The step's body. */
export function Step() {
  const [address, setAddress] = useState("");
  const [password, setPassword] = useState("");
  const [saved, setSaved] = useState(false);
  const [busy, setBusy] = useState(false);
  const [reply, setReply] = useState(null);

  useEffect(() => {
    get("/settings").then(s => {
      setAddress(s.values["email.address"] || s.values["you.email"] || "");
      setSaved(s.secrets["email.app_password"] === "saved");
    }).catch(() => {});
  }, []);

  const test = async () => {
    setBusy(true);
    setReply(null);
    try {
      const r = await post("/email/test", { address, password });
      setReply(r);
      if (r.ok) { setSaved(true); setPassword(""); }
    } catch (err) { setReply({ ok: false, words: err.message }); }
    setBusy(false);
  };

  return html`<h1 class="h1">Connect your email</h1><p class="sub">Optional. Hal reads job emails so interview invitations become reminders and sign-in codes are filled in for you. It never sends email from your account.</p>
    <section class="card" style="margin-top:16px"><div class="setting"><div><div class="lab">Your email</div><div class="small sub">Gmail for now. Outlook and Hotmail need a Microsoft sign-in, which comes later.</div></div>
        <div class="ctl"><select class="input" style="width:auto" aria-label="Your email"><option>Gmail</option></select></div></div>
      <div class="setting"><label class="lab" for="email-address">Your Gmail address</label>
        <div class="ctl"><input id="email-address" class="input" type="email" style="width:280px;max-width:100%" value=${address}
          onInput=${e => setAddress(e.target.value)} /></div></div>
      <ol class="steps-list">
        <li>Google needs <b>2-Step Verification</b> on first. <a class="btn small" href=${TWO_STEP} target="_blank" rel="noopener">Open Google's 2-Step Verification page</a>
          <div class="small sub" style="margin-top:6px">If it is on, Google's page looks like this:</div>
          <div class="two-step" role="img" aria-label="Google's page showing 2-Step Verification is on"><${Icon} n="lock" c="s" />2-Step Verification<span class="on">On</span></div></li>
        <li>Press <b>Open Google's page</b>. Sign in if asked. <a class="btn small" href=${APP_PASSWORDS} target="_blank" rel="noopener">Open Google's page</a></li>
        <li>Type <b>Hal</b> as the name and press <b>Create</b>.</li>
        <li>Copy the 16 letters Google shows, and paste them here:
          <div class="row" style="margin-top:6px"><input class="input" style="width:220px" placeholder="xxxx xxxx xxxx xxxx" aria-label="App password"
            autocomplete="off" value=${password} onInput=${e => setPassword(e.target.value)} />
          <button type="button" class="btn small" disabled=${busy} onClick=${test}>${busy ? "Testing..." : "Test"}</button></div></li>
      </ol>
      ${reply && html`<div class=${cx("note", !reply.ok && "warn")} role="status"><${Icon} n=${reply.ok ? "check" : "warn"} /><div>${reply.words}</div></div>`}
      ${saved && !reply && html`<div class="note" role="status"><${Icon} n="check" /><div>An app password is saved. Paste a new one and press Test to change it.</div></div>`}</section>`;
}
export default Step;
