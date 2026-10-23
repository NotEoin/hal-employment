/* The Look gallery on My CV's Type and look tab: each look drawn as a real preview of
   the person's CV, its name, whether job sites read it safely, and a note. Choosing one saves it at once
   (PUT /cv/look); a look changes only the CSS, never the words. */
import { html, cx } from "../core/html.js";
import { useApi, toast } from "../core/store.js";
import { put } from "../core/api.js";
import { DocPreview } from "../ui/doc-preview.js";

/** The gallery of looks for Hal's copy of the CV. */
export function LookPicker({ versionId = "current" }) {
  const looks = useApi("/cv/looks", { on: [ev => ev.type === "cv"] });
  if (looks.error) return html`<p class="small sub">${looks.error.message}</p>`;
  if (!looks.data) return html`<p class="small sub">Loading the looks.</p>`;
  const choose = async l => {
    try {
      await put("/cv/look", { id: l.id });
      looks.reload();
      toast("Look changed to " + l.name + ". Your words stay the same.");
    } catch (err) { toast(err.message); }
  };
  return html`<div class="cols" data-part="looks">${looks.data.map(l => html`
    <div class=${cx("thumb", l.chosen && "on")} onClick=${() => choose(l)}>
      <${DocPreview} versionId=${versionId} look=${l.id} thumb label=${"Your CV in the " + l.name + " look"} />
      <span class="row tight"><button type="button" class="btn quiet small" aria-pressed=${l.chosen ? "true" : "false"}
        onClick=${e => { e.stopPropagation(); choose(l); }}><b>${l.name}</b></button>
        <span class=${cx("chip", l.safe ? "good" : "warn")}>${l.safe ? "Safe for job sites" : "May confuse job sites"}</span></span>
      <span class="small sub">${l.note}</span>
    </div>`)}</div>`;
}
