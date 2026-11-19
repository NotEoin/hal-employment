/* The preview of a CV or letter: an iframe of the page Hal prints (/cv/versions/{id}/preview.html), so what
   the person sees is the HTML that becomes the PDF. The page is drawn at A4 width and scaled to
   fit its box, and it reloads when the CV changes. The iframe is sandboxed with no scripts; in a thumbnail
   it is kept out of the tab order, because the card's own button is what the keyboard reaches.

   A CV set to send "Your original file" sends that file and never Hal's page, so `SentPreview` shows what
   employers get: the file's own pages as pictures for a PDF (GET /cv/original/page/{n}, listed by the API as
   `original.page_urls`); for a Word file or text, which Hal cannot show as pages, Hal's page with a line saying
   it is Hal's reading and that employers get the file as uploaded. A CV that sends Hal's copy shows Hal's page
   as before. */
import { html, useEffect, useRef, useState } from "../core/html.js";
import { on } from "../core/events.js";
import { chosenCv } from "../cv/chosen.js";
import { Note } from "./controls.js";

const KIND_WORDS = { docx: "a Word file", md: "a text file", txt: "a text file" };

const PAGE_W = 794;   // A4 width in CSS pixels at 96 per inch
const PAGE_H = 1123;  // A4 height in CSS pixels

/** The preview page's address for a version, look, type and highlighted ids. */
export function previewUrl({ versionId = "current", look, type, hl = [], rev } = {}) {
  const q = new URLSearchParams();
  if (look) q.set("look", look);
  if (type) q.set("type", type);
  if (hl.length) q.set("hl", hl.join(","));
  if (rev) q.set("r", String(rev));
  if (versionId === "current" && chosenCv()) q.set("cv", chosenCv());
  const qs = q.toString();
  return "/api/v2/cv/versions/" + encodeURIComponent(versionId) + "/preview.html" + (qs ? "?" + qs : "");
}

/** An A4 preview of Hal's page; `src` overrides the CV preview address (a letter preview, for example). */
export function DocPreview({ src, versionId, look, type, hl = [], label = "CV preview", thumb = false }) {
  const box = useRef(null);
  const [scale, setScale] = useState(0.5);
  const [rev, setRev] = useState(0);
  useEffect(() => {
    const el = box.current;
    if (!el || typeof ResizeObserver === "undefined") return undefined;
    const ro = new ResizeObserver(() => setScale(el.clientWidth / PAGE_W || 0.5));
    ro.observe(el);
    return () => ro.disconnect();
  }, []);
  useEffect(() => on(ev => { if (ev.type === "cv") setRev(r => r + 1); }), []);
  const frame = useRef(null);
  // Chromium neither fires focus on an iframe nor matches :focus or :focus-within when Tab moves into it, so
  // the ring follows the window's blur and focus instead (checked in headless Chromium, 2026-10-23). The class
  // is set on the box at once, then checked again once focus has settled.
  useEffect(() => {
    if (thumb) return undefined;
    const sync = () => { if (box.current) box.current.classList.toggle("focused", document.activeElement === frame.current); };
    const check = () => { sync(); setTimeout(sync, 0); };
    window.addEventListener("blur", check);
    window.addEventListener("focus", check);
    document.addEventListener("focusin", check);
    return () => {
      window.removeEventListener("blur", check);
      window.removeEventListener("focus", check);
      document.removeEventListener("focusin", check);
    };
  }, [thumb]);
  const url = src || previewUrl({ versionId, look, type, hl, rev });
  const style = `border:0;position:absolute;top:0;left:0;width:${PAGE_W}px;height:${PAGE_H}px;background:#fff;`
    + `transform:scale(${scale});transform-origin:0 0;${thumb ? "pointer-events:none;" : ""}`;
  return html`<div class="docwrap"><div ref=${box} class="doc" style="padding:0;overflow:hidden">
    <iframe ref=${frame} src=${url} title=${label} sandbox="allow-same-origin" tabindex=${thumb ? "-1" : undefined}
      scrolling=${thumb ? "no" : undefined} style=${style}></iframe>
  </div></div>`;
}

/** The pages of the person's own PDF as pictures, each with alt text that counts it ("Page 1 of 2 of your CV
    file"). */
export function FilePages({ original }) {
  const n = original.page_urls.length;
  return html`<div class="stack s file-pages">${original.page_urls.map((u, i) => html`<img key=${u} class="file-page"
    src=${u} alt=${"Page " + (i + 1) + " of " + n + " of your CV file"} />`)}</div>`;
}

/** What employers get of a CV: its own PDF's pages while it sends that file (`original`, from the API), Hal's
    reading of a file that has no pages to show with a line saying so, else Hal's page (`DocPreview`). */
export function SentPreview({ original, versionId, look, label = "CV preview" }) {
  if (!original) return html`<${DocPreview} versionId=${versionId} look=${look} label=${label} />`;
  if (original.page_urls.length) {
    return html`<div class="stack s sent-preview"><${FilePages} original=${original} />
      <p class="small sub" style="margin:0">This is your file, ${original.name}, as employers get it.</p></div>`;
  }
  const words = original.why ? original.why + " This is Hal's reading of it."
    : "Employers get your file, " + original.name + ", as you uploaded it. Hal cannot show "
      + (KIND_WORDS[original.kind] || "this file") + " as pages, so this is Hal's reading of it.";
  return html`<div class="stack s sent-preview"><${Note} icon=${original.why ? "warn" : "file"} tone=${original.why ? "warn" : undefined}>${words}<//>
    <${DocPreview} versionId=${versionId || original.version_id} look=${look} label=${label} /></div>`;
}
