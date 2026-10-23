/* Setup guide step 7: Browser helper.

   Four steps per browser: open its page of add-ons, switch on "Developer mode", press "Load unpacked" and
   choose Hal's helper folder, then Connect. The steps, their words and their pictures come from
   GET /helpers/browsers (apply/browsers.toml), so each browser is explained in its own words.

   Connect asks for a one-time pairing code and opens Hal's /pair page in the chosen browser, because the
   app window is not always the helper's browser (on Windows it is Edge). In a window opened from another
   computer Hal cannot open that computer's browser, so the step shows the address to type and the pairing
   link instead. The step ticks on the `helper` event that the helper's first poll sends. */
import { html, cx, useState, useEffect } from "../../core/html.js";
import { post } from "../../core/api.js";
import { useApi, toast } from "../../core/store.js";
import { isRemote } from "../../core/remote.js";
import { Icon } from "../../ui/icons.js";

const PICTURE_W = 360;
const PICTURE_H = 200;
const TITLE = "Add Hal's helper to your browser";

/** Puts text on the clipboard and says so. */
async function copy(text, what) {
  try { await navigator.clipboard.writeText(text); toast(what); }
  catch (err) { toast("Hal could not reach the clipboard. Select the words and copy them yourself."); }
}

function Picture({ step }) {
  return html`<img class="helper-pic" src=${step.picture} alt=${step.text} width=${PICTURE_W} height=${PICTURE_H}
    style="display:block;max-width:100%;height:auto;margin-top:8px;border-radius:10px" />`;
}

/** The browser picks: supported browsers, the ones found first; Firefox only when it is on this computer. */
function Picks({ browsers, chosen, onPick }) {
  const shown = browsers.filter(b => b.supported || b.found);
  const sorted = [...shown.filter(b => b.found), ...shown.filter(b => !b.found)];
  return html`<section class="card" style="margin-top:16px"><div class="row" style="flex-wrap:wrap;gap:8px">
    <b>Which browser do you use?</b><span class="sp"></span>
    ${sorted.map(b => html`<button type="button" class=${cx("pick", chosen === b.id && "on")} aria-pressed=${chosen === b.id ? "true" : "false"}
      disabled=${!b.supported} onClick=${() => onPick(b.id)}>${b.name}${!b.supported ? " (not supported yet)" : b.found ? " (found)" : ""}</button>`)}
  </div></section>`;
}

/** The step's body. */
export function Step() {
  const info = useApi("/helpers/browsers");
  const paired = useApi("/helpers");
  const [chosen, setChosen] = useState("");
  const [link, setLink] = useState("");
  const [busy, setBusy] = useState(false);
  const remote = isRemote();
  const browsers = (info.data && info.data.browsers) || [];
  useEffect(() => {
    if (chosen || !browsers.length) return;
    const first = browsers.find(b => b.supported && b.found) || browsers.find(b => b.supported);
    if (first) setChosen(first.id);
  }, [browsers.length]);
  if (info.error) return html`<h1 class="h1">${TITLE}</h1><p class="sub">${info.error.message}</p>`;
  if (!browsers.length) return html`<h1 class="h1">${TITLE}</h1><p class="sub">Looking for your browsers...</p>`;

  const b = browsers.find(x => x.id === chosen) || browsers[0];
  const helpers = paired.data || [];
  const connected = helpers.some(h => !h.old && h.connected && (h.browser === b.id || h.this_browser));
  const old = helpers.find(h => h.old);
  const folder = info.data.folder;
  const [open, devMode, load] = b.steps;

  const openPage = async () => {
    try { await post("/helpers/open-extensions-page", { browser: b.id }); }
    catch (err) { toast(err.message); }
  };
  const connect = async () => {
    setBusy(true);
    try {
      const code = await post("/helpers/pairing-code", { browser: b.id });
      const url = location.origin + code.pair_path;
      if (remote) setLink(url);
      else {
        try { await post("/helpers/open-pair-page", { browser: b.id, code: code.code }); setLink(""); }
        catch (err) { setLink(url); toast(err.message); }
      }
    } catch (err) { toast(err.message); }
    finally { setBusy(false); }
  };

  return html`<h1 class="h1">${TITLE}</h1>
    <p class="sub">The helper lets Hal fill in forms in your browser. It only acts on tabs Hal opens or you hand to it, and it never presses Submit.</p>
    <${Picks} browsers=${browsers} chosen=${b.id} onPick=${id => { setChosen(id); setLink(""); }} />
    ${!b.supported ? html`<div class="note" style="margin-top:14px"><${Icon} n="info" /><div>${b.why}</div></div>` : html`
    <ol style="margin:18px 0;padding-left:22px;display:grid;gap:18px">
      <li><b>${open.text}</b>
        ${remote ? html`<div class="small sub" style="margin-top:6px">Type <code>${b.extensions_url}</code> in ${b.name}'s address bar on this computer.</div>`
          : html`<div style="margin-top:8px"><button type="button" class="btn primary small" disabled=${!b.found} onClick=${openPage}>${open.text.replace(/\.$/, "")}</button>
            ${!b.found && html`<span class="small sub"> Hal did not find ${b.name} here. Type <code>${b.extensions_url}</code> in its address bar.</span>`}</div>`}
        <${Picture} step=${open} /></li>
      <li><b>${devMode.text}</b> <span class="small sub">Leave it on: ${b.name} turns the helper off without it.</span><${Picture} step=${devMode} /></li>
      <li><b>${load.text}</b>
        <div class="row" style="margin-top:8px;gap:8px;flex-wrap:wrap"><code class="small" style="overflow-wrap:anywhere">${remote ? "Hal Employment/Browser helper (in your home folder on this computer)" : folder}</code>
          ${!remote && html`<button type="button" class="btn small" onClick=${() => copy(folder, "Copied the folder's address")}><${Icon} n="copy" c="s" />Copy</button>`}</div>
        ${!remote && !info.data.folder_visible && html`<div class="small sub">This folder is hidden. If ${b.name} cannot see it, ask the person who set up Hal to move it.</div>`}
        <div class="small sub" style="margin-top:6px">${b.name} will say the helper can <i>"read and change all your data on all websites"</i>. That sounds alarming: it is needed because job forms are on thousands of different sites. The helper only acts on tabs Hal opened or you handed to it.</div>
        <${Picture} step=${load} /></li>
      <li><b>Come back here and press Connect.</b> This links the helper to your copy of Hal, so no other program can use it.</li>
    </ol>
    <section class=${cx("card", connected ? "whyc" : "soft")} aria-live="polite"><div class="row">${connected
      ? html`<div class="ic good"><${Icon} n="check" /></div><b>Connected. Hal can now fill forms in ${b.name}.</b>`
      : html`<span class="eye sm busy"></span><b>Waiting for the helper...</b><span class="sp"></span>
        <button type="button" class="btn small primary" disabled=${busy} onClick=${connect}>Connect</button>`}</div>
      ${link && !connected && html`<p class="small" style="margin:10px 0 0">Open this link in ${b.name}${remote ? " on this computer" : ""}: <code style="overflow-wrap:anywhere">${link}</code>
        <button type="button" class="btn small" onClick=${() => copy(link, "Copied the link")}><${Icon} n="copy" c="s" />Copy</button></p>`}
    </section>
    ${old ? html`<section class="card" style="margin-top:12px"><b>Hal found an older helper.</b>
        <p class="small sub" style="margin:6px 0 0">Remove it, so the two do not get in each other's way. ${b.remove ? b.remove.text : ""}</p>
        ${b.remove && html`<${Picture} step=${b.remove} />`}</section>`
      : html`<p class="small sub" style="margin-top:12px">Found an older Hal helper in ${b.name}? Hal shows you how to remove it, so the two do not get in each other's way.</p>`}`}`;
}
export default Step;
