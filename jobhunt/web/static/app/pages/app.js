/* One application and its four steps: Documents, Form, Sent, Interview, with Previous
   and Next through the applications in progress ([ and ] on the keyboard). Discard, Back to Matches and
   "I sent it myself" work at every step. */
import { html, cx, useState, useLayoutEffect } from "../core/html.js";
import { useApi, openDialog } from "../core/store.js";
import { go, route } from "../core/router.js";
import { useKeys } from "../core/keys.js";
import { Head } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";
import { DocsStep } from "../app/docs-step.js";
import { FormStep } from "../app/form-step.js";
import { SentStep } from "../app/sent-step.js";
import { InterviewStep } from "../app/interview-step.js";

const REFRESH_ON = [ev => ev.type === "action" || ev.type === "step" || ev.type === "activity"];

/** The four steps' names and notes, as the page shows them under the title. */
export function stepInfo(a) {
  return (a && a.steps ? a.steps : []).map(s => [s.name, s.note]);
}

/** The application page. */
export function AppPage({ arg }) {
  const path = arg ? "/applications/" + encodeURIComponent(arg) : null;
  const { data: a, error, reload } = useApi(path, { on: REFRESH_ON });
  const [view, setView] = useState(null);
  // a layout effect: Preact runs a plain one up to a frame late, where it can undo the person's first action
  useLayoutEffect(() => { setView(null); }, [arg]);
  const nav = (a && a.nav) || {};
  useKeys({ "[": () => nav.prev && go(route("app", nav.prev)), "]": () => nav.next && go(route("app", nav.next)) },
          [nav.prev, nav.next]);
  const back = html`<button type="button" class="back" onClick=${() => go("apps")}><${Icon} n="chevL" c="s" />Applications</button>`;
  // The navbar's slot stays (null) while loading, so the h1 keeps its place and the focus the shell gave it.
  if (error) return html`${back}${null}<${Head} title="Application" /><div class="card"><p>${error.message}</p></div>`;
  if (!a) return html`${back}${null}<${Head} title="Application" /><div class="card"><p class="sub">Loading the application.</p></div>`;
  const j = a.job;
  const shown = view || a.step;
  const sub = [j.org, j.place].filter(Boolean).join(", ") + (j.pay ? ". " + j.pay : "") + ".";
  return html`${back}
    ${nav.index > 0 && html`<div class="navbar"><span><b>Application ${nav.index} of ${nav.total}</b> <span class="sub">in progress</span></span>
      <div class="row tight"><button type="button" class="btn small" disabled=${!nav.prev} onClick=${() => go(route("app", nav.prev))}><${Icon} n="chevL" c="s" />Previous</button>
      <button type="button" class="btn small" disabled=${!nav.next} onClick=${() => go(route("app", nav.next))}>Next</button></div></div>`}
    <${Head} title=${j.title} sub=${sub}>
      <button type="button" class="btn quiet" onClick=${() => go(route("job", j.id))}>The job</button>
      ${j.url && html`<a class="btn quiet" href=${j.url} target="_blank" rel="noopener"><${Icon} n="external" c="s" />Advert</a>`}
      <button type="button" class="btn" onClick=${() => openDialog("discard", { job: { id: j.id, title: j.title, org: j.org } })}>Discard</button>
    <//>
    <div class="steps" role="tablist" aria-label="Steps">${a.steps.map((s, k) => html`<button type="button" role="tab"
      aria-selected=${shown === k + 1 ? "true" : "false"} class=${cx("step", s.state === "done" && "done", s.state === "now" && "now", shown === k + 1 && "view")}
      onClick=${() => setView(k + 1)}><span class="num">${s.state === "done" ? html`<${Icon} n="check" c="s" />` : k + 1}</span>
      <span><b>${s.name}</b><small>${s.note || "Not yet"}</small></span></button>`)}</div>
    ${shown === 1 && html`<${DocsStep} a=${a} reload=${reload} onApproved=${() => { setView(2); reload(); }} />`}
    ${shown === 2 && html`<${FormStep} a=${a} reload=${reload} onReopen=${() => { setView(1); reload(); }} />`}
    ${shown === 3 && html`<${SentStep} a=${a} />`}
    ${shown === 4 && html`<${InterviewStep} a=${a} />`}`;
}
