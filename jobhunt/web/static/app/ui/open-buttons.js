/* Buttons for things that happen on the computer Hal runs on: open a folder, open a file, show the form.
   Every page uses these instead of calling the API itself, so a window opened from another computer gets
   the right behaviour in one place: there a folder cannot open, so the folder button gives way
   to the download the page names, a file opens in a browser tab, and "Show me the form" asks the browser
   helper in this browser to bring its tab forward. */
import { html } from "../core/html.js";
import { post } from "../core/api.js";
import { toast } from "../core/store.js";
import { can, isRemote } from "../core/remote.js";
import { Icon } from "./icons.js";

async function press(path, done) {
  try { await post(path); if (done) toast(done); } catch (err) { toast(err.message); }
}

/** "Open the folder": posts `path` (for example /documents/12/open-folder). From another computer it shows
    `download` instead ({ href, label }) when the page gives one, else nothing. */
export function OpenFolderButton({ path, label = "Open the folder", small, download }) {
  if (!can("open_folder")) {
    if (!download || !isRemote()) return null;
    return html`<a class=${small ? "btn small" : "btn"} href=${download.href}>
      <${Icon} n="download" c="s" />${download.label || "Download all"}</a>`;
  }
  return html`<button type="button" class=${small ? "btn small" : "btn"} onClick=${() => press(path)}>
    <${Icon} n="folder" c="s" />${label}</button>`;
}

/** "Open": shows a registered file in a new browser tab, on whichever computer this window is on. */
export function OpenFileButton({ fileId, label = "Open", small }) {
  const href = "/api/v2/files/" + encodeURIComponent(fileId);
  return html`<a class=${small ? "btn small" : "btn"} href=${href} target="_blank" rel="noopener">
    <${Icon} n="external" c="s" />${label}</a>`;
}

/** "Show me the form": brings the application's form to the front, in this computer's browser. */
export function ShowFormButton({ appId, label = "Show me the form", small }) {
  const done = isRemote() ? "The form is in front in the browser that has the helper." : null;
  return html`<button type="button" class=${small ? "btn small" : "btn"}
    onClick=${() => press("/applications/" + encodeURIComponent(appId) + "/form/show", done)}>
    <${Icon} n="external" c="s" />${label}</button>`;
}
