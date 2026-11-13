/* The editor's copy of the CV and its changes: the same targets and ops as the server's model
   (`jobhunt/cvstudio/model.py`: "s:<id>/e:<id>/b:<id>", then a field name), so the page shows each change at once
   and the server makes the same changes again, in order, on the version the editor opened. Only the ops the
   editor sends are here: replace, insert, delete, move, hide and show. */

const POOLS = { s: "sections", e: "entries", b: "bullets" };
const ID_BYTES = 4;   // as `model.new_id`: 8 hex characters
// An entry's id starts with its section kind's prefix, as `model.PREFIX`.
const PREFIX = { summary: "su", education: "ed", experience: "ex", volunteering: "vo", projects: "pr", research: "re",
  skills: "sk", achievements: "ac", publications: "pu", certifications: "ce", languages: "la", interests: "in",
  teaching: "te", talks: "ta", portfolio: "po", additional: "ad", custom: "cu" };

/** A fresh id for a part added in the editor ("b-3f9a1c2e"); `kind` gives an entry its section's prefix. */
export function newId(kind) {
  const hex = [...crypto.getRandomValues(new Uint8Array(ID_BYTES))].map(b => b.toString(16).padStart(2, "0")).join("");
  return (kind === "line" ? "b" : PREFIX[kind] || "cu") + "-" + hex;
}

/** A new, empty line. */
export const blankLine = () => ({ id: newId("line"), text: "", sources: [], locked: false });

/** A new, empty entry for a section of `kind`, written as `style` ("bullets" or "paragraph"). */
export const blankEntry = (kind, style = "bullets") => ({ id: newId(kind), heading: "", org: "", location: "",
  start: "", end: "", grade: "", meta: "", link: "", style, text: "", bullets: [], modules: [], show_marks_from: null,
  items: [], hidden: false, locked: false });

/** The target of a section, an entry in it, a line of that entry, or a field of any of them. */
export function target(section, entry, line, field) {
  return ["s:" + section.id, entry && "e:" + entry.id, line && "b:" + line.id, field].filter(Boolean).join("/");
}

function locate(doc, t) {
  let obj = doc, holder = null, field = null;
  for (const part of t ? t.split("/") : []) {
    const i = part.indexOf(":");
    if (i < 0) { field = part; break; }
    holder = obj[POOLS[part.slice(0, i)]];
    obj = holder.find(x => x.id === part.slice(i + 1));
    if (!obj) throw new Error("This part of your CV is not there any more.");
  }
  return { obj, holder, field };
}

/** The document with one change made; the input is never changed. */
export function applyChange(doc, c) {
  const out = JSON.parse(JSON.stringify(doc));
  const { obj, holder, field } = locate(out, c.target);
  if (c.op === "replace") {
    if (field) obj[field] = c.value;
    else if (typeof c.value === "string") obj.text = c.value;
    else Object.assign(obj, c.value);
  } else if (c.op === "insert") {
    const pool = obj.sections || obj.entries || obj.bullets;
    pool.splice(c.position == null ? pool.length : c.position, 0, JSON.parse(JSON.stringify(c.value)));
  } else if (c.op === "hide" || c.op === "show") {
    if ("visible" in obj) obj.visible = c.op === "show";
    else obj.hidden = c.op === "hide";
  } else if (c.op === "delete") {
    holder.splice(holder.indexOf(obj), 1);
  } else if (c.op === "move") {
    holder.splice(holder.indexOf(obj), 1);
    holder.splice(Math.max(0, Math.min(c.position, holder.length)), 0, obj);
  } else {
    throw new Error("The editor cannot make a " + c.op + " change.");
  }
  return out;
}

/** The change list with `c` added; typing into one field keeps one change for it, not one a keystroke. */
export function addChange(changes, c) {
  const last = changes[changes.length - 1];
  if (c.op === "replace" && last && last.op === "replace" && last.target === c.target) return [...changes.slice(0, -1), c];
  return [...changes, c];
}
