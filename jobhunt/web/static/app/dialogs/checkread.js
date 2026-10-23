/* The "Check what Hal read" dialog: the original on the left and Hal's
   reading on the right, each row "Fine" or "Check this" with an edit button.

   A PDF shows as pictures of its pages, a Word file or pasted text as its paragraphs. Saving a row sends
   PUT /cv/parse-report/{path}, which keeps the correction as a new version of Hal's copy; the file itself
   never changes. */
import { html, useState } from "../core/html.js";
import { put } from "../core/api.js";
import { useApi, closeDialog, toast, invalidate } from "../core/store.js";
import { DlgHead } from "../ui/dialog.js";
import { Icon } from "../ui/icons.js";

function Original({ o }) {
  if (o.page_urls.length) {
    return html`<div class="stack s">${o.page_urls.map((u, i) => html`<img src=${u} alt=${"Page " + (i + 1) + " of your CV"}
      style="width:100%;border-radius:3px;box-shadow:0 1px 2px rgba(0,0,0,0.1)" />`)}</div>`;
  }
  const out = [];
  let list = null;
  o.paragraphs.forEach((p, i) => {
    if (p.kind === "bullet") {
      if (!list) { list = []; out.push(html`<ul>${list}</ul>`); }
      list.push(html`<li>${p.text}</li>`);
      return;
    }
    list = null;
    if (p.kind === "title") out.push(html`<h1>${p.text}</h1>`);
    else if (p.kind === "heading") out.push(html`<div class="sec">${p.text}</div>`);
    else out.push(html`<p>${p.text}</p>`);
  });
  return html`<div class="docwrap"><div class="doc" style="aspect-ratio:auto;min-height:100%;font-size:clamp(7px,1.9cqw,11px);white-space:pre-wrap">${out}</div></div>`;
}

function Row({ r, onSaved }) {
  const [editing, setEditing] = useState(false);
  const [value, setValue] = useState(r.value);
  const [busy, setBusy] = useState(false);
  const save = async () => {
    setBusy(true);
    try {
      await put("/cv/parse-report/" + encodeURIComponent(r.path), { value });
      setEditing(false);
      toast("Saved. Hal keeps your correction as a new version.");
      onSaved();
    } catch (err) { toast(err.message); }
    setBusy(false);
  };
  if (editing) {
    return html`<form class="li" style="padding:9px 2px" onSubmit=${e => { e.preventDefault(); save(); }}>
      <div class="grow"><label class="small sub" for=${"fix-" + r.path}>${r.label}</label>
        <input id=${"fix-" + r.path} class="input" style="width:100%;margin-top:4px" value=${value} onInput=${e => setValue(e.target.value)} /></div>
      <button type="submit" class="btn small primary" disabled=${busy}>Save</button>
      <button type="button" class="btn small quiet" onClick=${() => { setEditing(false); setValue(r.value); }}>Cancel</button></form>`;
  }
  return html`<div class="li" style="padding:9px 2px" data-row=${r.path}><div class="grow"><div class="small sub">${r.label}</div>
      <b style="font-weight:500">${r.value || "Not found"}</b>${!r.sure && r.why && html`<div class="small sub">${r.why}</div>`}</div>
    ${r.sure ? html`<span class="flag ok"><${Icon} n="check" c="s" />Fine</span>` : html`<span class="flag check"><${Icon} n="warn" c="s" />Check this</span>`}
    <button type="button" class="iconbtn" aria-label=${"Edit " + r.label} onClick=${() => setEditing(true)}><${Icon} n="edit" c="s" /></button></div>`;
}

/** The dialog. */
export default function CheckRead() {
  const { data, error, reload } = useApi("/cv/parse-report");
  const saved = () => { reload(); invalidate("/cv*"); };
  const head = html`<${DlgHead} title="Check what Hal read" sub="Your file on the left, Hal's reading on the right. Correct anything Hal got wrong." icon="eye" />`;
  if (error) return html`${head}<p>${error.message}</p><footer><button type="button" class="btn" onClick=${closeDialog}>Close</button></footer>`;
  if (!data) return html`${head}<p class="small sub">Loading what Hal read.</p>`;
  if (!data.file) return html`${head}<p>Hal has not read a CV yet. Add your CV first.</p><footer><button type="button" class="btn" onClick=${closeDialog}>Close</button></footer>`;
  return html`${head}
    <div class="grid2 even"><div><${Original} o=${data.original} /></div>
      <div>${data.rows.map(r => html`<${Row} key=${r.path + ":" + r.value} r=${r} onSaved=${saved} />`)}</div></div>
    <footer><a class="btn quiet" href="/api/v2/cv/original" download=${data.file.name}><${Icon} n="download" c="s" />Original</a>
      <button type="button" class="btn primary" onClick=${closeDialog}>Looks right</button></footer>`;
}
CheckRead.wide = true;
