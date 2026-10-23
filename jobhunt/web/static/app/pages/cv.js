/* My CV and its five tabs. The "employers get" line is always on top, so the
   person never has to wonder which CV an employer will receive. The tab is part of the address
   (#cv-versions), written with replaceState so moving between tabs does not fill the Back history. */
import { html, useState } from "../core/html.js";
import { useApi, openDialog } from "../core/store.js";
import { Head, Tabs, Note } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";
import { CvTab } from "../cv/cv-tab.js";
import { SuggTab } from "../cv/sugg-tab.js";
import { StyleTab } from "../cv/style-tab.js";
import { ChecksTab } from "../cv/checks-tab.js";
import { VersionsTab } from "../cv/versions-tab.js";

const TABS = ["cv", "sugg", "style", "checks", "versions"];

function writeTab(t) {
  try { history.replaceState(null, "", "#" + (t === "cv" ? "cv" : "cv-" + t)); } catch (err) { /* a sandboxed frame may refuse */ }
}

/** The page: header, the employers-get line, and the tabs. */
export function CvStudio({ arg }) {
  const [t, setTab] = useState(TABS.includes(arg) ? arg : "cv");
  const setT = next => { setTab(next); writeTab(next); };
  const { data: cv, error } = useApi("/cv");
  const { data: versions } = useApi("/cv/versions");
  const pending = cv && cv.glance ? cv.glance.pending : 0;
  const shown = (versions || []).filter(v => !v.binned).length;
  return html`
    <${Head} title="My CV" sub="Your own CV is your main CV. Hal suggests changes, and nothing changes until you accept it.">
      <button type="button" class="btn" onClick=${() => openDialog("route")}>Change how your CV is made</button>
    <//>
    ${error && html`<${Note} icon="warn" tone="warn">${error.message}<//>`}
    ${cv && html`<div class="note lock cv-employers"><${Icon} n="file" /><div style="flex:1;min-width:0"><b>${cv.employers_get}</b> <span class="sub">${cv.employers_get_sub}</span></div>
      <button type="button" class="btn small" onClick=${() => setT("cv")}>Change</button></div>`}
    <${Tabs} label="My CV" value=${t} onChange=${setT} tabs=${[["cv", "Your CV", ""], ["sugg", "Suggestions", pending || ""],
      ["style", "Type and look", ""], ["checks", "Checks", ""], ["versions", "Versions", shown || ""]]} />
    ${t === "cv" && html`<${CvTab} cv=${cv} setT=${setT} />`}
    ${t === "sugg" && html`<${SuggTab} />`}
    ${t === "style" && html`<${StyleTab} />`}
    ${t === "checks" && html`<${ChecksTab} />`}
    ${t === "versions" && html`<${VersionsTab} cv=${cv} />`}`;
}
