/* STUB: the ai-setup part */
/* Setup guide step: How Hal thinks. This part is being built; until then it shows the stub card. */
import { html } from "../../core/html.js";
import { StubCard } from "../../ui/stub.js";

export const STUB = "ai-setup";
/** The step's body while it is being built. */
export function Step() { return html`<h2 class="h2">How Hal thinks</h2><${StubCard} owner="ai-setup" />`; }
export default Step;
