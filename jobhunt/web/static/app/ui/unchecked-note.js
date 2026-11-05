/* The notice on the pages where Hal writes for the person (My CV, an application's documents): how many facts
   Hal found in their files that they have not checked, that Hal only uses checked ones, and a button to check
   them. One component, so every page says it the same way; it shows nothing when there is nothing to check.

   The count is the profile overview's `to_check`, which a `profile` event makes stale (core/events.js), so the
   notice follows the person's checking on another page or computer. */
import { html } from "../core/html.js";
import { useApi } from "../core/store.js";
import { go } from "../core/router.js";
import { plural } from "../core/format.js";
import { Note } from "./controls.js";

/** "Hal found 120 facts ... Hal only uses facts you have checked.", with a button; nothing at none. */
export function UncheckedNote() {
  const { data } = useApi("/profile");
  const n = (data && data.to_check) || 0;
  if (!n) return null;
  return html`<div class="unchecked-note"><${Note}>
    <span>Hal found ${plural(n, "fact")} in your files that you have not checked yet. Hal only uses facts you have checked.</span>
    <div><button type="button" class="btn small" onClick=${() => go("check")}>Check them</button></div><//></div>`;
}
