/* Your record: the folder of certificates, references and notes Hal uses as evidence, and the profile Hal
   reads out of it (jobs, education, skills, projects and the rest), each in its own tab. "Check your record"
   (pages/check.js) asks about what Hal found one card at a time.

   The tab is part of the route ("record-work"), so a link opens the right one; the old "record-files" opens
   Documents. Each tab has its own "Add" panel (record/sections.js): Overview the CVs, each job and education
   card its own documents, Projects its ways to add a project, Documents its upload. Overview has "Sort a big
   folder for me" (record/sort.js), whose proposals the person accepts: a banner at its top leads to "Check the
   list" while a sort waits, and a sort that finishes while the page is open opens its list. It also shows how
   an older Hal's bulk read went; each profile tab lists what Hal
   found for the person to check, change or hide; Documents keeps the folder's groups, whose files download
   from any computer (so a laptop reaches the desktop's record), and "Ask your record", whose answer names
   the files it came from. */
import { html, useState, useLayoutEffect } from "../core/html.js";
import { post, run } from "../core/api.js";
import { useApi, toast } from "../core/store.js";
import { go } from "../core/router.js";
import { plural, agoWords } from "../core/format.js";
import { Head, Tabs, Chip, Note, Empty } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";
import { OpenFolderButton } from "../ui/open-buttons.js";
import { IngestCard } from "../record/add.js";
import { KindSection, ConflictsTab } from "../record/items.js";
import { basicKey } from "../record/fields.js";
import { SourcesPanel, ProjectsPanel } from "../record/sections.js";
import { SortCard, SortBanner, useSortsOpenWhenReady } from "../record/sort.js";
// [tab, words, kinds shown in it]
const TABS = [["overview", "Overview", []], ["work", "Work history", ["job"]], ["education", "Education", ["education"]],
  ["skills", "Skills", ["skill"]], ["projects", "Projects", ["project"]],
  ["certificates", "Certificates and achievements", ["certificate", "achievement"]],
  ["about", "About you and links", ["basic", "link"]], ["inconsistencies", "Inconsistencies", []], ["documents", "Documents", []]];
// Tabs that were renamed, so an old link still opens the right one.
const RENAMED = { files: "documents" };
const TAB_OF = Object.fromEntries(TABS.flatMap(([t, , kinds]) => kinds.map(k => [k, t])));

function writeTab(t) {
  try { history.replaceState(null, "", "#" + (t === "overview" ? "record" : "record-" + t)); } catch (err) { /* a sandboxed frame may refuse */ }
}

function Ask() {
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState(false);
  const [out, setOut] = useState(null);
  const ask = async () => {
    if (!q.trim()) return;
    setBusy(true);
    try {
      const done = await run("/record/ask", { question: q });
      setOut(done.result || { answer: "", sources: [], note: done.error || "" });
    } catch (err) { toast(err.message); }
    setBusy(false);
  };
  // with no AI, record.ask answers with the passages and a note; a failed ask has only the note
  const shown = out && (out.answer || (out.passages && out.passages.length ? out.passages[0].text
    : out.note ? "" : "Your record has nothing on that yet."));
  return html`<section class="card"><h2 class="h2">Ask your record</h2>
    <form class="row" onSubmit=${e => { e.preventDefault(); ask(); }}>
      <input class="input" style="flex:1;min-width:200px" value=${q} onInput=${e => setQ(e.target.value)} aria-label="Question" />
      <button class="btn" type="submit" disabled=${busy}>${busy ? "Asking" : "Ask"}</button></form>
    ${shown && html`<div class="quote mine answer" style="margin-top:12px"><small>${out.answer ? "Hal's answer" : "From your record"}</small>${shown}</div>`}
    ${out && !out.answer && out.note && html`<p class="small sub" style="margin:8px 0 0">Hal could not write an answer: ${out.note}</p>`}
    ${out && out.sources && out.sources.length > 0 && html`<p class="small sub" style="margin:8px 0 0">From: ${out.sources.join("; ")}</p>`}
  </section>`;
}

function Group({ g }) {
  const [open, setOpen] = useState(false);
  const { data } = useApi(open ? "/record/files?group=" + encodeURIComponent(g.name) : null);
  return html`<div class="li"><div class="ic"><${Icon} n="folder" /></div><div class="grow">
      <button type="button" class="btn quiet small" aria-expanded=${open ? "true" : "false"} onClick=${() => setOpen(!open)}>
        <b>${g.name}</b></button> <${Chip}>${g.n}<//>${g.private && html` <${Chip}>Private<//>`}
      <div class="small sub">${g.sample}</div>
      ${open && data && html`<ul class="small" style="margin:6px 0 0;padding-left:18px">${data.map(f => html`<li>
        ${f.sub ? f.sub + "/" : ""}${f.name} <a href=${"/api/v2/record/file/" + f.file_id} download=${f.name}>Download</a></li>`)}</ul>`}
    </div></div>`;
}

function DocumentsTab({ data }) {
  return html`<div class="stack"><${SourcesPanel} uid="rec-docs" heading="Add documents" parts=${[["document", null]]}
      cards=${["education", "job", "certificate"]}
      hint="Certificates, awards, references, checks and anything else that helps. Choose what each one is."
      empty="Nothing added here yet." />
    ${data && data.exists && html`<div class="grid2"><section class="card"><h2 class="h2">What is in it</h2>
      ${data.groups.length ? data.groups.map(g => html`<${Group} g=${g} />`) : html`<${Empty}>Nothing yet. Add your first file on the Overview tab.<//>`}
      <p class="small sub" style="margin:10px 0 0">${data.last_read ? "Last read " + agoWords(data.last_read) + ". " : ""}Private files you mark as private are never sent to the AI.</p></section>
    <${Ask} /></div>`}</div>`;
}

/** The profile in numbers: each kind with how many Hal found and how many wait to be checked, then the basics. */
function Summary({ profile, onTab }) {
  const kinds = profile.kinds.filter(k => k.kind !== "basic" && k.kind !== "link");
  const open = (profile.conflicts || []).length;
  return html`<section class="card stack s" aria-labelledby="rec-sum-h"><h2 class="h2" id="rec-sum-h">Your profile</h2>
    <p class="small sub rec-line">${!kinds.some(k => k.n) ? "Nothing yet. Add your CV and Hal fills this in from it."
      : "What Hal found about you in your files. " + (profile.to_check ? plural(profile.to_check, "thing") + " to check." : "Everything is checked.")}</p>
    ${(profile.to_check > 0 || open > 0) && html`<div><button type="button" class="btn primary" onClick=${() => go("check")}>
      <${Icon} n="check" c="s" />Check your record</button>
      <p class="small sub rec-line">Hal only uses the facts you have checked.</p></div>`}
    <ul class="rec-counts">${kinds.map(k => html`<li key=${k.kind}><button type="button" class="btn quiet small" onClick=${() => onTab(TAB_OF[k.kind])}>
      <b>${k.label}</b></button> <span>${k.n}</span>${k.to_check > 0 && html` <${Chip} tone="warn">${k.to_check} to check<//>`}${k.studied > 0
        && html` <span class="small sub">and ${k.studied} studied in your course</span>`}</li>`)}
      <li><button type="button" class="btn quiet small" onClick=${() => onTab("inconsistencies")}><b>Inconsistencies</b></button>
        <span>${open}</span>${open > 0 && html` <${Chip} tone="warn">${open} to sort out<//>`}</li></ul>
    ${(profile.basics.length > 0 || profile.links.length > 0) && html`<dl class="kv rec-kv">
      ${profile.basics.map(b => html`<dt>${basicKey(b.fields.key)}</dt><dd>${b.fields.value}</dd>`)}
      ${profile.links.map(l => html`<dt>${l.fields.label || "Link"}</dt><dd>${l.fields.url}</dd>`)}</dl>`}
  </section>`;
}

/** The Your record page. */
export function Record({ arg }) {
  const named = RENAMED[arg] || arg;
  const [t, setTab] = useState(TABS.some(x => x[0] === named) ? named : "overview");
  useLayoutEffect(() => { setTab(TABS.some(x => x[0] === named) ? named : "overview"); }, [named]);
  const setT = next => { setTab(next); writeTab(next); };
  const { data, error, reload } = useApi("/record", { on: [ev => ev.type === "action" && ev.kind === "record.sync"] });
  const profile = useApi("/profile").data;
  const sorts = useSortsOpenWhenReady();
  const make = async () => {
    try { await post("/record/create-folder"); reload(); } catch (err) { toast(err.message); }
  };
  const n = kinds => (profile ? kinds.reduce((sum, k) => sum + ((profile.kinds.find(x => x.kind === k) || {}).n || 0), 0) || "" : "");
  const tabs = TABS.map(([id, words, kinds]) => [id, words, id === "inconsistencies"
    ? (profile && profile.conflicts.length) || "" : n(kinds)]);
  const kinds = (TABS.find(x => x[0] === t) || TABS[0])[2];
  return html`
    <${Head} title="Your record" sub="Your certificates, references, reports and notes, and what Hal found in them about you. Hal uses it as evidence when it writes, and says which file each fact came from.">
      ${data && data.exists && html`<${OpenFolderButton} path="/record/open-folder" />`}
    <//>
    ${error && html`<${Note} tone="warn">${error.message}<//>`}
    ${data && !data.exists && html`<${Note}>Your record folder is not there yet.
      <button class="btn small" type="button" onClick=${make}>Make the folder</button><//>`}
    ${t === "overview" && html`<${SortBanner} data=${sorts} />`}
    <div class="rec-tabs"><${Tabs} label="Your record" value=${t} onChange=${setT} tabs=${tabs} /></div>
    ${t === "overview" && html`<div class="grid2"><div class="stack">
        <${SourcesPanel} uid="rec-cvs" heading="Your CVs" parts=${[["cv", null], ["letter", null]]}
          hint="Add every CV you have; Hal uses the newest as your main one. Cover letters and personal statements only teach Hal how you write."
          empty="No CVs yet." />
        <${SortCard} />
        <${IngestCard} /></div>
      ${profile ? html`<${Summary} profile=${profile} onTab=${setT} />` : html`<p class="sub">Loading</p>`}</div>`}
    ${t === "projects" && html`<div class="stack" style="margin-bottom:16px"><${ProjectsPanel} /></div>`}
    ${kinds.length > 0 && html`<div class="stack">${kinds.map(k => html`<${KindSection} key=${k} kind=${k} heading=${kinds.length > 1} />`)}</div>`}
    ${t === "inconsistencies" && (profile ? html`<${ConflictsTab} profile=${profile} />` : html`<p class="sub">Loading</p>`)}
    ${t === "documents" && html`<${DocumentsTab} data=${data} />`}`;
}
