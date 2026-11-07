/* Check your record: the facts Hal found in the person's files, one card at a time, so they can say which are
   right before Hal uses any of them. Hal only writes with the facts the person checked.

   The queue is the server's (`GET /profile/check`) and is worked out from what is checked, so leaving half
   way and coming back starts where the person stopped. Conflicts come first as "Which is right?", then the
   facts most worth checking, then skills in groups; the skills Hal is least sure of wait under "Show more",
   and the skills only course material names are listed apart as "Studied in your course".
   When a card is answered, focus moves to the next card's heading, so the page works from the keyboard. */
import { html, useEffect, useRef } from "../core/html.js";
import { useApi } from "../core/store.js";
import { go } from "../core/router.js";
import { plural } from "../core/format.js";
import { Head, Note, Bar } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";
import { ItemCheck, SkillsCheck, ConflictCheck, StudiedSkills } from "../record/check.js";

const PERCENT = 100;

/** The card to show now and the key that names it; null when the queue is empty. */
function current(data) {
  if (data.conflicts.length) {
    const c = data.conflicts[0];
    return { key: "conflict:" + c.id, view: html`<${ConflictCheck} key=${c.id} c=${c} />` };
  }
  const card = data.cards[0];
  if (!card) return null;
  return { key: card.key, view: card.type === "skills" ? html`<${SkillsCheck} key=${card.key} card=${card} />`
    : html`<${ItemCheck} key=${card.key} card=${card} />` };
}

/** The Check your record page. */
export function Check() {
  const { data, error } = useApi("/profile/check");
  const shownKey = useRef(null);
  const now = data ? current(data) : null;
  const key = data ? (now ? now.key : "ready") : null;
  useEffect(() => {
    // the first card keeps the page heading focused, as every page does; later ones take focus themselves
    if (key && shownKey.current && shownKey.current !== key) {
      const h = document.querySelector("#main .rec-check-h[tabindex], #main .rec-ready h2");
      if (h) h.focus();
    }
    if (key) shownKey.current = key;
  }, [key]);
  const left = data ? data.total - data.done : 0;
  return html`
    <${Head} title="Check your record" sub="Hal found these facts about you in your files. Say whether each one is right. Hal only uses the facts you have checked when it writes your CV, letters and answers.">
      <button type="button" class="btn" onClick=${() => go("record")}>Back to Your record</button>
    <//>
    ${error && html`<${Note} icon="warn" tone="warn">${error.message}<//>`}
    ${!data && !error && html`<p class="sub">Loading</p>`}
    ${data && html`<div class="stack rec-check-page">
      ${data.total > 0 && html`<div class="rec-check-progress">
        <${Bar} pct=${(data.done / data.total) * PERCENT} label="Facts checked" />
        <p class="small rec-line"><b>${data.done} of ${data.total} checked.</b> ${left > 0 ? "You can stop at any time and carry on later." : ""}</p></div>`}
      ${now ? now.view : html`<section class="card rec-ready"><div class="row"><div class="ic good"><${Icon} n="check" /></div>
          <h2 class="h2" tabindex="-1" style="margin:0">Your record is ready</h2></div>
        <p class="sub">You have checked everything Hal found. Hal uses what you said is right, and nothing you hid.</p>
        <div><button type="button" class="btn" onClick=${() => go("record")}>See your record</button></div></section>`}
      ${data.conflicts.length + data.cards.length > 1 && html`<p class="small sub rec-line">${plural(data.conflicts.length + data.cards.length - 1, "more card")} after this one.</p>`}
      ${data.more.length > 0 && html`<details class="rec-hidden rec-check-more"><summary>Show more (${plural(data.more.reduce((n, c) => n + c.items.length, 0), "skill")} Hal is less sure of)</summary>
        <p class="small sub rec-line">Hal is less sure of these: most come from only one file, such as course notes. Keep the ones that are yours; Hal never uses the rest.</p>
        <div class="stack s">${data.more.map(c => html`<${SkillsCheck} key=${c.key} card=${c} sub=${true} />`)}</div></details>`}
      ${(data.studied || []).length > 0 && html`<${StudiedSkills} items=${data.studied} />`}
    </div>`}`;
}
