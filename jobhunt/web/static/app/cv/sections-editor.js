/* The Sections card of Type and look: order, names, most entries, Lock and Show. Each
   change is saved at once as a new version (a name when the box is left), so Undo is choosing the version
   before it in Versions. Adding a section saves the whole document with the new, empty section. */
import { html, useState, useEffect } from "../core/html.js";
import { get, put } from "../core/api.js";
import { useApi, toast, invalidate } from "../core/store.js";
import { Switch } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";

const MAX_CHOICES = ["-", "1", "2", "3", "4", "6"];
const ADDABLE = [["projects", "Projects"], ["volunteering", "Volunteering"], ["publications", "Publications"],
  ["languages", "Languages"], ["certifications", "Certificates"], ["achievements", "Achievements"],
  ["interests", "Interests"], ["custom", "Your own"]];

function freeId(doc, kind) {
  const taken = new Set(doc.sections.map(s => s.id));
  if (!taken.has(kind)) return kind;
  let n = 2;
  while (taken.has(kind + "-" + n)) n += 1;
  return kind + "-" + n;
}

function AddSection({ onAdded }) {
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState("projects");
  const add = async () => {
    try {
      const doc = await get("/cv/document");
      const title = ADDABLE.find(a => a[0] === kind)[1];
      const { version_id, n, ...body } = doc;
      body.sections = [...body.sections, { id: freeId(body, kind), kind, title, text: "", entries: [], also: "",
        visible: true, locked: false, max_entries: null }];
      await put("/cv/document", { doc: body, what: "You added a section: " + title });
      setOpen(false); onAdded(); toast("Added " + title + ". Saved as a new version.");
    } catch (err) { toast(err.message); }
  };
  if (!open) return html`<button type="button" class="btn quiet small" style="margin-top:10px" onClick=${() => setOpen(true)}><${Icon} n="plus" c="s" />Add a section</button>`;
  return html`<div class="row" style="margin-top:10px"><select class="input" style="width:auto" aria-label="Section to add" value=${kind} onChange=${e => setKind(e.target.value)}>
      ${ADDABLE.map(([k, w]) => html`<option value=${k}>${w}</option>`)}</select>
    <button type="button" class="btn small" onClick=${add}>Add</button><button type="button" class="btn quiet small" onClick=${() => setOpen(false)}>Cancel</button></div>`;
}

/** The card. */
export function SectionsEditor() {
  const { data, reload } = useApi("/cv/layout");
  const [secs, setSecs] = useState(null);
  useEffect(() => { if (data) setSecs(data.sections); }, [data]);
  if (!secs) return html`<section class="card"><h2 class="h2">Sections</h2><p class="small sub">Loading your sections.</p></section>`;
  const save = async next => {
    setSecs(next);
    try { await put("/cv/layout", { sections: next, locked_lines: data.locked_lines }); invalidate("/cv*"); }
    catch (err) { toast(err.message); reload(); }
  };
  const move = (i, d) => { const n = [...secs]; const [x] = n.splice(i, 1); n.splice(i + d, 0, x); save(n); };
  const upd = (i, p) => save(secs.map((x, k) => k === i ? { ...x, ...p } : x));
  return html`<section class="card"><h2 class="h2">Sections</h2><p class="small sub">Order, names and limits. Locked sections are never changed by Hal.</p>
    ${secs.map((x, i) => html`<div class="secrow" key=${x.id}>
      <span class="updown"><button type="button" aria-label=${"Move " + x.title + " up"} disabled=${i === 0} onClick=${() => move(i, -1)}><${Icon} n="chevU" c="s" /></button><button type="button" aria-label=${"Move " + x.title + " down"} disabled=${i === secs.length - 1} onClick=${() => move(i, 1)}><${Icon} n="chevD" c="s" /></button></span>
      <input class="input" value=${x.title} aria-label="Section name" onInput=${e => setSecs(secs.map((y, k) => k === i ? { ...y, title: e.target.value } : y))}
        onChange=${e => upd(i, { title: e.target.value })} />
      <span class="row tight hide-n"><span class="small sub">Max</span><select class="input" style="width:auto" aria-label=${"Most entries in " + x.title}
        value=${x.max == null ? "-" : String(x.max)} onChange=${e => upd(i, { max: e.target.value === "-" ? null : Number(e.target.value) })}>
        ${[...new Set([...MAX_CHOICES, x.max == null ? "-" : String(x.max)])].map(o => html`<option value=${o}>${o}</option>`)}</select></span>
      <span class="row tight"><${Switch} on=${x.lock} label=${"Lock " + x.title} onChange=${v => upd(i, { lock: v })} /><${Icon} n="lock" c="s" /></span>
      <span class="row tight"><${Switch} on=${x.show} label=${"Show " + x.title} onChange=${v => upd(i, { show: v })} /><span class="small sub">Show</span></span>
    </div>`)}
    <${AddSection} onAdded=${() => { reload(); invalidate("/cv*"); }} /></section>`;
}
