/* Setup guide step 6: How should Hal think?

   Two choices with Hal's recommendation from this computer's memory and graphics card, then, for the Claude
   subscription, three rows: Install, Sign in, Test. Each button starts an action and the row follows it, so
   the page never guesses: Install shows the installer's own lines and the time so far, Sign in ticks when
   Claude Code says so, and Test shows how long Claude took. In a window from another computer the sign-in is
   refused with words, because Claude's sign-in window would open on the computer Hal runs on.
   `ClaudeRows` is shared with Settings, Hal's brain. */
import { html, cx, useState, useEffect } from "../../core/html.js";
import { get, post, put } from "../../core/api.js";
import { useApi, load, toast } from "../../core/store.js";
import { isRemote, host } from "../../core/remote.js";
import { Icon } from "../../ui/icons.js";
import { Note } from "../../ui/controls.js";

export const STATUS = "/ai/status?fresh=1";
const CLAUDE_TEXT = "Fast and good at writing. Needs Claude Pro or Max (the free plan cannot be used). The advert, your CV and the evidence Hal picks are sent to Claude when it writes.";
const LOCAL_TEXT = "Everything stays on this computer, but it needs a strong graphics card.";
const INSTALLING = "Installing Claude (this usually takes about a minute)";
// Claude names its own window "claude" on Windows 11, whatever title Hal gives it
const SIGN_IN_NOTE = "When you press Sign in, your browser opens Claude's page, and a small black window called \"claude\" may appear too. If the browser shows a code, paste it into that small window. This page ticks as soon as you are signed in.";
const TICK_MS = 1000;
const POLL_MS = 1000;   // the step list of a running action is read this often, so the installer's lines show live
const FINISHED = new Set(["done", "error", "stopped", "interrupted"]);

/** Posts `path`, then reads the action it started until it ends, passing each read to `onUpdate`. */
async function follow(path, onUpdate) {
  const started = await post(path, {});
  const id = started && started.action_id;
  if (!id) return started;
  for (;;) {
    const a = await get("/actions/" + encodeURIComponent(id));
    onUpdate(a);
    if (FINISHED.has(a.state)) return a;
    await new Promise(done => setTimeout(done, POLL_MS));
  }
}

/** "Sign in on <host>, the computer Hal runs on", with why. */
export function remoteSignInWords() {
  return "Sign in on " + host() + ", the computer Hal runs on. Claude's sign-in window opens on that computer, so the sign-in has to be done there.";
}

function useElapsed(on) {
  const [s, setS] = useState(0);
  useEffect(() => {
    if (!on) { setS(0); return undefined; }
    const started = Date.now();
    const t = setInterval(() => setS(Math.round((Date.now() - started) / 1000)), TICK_MS);
    return () => clearInterval(t);
  }, [on]);
  return s;
}

function Row({ id, title, sub, state, children }) {
  return html`<div class="li" data-ai-row=${id} data-state=${state} style="align-items:center">
    <div class=${cx("ic", state === "done" && "good")}>${state === "done" ? html`<${Icon} n="check" />`
      : state === "now" ? html`<span class="eye sm"></span>` : html`<${Icon} n="clock" />`}</div>
    <div class="grow"><b>${title}</b><div class="small sub">${sub}</div></div>${children}</div>`;
}

/** Install, Sign in and Test for the Claude subscription (or Test alone for another AI), with live status. */
export function ClaudeRows({ status, reload, compact, signWords = "Sign in" }) {
  const [busy, setBusy] = useState("");
  const [line, setLine] = useState("");
  const [fail, setFail] = useState("");
  const [tested, setTested] = useState(null);
  const elapsed = useElapsed(busy === "install");
  const st = status || {};
  const claude = st.provider === "claude_cli";
  const installed = !!st.installed;
  const signed = !!st.signed_in && st.plan_ok !== false;
  const freePlan = claude && installed && !!st.signed_in && st.plan_ok === false;
  const now = !claude ? 2 : !installed ? 0 : !signed ? 1 : tested && tested.ok ? 3 : 2;

  const go = async (key, path) => {
    setBusy(key); setFail(""); setLine("");
    try {
      const a = await follow(path, x => { const last = x.steps && x.steps[x.steps.length - 1]; if (last && x.steps.length > 1) setLine(last.text); });
      if (a.state !== "done") setFail(a.error || "That did not work. Hal kept the details in Activity.");
      else if (key === "test") { setTested(a.result); if (!a.result.ok) setFail(a.result.words); }
    // the new state is read first, so the Install button does not come back for a moment
    } catch (e) { setFail(e.message); } finally { await reload(); setBusy(""); }
  };
  const button = (key, path, words) => html`<button type="button" class="btn primary small" disabled=${!!busy}
    onClick=${() => go(key, path)}>${fail ? "Try again" : words}</button>`;
  const rows = [];
  if (claude) {
    rows.push(html`<${Row} key="install" id="install" title="Install Claude" state=${installed ? "done" : now === 0 ? "now" : "wait"}
      sub=${installed ? "Installed" : busy === "install" ? INSTALLING + ": " + elapsed + " s" + (line ? ". " + line : "")
        : "Hal downloads and installs it for you. No admin prompt."}>
      ${now === 0 && button("install", "/ai/claude/install", "Install")}<//>`);
    rows.push(html`<${Row} key="sign" id="sign" title="Sign in with your Claude account" state=${signed ? "done" : now === 1 ? "now" : "wait"}
      sub=${signed ? st.account : busy === "sign" ? "Waiting for you to sign in" : "Opens Claude's own sign-in page. Hal never sees your password."}>
      ${now === 1 && !isRemote() && !freePlan && button("sign", "/ai/claude/sign-in", signWords)}<//>`);
  }
  rows.push(html`<${Row} key="test" id="test" title="Test" state=${now === 3 ? "done" : now === 2 ? "now" : "wait"}
    sub=${tested && tested.ok ? tested.words : busy === "test" ? "Hal is asking a short question" : claude ? "Hal asks Claude a short question." : "Hal asks its AI a short question."}>
    ${(now === 2 || (!claude && tested)) && button("test", "/ai/test", "Test")}<//>`);
  return html`<section class="card" style=${compact ? "" : "margin-top:18px"} data-ai-steps>${rows}
    ${claude && now === 1 && isRemote() && html`<div style="margin-top:10px" data-remote-sign-in><${Note} tone="lock" icon="lock">${remoteSignInWords()}<//></div>`}
    ${claude && now === 1 && !isRemote() && !freePlan && html`<div style="margin-top:10px"><${Note}>${SIGN_IN_NOTE}<//></div>`}
    ${freePlan && html`<div style="margin-top:10px" role="alert" data-free-plan><${Note} tone="warn" icon="warn">${st.why}<//></div>`}
    ${fail && html`<div style="margin-top:10px" role="alert" data-ai-fail><${Note} tone="warn" icon="warn">${fail}<//></div>`}
    ${claude && now === 3 && !compact && html`<div style="margin-top:10px"><${Note}>Hal shares your Claude allowance with your own chats. It never uses more than ${st.cap} background checks a day, and it pauses them if you reach a limit. You can change this in Settings.<//></div>`}
  </section>`;
}

function Choice({ id, on, icon, title, chip, tone, text, pick }) {
  return html`<button type="button" class=${cx("choice", on && "on")} aria-pressed=${on ? "true" : "false"} data-provider=${id}
    onClick=${() => pick(id)}><span class=${cx("ic", on && "info")}><${Icon} n=${icon} /></span><b class="h3">${title}</b>
    ${chip && html`<span class=${cx("chip", tone)}>${chip}</span>`}<span class="small sub">${text}</span></button>`;
}

/** The step's body. */
export function Step() {
  const rec = useApi("/ai/recommend");
  const status = useApi(STATUS);
  const st = status.data;
  const r = rec.data;
  const provider = st && st.provider;
  const where = r && r.machine && r.machine.laptop ? "Best for this laptop" : "Best for this computer";
  const reload = () => load(STATUS).catch(() => {});
  const pick = async id => {
    if (id === provider) return;
    try { await put("/settings/ai.provider", { value: id }); await reload(); } catch (e) { toast(e.message); }
  };
  const claudeFits = !!r && r.pick === "claude_cli";
  const localText = !r ? LOCAL_TEXT : claudeFits ? LOCAL_TEXT + " " + r.why : "Everything stays on this computer. " + r.why;
  return html`<h1 class="h1">How should Hal think?</h1><p class="sub">Hal needs an AI to read adverts and write. Pick one; you can change it later.</p>
    <div class="cols" style="margin-top:16px">
      <${Choice} id="claude_cli" on=${provider === "claude_cli"} icon="cpu" title="Your Claude subscription" pick=${pick}
        chip=${claudeFits ? where : ""} tone="good" text=${CLAUDE_TEXT} />
      <${Choice} id="local" on=${provider === "local"} icon="lock" title="On this computer" pick=${pick}
        chip=${r ? (claudeFits ? "Too slow here" : where) : ""} tone=${claudeFits ? "warn" : "good"} text=${localText} />
    </div>
    ${(provider === "hal" || (st && st.hal_available)) && html`<div class="row" style="margin-top:10px">${provider === "hal"
      ? html`<span class="small sub">Hal uses its own AI on this computer, through the Hal gateway. Nothing goes to Claude.</span>`
      : html`<button type="button" class="btn quiet small" onClick=${() => pick("hal")}>Use Hal's own AI (Hal is installed)</button>`}</div>`}
    <div class="row" style="margin-top:10px">${provider === "openai_compat"
      ? html`<span class="small sub">Hal uses another local server. Its address is in Settings, Hal's brain.</span>`
      : html`<button type="button" class="btn quiet small" onClick=${() => pick("openai_compat")}>Use another local server instead</button>`}</div>
    ${status.error && html`<p class="small sub" role="alert">${status.error.message}</p>`}
    ${st ? html`<${ClaudeRows} status=${st} reload=${reload} />` : !status.error && html`<p class="small sub">Hal is looking at this computer.</p>`}`;
}
export default Step;
