/* The "Hal's instructions" dialog: read or change one instruction to the AI.

   Words in {curly brackets} are filled in from the settings each time; an edit that drops one Hal needs is
   refused by the server with words that name it, shown under the text. */
import { html, useState, useEffect } from "../core/html.js";
import { get, put, del } from "../core/api.js";
import { closeDialog, toast, invalidate } from "../core/store.js";
import { DlgHead } from "../ui/dialog.js";

/** One instruction, with Reset, Cancel and Save. */
export default function PromptDlg({ d }) {
  const [p, setP] = useState(null);
  const [text, setText] = useState("");
  const [err, setErr] = useState("");
  useEffect(() => {
    get("/prompts/" + encodeURIComponent(d.id)).then(x => { setP(x); setText(x.text); }, e => setErr(e.message));
  }, [d.id]);
  const done = words => { invalidate("/prompts"); closeDialog(); toast(words); };
  const save = async () => {
    try { await put("/prompts/" + encodeURIComponent(d.id), { text }); done("Saved. Hal uses your version from now on."); }
    catch (e) { setErr(e.message); }
  };
  const reset = async () => {
    try { await del("/prompts/" + encodeURIComponent(d.id)); done("Back to Hal's original"); } catch (e) { setErr(e.message); }
  };
  return html`<${DlgHead} title=${d.name || (p && p.name) || "Hal's instructions"} sub=${"Used for: " + (d.used_for || (p && p.used_for) || "")} icon="code" />
    <p class="small">Words in {curly brackets} are filled in from your settings each time. Change anything; Reset puts back Hal's original.</p>
    <textarea class="input prompt sc-prompt-text" aria-label="The instruction" value=${text} disabled=${!p}
      onInput=${e => setText(e.target.value)}></textarea>
    ${err && html`<p class="small sc-err" role="alert">${err}</p>`}
    <footer><button type="button" class="btn quiet" onClick=${reset}>Reset</button>
      <button type="button" class="btn quiet" onClick=${closeDialog}>Cancel</button>
      <button type="button" class="btn primary" disabled=${!p} onClick=${save}>Save</button></footer>`;
}
PromptDlg.wide = true;
