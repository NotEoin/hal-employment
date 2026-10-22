/* STUB: the cv-reading part */
/* Setup guide step: Your CV. This part is being built; until then it shows the stub card. */
import { html } from "../../core/html.js";
import { StubCard } from "../../ui/stub.js";

export const STUB = "cv-reading";
/** The step's body while it is being built. */
export function Step() { return html`<h2 class="h2">Your CV</h2><${StubCard} owner="cv-reading" />`; }
export default Step;
