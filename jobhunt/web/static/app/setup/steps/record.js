/* Setup guide step 5: Your record.

   Opening the step makes the record folder with its starter folders, or finds the one already there
   (POST /setup/record never empties or rearranges a folder). "Sort a big folder for me" takes a folder on
   this computer, or a zip or files uploaded (which works the same from another computer), and Hal proposes
   where each file goes (record/sort.js); nothing is added until the person checks the list on Your record.
   "Open it" shows the folder only on the computer Hal runs on. */
import { html, useState, useEffect } from "../../core/html.js";
import { post } from "../../core/api.js";
import { toast } from "../../core/store.js";
import { Icon } from "../../ui/icons.js";
import { OpenFolderButton } from "../../ui/open-buttons.js";
import { SortStart } from "../../record/sort.js";

/** The step's body. */
export function Step() {
  const [folder, setFolder] = useState(null);
  const [started, setStarted] = useState(false);

  useEffect(() => { post("/setup/record").then(setFolder).catch(err => toast(err.message)); }, []);

  return html`<h1 class="h1">Your record</h1><p class="sub">A folder of proof: certificates, transcripts, references, and notes about jobs and volunteering. Hal uses it as evidence when it writes, and never sends a whole file to anyone.</p>
    <section class="card" style="margin-top:16px">${folder ? html`<div class="row"><div class="ic good"><${Icon} n="folder" /></div>
        <div style="flex:1;min-width:0"><b>${folder.found ? "Hal found your record folder" : "Hal made a folder for you"}</b>
          <div class="small sub mono" style="overflow-wrap:anywhere" data-folder>${folder.folder_words}</div>
          ${folder.found && html`<div class="small sub">It already holds ${folder.files} ${folder.files === 1 ? "file" : "files"}. Hal leaves them where they are.</div>`}</div>
        <${OpenFolderButton} path="/record/open-folder" label="Open it" small=${true} /></div>`
      : html`<p class="loading">Making your folder...</p>`}</section>
    <section class="card stack s" style="margin-top:14px"><h2 class="h2">Sort a big folder for me</h2>
      <p class="small sub" style="margin:0">Have a folder of everything from your studies and jobs? Hal suggests where each file goes, and
        you check the list on Your record before anything is added. You can also add files one place at a time there, at any time.</p>
      <${SortStart} onStarted=${() => setStarted(true)} /></section>
    ${started && html`<div class="note" role="status" style="margin-top:12px"><${Icon} n="check" /><div>Hal is sorting your files.
      Check the list on Your record, Overview, when you finish setting up.</div></div>`}`;
}
export default Step;
