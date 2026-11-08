/* Your record, Overview: the card that shows how the latest bulk read of an older Hal went, with "Read them with
   the AI" and "Tidy my record" for a record built that way.

   New files never come in this way any more: each tab of Your record takes its own, and "Sort a big folder for
   me" (record/sort.js) proposes a place for each file of a big folder for the person to accept. The card
   follows the `record.ingest` action: its events and a `profile` event reload it, and while it runs it is
   also asked again every few seconds, because counting files read adds no step and so sends no event. Files
   kept out because they look private are listed by name and reason only, each with "Add it anyway". */
import { html, useEffect } from "../core/html.js";
import { post } from "../core/api.js";
import { useApi, toast, invalidate } from "../core/store.js";
import { plural } from "../core/format.js";
import { Bar, Chip, Note } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";

const INGEST = "/record/ingest";
const KIND = "record.ingest";
const POLL_MS = 3000;       // a running read updates its counts more often than this
const MINUTE_S = 60;
const HOUR_S = 3600;
const STAGE_WORDS = { discovered: "Found", converted: "Turned into text", understood: "Read by Hal",
  written: "Saved to your record" };
const STATE_WORDS = { done: "read", duplicate: "copies of files already read", no_text: "with no text to read",
  skipped: "skipped", attached: "data files, figures and photos listed with their project or course", older_version: "older copies of a project", covered: "slides your notes already cover", not_mine: "someone else's", error: "could not be read",
  pending: "still to read", private: "kept out because they look private" };
// Why files were skipped (the error column: ingest/vaultnotes.py, attach.py, tidy.py), counted in plain words.
const SKIP_WORDS = { "an empty note": "empty notes", "a picture with no words": "pictures with no words",
  "a data, log or design file with no project around it": "data and design files outside any project",
  "settings or tool file": "settings files" };
const LEFT_OUT_WORDS = { "junk folder": "hidden folders and folders of program files", link: "shortcuts",
  "program or data file": "programs and data files", "settings or tool file": "settings files and bundled code",
  unreadable: "files Hal could not open" };
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

function stopNow(id) {
  post("/actions/" + encodeURIComponent(id) + "/stop").then(
    () => { toast("Hal stopped. What it read so far is kept."); invalidate(INGEST); }, err => toast(err.message));
}

function readAgain() {
  post(INGEST + "/again").then(() => { toast("Hal is reading the rest of your documents."); invalidate(INGEST); },
    err => toast(err.message));
}

function tidyNow() {
  post(INGEST + "/tidy").then(() => { toast("Hal is tidying your record."); invalidate(INGEST); },
    err => toast(err.message));
}

function addAnyway(item) {
  post(INGEST + "/private/allow", { id: item.id }).then(out => {
    toast(out.action_id ? "Hal is reading " + item.name + " into your record."
      : "Hal no longer has " + item.name + ". Add it again and Hal will read it.");
    invalidate(INGEST);
  }, err => toast(err.message));
}

/** The files kept out because they look private: each one's name, why, and a button to add it anyway. */
function Private({ list }) {
  const one = list.length === 1;
  const them = one ? "it" : "them";
  return html`<${Note} tone="warn"><b>${plural(list.length, "file was", "files were")} kept out because ${one ? "it looks" : "they look"} private.</b>
    ${" "}Hal did not file ${them} in your record, send ${them} to the AI or take anything from ${them}. If one is fine to keep, add it anyway.
    <ul class="rec-paths rec-private">${list.map(item => html`<li class="row wrap"><span class="grow"><b>${item.name}</b>: ${item.reason}</span>
      <button type="button" class="btn small" aria-label=${"Add " + item.name + " anyway"} onClick=${() => addAnyway(item)}>Add it anyway</button></li>`)}</ul><//>`;
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
  // skipped files are counted by why, when the page knows why
  const why = counted(data.skipped);
  const states = counted(data.states).filter(([s]) => !["not_downloaded", "not_mine", "private"].includes(s)
    && !(s === "skipped" && why.length))
    .map(([s, n]) => n + " " + (STATE_WORDS[s] || s))
    .concat(why.map(([w, n]) => n + " " + (SKIP_WORDS[w] || "skipped")));
  const leftOut = counted(data.left_out);
  const title = data.tidy
    ? (running ? "Tidying your record" : data.state === "done" ? "Your record was tidied" : "Hal stopped tidying your record")
    : data.again
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
    ${states.length > 0 && html`<p class="small sub rec-line">${states.join(", ")}.</p>`}
    ${!running && data.skills && data.skills.usable + data.skills.mentioned > 0 && html`<p class="small sub rec-line">
      ${plural(data.skills.usable, "skill")} you can stand behind (on your CV, checked by you, or a tool of a project worth telling an employer about), and ${plural(data.skills.mentioned, "skill")} only mentioned in your files, folded away on Check your record.</p>`}
    ${data.not_downloaded > 0 && html`<${Note} tone="warn"><b>${plural(data.not_downloaded, "file is", "files are")} not downloaded.</b>
      ${" "}They are kept online only (for example in OneDrive, Dropbox or iCloud), so this computer has an empty copy and Hal cannot read it.
      Download them first (in OneDrive, choose "Always keep on this device"), then add them again.
      <ul class="rec-paths">${data.not_downloaded_paths.map(p => html`<li>${p}</li>`)}</ul>
      ${data.not_downloaded > data.not_downloaded_paths.length && html`<span class="small">and ${data.not_downloaded - data.not_downloaded_paths.length} more</span>`}<//>`}
    ${data.not_mine > 0 && html`<${Note} tone="info"><b>${plural(data.not_mine, "file looks", "files look")} like someone else's,</b>
      ${" "}such as another person's CV or a paper you downloaded, so Hal left ${data.not_mine === 1 ? "it" : "them"} out of your record.
      <ul class="rec-paths">${data.not_mine_paths.map(p => html`<li>${p}</li>`)}</ul>
      ${data.not_mine > data.not_mine_paths.length && html`<span class="small">and ${data.not_mine - data.not_mine_paths.length} more</span>`}<//>`}
    ${data.errors.length > 0 && html`<div><div class="small sub">Could not be read</div><ul class="rec-paths small">
      ${data.errors.map(e => html`<li><b>${e.path}</b>: ${e.error}</li>`)}</ul></div>`}
    ${!running && (data.private || []).length > 0 && html`<${Private} list=${data.private} />`}
    ${leftOut.length > 0 && html`<p class="small sub rec-line">Left out on purpose: ${leftOut.map(([w, n]) => n + " " + (LEFT_OUT_WORDS[w] || w)).join(", ")}.</p>`}
    ${!running && data.by_rules > 0 && html`<div class="row wrap rec-again"><p class="small sub grow rec-line">
      ${plural(data.by_rules, "note was", "notes were")} filed by headings and words only, without the AI reading them.</p>
      <button type="button" class="btn small" onClick=${readAgain}>Read them with the AI</button></div>`}
    ${!running && html`<div class="row wrap rec-tidy"><p class="small sub grow rec-line">
      Hal can tidy what it filed before: it backs up your record, then takes out other people's files, private
      files, lecturers' emails and older copies of your projects, and reads some files again with the AI.</p>
      <button type="button" class="btn small" onClick=${tidyNow}>Tidy my record</button></div>`}
  </section>`;
}
