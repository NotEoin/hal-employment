/* Documents: every application's files, ready to copy or download, and "Copy-paste
   details" for forms Hal cannot fill. Files come from the application's folder through /files, so a window
   opened from another computer gets them too; "Open the folder" shows only where folders can open. */
import { html, cx, useState, useEffect } from "../core/html.js";
import { useApi, toast } from "../core/store.js";
import { copyText } from "../ui/clipboard.js";
import { get, post } from "../core/api.js";
import { go } from "../core/router.js";
import { Icon } from "../ui/icons.js";
import { Head, Tabs, Empty } from "../ui/controls.js";
import { OpenFolderButton, OpenFileButton } from "../ui/open-buttons.js";

const ICONS = { cv_pdf: "file", cv_docx: "file", letter_pdf: "file", letter_txt: "file", answers_md: "chat",
  message_txt: "mail", advert_html: "globe", prep_md: "book" };
const COPY_WORDS = { answers_md: "Copy all", message_txt: "Copy", letter_pdf: "Copy text", letter_txt: "Copy text" };


function approvedWords(at) {
  if (!at) return "Not approved yet.";
  const d = new Date(at);
  const day = d.toLocaleDateString("en-GB", { day: "numeric", month: "short" });
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  return "You approved these on " + day + " at " + time + ".";
}

function FileRow({ app, f }) {
  const copy = async () => {
    try { const r = await get("/documents/" + app + "/text/" + f.text_kind); copyText(r.text); } catch (err) { toast(err.message); }
  };
  const download = "/api/v2/files/" + encodeURIComponent(f.file_id || "") + "?download=1";
  return html`<div class="li" style="align-items:center"><div class="ic"><${Icon} n=${ICONS[f.kind] || "file"} /></div>
    <div class="grow"><b>${f.name}</b><div class="small sub">${f.sub}</div></div>
    <div class="row tight">
      ${f.acts.includes("open") && f.file_id && html`<${OpenFileButton} fileId=${f.file_id} small=${true} />`}
      ${f.acts.includes("download") && f.file_id && html`<a class="btn small" href=${download}><${Icon} n="download" c="s" />Download</a>`}
      ${f.acts.includes("copy") && f.has_text && html`<button type="button" class="btn small" onClick=${copy}><${Icon} n="copy" c="s" />${COPY_WORDS[f.kind] || "Copy"}</button>`}
    </div></div>`;
}

function AppFiles({ id }) {
  const one = useApi(id ? "/documents/" + id : null);
  if (!id) return html`<section class="card"><${Empty}>Pick an application to see its files.<//></section>`;
  if (one.error) return html`<section class="card"><${Empty}>${one.error.message}<//></section>`;
  const a = one.data;
  if (!a) return html`<section class="card"><${Empty}>Loading<//></section>`;
  const sent = async () => {
    try { await post("/applications/" + encodeURIComponent(a.job_id) + "/sent"); toast("Moved to Sent"); one.reload(); }
    catch (err) { toast(err.message); }
  };
  const cvBits = [a.cv && a.cv.version_n && "CV version " + a.cv.version_n, a.cv && a.cv.type, a.cv && a.cv.look].filter(Boolean);
  return html`<section class="card"><h2 class="h2">${a.title}</h2>
    <p class="small sub">${[a.org, ...cvBits].join(". ")}. ${approvedWords(a.approved_at)}</p>
    ${a.files.length ? a.files.map(f => html`<${FileRow} app=${id} f=${f} />`) : html`<${Empty}>Hal has not made the documents for this job yet.<//>`}
    <div class="row" style="margin-top:14px">
      ${a.files.length > 0 && html`<a class="btn primary" href=${"/api/v2/documents/" + id + "/zip"}><${Icon} n="download" c="s" />Download all</a>`}
      ${a.files.length > 0 && html`<${OpenFolderButton} path=${"/documents/" + id + "/open-folder"} />`}
      ${a.sent_myself_ok && html`<button type="button" class="btn quiet" onClick=${sent}>I sent it myself</button>`}
    </div>
    ${a.folder_words && html`<p class="small sub mono docs-path">${a.folder_words}</p>`}</section>`;
}

function Details() {
  const d = useApi("/documents/details");
  const rows = (d.data && d.data.rows) || [];
  return html`<div class="card"><p class="small sub">For forms Hal cannot fill: copy each detail straight into the employer's form.</p>
    ${d.error ? html`<${Empty}>${d.error.message}<//>` : rows.length === 0 && d.data
      ? html`<${Empty}>Add your details in Settings, You, and they show here.<//>`
      : rows.map(r => html`<div class="li" style="align-items:center"><div class="grow"><div class="small sub">${r.label}</div><b>${r.value}</b></div>
        <button type="button" class="btn small" aria-label=${"Copy " + r.label} onClick=${() => copyText(r.value)}><${Icon} n="copy" c="s" />Copy</button></div>`)}</div>`;
}

/** The Documents page; `arg` is an application number or "details". */
export function Docs({ arg }) {
  const [tab, setTab] = useState(arg === "details" ? "profile" : "apps");
  const [q, setQ] = useState("");
  const list = useApi("/documents" + (q.trim() ? "?q=" + encodeURIComponent(q.trim()) : ""));
  const apps = (list.data && list.data.apps) || [];
  const [sel, setSel] = useState(arg && arg !== "details" ? arg : "");
  useEffect(() => { if (arg && arg !== "details") setSel(arg); if (arg === "details") setTab("profile"); }, [arg]);
  const chosen = sel || (apps[0] && String(apps[0].id)) || "";
  const pick = id => { setSel(String(id)); go("docs-" + id, { replace: true }); };
  return html`
    <${Head} title="Documents" sub="Every application's CV, letter and answers, ready to copy or download. Use these on sites Hal cannot fill in.">
      <input class="input docs-search" type="search" placeholder="Search documents" aria-label="Search documents" value=${q}
        onInput=${e => setQ(e.target.value)} />
    <//>
    <${Tabs} value=${tab} onChange=${setTab} label="Documents" tabs=${[["apps", "By application", apps.length], ["profile", "Copy-paste details", ""]]} />
    ${tab === "apps" ? html`<div class="grid2 even">
      <div class="card">${list.error ? html`<${Empty}>${list.error.message}<//>` : !list.data ? html`<${Empty}>Loading<//>`
        : apps.length === 0 ? html`<${Empty}>${q ? "No documents match." : "Documents appear here once Hal has made them for an application."}<//>`
        : apps.map(r => html`<button type="button" class=${cx("li docs-row", String(r.id) === chosen && "on")} style="align-items:center"
            aria-current=${String(r.id) === chosen ? "true" : null} onClick=${() => pick(r.id)}>
            <div class="ic"><${Icon} n="folder" /></div><div class="grow"><b>${r.title}</b><div class="small sub">${r.org}. ${r.column_title}.</div></div>
            <span class="chips">${r.kinds.map(k => html`<span class="chip">${k}</span>`)}</span></button>`)}</div>
      <${AppFiles} id=${chosen} />
    </div>` : html`<${Details} />`}`;
}
