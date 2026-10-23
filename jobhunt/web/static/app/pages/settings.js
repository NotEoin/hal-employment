/* Settings, drawn from /settings/schema.

   Every row comes from the registry, so a setting an area adds shows here with no change to this file.
   Groups with more than plain rows hand over to their panels: Is Hal working?, Privacy and data and
   "Use Hal from another computer" draw the whole group (the panel draws its rows under the same
   `data-key`s); the goal's earlier versions, Hal's instructions, AI and Updates add to the rows. A change saves at once; the server's SetResult says whether it needs a restart (the page
   offers Restart) or changes scores (the page offers "Re-check N jobs"). Rows marked advanced sit under
   "Show more" in their group, except while searching, which looks through every row's words. */
import { html, cx, useState } from "../core/html.js";
import { put, del, run } from "../core/api.js";
import { useApi, useStore, load, openDialog, openSetup, setTheme, setSimpleNav, toast } from "../core/store.js";
import { go, route } from "../core/router.js";
import { Head, Note } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";
import { SettingCtl } from "../ui/setting-control.js";
import { HealthPanel } from "../settings/health.js";
import { PrivacyPanel } from "../settings/privacy.js";
import { PromptsPanel } from "../settings/prompts.js";
import { GoalPanel } from "../settings/goal.js";
import { AiPanel, PANEL_KEYS as AI_PANEL_KEYS } from "../settings/ai.js";
import { UpdatesPanel } from "../settings/updates.js";
import { RemotePanel } from "../settings/remote.js";

const SUB = "Everything Hal does can be changed here. Most changes save straight away; the few that need a restart say so and offer a Restart button.";
const SHOWN_WORDS = new Set(["money", "times", "hours"]);  // kinds whose typed value the server rewords
const OTHER_PANELS = { ai: AiPanel, updates: UpdatesPanel };
// Info rows a panel draws with their words, so the group leaves out their empty catalogue rows.
const DRAWN_BY_PANEL = { ai: AI_PANEL_KEYS };
// Buttons whose action has an API path; the rest belong to panels or to areas still being built.
const BUTTON_PATHS = { "find.rescore": "/jobs/rescore", "ai.test": "/ai/test", "system.pause": "/status/pause",
  "system.quit": "/system/quit" };
const BUTTON_WORDS = { "ai.test": "Test now" };
const CLAUSE = /^([a-z][a-z0-9_.]*)\s*(?:(==|!=)\s*'([^']*)')?$/;

/** True when a row's visible_if holds for these values (`key == 'v'`, `key != 'v'`, `key`, joined by and). */
export function visibleIf(expr, values) {
  if (!expr || !expr.trim()) return true;
  return expr.trim().split(/\s+and\s+/).every(part => {
    const m = CLAUSE.exec(part.trim());
    if (!m) return true;
    const v = values[m[1]];
    const text = v == null ? "" : String(v);
    if (!m[2]) return !!v && !(Array.isArray(v) && !v.length);
    return m[2] === "==" ? text === m[3] : text !== m[3];
  });
}

/** True when the search words appear in a row's label, help or choices. */
export function matches(it, q) {
  const words = [it.label, it.help || "", ...(it.choices || []).map(c => c[1])].join(" ").toLowerCase();
  return q.toLowerCase().split(/\s+/).filter(Boolean).every(w => words.includes(w));
}

function useBrowsers(on) {
  const r = useApi(on ? "/health/browsers" : null);
  if (!r.data) return {};
  const list = r.data.browsers || [];
  return { found: new Set(list.filter(b => b.found).map(b => b.id)),
           unsupported: new Set(list.filter(b => !b.supported).map(b => b.id)) };
}

function Row({ it, ctx }) {
  const { vals, err, save, reset } = ctx;
  const value = vals.values[it.key];
  const changed = vals.changed.includes(it.key);
  const words = SHOWN_WORDS.has(it.kind) && vals.display[it.key];
  if (it.kind === "prompts") {
    return html`<div class="setting" data-key=${it.key}><div class="full"><div class="lab">${it.label}</div>
      ${it.help && html`<div class="small sub">${it.help}</div>`}<${PromptsPanel} /></div></div>`;
  }
  return html`<div class="setting" data-key=${it.key}><div class="sc-text">
      <div class="lab">${it.label}${changed && html`<span class="changed">Changed</span>`}</div>
      ${it.help && html`<div class="small sub">${it.help}</div>`}
      ${words && html`<div class="small sub">${words}</div>`}
      ${it.disabled_why && html`<div class="small sub">${it.disabled_why}</div>`}
      ${err[it.key] && html`<div class="small sc-err" role="alert">${err[it.key]}</div>`}</div>
    <div class="ctl"><${SettingCtl} it=${it} value=${value} onChange=${v => save(it, v)} extra=${ctx.extra(it)} />
      ${changed && !it.disabled_why && html`<button type="button" class="btn quiet small" aria-label=${"Reset " + it.label}
        onClick=${() => reset(it)}>Reset</button>`}</div></div>`;
}

function Group({ gr, ctx, searching }) {
  const [more, setMore] = useState(false);
  const drawn = !searching && DRAWN_BY_PANEL[gr.id];
  const shown = gr.items.filter(it => visibleIf(it.visible_if, ctx.vals.values) && !(drawn && drawn.has(it.key)));
  const plain = searching ? shown : shown.filter(it => !it.advanced);
  const hidden = searching ? [] : shown.filter(it => it.advanced);
  const Other = OTHER_PANELS[gr.id];
  let body;
  if (gr.id === "health" && !searching) body = html`<${HealthPanel} />`;
  else if (gr.id === "privacy" && !searching) body = html`<${PrivacyPanel} />`;
  else if (gr.id === "remote" && !searching) body = html`<${RemotePanel} />`;
  else {
    body = html`${plain.map(it => html`<${Row} key=${it.key} it=${it} ctx=${ctx} />`)}
      ${more && hidden.map(it => html`<${Row} key=${it.key} it=${it} ctx=${ctx} />`)}
      ${hidden.length > 0 && html`<div class="sc-more"><button type="button" class="btn quiet small" aria-expanded=${more ? "true" : "false"}
        onClick=${() => setMore(!more)}>${more ? "Show less" : "Show more (" + hidden.length + ")"}</button></div>`}
      ${gr.id === "goal" && !searching && html`<${GoalPanel} />`}
      ${Other && !searching && html`<${Other} />`}`;
  }
  return html`<section class="card" data-group=${gr.id}><h2 class="h2">${gr.name}</h2>
    ${ctx.notes(gr.id)}${body}</section>`;
}

/** The Settings page; `arg` is a group id from "#settings-<group>". */
export function Settings({ arg }) {
  const schema = useApi("/settings/schema");
  const values = useApi("/settings");
  const priv = useApi("/private");
  const problems = useStore(s => (s.status && s.status.problems) || 0);
  const [q, setQ] = useState("");
  const [err, setErr] = useState({});
  const [offers, setOffers] = useState({});   // group id -> {restart, rescore}
  const [busy, setBusy] = useState(false);
  const groups = (schema.data && schema.data.groups) || [];
  const g = groups.some(x => x.id === arg) ? arg : "you";
  const browsers = useBrowsers(g === "applying" || !!q);

  if (schema.error || values.error) {
    return html`<${Head} title="Settings" sub=${SUB} /><div class="card empty">${(schema.error || values.error).message}</div>`;
  }
  const vals = values.data || { values: {}, changed: [], display: {}, secrets: {} };
  const privateSaved = !!(priv.data && priv.data.questions.some(x => x.saved));

  const save = async (it, value) => {
    try {
      const r = await put("/settings/" + encodeURIComponent(it.key), { value });
      setErr(e => ({ ...e, [it.key]: null }));
      await load("/settings").catch(() => {});
      toast("Saved");
      if (it.key === "advanced.theme") setTheme(r.value);
      if (it.key === "advanced.simple_menu") setSimpleNav(r.value);
      const group = groupOf(groups, it.key);
      if (r.restart || r.rescore_count != null) {
        setOffers(o => ({ ...o, [group]: { restart: !!(r.restart || (o[group] && o[group].restart)),
          rescore: r.rescore_count != null ? r.rescore_count : o[group] && o[group].rescore } }));
      }
    } catch (e) {
      setErr(x => ({ ...x, [it.key]: e.message }));
    }
  };
  const reset = async it => {
    try {
      const r = await del("/settings/" + encodeURIComponent(it.key));
      setErr(e => ({ ...e, [it.key]: null }));
      await load("/settings").catch(() => {});
      toast("Back to " + (r.display || "the default"));
    } catch (e) { toast(e.message); }
  };
  const recheck = async () => {
    setBusy(true);
    try {
      await run(BUTTON_PATHS["find.rescore"], {});
      toast("Re-checked your jobs with these points.");
      setOffers({});
    } catch (e) { toast(e.message); } finally { setBusy(false); }
  };
  const press = async it => {
    const path = BUTTON_PATHS[it.action];
    if (it.action === "find.rescore") { recheck(); return; }
    if (!path) { toast("This part is being built."); return; }
    try { await run(path, {}); toast(it.label + ": done."); } catch (e) { toast(e.message); }
  };
  const recheckWords = n => (n == null ? "Re-check every open job" : "Re-check " + n + (n === 1 ? " job" : " jobs"));
  const ctx = {
    vals, err, save, reset,
    extra: it => ({ secrets: vals.secrets, privateSaved, press, busy,
      words: it.action === "find.rescore" ? recheckWords(offersN(offers)) : BUTTON_WORDS[it.action],
      ...(it.key === "applying.browser" ? browsers : {}) }),
    notes: id => {
      const o = offers[id];
      if (!o) return null;
      return html`${o.restart && html`<${Note} tone="warn" icon="refresh"><div class="row sc-note"><span>This change works after Hal restarts.</span>
          <button type="button" class="btn small" onClick=${() => openDialog("restart")}>Restart Hal</button></div><//>`}
        ${o.rescore != null && html`<${Note} icon="sliders"><div class="row sc-note"><span>These points change the scores of your open jobs.</span>
          <button type="button" class="btn small primary" disabled=${busy} onClick=${recheck}>${recheckWords(o.rescore)}</button></div><//>`}`;
    },
  };
  const searching = !!q.trim();
  const shownGroups = searching
    ? groups.map(gr => ({ ...gr, items: gr.items.filter(it => matches(it, q)) })).filter(gr => gr.items.length)
    : groups.filter(gr => gr.id === g);
  return html`
    <${Head} title="Settings" sub=${SUB}>
      <input class="input sc-search" type="search" placeholder="Search all settings" aria-label="Search all settings" value=${q}
        onInput=${e => setQ(e.target.value)} />
      <button type="button" class="btn" onClick=${() => openSetup(0)}>Open the setup guide again</button>
    <//>
    ${vals.restart_needed && !Object.values(offers).some(o => o.restart) && html`<div class="sc-banner"><${Note} tone="warn" icon="refresh">
      <div class="row sc-note"><span>A change you made works after Hal restarts.</span>
      <button type="button" class="btn small" onClick=${() => openDialog("restart")}>Restart Hal</button></div><//></div>`}
    <div class="settings">
      <nav class="sgroups" aria-label="Settings groups">${groups.map(gr => html`<button type="button"
        class=${cx("nav", !searching && g === gr.id && "on")} aria-current=${!searching && g === gr.id ? "page" : undefined}
        onClick=${() => { setQ(""); go(route("settings", gr.id)); }}><${Icon} n=${gr.icon} />${gr.name}
        ${gr.id === "health" && problems > 0 && html`<span class="count hot">${problems}</span>`}</button>`)}</nav>
      <div class="stack">${!schema.data || !values.data ? html`<div class="card empty">Loading your settings</div>`
        : shownGroups.map(gr => html`<${Group} key=${gr.id + (searching ? "-q" : "")} gr=${gr} ctx=${ctx} searching=${searching} />`)}
        ${searching && !shownGroups.length && html`<div class="card empty">No setting matches "${q}".</div>`}</div>
    </div>`;
}

function groupOf(groups, key) {
  const gr = groups.find(x => x.items.some(it => it.key === key));
  return gr ? gr.id : "";
}

function offersN(offers) {
  const o = Object.values(offers).find(x => x.rescore != null);
  return o ? o.rescore : null;
}
