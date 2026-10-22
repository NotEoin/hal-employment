/* Home: what needs you today, most urgent first, new matches, this week, your goal and what Hal did today.
   Every part comes from /home, which each area feeds; nothing here is made up. */
import { html } from "../core/html.js";
import { useApi, toast, openSetup } from "../core/store.js";
import { post } from "../core/api.js";
import { go } from "../core/router.js";
import { Icon } from "../ui/icons.js";
import { Head, Empty, Bar } from "../ui/controls.js";
import { JobCard } from "../ui/job-card.js";
import { WhyButton } from "../ui/why.js";

const PIPE_COLOURS = { ready: "var(--primary)", form: "var(--warn)", sent: "var(--why)", interview: "var(--good)",
  offer: "var(--good)", closed: "var(--line-strong)" };

async function doAct(act) {
  if (!act) return;
  if (act.route) { go(act.route); return; }
  if (act.url) { window.open(act.url, "_blank", "noopener"); return; }
  if (act.action) {
    try { await post(act.action); toast("Hal is on it."); } catch (err) { toast(err.message); }
  }
}

function Needs({ needs }) {
  return html`<section class="card" aria-labelledby="needs-h">
    <div class="row"><h2 class="h2" id="needs-h" style="margin:0">Needs you</h2><span class="sub small">Most urgent first</span></div>
    ${needs.length === 0 ? html`<${Empty}>Nothing needs you right now.<//>` : needs.map(n => html`<div class="li home-need">
      <div class=${"ic " + (n.tone || "")}><${Icon} n=${n.icon} /></div><div class="grow"><b>${n.title}</b>
      ${n.detail && html`<div class="sub small">${n.detail}</div>`}</div>${n.due && html`<span class="chip">${n.due}</span>`}
      ${n.act && html`<button type="button" class="btn small" onClick=${() => doAct(n.act)}>${n.act.label}</button>`}</div>`)}
  </section>`;
}

function NewMatches({ jobs, first }) {
  return html`<section aria-labelledby="new-h">
    <div class="row" style="margin-bottom:10px"><h2 class="h2" id="new-h" style="margin:0">New matches</h2><span class="sp"></span>
      <button type="button" class="btn quiet small" onClick=${() => go("jobs")}>All matches <${Icon} n="chevR" c="s" /></button></div>
    ${first ? html`<div class="card"><p class="small sub" style="margin:0 0 8px">Checked ${first.checked} of ${first.total} jobs</p>
        <${Bar} pct=${first.total ? (100 * first.checked) / first.total : 0} label="Jobs checked so far" /></div>`
      : html`<div class="stack s">${jobs.length ? jobs.map(j => html`<${JobCard} j=${j} compact=${true} />`)
        : html`<div class="card empty">No new matches since you last looked.</div>`}</div>`}
  </section>`;
}

function Week({ w }) {
  const pipe = (w.pipeline || []).filter(p => p.n > 0);
  const total = pipe.reduce((t, p) => t + p.n, 0);
  const words = pipe.map(p => p.n + " " + p.stage.toLowerCase()).join(", ");
  return html`<section class="card" aria-labelledby="week-h">
    <h2 class="h2" id="week-h">This week</h2>
    <div class="stats">
      <div class="stat"><b>${w.matches}</b><span>new matches</span></div>
      <div class="stat"><b>${w.sent}</b><span>applications sent</span></div>
      <div class="stat"><b>${w.replies}</b><span>${w.replies === 1 ? "reply" : "replies"}</span></div>
      <div class="stat"><b>${w.interviews}</b><span>${w.interviews === 1 ? "interview" : "interviews"}</span></div>
    </div>
    ${total > 0 && html`<p class="small sub" style="margin:14px 0 6px">Where your ${total} applications are</p>
      <div class="pipe" role="img" aria-label=${words}>${pipe.map(p => html`<i style=${"width:" + (100 * p.n / total) + "%;background:"
        + PIPE_COLOURS[p.key]}></i>`)}</div>
      <div class="row small sub" style="margin-top:8px;gap:6px 16px">${pipe.map(p => html`<span><span class="dot" style=${"background:"
        + PIPE_COLOURS[p.key]}></span> ${p.stage} ${p.n}</span>`)}</div>`}
  </section>`;
}

/** The goal card: what Hal is looking for, with Edit and History. */
export function GoalCard({ goal }) {
  const g = goal || {};
  return html`<section class="card whyc" aria-labelledby="goal-h">
    <div class="row"><h2 class="h2" id="goal-h" style="margin:0;color:var(--why)">Your goal</h2><span class="sp"></span>
      ${g.mode && html`<span class="chip line">${g.mode}</span>`}</div>
    <dl class="kv" style="margin-top:12px">${(g.rows || []).map(([k, v]) => html`<dt>${k}</dt><dd>${v}</dd>`)}</dl>
    <div class="row" style="margin-top:14px"><button type="button" class="btn small" onClick=${() => go("settings-goal")}><${Icon}
      n="edit" c="s" />Edit your goal</button><button type="button" class="btn quiet small" onClick=${() => toast("Version "
      + (g.version || 1) + " of your goal. Earlier versions can be restored in Settings.")}>History</button></div>
  </section>`;
}

function Setup({ s }) {
  if (!s || !s.todo || s.todo.length === 0) return null;
  return html`<section class="card" aria-labelledby="setup-h"><h2 class="h2" id="setup-h">Finish setting up</h2>
    <p class="small sub">${s.done} of ${s.total} steps done. The rest are optional.</p>
    <div style="margin:8px 0 12px"><${Bar} pct=${s.total ? (100 * s.done) / s.total : 0} label="Setup steps done" /></div>
    ${s.todo.map(t => html`<div class="li"><div class="ic"><${Icon} n="check" /></div><div class="grow"><b>${t.title}</b>
      <div class="small sub">${t.detail}</div></div><button type="button" class="btn small" onClick=${() => openSetup(t.step)}>Set up</button></div>`)}
  </section>`;
}

function Today({ items }) {
  return html`<section class="card" aria-labelledby="today-h">
    <div class="row"><h2 class="h2" id="today-h" style="margin:0">What Hal did today</h2><span class="sp"></span>
      <button type="button" class="btn quiet small" onClick=${() => go("activity")}>All activity</button></div>
    ${items.length === 0 ? html`<${Empty}>Nothing yet today.<//>` : items.map(a => html`<div class="li"><span class="mono small sub"
      style="padding-top:2px">${a.t}</span><div class="grow small">${a.text}</div><${WhyButton} item=${a} /></div>`)}
  </section>`;
}

/** The Home page. */
export function Home() {
  const home = useApi("/home");
  if (home.error) return html`<${Head} title="Home" /><${Empty}>${home.error.message}<//>`;
  const h = home.data;
  if (!h) return html`<${Head} title="Home" sub="Loading" />`;
  return html`
    <${Head} eyebrow=${h.date_words} title=${h.greeting} sub=${h.summary}>
      <button type="button" class="btn" onClick=${() => go("jobs")}><${Icon} n="briefcase" c="s" />See new matches</button>
    <//>
    <div class="grid2">
      <div class="stack">
        <${Needs} needs=${h.needs || []} />
        <${NewMatches} jobs=${h.new_matches || []} first=${h.first_search} />
        ${h.week && html`<${Week} w=${h.week} />`}
      </div>
      <div class="stack">
        ${h.goal && html`<${GoalCard} goal=${h.goal} />`}
        <${Setup} s=${h.setup} />
        <${Today} items=${h.today || []} />
      </div>
    </div>`;
}
