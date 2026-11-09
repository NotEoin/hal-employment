/* "Check the list" of a sort, as its own page ("sort-<id>"): the review of "Sort a big folder for me"
   (record/sort.js `SortReview`) given the whole window.

   The review was a card in the left column of Your record's Overview, whose two-column grid gave it about
   half the page however wide the window was (a graduate's feedback, 2026-11-09). On its own page, with the
   page's width cap lifted (`wide` in core/router.js), it fills the window and resizes with it; its table
   scrolls sideways inside itself on a small screen. */
import { html } from "../core/html.js";
import { go } from "../core/router.js";
import { Head } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";
import { SortReview } from "../record/sort.js";

/** The review of one sort, with the way back to Your record. */
export function SortPage({ arg }) {
  const id = Number(arg);
  return html`<div class="stack s rec-sort-page">
    <div><button type="button" class="btn quiet small" onClick=${() => go("record")}>
      <${Icon} n="chevL" c="s" />Back to your record</button></div>
    <${Head} title="Check the sorted list" sub="Hal suggests where each file goes. Nothing is added to your record until you add it." />
    ${Number.isInteger(id) && id > 0 ? html`<${SortReview} id=${id} />`
      : html`<p class="sub">Choose a sort to check on Your record, Overview.</p>`}</div>`;
}
