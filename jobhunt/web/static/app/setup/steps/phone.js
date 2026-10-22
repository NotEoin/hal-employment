/* STUB: the setup part */
/* Setup guide step: Phone alerts. This part is being built; until then it shows the stub card. */
import { html } from "../../core/html.js";
import { StubCard } from "../../ui/stub.js";

export const STUB = "setup";
/** The step's body while it is being built. */
export function Step() { return html`<h2 class="h2">Phone alerts</h2><${StubCard} owner="setup" />`; }
export default Step;
