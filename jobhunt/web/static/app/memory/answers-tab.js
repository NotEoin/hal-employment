/* What Hal remembers, Saved answers: the answers Hal reuses on forms, each with
   "Where may Hal use this?". An answer waiting for a choice shows "Choose where to use it" and is never used
   until the person picks. The answers Hal works out for each form are shown read-only. Private answers are
   never listed: the server leaves them out. */
import { html, useState } from "../core/html.js";
import { put, del } from "../core/api.js";
import { useApi, toast, invalidate } from "../core/store.js";
import { Chip, Empty } from "../ui/controls.js";

const PATH = "/answers";
const WAITING = "Choose where to use it";

const usedWords = n => (n === 1 ? "Used once" : `Used ${n} times`);

/** One saved answer: its words, where it may be used, and Delete. */
function AnswerRow({ an }) {
  const [text, setText] = useState(an.a);
  const save = async (body, said) => {
    try { await put(PATH + "/" + encodeURIComponent(an.id), body); toast(said); }
    catch (err) { toast(err.message); setText(an.a); }
    invalidate(PATH);
  };
  const choose = e => {
    const c = an.choices[Number(e.target.value)];
    if (c) save({ scope: c.scope, scope_key: c.scope_key }, "Saved. " + c.label + ".");
  };
  const remove = async () => {
    try { await del(PATH + "/" + encodeURIComponent(an.id)); toast("Deleted the saved answer."); }
    catch (err) { toast(err.message); }
    invalidate(PATH);
  };
  const picked = an.choices.findIndex(c => c.scope === an.scope && c.scope_key === an.scope_key);
  return html`<div class="field-row" data-answer=${an.id}>
    <div class="q">${an.q}</div>
    ${an.needs_choice ? html`<${Chip} tone="warn">${WAITING}</${Chip}>`
      : html`<span class="small sub">${an.auto ? "Worked out each time" : usedWords(an.uses)}</span>`}
    <div class="ans row">
      <input class="input" style="flex:1;min-width:200px" value=${an.auto ? an.a : text} readonly=${an.auto} aria-label=${an.q}
        onInput=${e => setText(e.target.value)}
        onChange=${e => { if (!an.auto && e.target.value.trim() !== an.a) save({ a: e.target.value }, "Saved the new answer."); }} />
      ${an.auto
        ? html`<select class="input" style="width:auto" aria-label="Where may Hal use this?" disabled><option>${an.scope_label}</option></select>`
        : html`<select class="input" style="width:auto" aria-label="Where may Hal use this?" onChange=${choose}>
            ${picked < 0 && html`<option value="" selected>${an.needs_choice ? WAITING : an.scope_label}</option>`}
            ${an.choices.map((c, i) => html`<option value=${String(i)} selected=${i === picked}>${c.label}</option>`)}</select>
          <button type="button" class="btn quiet small" aria-label=${"Delete the saved answer to " + an.q} onClick=${remove}>Delete</button>`}
    </div></div>`;
}

/** The number on the tab in What Hal remembers: the saved answers, not the ones Hal works out each time. */
export function useCount() {
  const { data } = useApi(PATH);
  const n = (data || []).filter(an => !an.auto).length;
  return n || "";
}

/** The Saved answers tab. */
export function AnswersTab() {
  const { data, error } = useApi(PATH);
  const intro = html`<p class="small sub">Answers Hal reuses on forms. Each one says where it may be used, so a salary or "why us" answer never lands on the wrong form.</p>`;
  if (error) return html`<div class="card">${intro}<p class="sub">${error.message}</p></div>`;
  if (!data) return html`<div class="card">${intro}<p class="sub">Loading</p></div>`;
  return html`<div class="card" data-answers-tab>${intro}
    ${data.length ? data.map(an => html`<${AnswerRow} key=${[an.id, an.a, an.scope, an.scope_key].join(":")} an=${an} />`)
      : html`<${Empty}>No saved answers yet. When you type an answer into a form, Hal keeps it for next time.</${Empty}>`}</div>`;
}
