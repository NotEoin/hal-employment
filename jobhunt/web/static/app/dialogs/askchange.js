/* The "Ask for changes" dialog: the person's own words about the documents. Hal's
   rewriting is `documents/change`. */
import { html, useState } from "../core/html.js";
import { post } from "../core/api.js";
import { closeDialog, toast } from "../core/store.js";
import { DlgHead } from "../ui/dialog.js";

/** The dialog; `d.app` is the application. */
export default function AskChange({ d }) {
  const [words, setWords] = useState("");
  const ask = async () => {
    if (!words.trim()) { toast("Say what you want changed first."); return; }
    try {
      await post("/applications/" + encodeURIComponent(d.app.id) + "/documents/change", { words });
      closeDialog();
      toast("Hal is making the changes. You will see each one highlighted.");
    } catch (err) { toast(err.message); }
  };
  return html`<${DlgHead} title="Ask for changes" sub="Say what you want in your own words." icon="edit" />
    <textarea class="input" aria-label="What to change" placeholder="For example: keep my original profile, and make the letter warmer" value=${words} onInput=${e => setWords(e.target.value)}></textarea>
    <footer><button type="button" class="btn quiet" onClick=${closeDialog}>Cancel</button><button type="button" class="btn primary" onClick=${ask}>Ask Hal</button></footer>`;
}
