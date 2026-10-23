/* "Something wrong here?": the person says what went wrong, Hal shows the
   note it will remember, and after saving offers three ways on.

   Step 1 keeps the report and starts `sites.propose_note`; while it runs the dialog says so. When the AI
   cannot turn the words into a note Hal acts on, the words are already saved as a note for this site, and
   the dialog goes straight to step 3 and says so. Opened from the tab without a site, it asks for one.
   `d` carries {site, platform, job_id}; without a job there is nothing to try again, so step 3 only closes. */
import { html, cx, useState } from "../core/html.js";
import { post, run } from "../core/api.js";
import { closeDialog, toast, invalidate } from "../core/store.js";
import { go } from "../core/router.js";
import { DlgHead } from "../ui/dialog.js";
import { Seg } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";

const WHAT = ["Hal closed the form", "This answer is wrong", "A list was not filled in", "The file upload did not work",
  "It needs an account", "Hal pressed the wrong button", "Something else"];

/** The dialog. */
export default function Report({ d }) {
  const [step, setStep] = useState(1);
  const [what, setWhat] = useState("This answer is wrong");
  const [words, setWords] = useState("");
  const [site, setSite] = useState(d.site || "");
  const [busy, setBusy] = useState(false);
  const [progress, setProgress] = useState("");
  const [problem, setProblem] = useState("");
  const [found, setFound] = useState(null);
  const [scope, setScope] = useState("site");
  const [rewording, setRewording] = useState(false);
  const [text, setText] = useState("");
  const shown = site || "this site";

  const next = async () => {
    setBusy(true); setProblem("");
    try {
      const a = await run("/sites/report", { job_id: d.job_id || null, site, what, words }, ev => setProgress(ev.text || ""));
      if (a.state !== "done") { setProblem(a.error || "Hal could not read your report. Try again."); return; }
      setFound(a.result);
      setText(a.result.draft ? a.result.draft.text : a.result.text);
      if (a.result.site) setSite(a.result.site);
      invalidate("/sites");
      setStep(a.result.saved_as_note ? 3 : 2);
    } catch (err) { setProblem(err.message); }
    finally { setBusy(false); setProgress(""); }
  };
  const save = async () => {
    setBusy(true); setProblem("");
    try {
      await post("/sites/reports/" + found.report_id + "/save", { scope, text: rewording ? text : "" });
      invalidate("/sites");
      setStep(3);
    } catch (err) { setProblem(err.message); }
    finally { setBusy(false); }
  };
  const after = async choice => {
    try {
      const r = await post("/sites/reports/" + found.report_id + "/next", { choice });
      closeDialog();
      if (choice === "retry") toast("Hal is filling the form again, using the new note");
      else if (choice === "skip") toast("Skipped. The job stays in Applications if you want to come back.");
      else if (r.route) go(r.route);
    } catch (err) { setProblem(err.message); }
  };
  const failed = problem && html`<p class="small bad-text" role="alert" id="report-problem">${problem}</p>`;

  if (step === 1) {
    return html`<${DlgHead} title="Something wrong here?" sub=${"On " + shown + (d.platform ? " (" + d.platform + ")" : "")} icon="flag" />
      <p class="small">Tell Hal what happened in your own words. Hal turns it into a note for this site and shows you the note before saving it.</p>
      ${!d.site && html`<label class="small" for="rp-site"><b>Which site?</b></label>
        <input id="rp-site" class="input" style="margin:6px 0 10px" placeholder="For example jobs.example.org" value=${site}
          onInput=${e => setSite(e.target.value)} />`}
      <div class="chips" style="margin:12px 0" role="group" aria-label="What happened">${WHAT.map(x => html`<button type="button"
        class=${cx("pick", what === x && "on")} aria-pressed=${what === x ? "true" : "false"} onClick=${() => setWhat(x)}>${x}</button>`)}</div>
      <label class="small" for="rp"><b>What happened?</b></label>
      <textarea id="rp" class="input" style="margin-top:6px" value=${words} onInput=${e => setWords(e.target.value)}
        placeholder="For example: the Location box only takes the city" aria-describedby=${problem ? "report-problem" : undefined}></textarea>
      ${busy && html`<p class="small sub" role="status" style="margin-top:8px">${progress || "Hal is reading what you told it..."}</p>`}
      ${failed}
      <footer><button type="button" class="btn quiet" onClick=${closeDialog}>Cancel</button>
        <button type="button" class="btn primary" disabled=${busy || !site.trim() || (what === "Something else" && !words.trim())} onClick=${next}>Next</button></footer>`;
  }
  if (step === 2) {
    const scopes = found.draft.scopes;
    const chosen = scopes.find(s => s.id === scope) || scopes[0];
    const system = scopes.find(s => s.id === "ats");
    return html`<${DlgHead} title="Here is what Hal will remember" icon="bulb" />
      <div class="card whyc"><b>On ${chosen.where}:</b> ${text}</div>
      ${rewording && html`<label class="small" for="rp-text" style="display:block;margin-top:10px"><b>The note in your words</b></label>
        <textarea id="rp-text" class="input" style="margin-top:6px" value=${text} onInput=${e => setText(e.target.value)}></textarea>`}
      <p class="small" style="margin:14px 0 8px"><b>Use this on</b></p>
      <${Seg} label="Where to use it" value=${scope} options=${scopes.map(s => [s.id, s.label])} onChange=${setScope} />
      <p class="small sub" style="margin-top:10px">${system ? system.help + " " : ""}"Just this site" is the safe choice.</p>
      ${failed}
      <footer><button type="button" class="btn quiet" onClick=${() => setStep(1)}>Back</button>
        <button type="button" class="btn" onClick=${() => setRewording(!rewording)}>Change the wording</button>
        <button type="button" class="btn primary" disabled=${busy || !text.trim()} onClick=${save}>Save this note</button></footer>`;
  }
  return html`<${DlgHead} title="Saved. What now?" sub="You can switch the note off any time in What Hal remembers." icon="check" />
    ${found && found.saved_as_note && html`<div class="note" style="margin-bottom:12px"><${Icon} n="info" /><div>Hal saved your words as a note
      for ${shown}, as you wrote them. Hal shows them at each visit there.</div></div>`}
    ${d.job_id ? html`<div class="stack s">
      <button type="button" class="choice" onClick=${() => after("retry")}><b>Try again with this note</b><span class="small sub">Hal fills the form again on this page.</span></button>
      <button type="button" class="choice" onClick=${() => after("myself")}><b>I'll finish it myself</b><span class="small sub">Opens this job's CV, letter and answers with Copy buttons, beside the employer's tab. Press "I sent it myself" when done.</span></button>
      <button type="button" class="choice" onClick=${() => after("skip")}><b>Skip this job for now</b><span class="small sub">Hal stops and closes its tab.</span></button>
    </div>` : html`<footer><button type="button" class="btn primary" onClick=${closeDialog}>Close</button></footer>`}
    ${failed}`;
}
