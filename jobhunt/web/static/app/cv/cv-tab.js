/* The Your CV tab: the CV as it stands, your file, what employers get, and a glance at
   the checks and suggestions. The paper here is a plain reading of the version in use; the preview drawn in
   the chosen look replaces it when looks arrive (ui/doc-preview.js). */
import { html } from "../core/html.js";
import { put } from "../core/api.js";
import { useApi, openDialog, toast, invalidate } from "../core/store.js";
import { dayWords, clock } from "../core/format.js";
import { Seg, Note } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";

const DATE_SEP = " - ";

function when(e) { return e.end ? e.start + DATE_SEP + e.end : e.start; }

function Body({ e }) {
  if (e.style === "paragraph") return e.text ? html`<p>${e.text}</p>` : null;
  return e.bullets.length > 0 && html`<ul>${e.bullets.map(b => html`<li key=${b.id}>${b.text}</li>`)}</ul>`;
}

function Entry({ e, kind }) {
  if (kind === "skills") return html`<p><b>${e.heading}:</b> ${e.items.join(", ")}</p>`;
  const org = [e.org, e.location].filter(Boolean).join(", ");
  const extra = [e.grade, e.meta].filter(Boolean).join(". ");
  return html`<div class="ent"><div class="top"><b>${e.heading}</b><span>${when(e)}</span></div>
    ${org && html`<div class="org">${org}</div>`}${extra && html`<div class="org">${extra}</div>`}<${Body} e=${e} /></div>`;
}

/** A plain reading of a CV document on paper: the name and contact from your details, then each section. */
export function CvPaper({ doc, name, contact }) {
  const secs = doc.sections.filter(s => s.visible);
  return html`<div class="docwrap"><div class="doc l-classic" aria-label="Your CV">
    <h1>${name || "Your name"}</h1>${doc.headline && html`<div class="contact">${doc.headline}</div>`}
    ${contact && html`<div class="contact">${contact}</div>`}
    ${secs.map(s => html`<div key=${s.id}><div class="sec">${s.title}</div>${s.text && html`<p>${s.text}</p>`}
      ${s.entries.filter((e, i) => !e.hidden && (s.max_entries == null || i < s.max_entries)).map(e => html`<${Entry} key=${e.id} e=${e} kind=${s.kind} />`)}
      ${s.also && html`<p>${s.also}</p>`}</div>`)}</div></div>`;
}

function YourFile({ cv }) {
  const b = cv.baseline;
  if (!b) {
    return html`<section class="card"><h2 class="h2">Your file</h2>
      <p class="small">You have not added a CV file, so Hal's copy is your main CV.</p>
      <${Note} icon="lock" tone="lock">Every change is saved as a new version, and you can go back to any version at any time.<//></section>`;
  }
  return html`<section class="card"><h2 class="h2">Your file</h2><dl class="kv"><dt>File</dt><dd>${b.name}</dd>
      <dt>Added</dt><dd>${dayWords(b.added)} at ${clock(b.added)}</dd>${b.pages && html`<dt>Pages</dt><dd>${b.pages}</dd>`}</dl>
    <div style="margin-top:12px"><${Note} icon="lock" tone="lock">Hal never changes your file. Every change is saved as a new version, and you can go back to your file at any time.<//></div>
    <div class="row" style="margin-top:12px"><button type="button" class="btn small" onClick=${() => openDialog("checkread")}><${Icon} n="eye" c="s" />Check what Hal read</button>
      <a class="btn quiet small" href="/api/v2/cv/original" download=${b.name}><${Icon} n="download" c="s" />Original</a></div></section>`;
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
    <div class="li"><div class="ic why"><${Icon} n="spark" /></div><div class="grow"><b>${g.pending || 0} suggestions</b> waiting<div class="small sub">One at a time, each with its reason.</div></div>
      <button type="button" class="btn small" onClick=${() => setT("sugg")}>Review</button></div></section>`;
}

/** The tab. */
export function CvTab({ cv, setT }) {
  const { data: doc } = useApi("/cv/document");
  if (!cv || !doc) return html`<p class="sub">Loading your CV.</p>`;
  return html`<div class="grid2 even">
    <div><${CvPaper} doc=${doc} name=${cv.name} contact=${cv.contact} />
      <p class="small sub" style="margin-top:8px">This is your CV as it stands (version ${doc.n}).</p></div>
    <div class="stack">
      ${cv.not_found.length > 0 && html`<${Note} icon="warn" tone="warn"><b>Some of your old CV is not in Hal's copy yet.</b> Check these and add them back if you need them: ${cv.not_found.join("; ")}<//>`}
      <${YourFile} cv=${cv} /><${Sending} cv=${cv} /><${Glance} cv=${cv} setT=${setT} /></div></div>`;
}
