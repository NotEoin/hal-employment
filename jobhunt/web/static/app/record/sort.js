/* Your record, "Sort a big folder for me": the person gives a folder on this computer, or uploads a zip or
   files, and Hal proposes where each file goes (jobhunt/sections/sorter.py). Nothing is added until the
   person accepts it.

   The review table shows the proposals one section at a time, a page of rows at once, with how many each
   section has, so a dump of thousands of files stays quick. Each row can go to another section, kind or
   card, be unticked, or (a project) be marked a showcase piece. "Add the ticked ones" accepts a section,
   "Add everything ticked" all of them; files that still need their course or job wait until one is chosen.
   "Left out" lists what Hal would not guess in, each with why, and "Copies" the older copies it set aside;
   either can be given a section by hand. Sorting runs as `record.sort` and adding as `record.sort_accept`;
   their events and a `sort` event reload the table. The folder box is left out in a window from another
   computer, where a zip is uploaded instead.

   "Courses Hal found" lists the courses the folders name (jobhunt/sections/sort_review.py), first, as
   coursework and lecture notes wait for their course: adding one makes its education card, and its files
   find it. Lecture material and coursework come one row a module, with its file count: ticking, moving or
   giving a card to that row changes all its files, and "Show the files" lists them one by one. A project's
   numbered folders ("lab1" to "lab8") come as one row the same way.

   A record an older Hal read in whole gets "Move my record to sections" (jobhunt/sections/move.py): the same
   table proposes a section for each of its notes. "Finish the move" sets aside what the notes nobody
   accepted gave, and "Undo the move" puts everything back as it was. */
import { html, useState } from "../core/html.js";
import { api, post, put, del } from "../core/api.js";
import { useApi, toast, invalidate } from "../core/store.js";
import { plural } from "../core/format.js";
import { isRemote } from "../core/remote.js";
import { Bar } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";
import { useSections } from "./sections.js";

const SORT = "/record/sort";
const KINDS = new Set(["record.sort", "record.sort_accept"]);
const PAGE = 50;            // jobhunt/sections/sorts.py PAGE
const HIDDEN = "position:absolute;width:1px;height:1px;opacity:0;overflow:hidden";
const STATE_WORDS = { new: "Waiting to start", sorting: "Sorting", ready: "Ready for you to check", failed: "Could not be sorted",
  undone: "Undone" };
const OTHER_VIEWS = [["left_out", "Left out"], ["copy", "Copies"], ["added", "Added"], ["failed", "Could not be added"]];
const reloadOn = id => [ev => (ev.type === "action" && KINDS.has(ev.kind)) || (ev.type === "sort" && (id == null || ev.sort_id === id))
  || ev.type === "profile"];

function refresh() { invalidate(SORT + "*"); invalidate("/record/sources*"); invalidate("/profile*"); }

async function act(fn, said) {
  try { const out = await fn(); if (said) toast(typeof said === "function" ? said(out) : said); refresh(); return out; }
  catch (err) { toast(err.message); return null; }
}

/** Starting a sort: a folder on this computer, or a zip or files uploaded. */
export function SortStart({ onStarted }) {
  const [folder, setFolder] = useState("");
  const [busy, setBusy] = useState(false);
  const started = out => { if (out && onStarted) onStarted(out.sort.id); };
  const sortFolder = async e => {
    e.preventDefault();
    if (!folder.trim()) { toast("Type or paste where the folder is."); return; }
    setBusy(true);
    started(await act(() => post(SORT, { folders: [folder.trim()] }), "Hal is sorting the folder. Nothing is added until you say so."));
    setBusy(false);
  };
  const upload = async list => {
    const files = [...(list || [])];
    if (!files.length) return;
    const form = new FormData();
    files.forEach((f, i) => form.append("file" + i, f, f.name));
    setBusy(true);
    started(await act(() => api("POST", SORT + "/upload", form), "Hal is sorting what you uploaded. Nothing is added until you say so."));
    setBusy(false);
  };
  return html`<div class="stack s">
    ${!isRemote() && html`<form class="row wrap" onSubmit=${sortFolder}>
      <label class="lab" for="rec-sort-folder" style="width:100%">A folder on this computer</label>
      <input class="input grow" id="rec-sort-folder" value=${folder} placeholder="The folder's path, for example from the top of its window"
        onInput=${e => setFolder(e.target.value)} />
      <button type="submit" class="btn" disabled=${busy}>Sort this folder</button></form>`}
    <label class="btn" style="position:relative;cursor:pointer;align-self:flex-start">${busy ? "Sending..." : "Upload a zip or files to sort"}
      <input type="file" multiple style=${HIDDEN} aria-label="Upload a zip or files to sort" disabled=${busy}
        onChange=${e => { upload(e.target.files); e.target.value = ""; }} /></label></div>`;
}

/** The kinds of document a section takes, as options. */
function kindOptions(sections, section) {
  const s = sections && sections[section];
  return s ? s.doc_kinds.filter(k => k.held === "upload" || k.held === "files" || k.held === "folder") : [];
}

/** One proposal, or one module's group of files: tick, file, section, kind, card, showcase, and why. A group
    is changed as a whole, and "Show the files" lists its own rows. */
function Row({ row, sections, cards, sortId, onOpen }) {
  const change = body => act(() => row.group ? post(SORT + "/" + sortId + "/groups", { group: row.group, ...body })
    : put("/record/sort-rows/" + row.id, body));
  const kinds = kindOptions(sections, row.section);
  const choices = row.target_kind ? (cards[row.target_kind] || []) : [];
  const uid = "rec-sort-" + (row.id == null ? row.group : row.id);
  const open = row.state === "proposed" || row.state === "left_out" || row.state === "copy" || row.state === "failed";
  const unit = row.section === "project" ? "folder" : "file";     // a project's group is its numbered folders
  return html`<tr data-row=${row.id == null ? row.group : row.id}>
    <td>${row.state === "proposed" && html`<input type="checkbox" checked=${row.ticked} aria-label=${"Add " + row.name}
      onChange=${e => change({ ticked: e.target.checked })} />`}</td>
    <td><b>${row.name}</b>${row.group && html` <span class="small sub">(${plural(row.count, unit)})</span>`}
      <div class="small sub" style="overflow-wrap:anywhere">${row.rel}</div>
      <div class="small sub">${row.why}${row.by === "ai" ? " (the AI's guess)" : ""}</div>
      ${row.group && html`<button type="button" class="btn quiet small" aria-label=${"Show the files of " + row.name}
        onClick=${() => onOpen(row.group)}>Show the files</button>`}</td>
    <td>${open ? html`<select class="input" aria-label=${"Where " + row.name + " goes"} value=${row.section}
        onChange=${e => change({ section: e.target.value })}>
        <option value="">Leave it out</option>
        ${Object.values(sections || {}).map(s => html`<option value=${s.id}>${s.label}</option>`)}</select>`
      : row.section_label}</td>
    <td>${open && row.section && !row.group ? html`<select class="input" aria-label=${"What " + row.name + " is"} value=${row.doc_kind}
        onChange=${e => change({ doc_kind: e.target.value })}>
        ${kinds.map(k => html`<option value=${k.id}>${k.label}</option>`)}</select>` : row.doc_kind_label}</td>
    <td>${open && row.target_kind ? html`<select class="input" id=${uid + "-card"} aria-label=${"Which card " + row.name + " is for"}
        value=${row.target_id == null ? "" : String(row.target_id)} onChange=${e => change({ target_id: e.target.value ? Number(e.target.value) : null })}>
        <option value="">${row.needs_card ? "Choose one" : "None"}</option>
        ${choices.map(c => html`<option value=${String(c.id)}>${c.title}</option>`)}</select>` : row.target_title}
      ${row.needs_card && row.state === "proposed" && html`<div class="small sub">Choose one, or add your CV first: Hal finds your courses and jobs in it.</div>`}</td>
    <td>${row.can_star && row.state === "proposed" && html`<label class="small"><input type="checkbox" checked=${row.star}
      onChange=${e => change({ star: e.target.checked })} /> Showcase</label>`}</td></tr>`;
}

/** One course Hal found in the folders: its words, which the person may change, and "Add this course". */
function CourseRow({ id, course }) {
  const [words, setWords] = useState({ qualification: course.qualification, subject: course.subject, institution: course.institution });
  const set = key => e => setWords({ ...words, [key]: e.target.value });
  const add = () => act(() => post(SORT + "/" + id + "/courses", { key: course.key, ...words }), out =>
    "Added " + (out.card.title || "the course") + ". " + plural(out.matched, "file") + " found it.");
  const facts = [plural(course.files, "file"), course.modules.length ? "modules: " + course.modules.join(", ") : "",
    course.start ? course.start + " to " + course.end : ""].filter(Boolean).join("; ");
  return html`<li class="row wrap rec-course" data-course=${course.key}>
    <label class="small">Qualification <input class="input" value=${words.qualification} onInput=${set("qualification")} /></label>
    <label class="small">Subject <input class="input" value=${words.subject} onInput=${set("subject")} /></label>
    <label class="small">Where <input class="input" value=${words.institution} onInput=${set("institution")} /></label>
    <span class="small sub grow" style="overflow-wrap:anywhere">${facts}</span>
    <button type="button" class="btn small primary" aria-label=${"Add the course " + course.title} onClick=${add}>Add this course</button></li>`;
}

/** "Courses Hal found": the courses the sort's folders name, shown first so coursework and lecture notes have a card. */
function CoursesFound({ id, courses }) {
  if (!courses || !courses.length) return null;
  return html`<div class="stack s rec-courses"><h3 class="h3">Courses Hal found</h3>
    <p class="small sub rec-line">Your folders name these courses. Add each one, changing its words if Hal got them wrong,
      and its coursework and lecture notes find it.</p>
    <ul class="rec-paths">${courses.map(c => html`<${CourseRow} key=${c.key} id=${id} course=${c} />`)}</ul></div>`;
}

/** The review table of one sort. */
export function SortReview({ id, onClose }) {
  const sections = useSections();
  const [view, setView] = useState({ section: "", state: "proposed", offset: 0, group: "" });
  const q = "?state=" + view.state + "&offset=" + view.offset + "&limit=" + PAGE + (view.section ? "&section=" + view.section : "")
    + (view.group ? "&group=" + encodeURIComponent(view.group) : "");
  const { data } = useApi(SORT + "/" + id + q, { on: reloadOn(id) });
  if (!data) return html`<p class="sub">Loading</p>`;
  const c = data.counts;
  const show = (section, state = "proposed") => setView({ section, state, offset: 0, group: "" });
  const openGroup = group => setView({ ...view, group, offset: 0 });
  const accept = section => act(() => post(SORT + "/" + id + "/accept", section ? { section } : {}), out =>
    (out.accepted ? "Adding " + plural(out.accepted, "file") + " to your record. Hal reads each in its place." : "Nothing ticked to add.")
    + (out.need_card ? " " + plural(out.need_card, "file waits", "files wait") + " for its course or job." : ""));
  const tickAll = ticked => act(() => post(SORT + "/" + id + "/tick", view.section ? { ticked, section: view.section } : { ticked }));
  const running = data.state === "new" || data.state === "sorting";
  const moving = data.kind === "move";
  const finish = () => act(() => post(SORT + "/" + id + "/finish"), out =>
    "Done. What " + plural(out.set_aside, "older note", "older notes") + " gave is set aside; Undo the move puts it back.");
  const undo = () => act(() => post(SORT + "/" + id + "/undo"), out =>
    "Undone. " + plural(out.removed, "file was", "files were") + " taken out and your record is as it was.");
  const label = view.state === "proposed" ? (view.section ? (c.sections.find(s => s.id === view.section) || {}).label : "Everything Hal proposes")
    : (OTHER_VIEWS.find(v => v[0] === view.state) || [, ""])[1];
  return html`<section class="card stack s rec-sort" aria-labelledby="rec-sort-h" data-sort=${id}>
    <div class="row rec-head"><h2 class="h2 grow" id="rec-sort-h">${data.kind === "move" ? "Moving your record to sections" : "Sorting " + data.label}</h2>
      ${onClose && html`<button type="button" class="btn quiet small" onClick=${onClose}>Close</button>`}</div>
    <p class="small sub rec-line">${STATE_WORDS[data.state] || data.state}. Nothing goes into your record until you add it.
      ${data.error && " " + data.error}</p>
    ${running && html`<${Bar} pct=${0} label="Sorting" />`}
    ${c.sections.some(s => s.needs_card) && html`<p class="small sub rec-line">Add your CVs and papers first, so your courses exist.</p>`}
    <${CoursesFound} id=${id} courses=${data.courses} />
    <div class="chips" role="group" aria-label="Show">
      <button type="button" class=${"btn small" + (view.state === "proposed" && !view.section ? " primary" : "")} onClick=${() => show("")}>All proposed: ${c.proposed}</button>
      ${c.sections.map(s => html`<button type="button" class=${"btn small" + (view.section === s.id && view.state === "proposed" ? " primary" : "")}
        onClick=${() => show(s.id)}>${s.label}: ${s.proposed}${s.needs_card ? " (" + s.needs_card + " need a card)" : ""}</button>`)}
      ${OTHER_VIEWS.filter(([state]) => c[state] > 0).map(([state, words]) => html`<button type="button"
        class=${"btn small quiet" + (view.state === state ? " primary" : "")} onClick=${() => show("", state)}>${words}: ${c[state]}</button>`)}</div>
    ${view.state === "proposed" && c.proposed > 0 && html`<div class="row wrap">
      <button type="button" class="btn small" onClick=${() => tickAll(true)}>Tick all</button>
      <button type="button" class="btn small" onClick=${() => tickAll(false)}>Untick all</button>
      ${view.section && html`<button type="button" class="btn small primary" onClick=${() => accept(view.section)}>Add the ticked ${label}</button>`}
      <button type="button" class="btn small primary" onClick=${() => accept(null)}>Add everything ticked</button></div>`}
    ${moving && !data.undone && html`<div class="row wrap">
      <p class="small sub grow rec-line">When you have added what you want, finish the move: what the other older notes gave is set aside, never deleted.</p>
      <button type="button" class="btn small" onClick=${finish}>Finish the move</button>
      ${data.can_undo && html`<button type="button" class="btn small" onClick=${undo}>Undo the move</button>`}</div>`}
    <div class="row wrap"><h3 class="h3 grow">${view.group ? "Files in this group" : label}: ${data.total}</h3>
      ${view.group && html`<button type="button" class="btn small" onClick=${() => setView({ ...view, group: "", offset: 0 })}>Back to the list</button>`}</div>
    ${data.rows.length ? html`<div style="overflow-x:auto"><table class="rec-sort-table"><thead><tr><th>Add</th><th>File</th><th>Where it goes</th>
      <th>What it is</th><th>Course or job</th><th>Showcase</th></tr></thead>
      <tbody>${data.rows.map(row => html`<${Row} key=${row.id == null ? row.group : row.id} row=${row} sections=${sections}
        cards=${data.cards} sortId=${id} onOpen=${openGroup} />`)}</tbody></table></div>`
      : html`<p class="small sub">Nothing here.</p>`}
    ${data.total > PAGE && html`<div class="row"><button type="button" class="btn small" disabled=${view.offset === 0}
        onClick=${() => setView({ ...view, offset: Math.max(0, view.offset - PAGE) })}>Previous</button>
      <span class="small sub">${view.offset + 1} to ${Math.min(view.offset + PAGE, data.total)} of ${data.total}</span>
      <button type="button" class="btn small" disabled=${view.offset + PAGE >= data.total}
        onClick=${() => setView({ ...view, offset: view.offset + PAGE })}>Next</button></div>`}
    ${Object.keys(data.skipped || {}).length > 0 && html`<p class="small sub rec-line">Not looked at, on purpose: ${Object.entries(data.skipped)
      .map(([why, n]) => n + " " + why).join(", ")}.</p>`}
  </section>`;
}

/** "Sort a big folder for me" on Overview: start one, list the latest, and review one. */
export function SortCard() {
  const { data } = useApi(SORT, { on: reloadOn(null) });
  const [open, setOpen] = useState(null);
  const forget = s => act(() => del(SORT + "/" + s.id), "Hal forgot that sort. What you added stays.");
  const sorts = data ? data.sorts : [];
  const moveRecord = async () => {
    const out = await act(() => post("/record/move"), "Hal is looking through your older record. Nothing changes until you say so.");
    if (out) setOpen(out.sort.id);
  };
  return html`<div class="stack">
    <section class="card stack s" aria-labelledby="rec-sort-start-h"><h2 class="h2" id="rec-sort-start-h">Sort a big folder for me</h2>
      <p class="small sub rec-line">Have a folder of everything from your studies and jobs? Hal looks through it and suggests where each file goes:
        your CVs, your coursework and lecture notes by course, your projects, your certificates. You check the list and add what is right.
        Hal leaves out anything it is unsure about, private papers such as medical letters, and older copies.</p>
      <${SortStart} onStarted=${setOpen} />
      ${data && data.can_move && html`<div class="row wrap"><p class="small sub grow rec-line">An older Hal read files into your record
        all at once. Move them into the parts of your record they belong in: you check each one first, and one click undoes it.</p>
        <button type="button" class="btn" onClick=${moveRecord}>Move my record to sections</button></div>`}
      ${sorts.length > 0 && html`<ul class="rec-paths">${sorts.map(s => html`<li class="row wrap" key=${s.id}>
        <span class="grow"><b>${s.label || "Your files"}</b>: ${STATE_WORDS[s.state] || s.state}.
          ${s.counts.proposed} to check, ${s.counts.added} added, ${s.counts.left_out} left out.</span>
        <button type="button" class="btn small" onClick=${() => setOpen(s.id)}>Check the list</button>
        <button type="button" class="btn quiet small" aria-label=${"Forget " + (s.label || "this sort")} onClick=${() => forget(s)}>
          <${Icon} n="x" c="s" />Forget</button></li>`)}</ul>`}</section>
    ${open != null && html`<${SortReview} id=${open} onClose=${() => setOpen(null)} />`}
  </div>`;
}

