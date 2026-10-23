/* Setup guide step: How should we start your CV?

   The three ways to start are the CV routes (as_is, improve, fresh), saved through PUT /cv/route.
   For the first two the step offers the upload part; Hal speaks for itself here, as in the rest of the
   guide. What the CV gives for About you is saved there only where nothing was typed yet. */
import { html, cx, useState, useEffect, useRef } from "../../core/html.js";
import { get, put } from "../../core/api.js";
import { toast } from "../../core/store.js";
import { Icon } from "../../ui/icons.js";
import { Upload } from "../../cv/upload.js";

export const ROUTES = [
  ["as_is", "file", "I have a CV: use it as it is", "Employers get your file exactly as it is. You can ask Hal for suggestions any time."],
  ["improve", "spark", "I have a CV: help me improve it", "Your CV stays the starting point. Hal suggests changes one at a time; you accept, edit or leave each one."],
  ["fresh", "edit", "Build me a fresh CV", "No CV, or want a new start? Hal asks you some questions, reads your documents, and writes one for you to check."],
];

/** The step's body. */
export function Step() {
  const [route, setRoute] = useState("improve");
  // the saved route arrives after the step draws; a choice made before then wins (CI, 2026-10-23)
  const chosen = useRef(false);
  useEffect(() => { get("/cv").then(cv => { if (cv && cv.route && !chosen.current) setRoute(cv.route); }).catch(() => {}); }, []);
  const choose = async id => {
    chosen.current = true;
    setRoute(id);
    try { await put("/cv/route", { route: id }); } catch (err) { toast(err.message); }
  };
  return html`<h1 class="h1">How should we start your CV?</h1><p class="sub">You can change this later in My CV.</p>
    <div class="cols" style="margin-top:18px">${ROUTES.map(([id, ic, t, d]) => html`<button type="button" class=${cx("choice", route === id && "on")}
      aria-pressed=${route === id ? "true" : "false"} onClick=${() => choose(id)}><span class="ic info"><${Icon} n=${ic} /></span>
      <b class="h3">${t}</b><span class="small sub">${d}</span></button>`)}</div>
    ${route !== "fresh" ? html`<div style="margin-top:20px"><${Upload} me=${true} /></div>`
      : html`<section class="card" style="margin-top:20px"><b>Next, Hal asks about your education, jobs, volunteering and skills.</b>
        <p class="small sub" style="margin:6px 0 0">You can also add old documents (a transcript, a reference, an old CV) in the Your record step. Hal only uses facts it finds there or that you tell it.</p></section>`}`;
}
export default Step;
