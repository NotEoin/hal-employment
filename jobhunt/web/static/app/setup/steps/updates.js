/* Setup guide step 10: Updates.

   Hal updates itself from GitHub, which needs no account for a public project, so this step only explains
   that and offers "Check for updates now", or "Sign in to GitHub" only when GitHub refused the last check. A
   developer's own checkout is updated with git, and the step says so. */
import { html, useState } from "../../core/html.js";
import { run } from "../../core/api.js";
import { useApi, load } from "../../core/store.js";
import { can, host } from "../../core/remote.js";

export const ABOUT = "Hal updates itself from GitHub and needs no account for that. Updates install only when you press Update Hal, and never while Hal is filling in a form.";

/** The step's body. */
export function Step() {
  const r = useApi("/updates");
  const auth = useApi("/updates/auth");
  const [said, setSaid] = useState("");
  const [busy, setBusy] = useState(false);
  const u = r.data;
  const signedOut = !!(auth.data && auth.data.signed_in === false);
  const go = async path => {
    setBusy(true);
    try {
      const a = await run(path, {});
      setSaid(a.state === "done" ? ((a.result && a.result.words) || "Signed in to GitHub.") : (a.error || "Hal could not reach GitHub."));
      await load("/updates").catch(() => {});
      await load("/updates/auth").catch(() => {});
    } catch (e) { setSaid(e.message); } finally { setBusy(false); }
  };
  if (u && u.developer) {
    return html`<h1 class="h1">Updates</h1><p class="sub">You run Hal from your own folder. Update it with git.</p>`;
  }
  return html`<h1 class="h1">Updates</h1><p class="sub">${ABOUT}</p>
    <section class="card" style="margin-top:16px"><div class="row">
      ${signedOut ? (can("sign_in_here")
        ? html`<button type="button" class="btn primary" disabled=${busy} onClick=${() => go("/updates/sign-in")}>Sign in to GitHub</button>`
        : html`<span class="small sub">Sign in to GitHub on ${host()}.</span>`)
        : html`<button type="button" class="btn" disabled=${busy} onClick=${() => go("/updates/check")}>Check for updates now</button>`}
      ${said && html`<span class="small" role="status">${said}</span>`}</div>
      <p class="small sub" style="margin:10px 0 0">Hal checks once a day and tells you on Home when a new version is ready. You can switch that off in Settings, Updates.</p></section>`;
}
export default Step;
