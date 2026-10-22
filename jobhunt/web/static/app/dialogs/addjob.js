/* "Add one advert": paste the address of a job found somewhere else. Hal reads it in
   the background, scores it against the goal and adds it to Matches; the window does not wait for it. */
import { html, useState } from "../core/html.js";
import { post } from "../core/api.js";
import { closeDialog, toast } from "../core/store.js";
import { DlgHead } from "../ui/dialog.js";

/** The dialog. */
export default function AddJob() {
  const [url, setUrl] = useState("");
  const [problem, setProblem] = useState("");
  const add = async () => {
    try {
      await post("/jobs/add-advert", { url: url.trim() });
      closeDialog();
      toast("Hal is reading the advert. It will appear in Matches.");
    } catch (err) {
      if (err.field) setProblem(err.message); else toast(err.message);
    }
  };
  return html`<${DlgHead} title="Add one advert" sub="Found a job somewhere else? Paste its address." icon="link" />
    <input class="input" placeholder="https://..." aria-label="Advert address" value=${url} aria-invalid=${problem ? "true" : "false"}
      aria-describedby=${problem ? "addjob-problem" : undefined} onInput=${e => { setUrl(e.target.value); setProblem(""); }}
      onKeyDown=${e => { if (e.key === "Enter") add(); }} />
    ${problem && html`<p class="small bad-text" id="addjob-problem" role="alert">${problem}</p>`}
    <p class="small sub" style="margin-top:10px">Hal reads the advert, scores it against your goal and adds it to your matches. You can also press the helper's button while you are on the advert.</p>
    <footer><button type="button" class="btn quiet" onClick=${closeDialog}>Cancel</button>
      <button type="button" class="btn primary" disabled=${!url.trim()} onClick=${add}>Add</button></footer>`;
}
