/* Your record, Overview: "Add to your record" (files and zips, folders on this computer, web links) and the
   card that shows how the latest read of them is going.

   A folder is read by its path on the computer Hal runs on, so that box is left out in a window from
   another computer (the route is local only); there, files and zips arrive as uploads. The progress card
   follows the `record.ingest` action: its events and a `profile` event reload it, and while it runs it is
   also asked again every few seconds, because counting files read adds no step and so sends no event. */
import { html, useState, useEffect } from "../core/html.js";
import { api, post } from "../core/api.js";
import { useApi, toast, invalidate } from "../core/store.js";
import { plural } from "../core/format.js";
import { isRemote } from "../core/remote.js";
import { Bar, Chip, Note } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";

const INGEST = "/record/ingest";
const KIND = "record.ingest";
const POLL_MS = 3000;       // a running read updates its counts more often than this
const MINUTE_S = 60;
const HOUR_S = 3600;
const HIDDEN = "position:absolute;width:1px;height:1px;opacity:0;overflow:hidden";
const STAGE_WORDS = { discovered: "Found", converted: "Turned into text", understood: "Read by Hal",
  written: "Saved to your record" };
const STATE_WORDS = { done: "read", duplicate: "copies of files already read", no_text: "with no text to read",
  skipped: "skipped", error: "could not be read", pending: "still to read" };
const LEFT_OUT_WORDS = { "junk folder": "folders of program files", link: "shortcuts",
  "program or data file": "programs and data files", unreadable: "files Hal could not open" };
const RUNNING = new Set(["queued", "running"]);
let following = null;       // the ingest action on screen, whose steps reload the card

/** Seconds left in plain words: "About 4 minutes left". */
export function etaWords(s) {
  if (s == null) return "";
  if (s < MINUTE_S) return "Less than a minute left";
  if (s < HOUR_S) return "About " + plural(Math.round(s / MINUTE_S), "minute") + " left";
  const h = Math.floor(s / HOUR_S);
  const m = Math.round((s % HOUR_S) / MINUTE_S);
  return "About " + plural(h, "hour") + (m ? " and " + plural(m, "minute") : "") + " left";
}

const lines = text => text.split(/\s*\n\s*/).map(s => s.trim()).filter(Boolean);

function Drop() {
  const [over, setOver] = useState(false);
  const send = async list => {
    const files = [...(list || [])];
    if (!files.length) return;
    const form = new FormData();
    files.forEach((f, i) => form.append("file" + i, f, f.name));
    try {
      const out = await api("POST", "/record/files", form);
      const added = out.added.length === 1 ? "Added " + out.added[0] : "Added " + plural(out.added.length, "file");
      toast(out.ingest_id ? added + ". Hal is reading the zip into your record." : added);
      invalidate("/record*");
    } catch (err) { toast(err.message); }
  };
  return html`<label class="drop" style=${"position:relative;display:block;cursor:pointer" + (over ? ";border-color:var(--primary)" : "")}
      onDragOver=${e => { e.preventDefault(); setOver(true); }} onDragLeave=${() => setOver(false)}
      onDrop=${e => { e.preventDefault(); setOver(false); send(e.dataTransfer.files); }}>
    <${Icon} n="upload" c="l" /><p style="margin:8px 0 4px"><b>Drop files here to add them</b></p>
    <p class="small" style="margin:0">PDF, Word, photos of certificates, notes, or a .zip of a whole folder. Hal reads them within 10 minutes.</p>
    <input type="file" multiple style=${HIDDEN} aria-label="Add files to your record"
      onChange=${e => { send(e.target.files); e.target.value = ""; }} /></label>`;
}

function Paste({ id, label, hint, button, path, field, said }) {
  const [text, setText] = useState("");
  const [busy, setBusy] = useState(false);
  const go = async e => {
    e.preventDefault();
    const list = lines(text);
    if (!list.length) { toast(hint); return; }
    setBusy(true);
    try { await post(path, { [field]: list }); setText(""); toast(said); invalidate(INGEST); }
    catch (err) { toast(err.message); }
    setBusy(false);
  };
  return html`<form class="rec-paste" onSubmit=${go}><label class="lab" for=${id}>${label}</label>
    <p class="small sub" id=${id + "-h"}>${hint}</p>
    <textarea class="input" id=${id} aria-describedby=${id + "-h"} value=${text} onInput=${e => setText(e.target.value)}></textarea>
    <div><button type="submit" class="btn small" disabled=${busy}>${button}</button></div></form>`;
}

/** The "Add to your record" card. */
export function AddCard() {
  return html`<section class="card stack s" aria-labelledby="rec-add-h"><h2 class="h2" id="rec-add-h">Add to your record</h2>
    <${Drop} />
    ${!isRemote() && html`<${Paste} id="rec-folders" label="Folders on this computer"
      hint="Type or paste where each folder is, one a line. You can copy it from the top of the folder's window. Hal reads everything inside."
      button="Read these folders" path=${INGEST} field="folders" said="Hal is reading the folders into your record." />`}
    <${Paste} id="rec-links" label="Web links"
      hint="Paste links to your own website or blog, your GitHub page, or a Google Doc shared with anyone, one a line."
      button="Read these links" path="/record/links" field="urls" said="Hal is reading the links into your record." /></section>`;
}

function stopNow(id) {
  post("/actions/" + encodeURIComponent(id) + "/stop").then(
    () => { toast("Hal stopped. What it read so far is kept."); invalidate(INGEST); }, err => toast(err.message));
}

function readAgain() {
  post(INGEST + "/again").then(() => { toast("Hal is reading the rest of your documents."); invalidate(INGEST); },
    err => toast(err.message));
}

const counted = obj => Object.entries(obj || {}).filter(([, n]) => n > 0);

/** The latest read of files into the record: a bar, time left, the file in hand, counts, and what went wrong. */
export function IngestCard() {
  const { data, reload } = useApi(INGEST, { on: [ev => (ev.type === "action" && ev.kind === KIND) || ev.type === "profile"
    || (ev.type === "step" && !!following && ev.action_id === following)] });
  following = data ? data.action_id : null;
  const running = !!data && RUNNING.has(data.state);
  useEffect(() => {
    if (!running) return undefined;
    const t = setInterval(() => reload().catch(() => {}), POLL_MS);
    return () => clearInterval(t);
  }, [running]);
  if (!data || data.state === "none") return null;
  const pct = data.total ? (data.done / data.total) * 100 : 0;
  const states = counted(data.states).filter(([s]) => s !== "not_downloaded");
  const leftOut = counted(data.left_out);
  const title = data.again
    ? (running ? "Reading the rest with the AI" : data.state === "done" ? "The rest was read with the AI" : "Hal stopped reading the rest")
    : (running ? "Reading your files" : data.state === "done" ? "Your files were read" : "Hal stopped reading your files");
  return html`<section class="card stack s rec-ingest" aria-labelledby="rec-ing-h" data-state=${data.state}>
    <div class="row rec-head"><h2 class="h2 grow" id="rec-ing-h">${title}</h2>
      ${running && data.action_id && html`<button type="button" class="btn stop small" onClick=${() => stopNow(data.action_id)}>
        <${Icon} n="stop" c="s" />Stop</button>`}</div>
    <${Bar} pct=${pct} label="Files read" />
    <p class="rec-line"><b>${data.done} of ${data.total} read (${Math.round(pct)}%)</b>${running && data.eta_s != null ? html`. <span>${etaWords(data.eta_s)}</span>` : ""}</p>
    ${running && data.current && html`<p class="small sub rec-line">Now: ${data.current}</p>`}
    ${counted(data.stages).length > 0 && html`<div><div class="small sub">How far each file got</div><div class="chips">
      ${counted(data.stages).map(([s, n]) => html`<${Chip} tone="line">${STAGE_WORDS[s] || s}: ${n}<//>`)}</div></div>`}
    ${counted(data.categories).length > 0 && html`<div><div class="small sub">What Hal found</div><div class="chips">
      ${counted(data.categories).map(([c, n]) => html`<${Chip} tone="info">${c}: ${n}<//>`)}</div></div>`}
    ${states.length > 0 && html`<p class="small sub rec-line">${states.map(([s, n]) => n + " " + (STATE_WORDS[s] || s)).join(", ")}.</p>`}
    ${data.not_downloaded > 0 && html`<${Note} tone="warn"><b>${plural(data.not_downloaded, "file is", "files are")} not downloaded.</b>
      ${" "}They are kept online only (for example in OneDrive, Dropbox or iCloud), so this computer has an empty copy and Hal cannot read it.
      Download them first (in OneDrive, choose "Always keep on this device"), then add them again.
      <ul class="rec-paths">${data.not_downloaded_paths.map(p => html`<li>${p}</li>`)}</ul>
      ${data.not_downloaded > data.not_downloaded_paths.length && html`<span class="small">and ${data.not_downloaded - data.not_downloaded_paths.length} more</span>`}<//>`}
    ${data.errors.length > 0 && html`<div><div class="small sub">Could not be read</div><ul class="rec-paths small">
      ${data.errors.map(e => html`<li><b>${e.path}</b>: ${e.error}</li>`)}</ul></div>`}
    ${leftOut.length > 0 && html`<p class="small sub rec-line">Left out on purpose: ${leftOut.map(([w, n]) => n + " " + (LEFT_OUT_WORDS[w] || w)).join(", ")}.</p>`}
    ${!running && data.by_rules > 0 && html`<div class="row wrap rec-again"><p class="small sub grow rec-line">
      ${plural(data.by_rules, "note was", "notes were")} filed by headings and words only, without the AI reading them.</p>
      <button type="button" class="btn small" onClick=${readAgain}>Read them with the AI</button></div>`}
  </section>`;
}
