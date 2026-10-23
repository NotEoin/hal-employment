/* Setup guide step 5: Your record.

   Opening the step makes the record folder with its starter folders, or finds the one already there
   (POST /setup/record never empties or rearranges a folder). "Add files" uploads the chosen files, which
   Hal copies into the folder, so nobody needs File Explorer; this works the same from another computer.
   "Open it" shows the folder only on the computer Hal runs on. */
import { html, useState, useEffect } from "../../core/html.js";
import { post, api } from "../../core/api.js";
import { toast, invalidate } from "../../core/store.js";
import { Icon } from "../../ui/icons.js";
import { OpenFolderButton } from "../../ui/open-buttons.js";

const HIDDEN = "position:absolute;width:1px;height:1px;opacity:0;overflow:hidden";

/** The step's body. */
export function Step() {
  const [folder, setFolder] = useState(null);
  const [over, setOver] = useState(false);
  const [busy, setBusy] = useState(false);
  const [added, setAdded] = useState("");

  useEffect(() => { post("/setup/record").then(setFolder).catch(err => toast(err.message)); }, []);

  const send = async list => {
    if (!list || !list.length) return;
    const form = new FormData();
    Array.from(list).forEach((f, i) => form.append("file" + i, f, f.name));  // one field each: the server keys files by field
    setBusy(true);
    try {
      const r = await api("POST", "/record/files", form);
      const n = r.added.length;
      setAdded("Added " + n + (n === 1 ? " file" : " files") + ". Hal is reading them.");
      invalidate("/record*");
    } catch (err) { toast(err.message); }
    setBusy(false);
  };

  return html`<h1 class="h1">Your record</h1><p class="sub">A folder of proof: certificates, transcripts, references, and notes about jobs and volunteering. Hal uses it as evidence when it writes, and never sends a whole file to anyone.</p>
    <section class="card" style="margin-top:16px">${folder ? html`<div class="row"><div class="ic good"><${Icon} n="folder" /></div>
        <div style="flex:1;min-width:0"><b>${folder.found ? "Hal found your record folder" : "Hal made a folder for you"}</b>
          <div class="small sub mono" style="overflow-wrap:anywhere" data-folder>${folder.folder_words}</div>
          ${folder.found && html`<div class="small sub">It already holds ${folder.files} ${folder.files === 1 ? "file" : "files"}. Hal leaves them where they are.</div>`}</div>
        <${OpenFolderButton} path="/record/open-folder" label="Open it" small=${true} /></div>`
      : html`<p class="loading">Making your folder...</p>`}</section>
    <div class="drop" style=${"margin-top:14px" + (over ? ";border-color:var(--primary)" : "")}
      onDragOver=${e => { e.preventDefault(); setOver(true); }} onDragLeave=${() => setOver(false)}
      onDrop=${e => { e.preventDefault(); setOver(false); send(e.dataTransfer.files); }}>
      <${Icon} n="upload" c="l" /><p style="margin:8px 0"><b>Add files</b>: drop them here, or</p>
      <label class="btn" style="position:relative;cursor:pointer">${busy ? "Adding..." : "Choose files"}
        <input type="file" multiple style=${HIDDEN} aria-label="Choose files to add to your record" disabled=${busy || !folder}
          onChange=${e => { send(e.target.files); e.target.value = ""; }} /></label>
      <p class="small" style="margin:10px 0 0">Hal copies them into the folder for you. You can add more at any time from Your record.</p></div>
    ${added && html`<div class="note" role="status" style="margin-top:12px"><${Icon} n="check" /><div>${added}</div></div>`}`;
}
export default Step;
