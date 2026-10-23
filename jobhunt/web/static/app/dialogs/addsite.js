/* "Add a job site or careers page" and "Watch a company".

   Look starts `find.detect_site` and follows it; the answer says what kind of site it is ("This is a
   Workable careers page for ...") and lists its jobs now, scored against the goal, with the ones worth
   keeping ticked. Save adds the site and keeps the ticked jobs. A page Hal cannot read by itself offers
   "Add one advert" instead; a company with no board found asks for its careers page. */
import { html, cx, useState } from "../core/html.js";
import { post, run } from "../core/api.js";
import { closeDialog, openDialog, toast, invalidate } from "../core/store.js";
import { DlgHead } from "../ui/dialog.js";
import { Icon } from "../ui/icons.js";

const GOOD = 65;    // the score band the job cards call "Good" (ui/controls.js)

/** The dialog; `d.company` opens it as "Watch a company". */
export default function AddSite({ d }) {
  const company = !!d.company;
  const [text, setText] = useState(d.url || "");
  const [busy, setBusy] = useState(false);
  const [step, setStep] = useState("");
  const [found, setFound] = useState(null);
  const [pick, setPick] = useState([]);
  const [problem, setProblem] = useState("");
  const look = async () => {
    const value = text.trim();
    if (!value) return;
    setBusy(true); setProblem(""); setFound(null);
    const isUrl = /^https?:\/\//i.test(value);
    try {
      const a = await run("/sources/detect", isUrl ? { url: value } : { company: value }, ev => setStep(ev.text || ""));
      if (a.state !== "done") { setProblem(a.error || "Hal could not look at that site."); return; }
      setFound(a.result);
      setPick((a.result.jobs || []).filter(j => j.keep).map(j => j.url));
    } catch (err) { setProblem(err.message); }
    finally { setBusy(false); setStep(""); }
  };
  const save = async () => {
    try {
      const r = await post("/sources", { detected_id: found.detected_id, keep_urls: pick });
      invalidate("/sources"); invalidate("/jobs");
      closeDialog();
      toast("Saved. Hal added " + r.added + (r.added === 1 ? " job" : " jobs") + " and will check this site at every search.");
    } catch (err) { setProblem(err.message); }
  };
  const toggle = url => setPick(pick.includes(url) ? pick.filter(u => u !== url) : [...pick, url]);
  const title = company ? "Watch a company" : "Add a job site or careers page";
  return html`<${DlgHead} title=${title} sub="Paste an address. Hal works out what kind of site it is." icon="globe" />
    <div class="row">
      <input class="input" style="flex:1;min-width:0" placeholder=${company ? "Company name or careers page address" : "https://..."}
        value=${text} aria-label=${company ? "Company name or address" : "Address"} aria-invalid=${problem ? "true" : "false"}
        aria-describedby=${problem ? "addsite-problem" : undefined}
        onInput=${e => { setText(e.target.value); setProblem(""); }} onKeyDown=${e => { if (e.key === "Enter") look(); }} />
      <button type="button" class="btn" disabled=${busy || !text.trim()} onClick=${look}>${busy ? "Looking..." : "Look"}</button>
    </div>
    ${busy && step && html`<p class="small sub" role="status" style="margin-top:8px">${step}</p>`}
    ${problem && html`<p class="small bad-text" id="addsite-problem" role="alert">${problem}</p>`}
    ${found && html`<div class="note" style="margin-top:14px"><${Icon} n=${found.can_watch ? "check" : "info"} /><div>${found.words}</div></div>
      ${!found.can_watch && !found.ask_for_page && html`<div class="row" style="margin-top:10px"><button type="button" class="btn small"
        onClick=${() => { closeDialog(); openDialog("addjob"); }}>Add one advert</button></div>`}
      ${(found.jobs || []).length > 0 && html`<div style="margin-top:10px">${found.jobs.map(j => html`<label class="li" style="align-items:center;cursor:pointer">
        <input type="checkbox" checked=${pick.includes(j.url)} onChange=${() => toggle(j.url)} />
        <div class="grow" style="min-width:0"><b>${j.title}</b><div class="small sub">${j.hidden ? "Hidden: " + j.hidden : j.sub}</div></div>
        <span class=${cx("chip", j.score >= GOOD ? "good" : "")}>${j.score}</span></label>`)}</div>`}`}
    <footer><button type="button" class="btn quiet" onClick=${closeDialog}>Cancel</button>
      <button type="button" class="btn primary" disabled=${!found || !found.can_watch} onClick=${save}>Save the site</button></footer>`;
}
