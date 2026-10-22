/* STUB: the cv-looks part */
/* A grey page where a CV or letter preview will show. This part is being built; until then it shows the stub card. */
import { html } from "../core/html.js";

export const STUB = "cv-looks";
/** A blank paper-coloured page the size of A4, standing in for the preview. */
export function DocPreview() {
  return html`<div class="card soft" style="aspect-ratio:210/297;max-width:520px" aria-label="Preview being built" role="img"></div>`;
}
