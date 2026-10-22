/* The "Private answers" dialog: answers for equality and adjustment questions.

   Every field starts empty and saved answers are never shown again: the dialog says only which are saved.
   Only the fields the person fills in are sent; Clear forgets one. */
import { html, useState } from "../core/html.js";
import { put, del } from "../core/api.js";
import { useApi, closeDialog, toast, invalidate } from "../core/store.js";
import { DlgHead } from "../ui/dialog.js";

const HELP = "Used only to answer these questions on forms. Never sent to the AI, never in logs or exports. Hal shows only whether they are saved.";

/** The private questions, each empty, with whether an answer is saved. */
export default function PrivateDlg() {
  const r = useApi("/private");
  const [answers, setAnswers] = useState({});
  const [err, setErr] = useState("");
  const set = (k, v) => setAnswers(a => ({ ...a, [k]: v }));
  const save = async () => {
    const filled = Object.fromEntries(Object.entries(answers).filter(([, v]) => String(v).trim()));
    if (!Object.keys(filled).length) { closeDialog(); return; }
    try { await put("/private", { answers: filled }); invalidate("/private"); closeDialog(); toast("Saved. Hal shows only that they are saved."); }
    catch (e) { setErr(e.message); }
  };
  const clear = async key => {
    try { await del("/private/" + encodeURIComponent(key)); invalidate("/private"); toast("Forgotten."); } catch (e) { setErr(e.message); }
  };
  const qs = (r.data && r.data.questions) || [];
  return html`<${DlgHead} title="Private answers" sub=${HELP} icon="lock" />
    ${qs.map(q => html`<div class="sc-q"><label class="lab" for=${"pq-" + q.key}>${q.label}</label>
      <div class="row tight"><span class="small sub">${q.saved ? "Saved" : "Not saved"}</span>
        ${q.saved && html`<button type="button" class="btn quiet small" onClick=${() => clear(q.key)}>Clear</button>`}</div>
      ${q.options ? html`<select class="input" id=${"pq-" + q.key} value=${answers[q.key] || ""} onChange=${e => set(q.key, e.target.value)}>
          <option value="">Leave as it is</option>${q.options.map(o => html`<option value=${Array.isArray(o) ? o[0] : o}>${Array.isArray(o) ? o[1] : o}</option>`)}</select>`
        : html`<textarea class="input" id=${"pq-" + q.key} rows="2" autocomplete="off" value=${answers[q.key] || ""}
          onInput=${e => set(q.key, e.target.value)}></textarea>`}</div>`)}
    ${err && html`<p class="small sc-err" role="alert">${err}</p>`}
    <footer><button type="button" class="btn quiet" onClick=${closeDialog}>Cancel</button>
      <button type="button" class="btn primary" onClick=${save}>Save</button></footer>`;
}
PrivateDlg.wide = true;
