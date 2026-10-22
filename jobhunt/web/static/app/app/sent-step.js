/* The Sent step of an application: when it went, the follow-up date, and the people
   to message. Hal drafts notes; the person sends them. */
import { html } from "../core/html.js";
import { Icon } from "../ui/icons.js";

const when = at => { try { return new Date(at).toLocaleString("en-GB", { day: "numeric", month: "short", hour: "2-digit", minute: "2-digit" }); } catch (err) { return at; } };

/** The Sent step. */
export function SentStep({ a }) {
  if (!a.sent) return html`<section class="card empty">Hal moves an application here when the employer's site says it was received, or when you press "I sent it myself".</section>`;
  const initials = n => n.split(/\s+/).map(w => w[0] || "").join("").slice(0, 2).toUpperCase();
  return html`<div class="grid2"><section class="card"><h2 class="h2">Timeline</h2>
      ${a.sent.timeline.map(t => html`<div class="li"><div class="ic"><${Icon} n="check" /></div><div class="grow"><b>${t.t}</b> <span class="small sub">${when(t.at)}</span><div class="small sub">${t.d}</div></div></div>`)}</section>
    <section class="card"><h2 class="h2">People</h2>
      ${a.sent.people.map(p => html`<div class="li"><div class="avatar">${initials(p.name)}</div><div class="grow"><b>${p.name}</b><div class="small sub">${[p.role, p.why].filter(Boolean).join(". ")}</div></div></div>`)}
      <p class="small sub">${a.sent.people.length ? "Hal drafts notes; you send them." : "Message the hiring manager? Hal drafted a note for you to send, under Documents."}</p></section></div>`;
}
