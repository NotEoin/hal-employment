/* STUB: the browser-helper part */
/* Setup guide step: Browser helper. This part is being built; until then it shows the stub card. */
import { html } from "../../core/html.js";
import { StubCard } from "../../ui/stub.js";

export const STUB = "browser-helper";
/** The step's body while it is being built. */
export function Step() { return html`<h2 class="h2">Browser helper</h2><${StubCard} owner="browser-helper" />`; }
export default Step;
