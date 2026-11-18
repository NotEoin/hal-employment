/* My CV ("My CVs" once there are several) and its six tabs. The list of CVs is on top (cv/cv-list.js); the CV
   chosen there drives every tab and dialog (cv/chosen.js), and the tabs are drawn again from scratch when it
   changes, so nothing typed for one CV lands in another. The "employers get" line is always on top, so the
   person never has to wonder which CV an employer will receive. The tab is part of the address (#cv-versions),
   written with replaceState so moving between tabs does not fill the Back history. */
import { html, useState, useEffect } from "../core/html.js";
import { useApi, openDialog, store, toast, invalidate } from "../core/store.js";
import { Head, Tabs, Note } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";
import { mustStay } from "../core/router.js";
import { put } from "../core/api.js";
import { CvTab } from "../cv/cv-tab.js";
import { EditTab } from "../cv/edit-tab.js";
import { previewUrl } from "../ui/doc-preview.js";
import { SuggTab } from "../cv/sugg-tab.js";
import { StyleTab } from "../cv/style-tab.js";
import { ChecksTab } from "../cv/checks-tab.js";
import { VersionsTab } from "../cv/versions-tab.js";
import { CvList } from "../cv/cv-list.js";
import { chosenCv, chooseCv, cvUrl } from "../cv/chosen.js";
import { UncheckedNote } from "../ui/unchecked-note.js";

const TABS = ["cv", "edit", "sugg", "style", "checks", "versions"];
// Changes made elsewhere (Your record's "Use this for applications", another window) that change the list.
const LIST_CHANGES = new Set(["star", "archive", "order", "create", "rename"]);
const listChanged = [ev => ev.type === "cv" && LIST_CHANGES.has(ev.what)];

function writeTab(t) {
  try { history.replaceState(null, "", "#" + (t === "cv" ? "cv" : "cv-" + t)); } catch (err) { /* a sandboxed frame may refuse */ }
}

/** "My CV" with one CV, "My CVs" with more (archived ones count: they are still the person's). */
export function cvTitle(count) { return count > 1 ? "My CVs" : "My CV"; }

function Archived({ cv }) {
  const restore = async () => {
    try { await put(cvUrl("/cv/archive"), { on: false }); invalidate("/cv*"); toast(cv.cv.name + " is back in your CVs"); }
    catch (err) { toast(err.message); }
  };
  return html`<${Note} icon="folder" tone="warn"><b>${cv.cv.name} is in the archive.</b> Hal does not use it for applications.
    <button type="button" class="btn small" style="margin-left:8px" onClick=${restore}>Restore</button><//>`;
}

/** The page: header, the CV list, the employers-get line, and the tabs of the chosen CV. */
export function CvStudio({ arg }) {
  const [t, setTab] = useState(TABS.includes(arg) ? arg : "cv");
  const move = next => { setTab(next); writeTab(next); };
  const setT = next => { if (next !== t && !mustStay(() => move(next))) move(next); };
  const list = useApi("/cv/cvs", { on: listChanged });
  const [picked, setPicked] = useState(chosenCv);
  const ready = !!list.data;
  const all = ready ? [...list.data.cvs, ...list.data.archived] : [];
  const shown = ready ? (all.some(c => c.id === picked) ? picked : list.data.lead) : null;
  if (ready) chooseCv(shown, shown === picked);
  const { data: cv, error } = useApi(ready ? cvUrl("/cv") : null);
  const { data: versions } = useApi(ready ? cvUrl("/cv/versions") : null);
  useEffect(() => { if (ready) store.set({ cvCount: all.length }); }, [ready, all.length]);
  const show = id => { const go = () => { chooseCv(id); setPicked(id); }; if (id !== shown && !mustStay(go)) go(); };
  const title = cvTitle(all.length);
  const pending = cv && cv.glance ? cv.glance.pending : 0;
  const count = (versions || []).filter(v => !v.binned).length;
  return html`
    <${Head} title=${title} sub=${all.length > 1 ? "Each CV keeps its own versions. Hal suggests changes, and nothing changes until you accept it."
      : "Your own CV is your main CV. Hal suggests changes, and nothing changes until you accept it."}>
      <button type="button" class="btn" onClick=${() => openDialog("route")}>Change how your CV is made</button>
    <//>
    ${(error || list.error) && html`<${Note} icon="warn" tone="warn">${(error || list.error).message}<//>`}
    ${ready && html`<${CvList} list=${list.data} shown=${shown} onShow=${show} onList=${() => list.reload()} />`}
    ${cv && cv.cv && cv.cv.archived && html`<${Archived} cv=${cv} />`}
    ${cv && html`<div class="note lock cv-employers"><${Icon} n="file" /><div style="flex:1;min-width:0"><b>${cv.employers_get}</b> <span class="sub">${cv.employers_get_sub}</span></div>
      <a class="btn quiet small" href=${cv.send === "original" ? "/api/v2" + cvUrl("/cv/original") : previewUrl({ versionId: cv.version.id, look: cv.look.id })}
        target="_blank" rel="noopener">${cv.send === "original" ? "Your file" : "Preview"}</a>
      <button type="button" class="btn small" onClick=${() => setT("cv")}>Change</button></div>`}
    <${UncheckedNote} />
    <${Tabs} label=${title} value=${t} onChange=${setT} tabs=${[["cv", "Your CV", ""], ["edit", "Edit your CV", ""],
      ["sugg", "Suggestions", pending || ""],
      ["style", "Type and look", ""], ["checks", "Checks", ""], ["versions", "Versions", count || ""]]} />
    ${ready && html`<div key=${shown}>
      ${t === "cv" && html`<${CvTab} cv=${cv} setT=${setT} />`}
      ${t === "edit" && html`<${EditTab} setT=${setT} />`}
      ${t === "sugg" && html`<${SuggTab} />`}
      ${t === "style" && html`<${StyleTab} />`}
      ${t === "checks" && html`<${ChecksTab} />`}
      ${t === "versions" && html`<${VersionsTab} cv=${cv} />`}</div>`}`;
}
