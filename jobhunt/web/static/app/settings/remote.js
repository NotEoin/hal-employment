/* Use Hal from another computer, in Settings.

   For people comfortable with a terminal: whether this computer's SSH server is there and running, this
   computer's name and user, the exact steps with Copy buttons, the launcher for the other computer as a
   download, and the windows open from other computers, each with Sign out. In a window from another
   computer it also offers pop-ups in this browser, which the browser allows only from a button press.
   Hal never runs the setup commands itself: installing the SSH server needs the owner's password. */
import { html, useState } from "../core/html.js";
import { del } from "../core/api.js";
import { useApi, toast } from "../core/store.js";
import { copyText } from "../ui/clipboard.js";
import { dayWords, clock } from "../core/format.js";
import { isRemote, popUpState, askForPopUps } from "../core/remote.js";
import { Note } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";

const TERMINAL = "For people comfortable with a terminal. Hal keeps running on this computer, and your other computer opens it through a secure connection, so nothing new is opened to your network.";
const POP_UP_WORDS = { granted: "Hal shows its alerts in this browser.", denied: "This browser blocks Hal's alerts. You can allow them in the browser's site settings.",
  none: "This browser cannot show alerts. Hal shows them inside this window instead.", default: "Hal's alerts can pop up in this browser while this window is open." };

function serverWords(s) {
  if (s.installed === null) return ["", "Hal can tell this only on Linux."];
  if (s.running) return ["good", "Installed and running"];
  if (s.installed) return ["warn", "Installed, not running: see step 2"];
  return ["warn", "Not installed yet: see step 1"];
}

const copy = text => copyText(text);

function Steps({ steps }) {
  return html`<ol class="sc-list">${steps.map(s => html`<li key=${s.n} style="margin-bottom:10px">
    <div class="small">${s.text}</div>
    ${s.command && html`<div class="row tight" style="align-items:flex-start">
      <pre class="sc-pre" style="flex:1;min-width:0" tabindex="0" aria-label=${"Step " + s.n + " command"}>${s.command}</pre>
      <button type="button" class="btn small" aria-label=${"Copy step " + s.n} onClick=${() => copy(s.command)}>
        <${Icon} n="copy" c="s" />Copy</button></div>`}</li>`)}</ol>`;
}

function PopUps() {
  const [state, setState] = useState(popUpState());
  return html`<div class="setting" data-key="remote.popups"><div class="sc-text"><div class="lab">Alerts in this browser</div>
    <div class="small sub">${POP_UP_WORDS[state] || POP_UP_WORDS.default}</div></div>
    <div class="ctl">${state === "default" && html`<button type="button" class="btn small"
      onClick=${async () => setState(await askForPopUps())}><${Icon} n="bell" c="s" />Allow alerts</button>`}</div></div>`;
}

function Windows() {
  const list = useApi("/remote/sessions");
  const signOut = async w => {
    try { await del("/remote/sessions/" + encodeURIComponent(w.id)); toast("Signed out"); list.reload().catch(() => {}); }
    catch (e) { toast(e.message); }
  };
  const rows = list.data || [];
  return html`<div class="setting" data-key="remote.sessions"><div class="sc-text"><div class="lab">Windows open from another computer</div>
    ${list.error && html`<div class="small sub">${list.error.message}</div>`}
    ${list.data && !rows.length && html`<div class="small sub">None open now.</div>`}
    ${rows.map(w => html`<div key=${w.id} class="row tight sc-note">
      <span class="small">${w.browser}${w.this_one ? " (this window)" : ""}, opened ${dayWords(w.since)} at ${clock(w.since)}</span>
      <button type="button" class="btn small" aria-label=${"Sign out " + w.browser + (w.this_one ? ", this window" : "")}
        onClick=${() => signOut(w)}>Sign out</button></div>`)}</div></div>`;
}

/** The whole group: steps, launcher downloads and open windows, drawn from /remote/instructions and /remote/sessions. */
export function RemotePanel() {
  const how = useApi("/remote/instructions");
  if (how.error) return html`<p class="small sub">${how.error.message}</p>`;
  if (!how.data) return html`<p class="small sub">Loading</p>`;
  const d = how.data;
  const [tone, words] = serverWords(d.ssh_server);
  return html`<div data-panel="remote">
    <${Note} icon="info">${TERMINAL}<//>
    <div class="setting" data-key="remote.server"><div class="sc-text"><div class="lab">The SSH server on this computer</div>
      <div class="small sub">This computer is ${d.host}, your user name is ${d.user} and Hal answers on port ${d.port}.</div></div>
      <div class="ctl"><span class=${tone ? "chip " + tone : "small sub"}>${words}</span></div></div>
    <div class="setting" data-key="remote.how"><div class="full"><div class="lab">How to use Hal from another computer</div>
      <${Steps} steps=${d.steps} /></div></div>
    <div class="setting" data-key="remote.launcher"><div class="sc-text"><div class="lab">Download the launcher for your other computer</div>
      <div class="small sub">Or download it here and copy it across. On Linux or a Mac, make it runnable and run it once with --install.</div></div>
      <div class="ctl row tight">
        <a class="btn small" href="/api/v2/remote/launcher?os=linux"><${Icon} n="download" c="s" />Linux and Mac</a>
        <a class="btn small" href="/api/v2/remote/launcher?os=windows"><${Icon} n="download" c="s" />Windows</a></div></div>
    ${isRemote() && html`<${PopUps} />`}
    <${Windows} />
  </div>`;
}
