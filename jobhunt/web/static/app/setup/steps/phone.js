/* Setup guide step 9: Phone alerts, optional.

   Opening the step asks for the QR code (POST /notify/phone/topic), which makes the private alert code
   only when none is saved, so a phone already subscribed keeps working when the step is opened again.
   The code itself never reaches the page, only its picture. */
import { html, cx, useState, useEffect } from "../../core/html.js";
import { post } from "../../core/api.js";
import { toast } from "../../core/store.js";
import { Icon } from "../../ui/icons.js";
import { QR } from "../qr.js";

/** The step's body. */
export function Step() {
  const [svg, setSvg] = useState("");
  const [busy, setBusy] = useState(false);
  const [reply, setReply] = useState(null);

  useEffect(() => { post("/notify/phone/topic").then(r => setSvg(r.qr_svg)).catch(err => toast(err.message)); }, []);

  const test = async () => {
    setBusy(true);
    try { setReply(await post("/notify/phone/test")); } catch (err) { setReply({ ok: false, words: err.message }); }
    setBusy(false);
  };

  return html`<h1 class="h1">Alerts on your phone</h1><p class="sub">Optional. Get a ping when an interview invitation arrives or a strong match appears.</p>
    <div class="row" style="margin-top:18px;align-items:flex-start;gap:24px"><${QR} svg=${svg} />
      <ol style="margin:0;padding-left:22px;display:grid;gap:10px;flex:1;min-width:240px">
        <li>Install the free <b>ntfy</b> app on your phone: <a href="https://ntfy.sh" target="_blank" rel="noopener">ntfy.sh</a> has links for Android and iPhone.</li>
        <li>Point your phone's camera at this code and open the link in ntfy. Press <b>Subscribe</b> if ntfy asks.</li>
        <li>Press <b>Send a test</b>. <button type="button" class="btn small" disabled=${busy || !svg} onClick=${test}>Send a test</button></li></ol></div>
    ${reply && html`<div class=${cx("note", !reply.ok && "warn")} role="status" style="margin-top:14px"><${Icon} n=${reply.ok ? "check" : "warn"} /><div>${reply.words}</div></div>`}`;
}
export default Step;
