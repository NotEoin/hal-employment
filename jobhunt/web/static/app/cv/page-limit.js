/* The Length card of My CV, Type and look: "Keep this CV to" no limit, one page or two pages, for the CV
   chosen on My CV, with how many pages it prints on now. Until the person chooses, the limit is the CV type's
   (`chosen` false), and the card says so. The choices come from the server (`pagelimit.choices`): a copied
   type with a longer rule adds it ("Three pages"), so its CV shows its real limit. The count is the real printed PDF's (`cvstudio/pagelimit.py`):
   reading the card prints the CV once when it has no count yet, so it may take a second, and when no browser
   can print the card says the count is not known. The person is only warned; nothing is blocked. */
import { html } from "../core/html.js";
import { put } from "../core/api.js";
import { useApi, toast, invalidate } from "../core/store.js";
import { Seg, Note } from "../ui/controls.js";
import { cvUrl } from "./chosen.js";

const PAGES = "/cv/pages";

/** "1 page", "3 pages". */
export const pagesWords = n => (n === 1 ? "1 page" : n + " pages");

/** The card. */
export function PageLimit() {
  const { data, error, reload } = useApi(cvUrl(PAGES));
  const head = html`<h2 class="h2">Length</h2>`;
  if (error) return html`<section class="card">${head}<p class="small">${error.message}</p></section>`;
  if (!data) return html`<section class="card" aria-busy="true">${head}<p class="small sub">Counting the pages of your CV...</p></section>`;
  const save = async pages => {
    try {
      await put(cvUrl(PAGES), { pages });
      invalidate("/cv*");
      reload();
      toast(pages ? "Saved. Hal keeps an eye on this CV's length." : "Saved. This CV has no page limit.");
    } catch (err) { toast(err.message); }
  };
  return html`<section class="card">${head}
    <p class="small sub">Hal counts the pages of this CV as it prints and keeps its own changes inside the limit. When your CV runs longer, Hal tells you; it never cuts your lines.</p>
    <div class="setting"><div><div class="lab">Keep this CV to</div>${!data.chosen && html`<div class="small sub">Until you choose, this follows your CV type.</div>`}</div>
      <div class="ctl"><${Seg} label="Keep this CV to" value=${data.limit} options=${data.choices} onChange=${save} /></div></div>
    ${data.over ? html`<${Note} icon="warn" tone="warn">${data.words}<//>`
      : html`<p class="small" role="status" style="margin:12px 0 0">${data.counted ? "It prints on " + pagesWords(data.pages) + " now."
        : data.words || "Hal counts the pages once it can print this CV."}</p>`}</section>`;
}
