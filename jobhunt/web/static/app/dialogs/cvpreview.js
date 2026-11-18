/* The preview of a CV of Your record, opened by its Preview button: the same A4 preview as My CVs
   (ui/doc-preview.js), in a dialog wide enough for the page. GET /record/sources/{id}/preview says what to
   show: a CV that is one of My CVs shows its version in use; any other shows Hal's quick reading of the file
   (its rules, no AI), which adds nothing to My CVs; a file Hal cannot read shows the reason, and the
   original file to open instead. */
import { html } from "../core/html.js";
import { useApi } from "../core/store.js";
import { DlgHead } from "../ui/dialog.js";
import { DocPreview } from "../ui/doc-preview.js";
import { Note } from "../ui/controls.js";

export default function CvPreview({ d }) {
  const { data, error } = useApi("/record/sources/" + encodeURIComponent(d.id) + "/preview");
  const label = "Preview of " + d.title;
  let body = html`<p class="small sub">Loading</p>`;
  if (error) body = html`<${Note} icon="warn" tone="warn">${error.message}<//>`;
  else if (data && data.why) {
    body = html`<div class="stack s"><${Note} icon="warn" tone="warn">${data.why}<//>
      <p><a class="btn small" href=${"/api/v2" + data.original_url} target="_blank" rel="noopener">Open your file</a></p></div>`;
  } else if (data && data.version_id) body = html`<${DocPreview} versionId=${data.version_id} label=${label} />`;
  else if (data) body = html`<${DocPreview} src=${"/api/v2" + data.url} label=${label} />`;
  const sub = data && !data.why && (data.version_id ? "Your CV as it is in My CVs."
    : "A quick reading of your file. It is not in My CVs: press Use this for applications to add it.");
  return html`<div class="stack s"><${DlgHead} title=${d.title} sub=${sub} icon="file" />${body}</div>`;
}
CvPreview.page = true;
