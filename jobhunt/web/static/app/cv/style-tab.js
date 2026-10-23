/* The Type and look tab: the frame that puts its five parts in order. Each
   part is its owner's file: the CV type, the look, the Sections card (here), the Writing card
   and adjusting for each job. */
import { html } from "../core/html.js";
import { TypePicker } from "./type-picker.js";
import { LookPicker } from "./look-picker.js";
import { SectionsEditor } from "./sections-editor.js";
import { WritingSettings } from "./writing-settings.js";
import { TailorSetting } from "./tailor-setting.js";

/** The tab. */
export function StyleTab() {
  return html`<div class="stack">
    <${TypePicker} />
    <${LookPicker} />
    <div class="grid2 even"><${SectionsEditor} /><${WritingSettings} /></div>
    <${TailorSetting} />
  </div>`;
}
