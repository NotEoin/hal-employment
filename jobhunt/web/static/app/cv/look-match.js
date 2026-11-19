/* "Match my file's look" on My CV, Type and look, beside the look gallery. For a CV whose own file is a PDF,
   Hal measures the file's look and makes a look of it, kept with the person's data (POST /cv/look/match).
   While the CV uses one of the person's own looks, the card shows the file's own pages beside Hal's copy in
   that look, with both page counts in words, what Hal could only approximate, a "Make it fit" step when Hal's
   copy is longer, and renaming. A look is deleted from its card in the gallery (`LookActions`). */
import { html, useState } from "../core/html.js";
import { post, put, del } from "../core/api.js";
import { useApi, toast, invalidate } from "../core/store.js";
import { DocPreview, FilePages } from "../ui/doc-preview.js";
import { Note } from "../ui/controls.js";
import { cvUrl } from "./chosen.js";

const MATCH = "/cv/look/match";
const looksUrl = id => "/cv/looks/" + encodeURIComponent(id);

function Rename({ look, onDone }) {
  const [name, setName] = useState(look.name);
  const save = async e => {
    e.preventDefault();
    try {
      const done = await put(looksUrl(look.id), { name });
      toast("Renamed the look " + done.name + ".");
      onDone();
    } catch (err) { toast(err.message); }
  };
  return html`<form class="row tight" onSubmit=${save}>
    <input class="input" style="width:auto;flex:1;min-width:0" aria-label="Name of this look" value=${name}
      onInput=${e => setName(e.target.value)} />
    <button class="btn small" type="submit">Rename</button></form>`;
}

/** The match card: the button, then the comparison while the CV uses a personal look. */
export function LookMatch() {
  const { data, error, reload } = useApi(cvUrl(MATCH), { on: [ev => ev.type === "cv"] });
  const [busy, setBusy] = useState(false);
  const head = html`<h3 class="h3" style="margin:0">Match my file's look</h3>`;
  if (error) return html`<section class="card soft">${head}<p class="small">${error.message}</p></section>`;
  if (!data) return html`<section class="card soft" aria-busy="true">${head}<p class="small sub">Looking at your file...</p></section>`;
  const act = async (fn, words) => {
    setBusy(true);
    try {
      await fn();
      invalidate("/cv*");
      reload();
      toast(words);
    } catch (err) { toast(err.message); }
    setBusy(false);
  };
  const matchIt = () => act(() => post(cvUrl(MATCH), {}), "Hal matched your file's look and chose it for this CV.");
  const fit = () => act(() => post(cvUrl(looksUrl(data.look.id) + "/fit"), {}), "Hal made the type a little tighter.");
  const look = data.look;
  return html`<section class="card soft" data-part="match">${head}
    ${!data.can ? html`<p class="small sub" style="margin:4px 0 0">${data.why}</p>` : html`
      <p class="small sub" style="margin:4px 0 8px">Hal measures your file's page: margins, type, headings, how each job is written and the
        colours. It makes a look of it that you can use for any CV. Your words stay the same.</p>
      ${!(look && look.from_this_file) && html`<button class="btn primary small" disabled=${busy} onClick=${matchIt}>Match my file's look</button>`}`}
    ${look && html`<div class="stack s" style="margin-top:12px">
      <p class="small" role="status" style="margin:0"><b>${look.name}.</b> ${look.words}</p>
      ${look.notes.map(n => html`<${Note} key=${n} icon="info">${n}<//>`)}
      ${data.file && data.file.page_urls.length > 0 && html`<div class="grid2 even">
        <div class="stack s"><b class="small">Your file</b><${FilePages} original=${data.file} /></div>
        <div class="stack s"><b class="small">Hal's copy</b><${DocPreview} look=${look.id} label=${"Your CV in the look " + look.name} /></div></div>`}
      <div class="row">${look.can_fit && html`<button class="btn small" disabled=${busy} onClick=${fit}>Make it fit</button>`}
        ${look.from_this_file && data.can && html`<button class="btn quiet small" disabled=${busy} onClick=${matchIt}>Measure my file again</button>`}</div>
      <${Rename} key=${look.id + look.name} look=${look} onDone=${() => { invalidate("/cv*"); reload(); }} />
    </div>`}
  </section>`;
}

/** Delete on a personal look's gallery card, asked twice; Hal refuses while a CV uses the look and says which. */
export function LookActions({ look, onDone }) {
  const [asking, setAsking] = useState(false);
  if (!look.personal) return null;
  const remove = async e => {
    e.stopPropagation();
    try {
      await del(looksUrl(look.id));
      toast("Deleted the look " + look.name + ".");
      onDone();
    } catch (err) { toast(err.message); }
    setAsking(false);
  };
  const stop = e => e.stopPropagation();
  return asking ? html`<span class="row tight" onClick=${stop}><span class="small">Delete ${look.name}?</span>
      <button type="button" class="btn small" onClick=${remove}>Delete</button>
      <button type="button" class="btn quiet small" onClick=${e => { stop(e); setAsking(false); }}>Keep it</button></span>`
    : html`<button type="button" class="btn quiet small" onClick=${e => { stop(e); setAsking(true); }}
        aria-label=${"Delete the look " + look.name}>Delete</button>`;
}
