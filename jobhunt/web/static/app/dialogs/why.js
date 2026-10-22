/* The "Why Hal did this" drawer, opened with openDialog("why", { item }) or ({ id }); see ui/why.js. */
import { html } from "../core/html.js";
import { WhyDrawer } from "../ui/why.js";

function Why({ d }) {
  return html`<${WhyDrawer} item=${d.item} id=${d.id} />`;
}
Why.drawer = true;
export default Why;
