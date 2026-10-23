/* The QR code for phone alerts. The server draws it (segno, as SVG) from the private
   alert code, which never reaches the page as text; the picture is shown as an image so its SVG cannot
   carry anything that runs. */
import { html } from "../core/html.js";

/** The QR code from `svg` (SVG text), or an empty square while it is being made. */
export function QR({ svg }) {
  if (!svg) return html`<div class="qr" role="img" aria-label="The QR code is being made"></div>`;
  const src = "data:image/svg+xml;charset=utf-8," + encodeURIComponent(svg);
  return html`<img class="qr-img" src=${src} alt="QR code to scan with your phone" width="150" height="150" />`;
}

export default QR;
