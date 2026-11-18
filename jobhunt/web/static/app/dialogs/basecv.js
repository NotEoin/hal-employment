/* The "Which CV is this application built from?" dialog, opened from the CV tab of an application's
   Documents step when more than one CV is in use for applications. It lists those CVs in the person's order
   with how well each fits the advert (`GET .../cv/bases`), marks Hal's pick and the CV in use now, and builds
   the application's CV on the one chosen (`PUT .../cv/base`). The letter, message and answers stay as they
   are. When some of the person's own CV edits would not fit the chosen CV, it says how many before they
   confirm, and the button says so too. */
import { html, cx, useState } from "../core/html.js";
import { put } from "../core/api.js";
import { useApi, closeDialog, toast, invalidate } from "../core/store.js";
import { DlgHead } from "../ui/dialog.js";
import { plural } from "../core/format.js";

const fitWords = c => (c.of ? `Covers ${c.have} of the ${c.of} skills this advert asks for` : "Not compared for this advert");

/** Asks which CV to build the application on; `d.app` is the application, `d.then` runs once Hal has started. */
export default function BaseCv({ d }) {
  const a = d.app || {};
  const path = "/applications/" + encodeURIComponent(a.id) + "/cv";
  const { data } = useApi(path + "/bases");
  const [choice, setChoice] = useState(null);
  const [busy, setBusy] = useState(false);
  const cvs = (data && data.cvs) || [];
  const now = cvs.find(c => c.chosen);
  const chosen = cvs.find(c => c.id === (choice || (now && now.id)));
  const lost = chosen && !chosen.chosen ? chosen.lost : 0;
  const use = async () => {
    setBusy(true);
    try {
      await put(path + "/base", { doc_id: chosen.id });
      closeDialog();
      invalidate("/applications*");
      toast("Hal is making your CV for this job from " + chosen.label + ".");
      if (d.then) d.then();
    } catch (err) { toast(err.message); setBusy(false); }
  };
  return html`<${DlgHead} title="Which CV should this application use?" sub=${[a.job && a.job.title, a.job && a.job.org].filter(Boolean).join(", ")} icon="file" />
    <p class="small">Hal makes your CV for this job again from the CV you choose. Your cover letter, message and answers stay as they are.</p>
    ${!data ? html`<p class="small sub">Loading your CVs.</p>` : html`<div class="stack s" role="radiogroup" aria-label="Your CVs for applications">
      ${cvs.map(c => html`<button type="button" key=${c.id} role="radio" aria-checked=${chosen && chosen.id === c.id ? "true" : "false"}
        class=${cx("choice", chosen && chosen.id === c.id && "on")} onClick=${() => setChoice(c.id)}>
        <b>${c.name}</b>${c.pick ? html` <span class="chip info">Hal's pick</span>` : ""}${c.chosen ? html` <span class="chip">In use now</span>` : ""}
        <span class="small sub">${fitWords(c)}</span></button>`)}</div>`}
    ${lost > 0 && html`<p class="note warn small" role="status">${plural(lost, "edit")} of your own to this job's CV cannot be carried over, as ${chosen.label} does not have the lines you changed. The rest are kept.</p>`}
    <footer><button type="button" class="btn quiet" onClick=${closeDialog}>Cancel</button>
      <button type="button" class="btn primary" disabled=${busy || !chosen || chosen.chosen} onClick=${use}>${lost > 0 ? "Use it and drop " + plural(lost, "edit") : "Use this CV"}</button></footer>`;
}
