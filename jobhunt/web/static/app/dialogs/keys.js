/* "Help and shortcuts": the keyboard shortcuts, the first-application guide again and a way back into the setup
   guide. "Read the guide" opens the first-application guide, because the app
   serves no user guide of its own. */
import { html } from "../core/html.js";
import { openDialog, openSetup } from "../core/store.js";
import { SHORTCUTS } from "../core/keys.js";
import { DlgHead } from "../ui/dialog.js";

/** The shortcuts list. */
export default function Keys() {
  return html`<${DlgHead} title="Help and shortcuts" icon="help" />
    <dl class="keys" style="margin:0">${SHORTCUTS.map(([k, d]) => html`<div class="li" style="padding:9px 2px;align-items:center">
      <dt style="min-width:90px"><kbd>${k}</kbd></dt><dd class="grow" style="margin:0">${d}</dd></div>`)}</dl>
    <div class="row" style="margin-top:14px"><button type="button" class="btn" onClick=${() => openDialog("firstapp", {})}>How applying with Hal works</button>
      <button type="button" class="btn quiet" onClick=${() => openSetup(0)}>Open the setup guide</button></div>`;
}
