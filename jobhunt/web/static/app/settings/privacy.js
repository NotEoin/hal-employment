/* Privacy and data, in Settings.

   Where the data is kept (with Open folder on Hal's own computer only), what leaves this computer, Export
   everything (a zip with passwords, keys and private answers left out) and Delete everything with its
   two-step confirm. In a window from another computer the folder is named in words and the zip is the way
   to reach the files. */
import { html, useState } from "../core/html.js";
import { post, run } from "../core/api.js";
import { useApi, toast } from "../core/store.js";
import { can } from "../core/remote.js";
import { SettingCtl } from "../ui/setting-control.js";

const DANGER = { key: "privacy.delete", kind: "danger", label: "Delete all your data from this computer" };

/** The privacy rows, drawn from GET /privacy. */
export function PrivacyPanel() {
  const p = useApi("/privacy");
  const [zip, setZip] = useState(null);
  const [busy, setBusy] = useState(false);
  const [gone, setGone] = useState(false);
  if (p.error) return html`<p class="small sub">${p.error.message}</p>`;
  if (!p.data) return html`<p class="small sub">Loading</p>`;
  const d = p.data;
  const openFolder = async () => { try { await post("/privacy/open-data-folder"); } catch (e) { toast(e.message); } };
  const exportAll = async () => {
    setBusy(true);
    try {
      const a = await run("/privacy/export", {});
      if (a.state === "done" && a.result) { setZip(a.result.file_id); toast("The zip is ready."); }
      else toast(a.error || "Hal could not make the zip. The details are in Activity.");
    } catch (e) { toast(e.message); } finally { setBusy(false); }
  };
  const deleteAll = async () => {
    try { await post("/privacy/delete", { confirm: "delete" }); setGone(true); } catch (e) { toast(e.message); }
  };
  if (gone) return html`<p>Hal deleted your data from this computer and stopped. Its own program files are still there.</p>`;
  return html`
    <div class="setting" data-key="privacy.where"><div class="sc-text"><div class="lab">Where your data is kept</div>
      <div class="small sub sc-info">${d.where.data}</div></div>
      <div class="ctl">${d.can_open && can("open_folder") ? html`<button type="button" class="btn small" onClick=${openFolder}>Open folder</button>`
        : html`<span class="small sub">Use Export everything to get a copy here.</span>`}</div></div>
    <div class="setting" data-key="privacy.leaves"><div class="sc-text"><div class="lab">What leaves this computer</div></div>
      <div class="ctl"><ul class="small sc-list">${d.leaves.map(x => html`<li>${x}</li>`)}</ul></div></div>
    <div class="setting" data-key="privacy.export"><div class="sc-text"><div class="lab">Export everything</div>
      <div class="small sub">A zip of everything Hal keeps. Passwords, keys and private answers are left out.</div></div>
      <div class="ctl"><button type="button" class="btn small" disabled=${busy} onClick=${exportAll}>${busy ? "Making the zip" : "Make a zip"}</button>
        ${zip && html`<a class="btn small primary" href=${"/api/v2/files/" + encodeURIComponent(zip) + "?download=1"}>Save the zip</a>`}</div></div>
    <div class="setting" data-key="privacy.delete"><div class="sc-text"><div class="lab">${DANGER.label}</div>
      <div class="small sub">Hal's own program files and your record folder are not touched.</div></div>
      <div class="ctl"><${SettingCtl} it=${DANGER} extra=${{ press: deleteAll }} /></div></div>`;
}
