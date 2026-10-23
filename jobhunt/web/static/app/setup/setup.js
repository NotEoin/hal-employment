/* The setup guide, drawn over any page.

   The frame owns the step list, the progress, Back, Continue, "Skip for now" and "Save and finish later";
   each step's body is `steps/<id>.js` (default export `Step`), loaded by file name, so the steps other
   areas own (CV, goal, AI, helper, updates) plug in without touching this file. A step gets
   `{ steps, next }`: every step's state, and a function that moves on as Continue does.

   Every change is saved as it happens: the steps save their own fields, and the frame tells the server
   which step is open, so closing the window halfway and opening the guide again carries on at the same
   step. Opening the guide at its first step resumes where the person left it, unless the guide was
   already finished (then "Open the setup guide" starts at Welcome). */
import { html, cx, useState, useEffect, useRef } from "../core/html.js";
import { get, post, put } from "../core/api.js";
import { store, useStore, closeSetup, toast, invalidate } from "../core/store.js";
import { go } from "../core/router.js";
import { Icon } from "../ui/icons.js";
import { trapTab } from "../ui/dialog.js";

const FIRST = "welcome";
const LAST = "done";

function stepIndex(steps, want) {
  if (typeof want === "number") return Math.max(0, Math.min(steps.length - 1, want));
  const i = steps.findIndex(s => s.id === want);
  return i < 0 ? 0 : i;
}

function focusHeading(root) {
  const h = root && root.querySelector(".sinner h1");
  if (h) { h.setAttribute("tabindex", "-1"); h.focus(); }
}

function StepBody({ id, steps, next, onDrawn }) {
  const [mod, setMod] = useState(null);
  const [failed, setFailed] = useState(null);
  useEffect(() => {
    let live = true;
    setMod(null); setFailed(null);
    import("./steps/" + id + ".js").then(m => { if (live) setMod(m); }, err => { if (live) setFailed(err); });
    return () => { live = false; };
  }, [id]);
  useEffect(() => { if (mod) onDrawn(); }, [mod]);
  if (failed) {
    return html`<div class="note warn" role="alert"><${Icon} n="warn" /><div>This step did not load. Close the guide and open it again.</div></div>`;
  }
  if (!mod) return html`<p class="loading">Loading...</p>`;
  const Comp = mod.Step || mod.default;
  return html`<${Comp} steps=${steps} next=${next} />`;
}

/** The guide's frame. */
export function Setup() {
  const asked = useStore(s => s.setup.step);
  const ref = useRef(null);
  const body = useRef(null);
  const [data, setData] = useState(null);
  const [at, setAt] = useState(null);
  const [problem, setProblem] = useState("");

  useEffect(() => {
    get("/setup").then(d => {
      setData(d);
      const resume = (asked === 0 || asked === FIRST || asked === undefined) && !d.finished ? d.step : asked;
      setAt(stepIndex(d.steps, resume));
    }).catch(err => setProblem(err.message));
  }, []);
  useEffect(() => { if (data && asked !== 0 && asked !== undefined) setAt(stepIndex(data.steps, asked)); }, [asked]);

  const steps = data ? data.steps : [];
  const step = steps[at];
  useEffect(() => {
    if (!step) return;
    if (body.current) body.current.scrollTop = 0;
    put("/setup/" + step.id, { state: "current" }).catch(() => {});
  }, [step && step.id]);

  const mark = async (id, state) => {
    try { const d = await put("/setup/" + id, { state }); setData(d); invalidate("/home"); return true; }
    catch (err) { toast(err.message); return false; }
  };
  const later = async () => {
    try { await post("/setup/later"); } catch (err) { toast(err.message); }
    store.set(s => ({ session: s.session ? { ...s.session, setup_done: true } : s.session }));
    invalidate("/home");
    closeSetup();
  };
  const next = async () => { if (step && await mark(step.id, "done")) setAt(Math.min(at + 1, steps.length - 1)); };
  const skip = async () => { if (step && await mark(step.id, "skipped")) setAt(Math.min(at + 1, steps.length - 1)); };
  const back = () => setAt(Math.max(at - 1, 0));
  const home = async () => {
    await mark(LAST, "done");
    store.set(s => ({ session: s.session ? { ...s.session, setup_done: true } : s.session }));
    closeSetup();
    go("home");
  };
  const onKey = e => { if (e.key === "Escape") later(); else trapTab(e, ref.current); };

  const frame = inside => html`<div class="setup" role="dialog" aria-modal="true" aria-label="Setup guide" ref=${ref}
    onKeyDown=${onKey}>${inside}</div>`;
  if (problem) {
    return frame(html`<aside></aside><div class="sbody"><div class="sinner"><h1 class="h1">Setup guide</h1>
      <div class="note warn" role="alert"><${Icon} n="warn" /><div>${problem}</div></div>
      <div class="row" style="margin-top:18px"><button type="button" class="btn primary" onClick=${closeSetup}>Close the guide</button></div></div></div>`);
  }
  if (!step) return frame(html`<aside></aside><div class="sbody"><div class="sinner"><p class="loading">Loading...</p></div></div>`);

  const isDone = s => s.state === "done" && s.id !== step.id;
  return frame(html`
    <aside><div class="brand"><span class="eye"></span><div>Setting up Hal<small>Saved as you go</small></div></div>
      ${steps.map((s, i) => html`<button type="button" class=${cx("sstep", i === at && "now", isDone(s) && "done")}
        aria-current=${i === at ? "step" : null} onClick=${() => setAt(i)}><span class="n">${isDone(s)
          ? html`<${Icon} n="check" c="s" />` : i + 1}</span><span>${s.name}${s.sub && html`<small>${s.sub}</small>`}</span></button>`)}
      <span class="sp"></span><button type="button" class="btn quiet small" style="margin-top:16px" onClick=${later}>Save and finish later</button></aside>
    <div class="sbody" ref=${body}><div class="sprog"><i style=${"width:" + Math.round((at + 1) / steps.length * 100) + "%"}></i></div>
      <div class="sinner" data-step=${step.id}><${StepBody} key=${step.id} id=${step.id} steps=${steps} next=${next} onDrawn=${() => focusHeading(ref.current)} /></div>
      <div class="sfoot">${at > 0 && html`<button type="button" class="btn quiet" onClick=${back}><${Icon} n="chevL" c="s" />Back</button>`}<span class="sp"></span>
        <button type="button" class="btn quiet small" onClick=${later}>Finish later</button>${step.optional
          && html`<button type="button" class="btn" onClick=${skip}>Skip for now</button>`}
        ${step.id === LAST ? html`<button type="button" class="btn primary" onClick=${home}>Go to Home</button>`
          : html`<button type="button" class="btn primary" onClick=${next}>${at === 0 ? "Let's start" : "Continue"}<${Icon} n="chevR" c="s" /></button>`}</div></div>`);
}

export default Setup;
