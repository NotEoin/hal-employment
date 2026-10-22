/* Hal's instructions to the AI, under Settings, Advanced.

   Lists every instruction with what it is used for, marks the ones the person changed and the ones whose
   original changed in an update since, and opens each in the prompt dialog. */
import { html } from "../core/html.js";
import { useApi, openDialog } from "../core/store.js";

/** The list of instructions with "Read or change". */
export function PromptsPanel() {
  const r = useApi("/prompts");
  if (r.error) return html`<p class="small sub">${r.error.message}</p>`;
  if (!r.data) return html`<p class="small sub">Loading</p>`;
  return html`<div class="sc-prompts">${r.data.map(p => html`<div class="li sc-prompt" data-prompt=${p.id}><div class="grow">
      <b>${p.name}</b>${p.changed && html`<span class="changed">Changed</span>`}
      ${p.default_changed && html`<span class="chip warn small">Hal's original changed</span>`}
      <div class="small sub">Used for: ${p.used_for}</div></div>
    <button type="button" class="btn small" aria-label=${"Read or change: " + p.name}
      onClick=${() => openDialog("prompt", { id: p.id, name: p.name, used_for: p.used_for })}>Read or change</button></div>`)}</div>`;
}
