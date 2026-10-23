/* Setup guide step 1: Welcome (there is no tray icon).

   What Hal does, what it never does, what stays on the computer, and that closing the window does not
   stop Hal. When an older Hal is found on this computer (the stick install), the step offers to
   copy its jobs, applications and settings; that import belongs to the install area's route. */
import { html, useState } from "../../core/html.js";
import { run } from "../../core/api.js";
import { useApi, useStore, toast, invalidate } from "../../core/store.js";
import { Icon } from "../../ui/icons.js";

const DOES = ["Find jobs that fit your goal, and say why", "Prepare your CV, letter and answers for each one",
  "Fill in application forms in your browser", "Sort job emails and remind you of interviews"];
const NEVER = ["Press Submit on an application", "Send emails or messages to employers",
  "Make accounts or sign in for you", "Make up qualifications or numbers"];

function OldHal({ found }) {
  const [busy, setBusy] = useState(false);
  const [done, setDone] = useState("");
  const path = typeof found === "string" ? found : found.path;
  const copy = async () => {
    setBusy(true);
    try {
      const a = await run("/setup/import-usb", { path });
      if (a && a.state && a.state !== "done") throw new Error(a.error || "Hal could not copy your old Hal.");
      setDone("Copied your jobs, applications and settings from your old Hal.");
      invalidate("*");
    } catch (err) { toast(err.message); }
    setBusy(false);
  };
  return html`<section class="card whyc" style="margin-top:18px"><div class="row"><div class="ic good"><${Icon} n="folder" /></div>
    <div class="grow" style="min-width:0"><b>We found an older Hal on this computer</b>
      <div class="small sub">${done || "Hal can copy its jobs, applications and settings, so you do not type them again. The old one is kept."}</div></div>
    ${!done && html`<button type="button" class="btn primary small" disabled=${busy} onClick=${copy}>${busy ? "Copying..." : "Copy them"}</button>`}</div></section>`;
}

/** The step's body. */
export function Step() {
  const setup = useApi("/setup");
  const windows = useStore(s => s.session && s.session.platform === "windows");
  const found = setup.data && setup.data.found && setup.data.found.usb_install;
  const where = windows ? "from the Start menu or the desktop" : "from your list of apps";
  return html`<div class="row"><span class="eye" style="width:44px;height:44px"></span><div class="eyebrow">Hal Employment</div></div>
    <h1 class="h1 welcome-title">Hi, I'm Hal. I help you find jobs and apply for them.</h1>
    <p class="sub" style="font-size:17px">Setting up takes about 15 minutes. You can stop at any point and carry on later; everything you enter is saved as you go.</p>
    ${found && html`<${OldHal} found=${found} />`}
    <div class="cols" style="margin-top:22px">
      <section class="card"><h2 class="h3" style="margin-bottom:8px">What I do</h2><ul class="small" style="margin:0;padding-left:18px">${DOES.map(t => html`<li>${t}</li>`)}</ul></section>
      <section class="card"><h2 class="h3" style="margin-bottom:8px">What I never do</h2><ul class="small" style="margin:0;padding-left:18px">${NEVER.map(t => html`<li>${t}</li>`)}</ul></section>
      <section class="card"><h2 class="h3" style="margin-bottom:8px">What stays on your computer</h2><p class="small" style="margin:0">Everything I store. I only send what each task needs: searches to job sites, and the advert and your evidence to Claude when I write. Settings, Privacy lists it all.</p></section>
    </div>
    <div class="note" style="margin-top:18px"><${Icon} n="info" /><div>I work in the background while your computer is on. Closing this window does not stop Hal. Open it again ${where}. Pause and Quit are in Settings, Advanced.</div></div>`;
}
export default Step;
