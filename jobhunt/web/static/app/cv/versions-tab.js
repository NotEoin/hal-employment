/* The Versions tab: every version of the main CV, newest first. Tick two to compare;
   use any one again; move one to the bin, where it stays for 30 days. Versions never change, so using an
   old one only changes which one is in use. */
import { html, useState } from "../core/html.js";
import { get, post, del } from "../core/api.js";
import { useApi, toast, invalidate } from "../core/store.js";
import { dayWords, clock } from "../core/format.js";
import { Chip, Note } from "../ui/controls.js";
import { Icon } from "../ui/icons.js";

const SHOW = { Reworded: "Reworded", Added: "Added", Moved: "Moved", "Left out": "Left out", Hidden: "Hidden", Shown: "Shown" };

function words(v) {
  if (v == null || v === "") return "";
  return typeof v === "string" ? v : Array.isArray(v) ? v.map(x => (typeof x === "object" ? x.name : x)).join(", ") : String(v);
}

function Changes({ out }) {
  if (!out.changes.length) return html`<p class="small sub">Version ${out.a.n} and version ${out.b.n} are the same.</p>`;
  return html`<div class="cv-changes"><h3 class="h3">From version ${out.a.n} to version ${out.b.n}</h3>
    ${out.changes.map((c, i) => html`<div class="li" key=${i}><${Chip} tone=${c.kind === "Left out" ? "warn" : ""}>${SHOW[c.kind] || c.kind}<//>
      <div class="grow"><div class="small sub">${c.where}</div>${(words(c.before) || words(c.after)) && html`<div class="ba"><div class="b"><small>Before</small>${words(c.before)}</div>
      <div class="a"><small>After</small>${words(c.after)}</div></div>`}</div></div>`)}</div>`;
}

/** The tab. */
export function VersionsTab({ cv }) {
  const { data: versions, reload } = useApi("/cv/versions");
  const [pick, setPick] = useState([]);
  const [out, setOut] = useState(null);
  if (!versions) return html`<p class="sub">Loading your versions.</p>`;
  const flip = id => setPick(pick.includes(id) ? pick.filter(x => x !== id) : [...pick.slice(-1), id]);
  const byId = Object.fromEntries(versions.map(v => [v.id, v]));
  const inUse = versions.find(v => v.current);
  const act = async (fn, said) => { try { await fn(); invalidate("/cv*"); reload(); toast(said); } catch (err) { toast(err.message); } };
  const compare = async () => {
    const [a, b] = [...pick].sort((x, y) => byId[x].n - byId[y].n);
    try { setOut(await get("/cv/compare?a=" + encodeURIComponent(a) + "&b=" + encodeURIComponent(b))); } catch (err) { toast(err.message); }
  };
  const first = versions[versions.length - 1];
  return html`<div class="stack">
    ${cv && cv.not_found && cv.not_found.length > 0 && html`<${Note} icon="warn" tone="warn"><b>Not found in Hal's copy:</b> ${cv.not_found.join("; ")}<//>`}
    <div class="card"><div class="row" style="margin-bottom:6px"><p class="sub" style="margin:0;flex:1;min-width:200px">Every change makes a new version. Tick two to compare them.</p>
      <button type="button" class="btn small" disabled=${pick.length !== 2} onClick=${compare}>${pick.length === 2 ? "Compare " + pick.map(x => byId[x].n).join(" and ") : "Compare"}</button></div>
    ${versions.map(v => html`<div class="li" key=${v.id} style="align-items:center"><input type="checkbox" checked=${pick.includes(v.id)} aria-label=${"Pick version " + v.n} onChange=${() => flip(v.id)} />
      <div class="grow"><b>Version ${v.n}</b> ${v.current && html`<${Chip} tone="good">In use<//>`} ${v.baseline && html`<${Chip}><${Icon} n="lock" c="s" />${cv && cv.baseline ? "Your file" : "First version"}<//>`} ${v.binned && html`<${Chip} tone="warn">In the bin<//>`}
        <div class="small sub">${v.what}. ${dayWords(v.when)} ${clock(v.when)}.</div></div>
      ${v.binned ? html`<button type="button" class="btn small" onClick=${() => act(() => post("/cv/versions/" + encodeURIComponent(v.id) + "/restore"), "Version " + v.n + " is out of the bin")}>Take out of the bin</button>`
        : !v.current && html`<button type="button" class="btn small" onClick=${() => act(() => post("/cv/versions/" + encodeURIComponent(v.id) + "/use"), "Version " + v.n + " is now in use. Version " + (inUse ? inUse.n : "") + " is kept.")}>Use this version</button>
          ${v.id !== first.id && html`<button type="button" class="iconbtn" aria-label=${"Move version " + v.n + " to the bin"} onClick=${() => act(() => del("/cv/versions/" + encodeURIComponent(v.id)), "Version " + v.n + " is in the bin for 30 days")}><${Icon} n="trash" c="s" /></button>`}`}
    </div>`)}
    <p class="small sub" style="margin:12px 0 0">Per-job versions live with each application in Documents. Deleted versions stay in the bin for 30 days.</p></div>
    ${out && html`<div class="card"><${Changes} out=${out} /></div>`}</div>`;
}
