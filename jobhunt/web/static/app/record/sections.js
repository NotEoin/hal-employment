/* Your record, the "Add" panels: each tab takes its own files, links and folders, and the place says what
   they are (jobhunt/sections). Overview has the CVs panel, each job and education card its own documents,
   Projects its ways to add a project, and Documents its upload.

   A panel lists its section's sources with their kind, state and when they were last read, and the person's
   choices: "Showcase" (coursework; the app's word for a star, as "STAR" names a way to tell examples),
   "Make this my current CV" (the newest-dated CV is current until the person picks another; the others show
   "Older CV, fills gaps"), "Read again", "Remove", and a hint to move one
   that looks like it belongs elsewhere, or (in Documents) looks like another kind of document (Hal never
   moves or changes it on its own). Documents may name the card they are about: a course, a job or a
   certificate. Coursework and lecture material say which of the course's modules they are for: Hal suggests
   one, and the person confirms or changes it (`ModuleChoice`). A piece of coursework in the showcase becomes
   a project card of the degree, and one piece per course can be marked "This is my dissertation". Reading
   runs as the
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

/** Which of the course's modules a piece of coursework or lecture material is for: Hal's suggestion to
    confirm, or the person's own choice; `null` until they choose, "" for not one module. */
function ModuleChoice({ s, path }) {
  const uid = "rec-mod-" + s.id;
  if (!s.module_choices.length) return html`<p class="small sub rec-line">Add your transcript under Official papers to say which module this is for.</p>`;
  const choose = value => act(() => put(path, { module: value }), value ? "Saved: this is for " + value + "." : "Saved: not for one module.");
  return html`<div class="stack s rec-module">
    ${s.module === null && s.module_suggested && html`<${Note} tone="info">Hal thinks this is for ${s.module_suggested}.
      <button type="button" class="btn small" onClick=${() => choose(s.module_suggested)}>Yes, that is right</button><//>`}
    <div class="rec-field"><label class="lab" for=${uid}>Module</label>
      <select class="input" id=${uid} value=${s.module === null ? "?" : s.module} onChange=${e => choose(e.target.value)}>
        ${s.module === null && html`<option value="?" disabled>Choose a module</option>`}
        ${s.module_choices.map(m => html`<option key=${m} value=${m}>${m}</option>`)}
        <option value="">Not for one module</option></select></div></div>`;
}

/** One source: its title, kind, state and last read, with the person's choices. */
function SourceRow({ s }) {
  const id = encodeURIComponent(s.id);
  const path = SOURCES + "/" + id;
  return html`<li class=${"rec-source" + (s.parent_id != null ? " rec-child" : "")} data-source=${s.id}>
    <div class="grow"><div><b>${s.title}</b> <span class="small sub">${s.doc_kind_label}</span></div>
      <div class="chips"><${Chip} tone=${STATE_TONE[s.state] || "line"}>${s.state_words}<//>
        ${s.current && html`<${Chip} tone="info">Current CV<//>`}
        ${s.can_be_current && !s.current && s.state === "read" && html`<${Chip} tone="line">Older CV, fills gaps<//>`}</div>
      <div class="small sub">${s.last_checked ? "Last read " + agoWords(s.last_checked) : "Not read yet"}${s.dated ? ", dated " + s.dated : ""}${s.mark ? ". Mark: " + s.mark : ""}</div>
      ${s.summary && html`<p class="small rec-line">${s.summary}</p>`}
      ${s.error && html`<p class="small sub rec-line">${s.error}</p>`}
      ${s.can_have_module && s.state === "read" && html`<${ModuleChoice} s=${s} path=${path} />`}
      ${s.what_changed && html`<p class="small rec-line rec-changed"><b>What changed:</b> ${s.what_changed}</p>`}
      ${s.suggestion && html`<${Note} tone="info">This looks like it belongs in ${s.suggestion_label}.
        <button type="button" class="btn small" onClick=${() => act(() => put(path, { section: s.suggestion }),
          "Moved to " + s.suggestion_label + ". Hal is reading it again.")}>Move it to ${s.suggestion_label}</button><//>`}
      ${s.suggestion_kind && html`<${Note} tone="info">This looks like a ${s.suggestion_kind_label.toLowerCase()}.
        <button type="button" class="btn small" onClick=${() => act(() => put(path, { doc_kind: s.suggestion_kind }),
          "Marked as a " + s.suggestion_kind_label.toLowerCase() + ". Hal is reading it again.")}>Mark it as a ${s.suggestion_kind_label.toLowerCase()}</button><//>`}
      ${s.not_yours && !s.theirs && html`<${Note} tone="info">None of the changes are yours, so Hal made no card from it.
        <button type="button" class="btn small" aria-label=${"This is mine: " + s.title} onClick=${() => act(() => put(path, { theirs: true }),
          "Hal is reading it again as yours.")}>This is mine</button><//>`}
      ${s.pieces && s.pieces.length > 0 && html`<${PiecePicker} s=${s} path=${path} />`}</div>
    <div class="row wrap rec-acts">
      ${s.can_star && html`<button type="button" class="btn small" aria-pressed=${s.starred ? "true" : "false"}
        aria-label=${(s.starred ? "Take out of your showcase: " : "Showcase: ") + s.title}
        onClick=${() => act(() => put(path, { starred: !s.starred }), s.starred ? "Taken out of your showcase." : "In your showcase. Hal tells employers about it.")}>
        <${Icon} n="star" c="s" />${s.starred ? "In your showcase" : "Showcase"}</button>`}
      ${s.can_be_dissertation && html`<button type="button" class="btn small" aria-pressed=${s.dissertation ? "true" : "false"}
        aria-label=${(s.dissertation ? "This is my dissertation, press to undo: " : "This is my dissertation: ") + s.title}
        onClick=${() => act(() => put(path, { dissertation: !s.dissertation }), s.dissertation ? "No longer marked as your dissertation." : "Marked as your dissertation.")}>
        ${s.dissertation ? "My dissertation" : "This is my dissertation"}</button>`}
      ${s.can_be_current && !s.current && html`<button type="button" class="btn small"
        onClick=${() => act(() => put(path, { current: true }), "This is your current CV now.")}>Make this my current CV</button>`}
      ${s.state === "kept_out" && html`<button type="button" class="btn small" aria-label=${"Add " + s.title + " anyway"}
        onClick=${() => act(() => post(path + "/reread", { allow: true }), "Hal is reading it.")}>Add it anyway</button>`}
      ${s.can_go_back && html`<button type="button" class="btn quiet small" aria-label=${"Go back to the last read: " + s.title}
        onClick=${() => act(() => post(path + "/back"), "Hal put back what the last read found.")}>Go back to the last read</button>`}
      <button type="button" class="btn quiet small" aria-label=${"Read again: " + s.title}
        onClick=${() => act(() => post(path + "/reread"), "Hal is reading it again.")}><${Icon} n="refresh" c="s" />Read again</button>
      <button type="button" class="btn danger small" aria-label=${"Remove: " + s.title}
        onClick=${() => act(() => del(path), "Removed, with what Hal took from it.")}><${Icon} n="trash" c="s" />Remove</button></div></li>`;
}

/** The pieces a portfolio lists, to tick the ones to add as projects; each ticked piece becomes its own project. */
function PiecePicker({ s, path }) {
  const [ticked, setTicked] = useState(() => new Set(s.picked || []));
  const flip = url => setTicked(old => { const next = new Set(old); next.has(url) ? next.delete(url) : next.add(url); return next; });
  const uid = "rec-pieces-" + s.id;
  return html`<details class="rec-pieces"><summary>Pick pieces to add (${plural(s.pieces.length, "piece")} found)</summary>
    <fieldset class="stack s"><legend class="small sub">Each piece you tick becomes its own project.</legend>
      ${s.pieces.map((p, i) => html`<label class="row" for=${uid + "-" + i}>
        <input type="checkbox" id=${uid + "-" + i} checked=${ticked.has(p.url)} onChange=${() => flip(p.url)} />
        <span>${p.title || p.url}</span></label>`)}
      <div><button type="button" class="btn small primary" onClick=${() => act(() => put(path, { picked: [...ticked] }),
        "Hal is adding the pieces you ticked.")}>Add the ticked pieces</button></div></fieldset></details>`;
}

/** The sources of one section (for one card when `target` is given). */
export function SourceList({ section, target, empty }) {
  const path = SOURCES + "?section=" + section + (target != null ? "&target=" + encodeURIComponent(target) : "");
  const { data, error } = useApi(path, { on: reloadOn });
  if (error) return html`<p class="small sub">${error.message}</p>`;
  if (!data) return html`<p class="small sub">Loading</p>`;
  if (!data.length) return empty ? html`<p class="small sub rec-line">${empty}</p>` : null;
  return html`<ul class="rec-source-list">${nested(data).map(s => html`<${SourceRow} key=${s.id + ":" + s.revision + ":" + s.state} s=${s} />`)}</ul>`;
}

/** Sources in order with each one's children (an account's projects) right after it. */
function nested(list) {
  const ids = new Set(list.map(s => s.id));
  const kids = id => list.filter(s => s.parent_id === id);
  const walk = s => [s, ...kids(s.id).flatMap(walk)];
  return list.filter(s => s.parent_id == null || !ids.has(s.parent_id)).flatMap(walk);
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

// The cards a document may be about, in the order the page offers them, with the words for each kind.
const CARD_KINDS = [["education", "Education"], ["job", "Work"], ["certificate", "Certificates"]];

/** "Is it about one of these?": the cards of `kinds` a document may name, or none. */
function CardPick({ uid, kinds, value, onChange }) {
  const path = kind => (kinds.includes(kind) ? "/profile/" + kind : null);
  const lists = [useApi(path("education")).data, useApi(path("job")).data, useApi(path("certificate")).data];
  return html`<div class="rec-field"><label class="lab" for=${uid}>Is it about one of these? (optional)</label>
    <select class="input" id=${uid} value=${value == null ? "" : String(value)}
      onChange=${e => onChange(e.target.value ? Number(e.target.value) : null)}>
      <option value="">None of them</option>
      ${CARD_KINDS.map(([kind, words], i) => lists[i] && lists[i].items.length > 0 && html`<optgroup label=${words}>
        ${lists[i].items.filter(it => it.state !== "hidden").map(it => html`<option value=${String(it.id)}>${it.title}</option>`)}</optgroup>`)}
    </select></div>`;
}

/** The panel of a section (or of a few that share one, as CVs and letters do): what is it, add it, and the list.

    `parts` lists [section id, kinds to offer or null for all]; `target` is the card the sources belong to;
    `cards` lists the kinds of card the person may say a document is about (Documents). */
export function SourcesPanel({ uid, heading, hint, parts, target, empty, cards, level = "h2" }) {
  const sections = useSections();
  const [choice, setChoice] = useState(0);
  const [about, setAbout] = useState(null);
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
    ${cards && html`<${CardPick} uid=${uid + "-about"} kinds=${cards} value=${about} onChange=${setAbout} />`}
    <div class="row wrap">
      ${UPLOADS.has(chosen.kind.held) && html`<${Upload} uid=${uid + "-files"} section=${chosen.section.id} kind=${chosen.kind.id}
        target=${target ?? about} words="Add files" />`}
      ${chosen.kind.held === "files" && html`<${Upload} uid=${uid + "-folder"} section=${chosen.section.id} kind=${chosen.kind.id}
        target=${target ?? about} folder=${true} words="Add a folder" />`}</div>
    ${!chosen.kind.ready && html`<p class="small sub rec-line">Hal keeps these now and reads them once it can.</p>`}
    ${parts.map(([id]) => html`<${SourceList} key=${id} section=${id} target=${target} empty=${empty} />`)}
  </section>`;
}

// The fields of a project added by hand (jobhunt/sections/manual.py FIELDS): [field, label, hint, many lines].
const MANUAL_FIELDS = [["name", "Name", "Harbour clean-up day", false],
  ["what", "What it was", "An exhibition, a campaign, a piece of research, an event, something you built", true],
  ["when", "When", "Spring 2025", false], ["part", "Your part", "Organiser, lead designer, researcher", false],
  ["tools", "Tools or methods", "Separate them with commas", false], ["links", "Links", "One web address a line", true]];

/** The form for a project added by hand, with files and pictures to attach. */
function ManualForm({ onDone }) {
  const [form, setForm] = useState({});
  const [files, setFiles] = useState([]);
  const [busy, setBusy] = useState(false);
  const go = async e => {
    e.preventDefault();
    if (!(form.name || "").trim()) { toast("Give the project a name."); return; }
    setBusy(true);
    const out = await act(() => post(SOURCES + "/project/link", { doc_kind: "manual", form }), saidAdded);
    if (out && out.added.length && files.length) {
      const data = new FormData();
      files.forEach((f, i) => data.append("file" + i, f, f.name));
      await act(() => api("POST", SOURCES + "/" + encodeURIComponent(out.added[0].id) + "/files", data),
        "Attached " + plural(files.length, "file") + ".");
    }
    setBusy(false);
    if (out) onDone();
  };
  return html`<form class="stack s rec-manual" onSubmit=${go} aria-label="Add a project by hand">
    ${MANUAL_FIELDS.map(([key, label, hint, many]) => html`<div class="rec-field" key=${key}>
      <label class="lab" for=${"rec-manual-" + key}>${label}</label>
      ${many ? html`<textarea class="input" id=${"rec-manual-" + key} rows="3" placeholder=${hint}
          onInput=${e => setForm({ ...form, [key]: e.target.value })}>${form[key] || ""}</textarea>`
        : html`<input class="input" id=${"rec-manual-" + key} placeholder=${hint} value=${form[key] || ""}
          onInput=${e => setForm({ ...form, [key]: e.target.value })} />`}</div>`)}
    <div class="rec-field"><label class="lab" for="rec-manual-files">Files and pictures</label>
      <input id="rec-manual-files" type="file" multiple onChange=${e => setFiles([...e.target.files])} /></div>
    <div class="row wrap"><button type="submit" class="btn small primary" disabled=${busy}>${busy ? "Adding" : "Add the project"}</button>
      <button type="button" class="btn quiet small" onClick=${onDone}>Cancel</button></div></form>`;
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
      : chosen.held === "manual" ? html`<${ManualForm} onDone=${() => setWay(null)} />`
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

/** On a project card: what the last read of its own sources changed (jobhunt/sections/revisions.py). */
export function WhatChanged({ item }) {
  const { data } = useApi(SOURCES + "?section=project", { on: reloadOn });
  const ids = new Set(item.evidence.map(e => e.source_id).filter(id => id != null));
  const notes = (data || []).filter(s => ids.has(s.id) && s.what_changed).map(s => s.what_changed);
  if (!notes.length) return null;
  return html`<p class="small rec-line rec-changed"><b>What changed:</b> ${[...new Set(notes)].join(" ")}</p>`;
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
