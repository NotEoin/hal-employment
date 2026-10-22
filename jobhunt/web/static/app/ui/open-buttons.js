/* Buttons for things that happen on the computer Hal runs on: open a folder, open a file, show the form.
   Every page uses these instead of calling the API itself, so a window opened from another computer
   gets the right behaviour in one place (this file has the local behaviour). */
import { html } from "../core/html.js";
import { post } from "../core/api.js";
import { toast } from "../core/store.js";
import { can } from "../core/remote.js";
import { Icon } from "./icons.js";

async function press(path, done) {
  try { await post(path); if (done) toast(done); } catch (err) { toast(err.message); }
}

/** "Open the folder": posts `path` (for example /documents/12/open-folder); hidden where folders cannot open. */
export function OpenFolderButton({ path, label = "Open the folder", small }) {
  if (!can("open_folder")) return null;
  return html`<button type="button" class=${small ? "btn small" : "btn"} onClick=${() => press(path)}>
    <${Icon} n="folder" c="s" />${label}</button>`;
}

/** "Open": shows a registered file in a new browser tab. */
export function OpenFileButton({ fileId, label = "Open", small }) {
  const href = "/api/v2/files/" + encodeURIComponent(fileId);
  return html`<a class=${small ? "btn small" : "btn"} href=${href} target="_blank" rel="noopener">
    <${Icon} n="external" c="s" />${label}</a>`;
}

/** "Show me the form": brings the application's form to the front. */
export function ShowFormButton({ appId, label = "Show me the form", small }) {
  return html`<button type="button" class=${small ? "btn small" : "btn"}
    onClick=${() => press("/applications/" + encodeURIComponent(appId) + "/form/show")}>
    <${Icon} n="external" c="s" />${label}</button>`;
}
