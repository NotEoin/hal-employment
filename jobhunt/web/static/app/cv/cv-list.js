/* The list of the person's CVs on top of My CV: each CV's name, its star ("Use this for applications") and
   which one the page shows; for the shown CV, Rename, Use first and Move to the archive; "Add another CV"; and
   the archived CVs with Restore. The server keeps one CV starred at all times and says why when the last star
   cannot come off; this list only shows its answer. Every change answers the whole list (GET /cv/cvs), so the
   list is redrawn from what the server now holds. */
import { html, cx, useState } from "../core/html.js";
import { put } from "../core/api.js";
import { toast, invalidate } from "../core/store.js";
import { Icon } from "../ui/icons.js";
import { cvUrl } from "./chosen.js";
import { Upload, duplicateWords } from "./upload.js";

const STAR = "Use this for applications";

function Star({ c, onStar }) {
  return html`<button type="button" class=${cx("iconbtn cv-star", c.starred && "on")} aria-pressed=${c.starred ? "true" : "false"}
    aria-label=${STAR + ": " + c.name} title=${STAR} onClick=${() => onStar(c)}><${Icon} n="star" c="s" /></button>`;
}

function Rename({ c, onDone }) {
  const [name, setName] = useState(c.name);
  const save = async e => {
    e.preventDefault();
    try { onDone(await put(cvUrl("/cv/name", c.id), { name })); toast("Renamed to " + name.trim()); }
    catch (err) { toast(err.message); }
  };
  return html`<form class="row cv-rename" onSubmit=${save}><label class="lab" for="cv-name">Name of this CV</label>
    <input id="cv-name" class="input grow" value=${name} maxlength="80" onInput=${e => setName(e.target.value)} />
    <button type="submit" class="btn small primary">Save name</button>
    <button type="button" class="btn quiet small" onClick=${() => onDone(null)}>Cancel</button></form>`;
}

/** The list; `list` is GET /cv/cvs, `shown` the id of the CV the page shows, `onShow(id)` shows another one and
    `onList(list)` takes the list a change answered with. */
export function CvList({ list, shown, onShow, onList }) {
  const [renaming, setRenaming] = useState(false);
  const [adding, setAdding] = useState(false);
  const [archive, setArchive] = useState(false);
  const all = [...list.cvs, ...list.archived];
  const c = all.find(x => x.id === shown) || list.cvs[0];
  const change = async (fn, said) => {
    try { const out = await fn(); onList(out); invalidate("/cv*"); toast(said); return out; }
    catch (err) { toast(err.message); return null; }
  };
  const star = x => change(() => put(cvUrl("/cv/star", x.id), { on: !x.starred }),
    x.starred ? "Hal no longer uses " + x.name + " for applications" : "Hal uses " + x.name + " for applications");
  const toArchive = async () => {
    const out = await change(() => put(cvUrl("/cv/archive", c.id), { on: true }),
      c.name + " is in the archive. You can restore it from Archived CVs.");
    if (out) onShow(out.lead);
  };
  const restore = x => change(() => put(cvUrl("/cv/archive", x.id), { on: false }), x.name + " is back in your CVs");
  const first = () => change(() => put("/cv/order", { ids: [c.id, ...list.cvs.filter(x => x.starred && x.id !== c.id).map(x => x.id)] }),
    "Hal tries " + c.name + " first for applications");
  const added = out => {
    setAdding(false);
    if (out && out.duplicate) toast(duplicateWords(out.duplicate));
    if (out && out.cv) onShow(out.cv);
  };
  return html`<section class="card cv-list" aria-labelledby="cv-list-head">
    <div class="row"><h2 class="h2 grow" id="cv-list-head" style="margin:0">Your CVs</h2>
      <button type="button" class="btn small" aria-expanded=${adding ? "true" : "false"} onClick=${() => setAdding(!adding)}><${Icon} n="plus" c="s" />Add another CV</button>
      ${list.archived.length > 0 && html`<button type="button" class="btn quiet small" aria-expanded=${archive ? "true" : "false"}
        onClick=${() => setArchive(!archive)}>Archived CVs (${list.archived.length})</button>`}</div>
    <p class="small sub" style="margin:6px 0 10px">Hal uses the CVs marked "Use this for applications" when it writes your applications. Each CV keeps its own versions.</p>
    <ul class="cv-picks">${list.cvs.map(x => html`<li key=${x.id} class=${cx("cv-pick", x.id === c.id && "on")}>
      <${Star} c=${x} onStar=${star} />
      <button type="button" class="cv-name" aria-current=${x.id === c.id ? "true" : null} onClick=${() => onShow(x.id)}>
        <b>${x.name}</b><span class="small sub">${x.n ? "Version " + x.n : "No versions yet"}${x.lead ? ", tried first" : ""}</span></button></li>`)}</ul>
    ${!c.archived && html`<div class="row" style="margin-top:10px">
      <button type="button" class="btn quiet small" aria-expanded=${renaming ? "true" : "false"} onClick=${() => setRenaming(!renaming)}><${Icon} n="edit" c="s" />Rename</button>
      ${c.starred && !c.lead && html`<button type="button" class="btn quiet small" onClick=${first}>Try this CV first</button>`}
      ${list.cvs.length > 1 && html`<button type="button" class="btn quiet small" onClick=${toArchive}><${Icon} n="folder" c="s" />Move to the archive</button>`}</div>`}
    ${renaming && html`<${Rename} key=${c.id} c=${c} onDone=${out => { setRenaming(false); if (out) { onList(out); invalidate("/cv*"); } }} />`}
    ${adding && html`<div style="margin-top:12px"><p class="small sub" style="margin:0 0 8px">A new file becomes a CV of its own, starting at version 1. To update this CV with a new file, use Versions.</p>
      <${Upload} fresh=${true} onRead=${added} /></div>`}
    ${archive && list.archived.length > 0 && html`<div class="cv-archive" style="margin-top:12px"><h3 class="h3" style="margin:0 0 6px">Archived CVs</h3>
      <p class="small sub" style="margin:0 0 8px">Hal keeps every version of an archived CV, so applications sent with it still have it.</p>
      ${list.archived.map(x => html`<div class="li" key=${x.id}><div class="grow"><button type="button" class="btn quiet small"
          aria-current=${x.id === c.id ? "true" : null} onClick=${() => onShow(x.id)}>${x.name}</button>
        <span class="small sub">${x.n ? "Version " + x.n : ""}</span></div>
        <button type="button" class="btn small" onClick=${() => restore(x)}>Restore</button></div>`)}</div>`}</section>`;
}
