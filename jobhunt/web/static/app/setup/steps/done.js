/* Setup guide step 11: All set.

   Reaching this step finishes the guide and starts Hal's first search (PUT /setup/done, which starts it
   only once). The list shows each step between Welcome and All set: done, or left for later, in which
   case it stays on Home's "Finish setting up" card. */
import { html, useEffect } from "../../core/html.js";
import { put } from "../../core/api.js";
import { invalidate } from "../../core/store.js";
import { Icon } from "../../ui/icons.js";

/** The step's body. */
export function Step({ steps = [] }) {
  useEffect(() => { put("/setup/done", { state: "done" }).then(() => invalidate("/home")).catch(() => {}); }, []);
  const middle = steps.filter(s => s.id !== "welcome" && s.id !== "done");
  return html`<div class="row"><div class="ic good" style="width:48px;height:48px"><${Icon} n="check" c="l" /></div><h1 class="h1">You're set up</h1></div>
    <p class="sub" style="margin-top:10px;font-size:17px">Hal's first search is starting now. The first matches usually appear within 10 minutes; Home shows progress such as "Checked 40 of 300 jobs" until then.</p>
    <section class="card" style="margin-top:16px">${middle.map(s => {
      const ok = s.state === "done";
      return html`<div class="li" style="align-items:center;padding:9px 2px"><span class=${ok ? "flag ok" : "flag check"}><${Icon} n=${ok ? "check" : "clock"} c="s" /></span>
        <div class="grow">${s.name}</div><span class="small sub">${ok ? "Done" : "Skipped: it is on Home when you want it"}</span></div>`;
    })}</section>
    <p class="small sub" style="margin-top:12px">Everything here can be changed in Settings, and the guide can be run again from there. Hal keeps working when you close its window.</p>`;
}
export default Step;
