/* STUB: the sources part */
/* Setup guide step: What you're looking for. This part is being built; until then it shows the stub card. */
import { html } from "../../core/html.js";
import { StubCard } from "../../ui/stub.js";

export const STUB = "sources";
/** The step's body while it is being built. */
export function Step() { return html`<h2 class="h2">What you're looking for</h2><${StubCard} owner="sources" />`; }
export default Step;
