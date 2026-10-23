/* Settings, Updates: the version, Check now, the GitHub sign-in, Update Hal,
   What's new, Go back to the previous version, and whether updates are checked for the owner's signature.

   Everything comes from GET /updates. Update Hal opens the update dialog, which starts the updater and waits
   for Hal to come back. In a window from another computer, signing in to GitHub happens on the computer
   Hal runs on; Update Hal and Go back still work. `PANEL_KEYS` names the catalogue rows this
   panel draws itself, so the page leaves them out: drawn twice, the catalogue's copies came first with an
   empty Version and an Update Hal button that did nothing (Windows 11 install test). The daily check stays a
   catalogue row. */
import { html, useState } from "../core/html.js";
import { post, run } from "../core/api.js";
import { useApi, load, openDialog, toast } from "../core/store.js";
import { can, host } from "../core/remote.js";
import { ask } from "../ui/dialog.js";

const CHECKING = "Looking for a new version";
export const PANEL_KEYS = new Set(["updates.version", "updates.github", "updates.apply", "updates.whats_new",
                                   "updates.rollback", "updates.helper"]);

/** "today at 09:00" or "21 October at 09:00". */
export function when(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  if (isNaN(d)) return "";
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  if (new Date().toDateString() === d.toDateString()) return "today at " + time;
  return d.toLocaleDateString("en-GB", { day: "numeric", month: "long" }) + " at " + time;
}

/** "1 change" or "3 changes". */
export function changes(n) { return n === 1 ? "1 change" : n + " changes"; }

/** The words of the Version row. */
export function versionWords(u) {
  const checked = u.checked_at ? " Checked " + when(u.checked_at) + "." : "";
  if (u.developer) return u.version + "." + checked;
  if (u.available) return u.version + ". Hal " + (u.new_version || "") + " is ready (" + changes(u.behind) + ")." + checked;
  return u.version + ", the newest." + checked;
}

function Row({ k, label, sub, children }) {
  return html`<div class="setting" data-key=${k}><div class="sc-text"><div class="lab">${label}</div>
    ${sub && html`<div class="small sub">${sub}</div>`}</div><div class="ctl">${children}</div></div>`;
}

/** The Updates rows. */
export function UpdatesPanel() {
  const r = useApi("/updates");
  const auth = useApi("/updates/auth");
  const [busy, setBusy] = useState("");
  if (r.error) return html`<p class="small sub">${r.error.message}</p>`;
  if (!r.data) return html`<p class="small sub">Loading</p>`;
  const u = r.data;
  const signedOut = !!(auth.data && auth.data.signed_in === false);
  const refresh = async () => { await load("/updates").catch(() => {}); await load("/updates/auth").catch(() => {}); };
  const checkNow = async () => {
    setBusy("check");
    try {
      const a = await run("/updates/check", {});
      toast(a.state === "done" ? ((a.result && a.result.words) || "Hal is up to date.") : (a.error || "Hal could not look for a new version."));
      await refresh();
    } catch (e) { toast(e.message); } finally { setBusy(""); }
  };
  const signIn = async () => {
    setBusy("sign-in");
    try {
      const a = await run("/updates/sign-in", {});
      toast(a.state === "done" ? "Signed in to GitHub." : (a.error || "Hal could not sign in to GitHub."));
      await refresh();
    } catch (e) { toast(e.message); } finally { setBusy(""); }
  };
  const goBack = async () => {
    const yes = await ask({ title: "Go back to the previous version", text: u.go_back_warning, yes: "Go back",
                            no: "Keep this version", danger: true });
    if (!yes) return;
    try { await post("/updates/rollback", { confirm: true }); openDialog("update", { goingBack: true }); } catch (e) { toast(e.message); }
  };
  const last = u.last_result;
  const github = signedOut ? "Hal cannot reach its updates on GitHub. Sign in to GitHub again."
    : "Hal uses the sign-in you made when you installed it.";
  return html`
    <${Row} k="updates.version" label="Version" sub=${versionWords(u)}>
      ${!u.developer && html`<button type="button" class="btn small" disabled=${!!busy} onClick=${checkNow}>${busy === "check" ? CHECKING : "Check now"}</button>`}<//>
    ${u.developer ? html`<${Row} k="updates.developer" label="Updates" sub=${u.blocked_by}>
        ${u.commit && html`<span class="small sub sc-info">${/* details */ "git commit " + u.commit.slice(0, 7)}</span>`}<//>`
      : html`<${Row} k="updates.github" label="GitHub sign-in" sub=${github}>
        ${signedOut && (can("sign_in_here")
          ? html`<button type="button" class="btn small primary" disabled=${!!busy} onClick=${signIn}>Sign in to GitHub again</button>`
          : html`<span class="small sub">Do this on ${host()}</span>`)}<//>`}
    ${u.available && !u.developer && html`<${Row} k="updates.apply" label="Update Hal"
        sub=${u.blocked_by || "Updates install only when you press Update Hal, and never while Hal is filling in a form."}>
        <button type="button" class="btn small primary" disabled=${!!u.blocked_by} onClick=${() => openDialog("update", { u })}>${"Update Hal (" + changes(u.behind) + ")"}</button><//>`}
    ${u.waiting && html`<${Row} k="updates.waiting" label="Waiting to update" sub="Hal is filling in a form. It will update when it has finished." />`}
    ${u.whats_new && u.whats_new.length > 0 && html`<${Row} k="updates.whats_new" label="What's new">
        <ul class="small sc-list">${u.whats_new.map(x => html`<li>${x}</li>`)}</ul><//>`}
    ${last && last.words && html`<${Row} k="updates.last" label="Last update" sub=${last.words + (last.at ? " (" + when(last.at) + ")" : "")}>
        ${last.details && html`<details class="small"><summary>Details</summary><p class="sub">${/* details */ last.details}</p></details>`}<//>`}
    ${u.can_rollback && html`<${Row} k="updates.rollback" label="Go back to the previous version" sub=${u.go_back_warning}>
        <button type="button" class="btn small" onClick=${goBack}>Go back</button><//>`}
    <${Row} k="updates.signing" label="Who can send updates" sub=${u.signing && u.signing.words} />
    ${u.helper_wanted && html`<${Row} k="updates.helper" label="Browser helper" sub=${"This Hal comes with version " + u.helper_wanted + " of the browser helper. It updates itself when Hal does."} />`}`;
}
