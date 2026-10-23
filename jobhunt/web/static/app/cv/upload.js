/* The drop zone and paste box for a CV, used by the setup guide's CV step and by My CV.

   A file or pasted text goes to POST /cv/upload. The server answers at once when it cannot read the file
   (too big, too long, a picture of a CV) and otherwise starts the reading as an action; this part follows
   that action and then shows what Hal found, with "Check what Hal read" and a note for anything Hal was
   not sure of. In the setup guide Hal speaks for itself ("Check what I read"). */
import { html, useState } from "../core/html.js";
import { run } from "../core/api.js";
import { useApi, openDialog, invalidate } from "../core/store.js";
import { plural } from "../core/format.js";
import { Icon } from "../ui/icons.js";

const HIDDEN = "position:absolute;width:1px;height:1px;opacity:0;overflow:hidden";
const ACCEPT = ".pdf,.docx,.txt,application/pdf,application/vnd.openxmlformats-officedocument.wordprocessingml.document";
const DATES = " dates";

/** "Found your contact details, 1 degree, 2 jobs and 14 skills." from the report's `found`. */
export function foundSentence(found) {
  if (!found) return "";
  const parts = found.split(", ").map(p => (p === "contact details" ? "your contact details" : p));
  const list = parts.length > 1 ? parts.slice(0, -1).join(", ") + " and " + parts[parts.length - 1] : parts[0];
  return "Found " + list + ".";
}

function unsureWords(u) {
  if (u.label.endsWith(DATES)) return "the dates for " + u.label.slice(0, -DATES.length) + (u.value ? " (" + u.value + ")" : "");
  const label = u.label.charAt(0).toLowerCase() + u.label.slice(1);
  return u.value ? label + " (" + u.value + ")" : label;
}

/** The note under a reading: what Hal was not sure about, in the setup voice (`me`) or Hal's. */
export function UnsureNote({ unsure, me }) {
  if (!unsure || !unsure.length) return null;
  const who = me ? "I was" : "Hal was";
  const what = unsure.length === 1 ? "one thing: " + unsureWords(unsure[0])
    : plural(unsure.length, "thing") + ": " + unsure.map(unsureWords).join("; ");
  return html`<div class="note warn" style="margin-top:12px"><${Icon} n="warn" /><div>${who} not sure about ${what}.
    Please check ${unsure.length === 1 ? "it" : "them"} when you look.</div></div>`;
}

function ReadCard({ report, me }) {
  const pages = report.file.pages;
  const unsure = report.rows.filter(r => !r.sure);
  return html`<section class="card" data-cv-read><div class="row"><div class="ic good"><${Icon} n="check" /></div>
      <div style="flex:1;min-width:0"><b>Read ${report.file.name}${pages ? ": " + plural(pages, "page") : ""}</b>
        <div class="small sub">${foundSentence(report.found)}</div></div>
      <button type="button" class="btn small" onClick=${() => openDialog("checkread")}>${me ? "Check what I read" : "Check what Hal read"}</button></div>
    <${UnsureNote} unsure=${unsure} me=${me} /></section>`;
}

/** The upload part. `me` makes Hal speak for itself (the setup guide); `fresh` starts at the drop zone even
    when a reading exists (My CV's Replace); `onRead` hears each finished reading. */
export function Upload({ me = false, fresh = false, onRead }) {
  const { data: report, reload } = useApi("/cv/parse-report");
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState("");
  const [problem, setProblem] = useState("");
  const [pasting, setPasting] = useState(false);
  const [text, setText] = useState("");
  const [again, setAgain] = useState(fresh);

  const read = async body => {
    setProblem("");
    setBusy("Reading your CV");
    try {
      const done = await run("/cv/upload", body, ev => setBusy(ev.text || "Reading your CV"));
      if (done && done.state && done.state !== "done") throw new Error(done.error || "Hal could not read your CV.");
      invalidate("/cv*");
      await reload();
      setPasting(false);
      setAgain(false);
      setText("");
      if (onRead && done) onRead(done.result);
    } catch (err) { setProblem(err.message); }
    setBusy("");
  };
  const send = list => {
    const file = list && list[0];
    if (!file) return;
    const form = new FormData();
    form.append("file", file, file.name);
    read(form);
  };
  const paste = () => { if (text.trim()) read({ text }); else setProblem("Paste the text of your CV first."); };

  const status = busy && html`<div class="note" role="status" style="margin-top:12px"><${Icon} n="spark" /><div>${busy}.</div></div>`;
  const error = problem && html`<div class="note warn" role="alert" style="margin-top:12px"><${Icon} n="warn" /><div>${problem}</div></div>`;
  if (report && report.file && !again && !busy) {
    return html`<div><${ReadCard} report=${report} me=${me} />
      <p class="small sub" style="margin:8px 0 0"><button type="button" class="btn quiet small" onClick=${() => setAgain(true)}>
        <${Icon} n="upload" c="s" />Add a different CV</button></p>${error}</div>`;
  }
  if (pasting) {
    return html`<section class="card"><label class="lab" for="cv-paste">Paste the text of your CV</label>
      <textarea id="cv-paste" class="input" rows="12" style="width:100%;margin-top:8px" value=${text} onInput=${e => setText(e.target.value)}></textarea>
      <div class="row" style="margin-top:10px"><button type="button" class="btn primary" disabled=${!!busy} onClick=${paste}>Read this</button>
        <button type="button" class="btn quiet" onClick=${() => setPasting(false)}>Choose a file instead</button></div>${status}${error}</section>`;
  }
  return html`<div><div class="drop" style=${over ? "border-color:var(--primary)" : ""}
      onDragOver=${e => { e.preventDefault(); setOver(true); }} onDragLeave=${() => setOver(false)}
      onDrop=${e => { e.preventDefault(); setOver(false); send(e.dataTransfer.files); }}>
      <${Icon} n="upload" c="l" /><p style="margin:8px 0"><b>Drop your CV here</b>, or</p>
      <label class="btn primary" style="position:relative;cursor:pointer">Choose a file
        <input type="file" accept=${ACCEPT} style=${HIDDEN} aria-label="Choose your CV" disabled=${!!busy}
          onChange=${e => { send(e.target.files); e.target.value = ""; }} /></label>
      <p class="small" style="margin:10px 0 0">PDF or Word. Or <button type="button" class="btn quiet small" onClick=${() => setPasting(true)}>paste the text</button></p></div>
    ${status}${error}</div>`;
}

export default Upload;
