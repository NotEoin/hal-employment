/* Your record, the "Add" panels: each tab takes its own files, links and folders, and the place says what
   they are (jobhunt/sections). Overview has the CVs panel, each job and education card its own documents,
   Projects its ways to add a project, and Documents its upload.

   A panel lists its section's sources with their kind, state and when they were last read, and the person's
   choices: "Showcase" (coursework; the app's word for a star, as "STAR" names a way to tell examples),
   "Make this my current CV" (the newest-dated CV is current until the person picks another; the others show
   "Older CV, fills gaps"), "Read again", "Remove", and a hint to move one
   that looks like it belongs elsewhere (Hal never moves it on its own). Reading runs as the
   `record.sources` action; its events and `profile` events reload the lists. A kind of document whose reader
   is still to come can still be uploaded: Hal keeps the file and reads it once it can. A link, folder or
   account whose reader is still to come is shown, but switched off as "coming soon". */
import { html, useState } from "../core/html.js";
import { api, post, put, del } from "../core/api.js";
import { useApi, toast, invalidate } from "../core/store.js";
import { plural, agoWords } from "../core/format.js";
import { isRemote } from "../core/remote.js";
import { Chip, Note } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";

const SOURCES = "/record/sources";
const SECTIONS = "/record/sections";
const KIND = "record.sources";
const HIDDEN = "position:absolute;width:1px;height:1px;opacity:0;overflow:hidden";
const STATE_TONE = { new: "line", reading: "info", read: "good", failed: "bad", kept_out: "warn" };
const UPLOADS = new Set(["upload", "files"]);
// How the page offers each way to add a project, in this order (jobhunt/sections/registry.py PROJECT_READERS).
const PROJECT_WAYS = [["github_account", "Your GitHub account", "Your GitHub username"],
  ["repo_url", "Link to one code project", "https://github.com/you/project"],
  ["local_folder", "A folder on this computer", "The folder's path"],
  ["upload", "Upload a folder", ""],
  ["portfolio_site", "Portfolio site", "https://www.behance.net/you"],
  ["web_page", "Web page or online document", "https://example.org/your-article"],
  ["manual", "Add one by hand", "The project's name"]];
const reloadOn = [ev => (ev.type === "action" && ev.kind === KIND) || ev.type === "profile"];

function refresh() { invalidate(SOURCES + "*"); invalidate("/profile*"); }

/** What an upload or link added, in a toast: how many, and where any already were. */
function saidAdded(out) {
  const parts = [];
  if (out.added.length) parts.push((out.added.length === 1 ? "Added " + out.added[0].title : "Added " + plural(out.added.length, "source"))
    + ". Hal is reading " + (out.added.length === 1 ? "it." : "them."));
  out.duplicates.forEach(d => parts.push(d.name + " is already in your record, under " + d.section_label + "."));
  (out.problems || []).forEach(p => parts.push(p + "."));
  return parts.join(" ") || "Nothing was added.";
}

async function act(fn, said) {
  try { const out = await fn(); if (said) toast(typeof said === "function" ? said(out) : said); refresh(); return out; }
  catch (err) { toast(err.message); return null; }
}

/** The sections and their kinds of document, from the server. */
export function useSections() {
  const { data } = useApi(SECTIONS);
  return data ? Object.fromEntries(data.map(s => [s.id, s])) : null;
}

function uploadFiles(section, kind, target, list, folder) {
  const files = [...(list || [])];
  if (!files.length) return Promise.resolve(null);
  const form = new FormData();
  form.append("doc_kind", kind);
  if (target != null) form.append("target_id", String(target));
  files.forEach((f, i) => {
    form.append("file" + i, f, f.name);
    if (folder && f.webkitRelativePath) form.append("path" + i, f.webkitRelativePath);
  });
  return act(() => api("POST", SOURCES + "/" + section, form), saidAdded);
}

/** One source: its title, kind, state and last read, with the person's choices. */
function SourceRow({ s }) {
  const id = encodeURIComponent(s.id);
  const path = SOURCES + "/" + id;
  return html`<li class="rec-source" data-source=${s.id}>
    <div class="grow"><div><b>${s.title}</b> <span class="small sub">${s.doc_kind_label}</span></div>
      <div class="chips"><${Chip} tone=${STATE_TONE[s.state] || "line"}>${s.state_words}<//>
        ${s.current && html`<${Chip} tone="info">Current CV<//>`}
        ${s.can_be_current && !s.current && s.state === "read" && html`<${Chip} tone="line">Older CV, fills gaps<//>`}</div>
      <div class="small sub">${s.last_checked ? "Last read " + agoWords(s.last_checked) : "Not read yet"}${s.dated ? ", dated " + s.dated : ""}</div>
      ${s.error && html`<p class="small sub rec-line">${s.error}</p>`}
      ${s.suggestion && html`<${Note} tone="info">This looks like it belongs in ${s.suggestion_label}.
        <button type="button" class="btn small" onClick=${() => act(() => put(path, { section: s.suggestion }),
          "Moved to " + s.suggestion_label + ". Hal is reading it again.")}>Move it to ${s.suggestion_label}</button><//>`}</div>
    <div class="row wrap rec-acts">
      ${s.can_star && html`<button type="button" class="btn small" aria-pressed=${s.starred ? "true" : "false"}
        aria-label=${(s.starred ? "Take out of your showcase: " : "Showcase: ") + s.title}
        onClick=${() => act(() => put(path, { starred: !s.starred }), s.starred ? "Taken out of your showcase." : "In your showcase. Hal tells employers about it.")}>
        <${Icon} n="star" c="s" />${s.starred ? "In your showcase" : "Showcase"}</button>`}
      ${s.can_be_current && !s.current && html`<button type="button" class="btn small"
        onClick=${() => act(() => put(path, { current: true }), "This is your current CV now.")}>Make this my current CV</button>`}
      ${s.state === "kept_out" && html`<button type="button" class="btn small" aria-label=${"Add " + s.title + " anyway"}
        onClick=${() => act(() => post(path + "/reread", { allow: true }), "Hal is reading it.")}>Add it anyway</button>`}
      <button type="button" class="btn quiet small" aria-label=${"Read again: " + s.title}
        onClick=${() => act(() => post(path + "/reread"), "Hal is reading it again.")}><${Icon} n="refresh" c="s" />Read again</button>
      <button type="button" class="btn danger small" aria-label=${"Remove: " + s.title}
        onClick=${() => act(() => del(path), "Removed, with what Hal took from it.")}><${Icon} n="trash" c="s" />Remove</button></div></li>`;
}

/** The sources of one section (for one card when `target` is given). */
export function SourceList({ section, target, empty }) {
  const path = SOURCES + "?section=" + section + (target != null ? "&target=" + encodeURIComponent(target) : "");
  const { data, error } = useApi(path, { on: reloadOn });
  if (error) return html`<p class="small sub">${error.message}</p>`;
  if (!data) return html`<p class="small sub">Loading</p>`;
  if (!data.length) return empty ? html`<p class="small sub rec-line">${empty}</p>` : null;
  return html`<ul class="rec-source-list">${data.map(s => html`<${SourceRow} key=${s.id + ":" + s.revision + ":" + s.state} s=${s} />`)}</ul>`;
}

/** An upload button for one section and kind; `folder` picks a whole folder instead of files. */
function Upload({ uid, section, kind, target, folder, words }) {
  const [busy, setBusy] = useState(false);
  const send = async list => { setBusy(true); await uploadFiles(section, kind, target, list, folder); setBusy(false); };
  const pick = folder ? { webkitdirectory: "", directory: "" } : { multiple: true };
  return html`<label class="btn small rec-upload" for=${uid} aria-disabled=${busy ? "true" : "false"}>
      <${Icon} n="upload" c="s" />${busy ? "Adding" : words}
      <input id=${uid} type="file" style=${HIDDEN} ...${pick} disabled=${busy}
        onChange=${e => { send(e.target.files); e.target.value = ""; }} /></label>`;
}

/** A box for a link, an account, a folder's path or a name, and its Add button. */
function LinkBox({ uid, section, kind, target, label, hint }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async e => {
    e.preventDefault();
    if (!text.trim()) { toast(hint || "Type what to add."); return; }
    setBusy(true);
    const out = await act(() => post(SOURCES + "/" + section + "/link", { locator: text, doc_kind: kind, target_id: target }), saidAdded);
    if (out) setText("");
    setBusy(false);
  };
  return html`<form class="row wrap rec-link" onSubmit=${go}>
    <label class="lab" for=${uid}>${label}</label>
    <input class="input" id=${uid} value=${text} placeholder=${hint || ""} onInput=${e => setText(e.target.value)} />
    <button type="submit" class="btn small" disabled=${busy}>Add</button></form>`;
}

/** The panel of a section (or of a few that share one, as CVs and letters do): what is it, add it, and the list.

    `parts` lists [section id, kinds to offer or null for all]; `target` is the card the sources belong to. */
export function SourcesPanel({ uid, heading, hint, parts, target, empty, level = "h2" }) {
  const sections = useSections();
  const [choice, setChoice] = useState(0);
  if (!sections) return null;
  const options = parts.flatMap(([id, kinds]) => {
    const s = sections[id];
    return s ? s.doc_kinds.filter(k => !kinds || kinds.includes(k.id)).map(k => ({ section: s, kind: k })) : [];
  });
  if (!options.length) return null;
  const chosen = options[Math.min(choice, options.length - 1)];
  const H = level === "h3" ? "h3" : "h2";
  return html`<section class="card stack s rec-panel" data-section=${parts.map(p => p[0]).join(" ")} aria-labelledby=${uid + "-h"}>
    <${H} class=${level === "h3" ? "h3" : "h2"} id=${uid + "-h"}>${heading}<//>
    ${hint && html`<p class="small sub rec-line">${hint}</p>`}
    ${options.length > 1 && html`<div class="rec-field"><label class="lab" for=${uid + "-kind"}>What is it?</label>
      <select class="input" id=${uid + "-kind"} value=${String(Math.min(choice, options.length - 1))}
        onChange=${e => setChoice(Number(e.target.value))}>
        ${options.map((o, i) => html`<option value=${String(i)}>${o.kind.label}</option>`)}</select></div>`}
    <div class="row wrap">
      ${UPLOADS.has(chosen.kind.held) && html`<${Upload} uid=${uid + "-files"} section=${chosen.section.id} kind=${chosen.kind.id}
        target=${target} words="Add files" />`}
      ${chosen.kind.held === "files" && html`<${Upload} uid=${uid + "-folder"} section=${chosen.section.id} kind=${chosen.kind.id}
        target=${target} folder=${true} words="Add a folder" />`}</div>
    ${!chosen.kind.ready && html`<p class="small sub rec-line">Hal keeps these now and reads them once it can.</p>`}
    ${parts.map(([id]) => html`<${SourceList} key=${id} section=${id} target=${target} empty=${empty} />`)}
  </section>`;
}

/** The ways to add a project: each one's box, or "coming soon" while its reader is still to come. */
export function ProjectsPanel() {
  const sections = useSections();
  const [way, setWay] = useState(null);
  if (!sections || !sections.project) return null;
  const kinds = Object.fromEntries(sections.project.doc_kinds.map(k => [k.id, k]));
  const remote = isRemote();
  const chosen = way && kinds[way];
  const entry = way && PROJECT_WAYS.find(w => w[0] === way);
  return html`<section class="card stack s rec-panel" data-section="project" aria-labelledby="rec-proj-h">
    <h2 class="h2" id="rec-proj-h">Add projects</h2>
    <p class="small sub rec-line">${sections.project.hint} Hal tells employers about the projects in your showcase.</p>
    <ul class="rec-ways">${PROJECT_WAYS.filter(([id]) => kinds[id]).map(([id, words]) => {
      const off = !kinds[id].ready || (id === "local_folder" && remote);
      return html`<li key=${id}><button type="button" class=${"btn small" + (way === id ? " primary" : "")} disabled=${off}
        aria-pressed=${way === id ? "true" : "false"} onClick=${() => setWay(id)}>${words}</button>
        ${!kinds[id].ready && html` <${Chip} tone="line">Coming soon<//>`}
        ${kinds[id].ready && id === "local_folder" && remote && html` <span class="small sub">On the computer Hal runs on</span>`}</li>`;
    })}</ul>
    ${chosen && (UPLOADS.has(chosen.held)
      ? html`<div class="row wrap"><${Upload} uid="rec-proj-folder" section="project" kind=${way} folder=${true} words="Choose a folder" /></div>`
      : html`<${LinkBox} uid=${"rec-proj-" + way} section="project" kind=${way} label=${entry[1]} hint=${entry[2]} />`)}
    <${SourceList} section="project" />
  </section>`;
}

/** Inside an education card: its coursework, lecture material and official papers. */
export function EducationSources({ item }) {
  const uid = "rec-ed" + item.id;
  return html`<details class="rec-hidden rec-card-sources"><summary>Coursework, lecture material and official papers</summary>
    <div class="stack s">
      <${SourcesPanel} uid=${uid + "-cw"} level="h3" heading="Coursework" parts=${[["coursework", null]]} target=${item.id}
        hint="Essays, reports, lab reports, portfolios and your dissertation. Showcase the ones you are proudest of." />
      <${SourcesPanel} uid=${uid + "-lec"} level="h3" heading="Lecture material" parts=${[["lecture", null]]} target=${item.id}
        hint="Slides and notes from your course. Hal counts these as what you studied, never as work you did." />
      <${SourcesPanel} uid=${uid + "-off"} level="h3" heading="Official papers" parts=${[["official", null]]} target=${item.id}
        hint="Your transcript and certificates. These set your modules, marks and grade." /></div></details>`;
}

/** Inside a job card: its documents (a reference, a contract, the job description). */
export function JobSources({ item }) {
  return html`<details class="rec-hidden rec-card-sources"><summary>Add a document for this job</summary>
    <${SourcesPanel} uid=${"rec-job" + item.id} level="h3" heading="Documents for this job" parts=${[["job_doc", null]]}
      target=${item.id} hint="A reference, a contract or the job description." /></details>`;
}

/** "Showcase" on a project or job card (the card's star): a project to tell employers about, a job to lead with. */
export function StarButton({ item }) {
  const what = item.title || "this";
  return html`<button type="button" class="btn quiet small rec-star" aria-pressed=${item.star ? "true" : "false"}
    aria-label=${(item.star ? "Take out of your showcase: " : "Showcase: ") + what}
    onClick=${() => act(() => put("/profile/items/" + encodeURIComponent(item.id) + "/star", { star: !item.star }),
      item.star ? "Taken out of your showcase." : "In your showcase. Hal tells employers about it first.")}>
    <${Icon} n="star" c="s" />${item.star ? "In your showcase" : "Showcase"}</button>`;
}
