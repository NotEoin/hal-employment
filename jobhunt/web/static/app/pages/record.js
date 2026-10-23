/* Your record: the folder of certificates, references and notes Hal uses as evidence.
   Files dropped here are copied into the record's Inbox and read at once; a question is an action whose
   answer names the files it came from. Each group opens to its files, which download from any computer,
   so a laptop reaches the desktop's record. */
import { html, useState } from "../core/html.js";
import { api, post, run } from "../core/api.js";
import { useApi, toast } from "../core/store.js";
import { plural } from "../core/format.js";
import { Head, Chip, Note, Empty } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";
import { OpenFolderButton } from "../ui/open-buttons.js";

const MINUTE_MS = 60000;
const HOUR_MIN = 60;
const DAY_MIN = 1440;
const HIDDEN = "position:absolute;width:1px;height:1px;opacity:0;overflow:hidden";

function agoWords(iso) {
  const min = Math.max(0, Math.round((Date.now() - new Date(iso).getTime()) / MINUTE_MS));
  if (min < 1) return "just now";
  if (min < HOUR_MIN) return plural(min, "minute") + " ago";
  if (min < DAY_MIN) return plural(Math.round(min / HOUR_MIN), "hour") + " ago";
  return plural(Math.round(min / DAY_MIN), "day") + " ago";
}

function Drop({ onAdded }) {
  const [over, setOver] = useState(false);
  const send = async list => {
    const files = [...(list || [])];
    if (!files.length) return;
    const form = new FormData();
    files.forEach((f, i) => form.append("file" + i, f, f.name));
    try {
      const out = await api("POST", "/record/files", form);
      toast(out.added.length === 1 ? "Added " + out.added[0] : "Added " + plural(out.added.length, "file"));
      onAdded();
    } catch (err) { toast(err.message); }
  };
  return html`<label class="drop" style=${"position:relative;display:block;cursor:pointer" + (over ? ";border-color:var(--primary)" : "")}
      onDragOver=${e => { e.preventDefault(); setOver(true); }} onDragLeave=${() => setOver(false)}
      onDrop=${e => { e.preventDefault(); setOver(false); send(e.dataTransfer.files); }}>
    <${Icon} n="upload" c="l" /><p style="margin:8px 0 4px"><b>Drop files here to add them</b></p>
    <p class="small" style="margin:0">PDF, Word, photos of certificates, or notes. Hal reads them within 10 minutes.</p>
    <input type="file" multiple style=${HIDDEN} aria-label="Add files to your record"
      onChange=${e => { send(e.target.files); e.target.value = ""; }} /></label>`;
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

/** The Your record page. */
export function Record() {
  const { data, error, reload } = useApi("/record", { on: [ev => ev.type === "action" && ev.kind === "record.sync"] });
  const make = async () => {
    try { await post("/record/create-folder"); reload(); } catch (err) { toast(err.message); }
  };
  return html`
    <${Head} title="Your record" sub="A folder of certificates, references, reports and notes. Hal uses it as evidence when it writes, and says which file each fact came from.">
      ${data && data.exists && html`<${OpenFolderButton} path="/record/open-folder" />`}
    <//>
    ${error && html`<${Note} tone="warn">${error.message}<//>`}
    ${data && !data.exists && html`<${Note}>Your record folder is not there yet.
      <button class="btn small" type="button" onClick=${make}>Make the folder</button><//>`}
    ${data && data.exists && html`<div class="grid2"><div class="stack">
      <${Drop} onAdded=${reload} />
      <${Ask} />
    </div>
    <section class="card"><h2 class="h2">What is in it</h2>
      ${data.groups.length ? data.groups.map(g => html`<${Group} g=${g} />`) : html`<${Empty}>Nothing yet. Drop your first file on the left.<//>`}
      <p class="small sub" style="margin:10px 0 0">${data.last_read ? "Last read " + agoWords(data.last_read) + ". " : ""}Private files you mark as private are never sent to the AI.</p></section></div>`}`;
}
