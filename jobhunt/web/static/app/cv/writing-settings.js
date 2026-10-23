/* The Writing card of My CV, Type and look. Length, tone, entries, numbers and "Never
   use" are part of the CV type, so a change to a built-in type first makes a copy, and the card says so in
   the server's words. Spelling is the "CV and documents" setting of the same name. */
import { html, useState } from "../core/html.js";
import { put } from "../core/api.js";
import { useApi, toast, invalidate } from "../core/store.js";
import { go } from "../core/router.js";
import { Seg } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";

const TYPES = "/cv/types";
const PAGES = [[1, "1 page"], [2, "Up to 2 pages"], [0, "As needed"]];
const TONES = [["plain", "Plain"], ["formal", "Formal"], ["confident", "Confident"]];
const FORMATS = [["bullets", "Bullets"], ["paragraphs", "Short paragraphs"]];
const SPELLINGS = [["en-GB", "UK English"], ["en-US", "US English"]];
const RESULTS = [["where_have", "Where you have them"], ["rarely", "Rarely"]];

function Row({ lab, sub, children }) {
  return html`<div class="setting"><div><div class="lab">${lab}</div>${sub && html`<div class="small sub">${sub}</div>`}</div><div class="ctl">${children}</div></div>`;
}

/** The Writing card. */
export function WritingSettings() {
  const { data, error, reload } = useApi(TYPES);
  const [word, setWord] = useState("");
  if (error) return html`<section class="card"><h2 class="h2">Writing</h2><p class="small">${error.message}</p></section>`;
  if (!data) return html`<section class="card"><h2 class="h2">Writing</h2><p class="small sub">Loading...</p></section>`;
  const w = data.writing;
  const save = async change => {
    try {
      const out = await put("/cv/writing", change);
      toast(out.note || "Saved for " + out.type.name);
      reload();
    } catch (err) { toast(err.message); }
  };
  const spelling = async v => {
    try { await put("/settings/cvdocs.spelling", { value: v }); invalidate(TYPES); toast("Saved"); }
    catch (err) { toast(err.message); }
  };
  const pages = PAGES.some(([v]) => v === w.pages) ? w.pages : 2;
  const add = e => {
    e.preventDefault();
    const t = word.trim();
    if (!t || w.never_use.includes(t)) return;
    setWord("");
    save({ never_use: [...w.never_use, t] });
  };
  return html`<section class="card"><h2 class="h2">Writing</h2>
    <p class="small sub">For ${data.current.name}.${data.current.builtin ? " Changing these makes your own copy of this type." : ""}</p>
    <${Row} lab="Length"><${Seg} label="Length" value=${pages} options=${PAGES} onChange=${v => save({ pages: v })} /><//>
    <${Row} lab="Tone"><${Seg} label="Tone" value=${w.tone} options=${TONES} onChange=${v => save({ tone: v })} /><//>
    <${Row} lab="Entries as"><${Seg} label="Entries as" value=${w.format} options=${FORMATS} onChange=${v => save({ format: v })} /><//>
    <${Row} lab="Spelling"><${Seg} label="Spelling" value=${data.spelling} options=${SPELLINGS} onChange=${spelling} /><//>
    <${Row} lab="Numbers and results"><${Seg} label="Numbers and results" value=${w.results} options=${RESULTS} onChange=${v => save({ results: v })} /><//>
    <${Row} lab="Never use" sub="Words and phrases Hal must avoid.">
      ${w.never_use.map(t => html`<span class="tag" key=${t}>${t}<button aria-label=${"Remove " + t}
        onClick=${() => save({ never_use: w.never_use.filter(x => x !== t) })}><${Icon} n="x" c="s" /></button></span>`)}
      <form class="row tight" onSubmit=${add}><input class="input" style="width:12em" aria-label="Add a word or phrase Hal must avoid"
        value=${word} onInput=${e => setWord(e.target.value)} /><button class="btn small" type="submit">Add</button></form><//>
    <${Row} lab="Lines Hal must not touch" sub="Hal never changes a line you lock.">
      <button class="btn small" onClick=${() => { go("cv"); toast("Tap lines on your CV to lock them"); }}>Choose lines</button><//>
  </section>`;
}
