/* The preview of a CV or letter: an iframe of the page Hal prints (/cv/versions/{id}/preview.html), so what
   the person sees is the HTML that becomes the PDF. The page is drawn at A4 width and scaled to
   fit its box, and it reloads when the CV changes. The iframe is sandboxed with no scripts; in a thumbnail
   it is kept out of the tab order, because the card's own button is what the keyboard reaches. */
import { html, useEffect, useRef, useState } from "../core/html.js";
import { on } from "../core/events.js";

const PAGE_W = 794;   // A4 width in CSS pixels at 96 per inch
const PAGE_H = 1123;  // A4 height in CSS pixels

/** The preview page's address for a version, look, type and highlighted ids. */
export function previewUrl({ versionId = "current", look, type, hl = [], rev } = {}) {
  const q = new URLSearchParams();
  if (look) q.set("look", look);
  if (type) q.set("type", type);
  if (hl.length) q.set("hl", hl.join(","));
  if (rev) q.set("r", String(rev));
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
  const url = src || previewUrl({ versionId, look, type, hl, rev });
  const style = `border:0;position:absolute;top:0;left:0;width:${PAGE_W}px;height:${PAGE_H}px;background:#fff;`
    + `transform:scale(${scale});transform-origin:0 0;${thumb ? "pointer-events:none;" : ""}`;
  return html`<div class="docwrap"><div ref=${box} class="doc" style="padding:0;overflow:hidden">
    <iframe src=${url} title=${label} sandbox="allow-same-origin" tabindex=${thumb ? "-1" : undefined}
      scrolling=${thumb ? "no" : undefined} style=${style}></iframe>
  </div></div>`;
}
