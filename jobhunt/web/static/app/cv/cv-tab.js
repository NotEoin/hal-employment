/* The Your CV tab: the CV as it stands, your file, what employers get, and a glance at
   the checks and suggestions. The CV is the page Hal prints, in the chosen look (ui/doc-preview.js), so what
   the person reads here is what an employer gets as Hal's copy. */
import { html, useState } from "../core/html.js";
import { put } from "../core/api.js";
import { openDialog, toast, invalidate } from "../core/store.js";
import { dayWords, clock, plural } from "../core/format.js";
import { Seg, Note } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";
import { DocPreview } from "../ui/doc-preview.js";
import { Upload } from "./upload.js";

function YourFile({ cv }) {
  const [replacing, setReplacing] = useState(false);
  const b = cv.baseline;
  if (!b) {
    return html`<section class="card"><h2 class="h2">Your file</h2>
      <p class="small">You have not added a CV file, so Hal's copy is your main CV.</p>
      <div style="margin:12px 0"><${Upload} /></div>
      <${Note} icon="lock" tone="lock">Every change is saved as a new version, and you can go back to any version at any time.<//></section>`;
  }
  return html`<section class="card"><h2 class="h2">Your file</h2><dl class="kv"><dt>File</dt><dd>${b.name}</dd>
      <dt>Added</dt><dd>${dayWords(b.added)} at ${clock(b.added)}</dd>${b.pages && html`<dt>Pages</dt><dd>${b.pages}</dd>`}</dl>
    <div style="margin-top:12px"><${Note} icon="lock" tone="lock">Hal never changes your file. Every change is saved as a new version, and you can go back to your file at any time.<//></div>
    <div class="row" style="margin-top:12px"><button type="button" class="btn small" onClick=${() => openDialog("checkread")}><${Icon} n="eye" c="s" />Check what Hal read</button>
      <button type="button" class="btn small" aria-expanded=${replacing ? "true" : "false"} onClick=${() => setReplacing(!replacing)}><${Icon} n="upload" c="s" />Replace</button>
      <a class="btn quiet small" href="/api/v2/cv/original" download=${b.name}><${Icon} n="download" c="s" />Original</a></div>
    ${replacing && html`<div style="margin-top:12px"><p class="small sub" style="margin:0 0 8px">Choose a new PDF or Word file. Your versions are kept.</p>
      <${Upload} fresh=${true} onRead=${() => setReplacing(false)} /></div>`}</section>`;
}

function Sending({ cv }) {
  const choose = async v => {
    try { await put("/cv/send", { send: v }); invalidate("/cv*"); toast(v === "original" ? "Employers now get your original file" : "Employers now get Hal's copy"); }
    catch (err) { toast(err.message); }
  };
  return html`<section class="card"><h2 class="h2">What employers get</h2>
    <${Seg} label="What employers get" value=${cv.send} options=${[["original", "Your original file"], ["hal_copy", "Hal's copy"]]} onChange=${choose} />
    <p class="small sub" style="margin-top:10px">${cv.send === "original"
      ? "Employers get your file exactly as you uploaded it. Hal cannot adjust it for each job, and accepted suggestions are kept in Hal's copy until you switch."
      : "Employers get Hal's copy: your CV in the look you chose, with the changes you accepted and any changes for that job, each approved by you."}</p>
    <p class="small sub" style="margin:0">Want to change it in Word? <a class="btn quiet small" href="/api/v2/cv/export?format=docx">Download as Word</a></p></section>`;
}

function Glance({ cv, setT }) {
  const g = cv.glance || {};
  return html`<section class="card"><h2 class="h2">At a glance</h2>
    ${g.scanner != null && html`<div class="li"><div class="ic good"><${Icon} n="check" /></div><div class="grow"><b>${g.scanner} of 100</b> for job-site scanners</div>
      <button type="button" class="btn quiet small" onClick=${() => setT("checks")}>Details</button></div>`}
    ${g.type_total != null && html`<div class="li"><div class="ic warn"><${Icon} n="warn" /></div><div class="grow"><b>${g.type_pass} of ${g.type_total}</b> checks for your CV type pass<div class="small sub">${cv.type.name}.</div></div>
      <button type="button" class="btn quiet small" onClick=${() => setT("checks")}>Details</button></div>`}
    <div class="li"><div class="ic why"><${Icon} n="spark" /></div><div class="grow"><b>${plural(g.pending || 0, "suggestion")}</b> waiting<div class="small sub">One at a time, each with its reason.</div></div>
      <button type="button" class="btn small" onClick=${() => setT("sugg")}>Review</button></div></section>`;
}

/** The tab. */
export function CvTab({ cv, setT }) {
  if (!cv) return html`<p class="sub">Loading your CV.</p>`;
  return html`<div class="grid2 even">
    <div><${DocPreview} versionId=${cv.version.id} look=${cv.look.id} label="Your CV" />
      <p class="small sub" style="margin-top:8px">This is your CV as it stands (version ${cv.version.n}).</p></div>
    <div class="stack">
      ${cv.not_found.length > 0 && html`<${Note} icon="warn" tone="warn"><b>Some of your old CV is not in Hal's copy yet.</b> Check these and add them back if you need them: ${cv.not_found.join("; ")}<//>`}
      <${YourFile} cv=${cv} /><${Sending} cv=${cv} /><${Glance} cv=${cv} setT=${setT} /></div></div>`;
}
