/* Jobs: Matches, Saved, Hidden and Where Hal looks.

   Matches and Saved come from /jobs with the sort and filters as query words. "Show more jobs" asks for a
   longer list (`limit`) rather than keeping extra pages, so the whole list reloads when a job changes. The "Kind"
   row appears only when the goal holds more than one kind of role, so a person who wants only graduate jobs
   never sees it. Hidden lists each reason with its jobs and a way to change it: where one value would do
   (add a kind of role, show unpaid roles) the button changes it here and offers a re-check of every job.

   J and K move the choice down and up the list; A, Q, S and N act on the chosen job. */
import { html, cx, useState, useEffect, useRef, useLayoutEffect } from "../core/html.js";
import { get, post, put, run } from "../core/api.js";
import { go } from "../core/router.js";
import { useApi, openDialog, toast } from "../core/store.js";
import { useKeys } from "../core/keys.js";
import { plural } from "../core/format.js";
import { Icon } from "../ui/icons.js";
import { Head, Tabs, Note } from "../ui/controls.js";
import { JobCard, applyJob, queueJob, saveJob, notForMe, refreshJobs, jobPath } from "../ui/job-card.js";
import { Sources } from "../jobs/where.js";

const TABS = ["matches", "saved", "hidden", "where"];
const SORTS = [["best", "Best match"], ["closing", "Closing soon"], ["newest", "Newest"], ["pay", "Highest pay"]];
const PAGE = 50;   // jobs per page, as find/routes.py PAGE

function writeHash(tab) {
  try { history.replaceState(null, "", "#" + (tab === "matches" ? "jobs" : "jobs-" + tab)); }
  catch (err) { /* a sandboxed frame may refuse; the tab still changes */ }
}

function query(tab, sort, place, pattern, kind, limit) {
  const q = new URLSearchParams({ tab, sort });
  if (place) q.set("place", place);
  if (pattern) q.set("pattern", pattern);
  if (kind) q.set("kind", kind);
  if (limit > PAGE) q.set("limit", String(limit));
  return "/jobs?" + q.toString();
}

/** A row of one-choice filter buttons; pressing the chosen one again clears it. */
function Picks({ label, options, value, onChange }) {
  return html`<div class="picks" role="group" aria-label=${label}>${options.map(([v, words]) => html`<button type="button"
    class=${cx("pick", value === v && "on")} aria-pressed=${value === v ? "true" : "false"}
    onClick=${() => onChange(value === v ? "" : v)}>${words}</button>`)}</div>`;
}

/** Matches or Saved, with the sort, the filters and the J K A Q S N keys. */
function JobList({ tab }) {
  const [sort, setSort] = useState("best");
  const [where, setWhere] = useState({ place: "", pattern: "" });
  const [kind, setKind] = useState("");
  const [limit, setLimit] = useState(PAGE);
  const [sel, setSel] = useState(-1);
  const filters = query(tab, sort, where.place, where.pattern, kind);
  const path = query(tab, sort, where.place, where.pattern, kind, limit);
  const fresh = useApi(path, { on: [ev => ev.type === "job" || ev.type === "setting"] });
  // a longer list keeps the shorter one on screen while it loads, so the place on the page is kept
  const shown = useRef(null);
  if (fresh.data) shown.current = { filters, data: fresh.data };
  const data = fresh.data || (shown.current && shown.current.filters === filters ? shown.current.data : undefined);
  const error = fresh.error;
  const list = data ? data.jobs : [];
  const cursor = data && data.cursor;
  // the keys read the newest list and choice through a ref, so two quick presses never use a stale one
  const live = useRef({ list: [], sel: -1 });
  live.current.list = list;
  // a layout effect: Preact runs a plain one up to a frame late, where it can undo the person's first action
  useLayoutEffect(() => { setLimit(PAGE); setSel(-1); live.current.sel = -1; }, [filters]);
  const pick = delta => {
    const l = live.current;
    if (!l.list.length) return;
    const n = Math.max(0, Math.min(l.list.length - 1, l.sel + delta));
    l.sel = n;
    setSel(n);
    const el = document.querySelectorAll(".joblist .job .title")[n];
    if (el) { el.focus(); el.scrollIntoView({ block: "nearest" }); }
  };
  // a job that left the list (queued, saved, hidden) can leave the choice past its end: keep it inside
  const act = fn => { const l = live.current; const j = l.list[Math.min(Math.max(l.sel, 0), l.list.length - 1)]; if (j) fn(j); };
  useKeys({ j: () => pick(1), k: () => pick(-1), a: () => act(applyJob), q: () => act(queueJob), s: () => act(saveJob), n: () => act(notForMe) }, []);
  const loadMore = () => setLimit(list.length + PAGE);
  if (error) return html`<div class="card empty">${error.message}</div>`;
  if (!data) return html`<div class="card empty">Loading jobs...</div>`;
  const f = data.filters || {};
  const placeOpts = [...(f.places || []).map(p => ["place:" + p, p]), ["place:Remote", "Remote"],
    ...(f.patterns || []).filter(p => p !== "Remote").map(p => ["pattern:" + p, p])];
  const placeValue = where.place ? "place:" + where.place : where.pattern ? "pattern:" + where.pattern : "";
  const setPlace = v => setWhere(v.startsWith("place:") ? { place: v.slice(6), pattern: "" }
    : v.startsWith("pattern:") ? { place: "", pattern: v.slice(8) } : { place: "", pattern: "" });
  return html`
    <div class="toolbar">
      <label class="small sub" for="jobs-sort">Sort</label>
      <select id="jobs-sort" class="input" style="width:auto" value=${sort} onChange=${e => setSort(e.target.value)}>
        ${SORTS.map(([v, w]) => html`<option value=${v}>${w}</option>`)}</select>
      <span class="sp"></span>
      <${Picks} label="Where" options=${placeOpts} value=${placeValue} onChange=${setPlace} />
    </div>
    ${f.kinds && f.kinds.length > 0 && html`<div class="toolbar kinds">
      <span class="small sub" id="jobs-kind">Kind</span>
      <${Picks} label="Kind of role" options=${[["", "All kinds"], ...f.kinds.map(k => [k.id, k.label + " (" + k.n + ")"])]}
        value=${kind} onChange=${setKind} />
    </div>`}
    <div class="stack s joblist">${list.length ? list.map((j, i) => html`<${JobCard} j=${j} sel=${i === sel} />`)
      : html`<div class="card empty">${tab === "saved" ? "Nothing saved yet. Press Save on a job to keep it here."
        : "No matches for these filters."}</div>`}</div>
    ${cursor && html`<div class="row" style="margin-top:12px"><button type="button" class="btn" onClick=${loadMore}>Show more jobs</button></div>`}`;
}

/** One Hidden group: its reason, how many, examples, "Show them" and the way to change it. */
function Group({ g, onChanged }) {
  const [open, setOpen] = useState(false);
  // read through the cache while open, so a job put back or applied for leaves the list at once
  const jobs = useApi(open ? "/jobs/hidden/" + encodeURIComponent(g.rule) : null);
  useEffect(() => { if (jobs.error && open) { toast(jobs.error.message); setOpen(false); } }, [jobs.error]);
  const shown = open && jobs.data ? jobs.data.jobs : null;
  const show = () => setOpen(!open);
  const fix = async () => {
    if (!g.fix.set) { go(g.fix.route); return; }
    try {
      await put("/settings/" + encodeURIComponent(g.fix.set.key), { value: g.fix.set.value });
      onChanged(g.fix.label);
    } catch (err) { toast(err.message); }
  };
  return html`<div class="li hidden-group">
    <div class="ic"><${Icon} n="eye" /></div>
    <div class="grow"><b>${g.reason}</b>${g.why && g.why !== g.reason && html`<div class="small">${g.why}</div>`}
      <div class="small sub">${plural(g.n, "job")}, for example: ${g.examples.join("; ")}</div>
      ${shown && html`<div class="stack s" style="margin-top:12px">${shown.map(j => html`<${JobCard} j=${j} compact why=${j.why} />`)}</div>`}
    </div>
    <div class="row tight">
      <button type="button" class="btn small" aria-expanded=${open ? "true" : "false"} onClick=${show}>${open ? "Hide them" : "Show them"}</button>
      ${g.fix && html`<button type="button" class="btn quiet small" onClick=${fix}>${g.fix.label}</button>`}
    </div>
  </div>`;
}

/** Hidden: the groups, then the jobs the person hid themselves. */
function Hidden() {
  const { data, error, reload } = useApi("/jobs/hidden", { on: [ev => ev.type === "job" || ev.type === "setting"] });
  const [changed, setChanged] = useState("");
  const [count, setCount] = useState(null);
  const onChanged = async label => {
    setChanged(label);
    try { setCount((await get("/jobs/rescore/count")).n); } catch (err) { setCount(null); }
  };
  const recheck = async () => {
    setChanged("");
    toast("Hal is re-checking your jobs. Watch it in Activity.");
    try { await run("/jobs/rescore", { setting: "your goal" }); refreshJobs(); reload(); toast("Jobs re-checked"); }
    catch (err) { toast(err.message); }
  };
  const putBack = async j => {
    try { await post(jobPath(j.id) + "/put-back"); refreshJobs(); toast("Back in your matches"); }
    catch (err) { toast(err.message); }
  };
  if (error) return html`<div class="card empty">${error.message}</div>`;
  if (!data) return html`<div class="card empty">Loading...</div>`;
  const empty = !data.groups.length && !data.yours.length;
  return html`
    <div style="margin-bottom:16px"><${Note}>Hal hid these because of your goal. Open a group to see the jobs, or change the setting that hid them.<//></div>
    ${changed && html`<div style="margin-bottom:16px"><${Note} icon="check">Changed: ${changed}. Scores change when Hal
      re-checks your jobs${count != null ? " (" + plural(count, "job") + ")" : ""}.
      <button type="button" class="btn small" style="margin-left:8px" onClick=${recheck}>Re-check jobs</button><//></div>`}
    <div class="card">${empty ? html`<div class="empty">Nothing is hidden.</div>` : ""}
      ${data.groups.map(g => html`<${Group} key=${g.rule} g=${g} onChanged=${onChanged} />`)}
      ${data.yours.map(j => html`<div class="li"><div class="ic"><${Icon} n="eye" /></div><div class="grow"><b>${j.title}</b>
        <div class="small sub">${j.org}. ${j.why}</div></div>
        <button type="button" class="btn small" onClick=${() => putBack(j)}>Put it back</button></div>`)}
    </div>`;
}

/** The Jobs page; `arg` is the tab from the route ("jobs-saved"). */
export function Jobs({ arg }) {
  const [t, setT] = useState(TABS.includes(arg) ? arg : "matches");
  useEffect(() => { if (TABS.includes(arg)) setT(arg); else if (!arg) setT("matches"); }, [arg]);
  const { data } = useApi(query("matches", "best"), { on: [ev => ev.type === "job"] });
  const counts = (data && data.counts) || {};
  const change = tab => { setT(tab); writeHash(tab); };
  return html`
    <${Head} title="Jobs" sub="Hal ranks jobs against your goal and shows why. Nothing is ever hidden without a reason you can see.">
      <button type="button" class="btn" onClick=${() => openDialog("addsite")}><${Icon} n="plus" c="s" />Add a job site</button>
      <button type="button" class="btn" onClick=${() => openDialog("addjob")}><${Icon} n="link" c="s" />Add one advert</button>
    <//>
    <${Tabs} label="Jobs" value=${t} onChange=${change} tabs=${[["matches", "Matches", counts.matches], ["saved", "Saved", counts.saved],
      ["hidden", "Hidden", counts.hidden], ["where", "Where Hal looks", counts.sources_on]]} />
    ${(t === "matches" || t === "saved") && html`<${JobList} key=${t} tab=${t} />`}
    ${t === "hidden" && html`<${Hidden} />`}
    ${t === "where" && html`<${Sources} />`}`;
}
