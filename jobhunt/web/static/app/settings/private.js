/* Private answers, in Settings and the setup guide.

   Shows only whether answers are saved, never what they are, with "Change answers", which opens a form with
   empty fields. */
import { html, cx } from "../core/html.js";
import { useApi, openDialog } from "../core/store.js";
import { Icon } from "../ui/icons.js";

/** "Saved" or "Not saved" and the Change answers button. */
export function PrivatePanel() {
  const r = useApi("/private");
  const saved = !!(r.data && r.data.questions.some(q => q.saved));
  return html`<span class=${cx("chip", saved && "good")}><${Icon} n="lock" c="s" />${saved ? "Saved" : "Not saved"}</span>
    <button type="button" class="btn small" onClick=${() => openDialog("private")}>Change answers</button>`;
}
