/* The Interview step of an application: the booking and Hal's notes. The practice
   buttons are cut for now. */
import { html } from "../core/html.js";

/** The Interview step. */
export function InterviewStep({ a }) {
  if (!a.interview) return html`<section class="card empty">When an interview or test email arrives, Hal adds the date here and prepares practice questions.</section>`;
  const iv = a.interview;
  return html`<div class="stack"><section class="card"><h2 class="h2">${iv.when || "Interview or test booked"}</h2>
      ${iv.where && html`<p class="sub">${iv.where}</p>`}</section>
    ${iv.prep_md && html`<section class="card"><h2 class="h2">Hal's notes</h2><div class="advert">${iv.prep_md}</div></section>`}
    ${iv.questions.length > 0 && html`<section class="card"><h2 class="h2">Likely questions</h2>${iv.questions.map(q => html`<div class="li"><div class="grow">${q}</div></div>`)}</section>`}</div>`;
}
