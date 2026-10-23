/* Hal's brain in Settings: the Claude account, the allowance in counts, and Install, Sign in and Test

   The account reads "Signed in as <email> (Claude Pro)" from `claude auth status`, and the allowance says
   what Hal can count ("Hal used Claude 23 times today. 12 were background checks (your limit is 30).")
   because Claude Code does not report the share of the limit used. Sign in and Sign out are offered only on
   the computer Hal runs on. Install and sign-in rows show only while Claude is not ready; Test is the group's
   own "Test now" row. `PANEL_KEYS` names the info rows this panel draws, so the page can leave out
   their empty catalogue rows. */
import { html, useState } from "../core/html.js";
import { post } from "../core/api.js";
import { useApi, load, toast } from "../core/store.js";
import { isRemote } from "../core/remote.js";
import { ClaudeRows, STATUS, remoteSignInWords } from "../setup/steps/ai.js";

export const PANEL_KEYS = new Set(["ai.claude_account", "ai.allowance"]);

/** The Claude account, the allowance and the setup rows for the AI in use. */
export function AiPanel() {
  const s = useApi(STATUS);
  const [busy, setBusy] = useState(false);
  const reload = () => load(STATUS).catch(() => {});
  if (s.error) return html`<p class="small sub">${s.error.message}</p>`;
  if (!s.data) return html`<p class="small sub">Hal is checking its AI.</p>`;
  const st = s.data;
  const claude = st.provider === "claude_cli";
  const signOut = async () => {
    setBusy(true);
    try { await post("/ai/claude/sign-out"); toast("Signed out of Claude on this computer."); }
    catch (e) { toast(e.message); } finally { setBusy(false); reload(); }
  };
  return html`<div data-ai-panel>
    ${claude && html`<div class="setting" data-key="ai.claude_account.now"><div class="sc-text"><div class="lab">Claude account</div>
        <div class="small sub sc-info" data-account>${st.account}</div>
        ${isRemote() && html`<div class="small sub">${remoteSignInWords()}</div>`}</div>
      <div class="ctl">${st.signed_in && !isRemote() && html`<button type="button" class="btn small" disabled=${busy} onClick=${signOut}>Sign out</button>`}</div></div>
      <div class="setting" data-key="ai.allowance.now"><div class="sc-text"><div class="lab">Your Claude allowance</div>
        <div class="small sub sc-info" data-allowance>${st.allowance}</div></div></div>`}
    ${!claude && st.why && html`<div class="setting"><div class="sc-text"><div class="lab">Is the AI ready?</div><div class="small sub">${st.why}</div></div></div>`}
    ${claude && !st.ready && html`<${ClaudeRows} status=${st} reload=${reload} compact=${true} signWords="Sign in again" />`}
  </div>`;
}
