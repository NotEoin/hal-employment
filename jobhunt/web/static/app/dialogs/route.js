/* The "How your CV is made" dialog, in the words of the setup guide's CV step.
   The choice also sets what employers get: your original file for "as it is", Hal's copy otherwise. */
import { html, useState } from "../core/html.js";
import { put } from "../core/api.js";
import { useApi, closeDialog, toast, invalidate } from "../core/store.js";
import { DlgHead } from "../ui/dialog.js";
import { Icon } from "../ui/icons.js";

const CHOICES = [
  ["as_is", "file", "Use my CV as it is", "Employers get your file exactly as it is. You can ask Hal for suggestions any time."],
  ["improve", "spark", "Help me improve it", "Your CV stays the starting point. Hal suggests changes one at a time; you accept, edit or leave each one."],
  ["fresh", "edit", "Build me a fresh CV", "No CV, or want a new start? Hal asks you some questions, reads your documents, and writes one for you to check."],
];

/** The dialog. */
export default function RouteDlg() {
  const { data: cv } = useApi("/cv");
  const [r, setR] = useState(null);
  const chosen = r || (cv && cv.route) || "improve";
  const save = async () => {
    try { await put("/cv/route", { route: chosen }); invalidate("/cv*"); closeDialog(); toast("Saved"); }
    catch (err) { toast(err.message); }
  };
  return html`<${DlgHead} title="How your CV is made" sub="Your versions are kept whichever you choose." icon="file" />
    <div class="stack s" role="radiogroup" aria-label="How your CV is made">${CHOICES.map(([id, ic, t, dsc]) => html`<button type="button"
      class=${"choice" + (chosen === id ? " on" : "")} role="radio" aria-checked=${chosen === id ? "true" : "false"} onClick=${() => setR(id)}>
      <span class="ic info"><${Icon} n=${ic} /></span><b>${t}</b><span class="small sub">${dsc}</span></button>`)}</div>
    <footer><button type="button" class="btn quiet" onClick=${closeDialog}>Cancel</button><button type="button" class="btn primary" onClick=${save}>Use this</button></footer>`;
}
RouteDlg.wide = false;
