/* What Hal remembers, Notes about job sites.

   One card per site from GET /sites: its application system, whether it needs an account, when Hal was last
   there, and its notes. Every note says where it came from and how often it worked; each can be switched
   off, reworded and shared, and the person's own can be deleted (built-in notes only switch off). A note Hal
   learned waits for "Use this". Notes that disagree are named on the card. The first card starts open. */
import { html, useState } from "../core/html.js";
import { put, del, get } from "../core/api.js";
import { useApi, mutate, openDialog, toast } from "../core/store.js";
import { Switch, Chip, Empty } from "../ui/controls.js";
import { ask } from "../ui/dialog.js";
import { Icon } from "../ui/icons.js";

const DAY = new Intl.DateTimeFormat("en-GB", { day: "numeric", month: "short" });
const DAY_MS = 86400000;

/** "Today", "Yesterday" or "20 Oct" for an ISO time or day; "Not yet" when there is none. */
export function seenWords(iso) {
  if (!iso) return "Not yet";
  const d = new Date(iso.length === 10 ? iso + "T12:00:00Z" : iso);
  const days = Math.round((new Date().setHours(0, 0, 0, 0) - new Date(d).setHours(0, 0, 0, 0)) / DAY_MS);
  return days <= 0 ? "Today" : days === 1 ? "Yesterday" : DAY.format(d);
}

/** Puts words on the clipboard and says so. */
async function copy(text, said) {
  try { await navigator.clipboard.writeText(text); toast(said); }
  catch (err) { toast("Hal could not reach the clipboard. Select the words and copy them yourself."); }
}

function tone(n) {
  return n.origin === "you" ? "info" : n.origin === "builtin" ? "" : "why";
}

function NoteRow({ n, reload }) {
  const [editing, setEditing] = useState(false);
  const [text, setText] = useState(n.text);
  const [problem, setProblem] = useState("");
  const id = encodeURIComponent(n.id);
  const replace = changed => mutate("/sites", cards => (cards || []).map(c => ({ ...c, notes: c.notes.map(x => (x.id === n.id ? changed : x)) })));
  const flip = async on => {
    replace({ ...n, on });
    try { replace(await put("/sites/notes/" + id, { on })); toast(on ? "Hal will use this note again" : "Hal will not use this note"); }
    catch (err) { toast(err.message); reload(); }
  };
  const accept = async () => {
    try { replace(await put("/sites/notes/" + id, { accept: true })); toast("Hal will use this note on this site"); }
    catch (err) { toast(err.message); }
  };
  const save = async () => {
    try { replace(await put("/sites/notes/" + id, { text })); setEditing(false); setProblem(""); toast("Saved the new wording"); }
    catch (err) { setProblem(err.message); }
  };
  const share = async () => {
    try { const r = await get("/sites/notes/" + id + "/share"); copy(r.text, "Copied. Send it to whoever looks after Hal."); }
    catch (err) { toast(err.message); }
  };
  const remove = async () => {
    if (!(await ask({ title: "Delete this note?", text: n.text, yes: "Delete", no: "Keep it", danger: true }))) return;
    try { await del("/sites/notes/" + id); reload(); toast("Deleted the note"); }
    catch (err) { toast(err.message); }
  };
  const counts = "Used " + n.used + (n.used === 1 ? " time" : " times") + ", worked " + n.worked + "." + (n.when ? " Added " + seenWords(n.when) + "." : "");
  return html`<div class="li" style="align-items:center" data-note=${n.id}><div class="grow" style="min-width:0">
      ${editing ? html`<label class="small" for=${"note-" + n.id}><b>The note in your words</b></label>
        <textarea id=${"note-" + n.id} class="input" style="margin-top:6px" value=${text} onInput=${e => setText(e.target.value)}
          aria-invalid=${problem ? "true" : "false"}></textarea>
        ${problem && html`<p class="small bad-text" role="alert">${problem}</p>`}
        <div class="row tight" style="margin-top:6px"><button type="button" class="btn small primary" disabled=${!text.trim()} onClick=${save}>Save</button>
          <button type="button" class="btn small quiet" onClick=${() => { setEditing(false); setText(n.text); setProblem(""); }}>Cancel</button></div>`
      : html`<b style="font-weight:500">${n.text}</b>`}
      <div class="row tight" style="margin-top:4px"><${Chip} tone=${tone(n)}>${n.origin_label}</${Chip}>
        ${n.may_be_out_of_date && html`<${Chip} tone="warn">May be out of date</${Chip}>`}
        ${!n.built_in && n.where && html`<span class="small sub">For ${n.where}.</span>`}
        <span class="small sub">${counts}</span></div></div>
    ${n.proposed ? html`<button type="button" class="btn small good" onClick=${accept}>Use this</button>`
      : html`<${Switch} on=${n.on} label=${"Use: " + n.text} onChange=${flip} />`}
    <button type="button" class="iconbtn" aria-label="Edit" title="Change the wording" onClick=${() => setEditing(true)}><${Icon} n="edit" c="s" /></button>
    ${n.can_share && html`<button type="button" class="iconbtn" aria-label="Share this note"
      title="Copy this note to send to whoever looks after Hal, so it can be built in for everyone" onClick=${share}><${Icon} n="copy" c="s" /></button>`}
    ${!n.built_in && html`<button type="button" class="iconbtn" aria-label="Delete" onClick=${remove}><${Icon} n="trash" c="s" /></button>`}
  </div>`;
}

function SiteCard({ site, open, onToggle, reload }) {
  const count = site.notes.length === 1 ? "1 note" : site.notes.length + " notes";
  const visited = site.key.startsWith("ats:") || site.key === "global" ? "" : "Last visit: " + seenWords(site.seen);
  const sub = [site.account, count, visited].filter(Boolean).join(". ") + ".";
  const reportable = site.key !== "global" && !site.key.startsWith("ats:");
  return html`<section class="card" data-site=${site.key}>
    <button type="button" class="row" style="width:100%;background:none;border:0;padding:0;cursor:pointer;color:inherit;text-align:left"
      aria-expanded=${open ? "true" : "false"} onClick=${onToggle}><div class="ic"><${Icon} n="globe" /></div>
      <div style="flex:1;min-width:0"><b>${site.name}</b> <span class="chip">${site.platform}</span><div class="small sub">${sub}</div></div>
      <${Icon} n=${open ? "chevU" : "chevD"} /></button>
    ${open && html`<div style="margin-top:10px">
      ${site.conflicts.map(c => html`<div class="note" style="margin-bottom:8px"><${Icon} n="warn" /><div>${c.words}</div></div>`)}
      ${site.notes.map(n => html`<${NoteRow} key=${n.id + n.text} n=${n} reload=${reload} />`)}
      ${reportable && html`<button type="button" class="btn small" style="margin-top:10px"
        onClick=${() => openDialog("report", { site: site.key, platform: site.ats ? site.platform : "" })}><${Icon} n="flag" c="s" />Something wrong on this site?</button>`}
    </div>`}</section>`;
}

/** The tab: what the notes are, then one card per site. */
export function SitesTab() {
  const { data, error, loading, reload } = useApi("/sites", { on: [ev => ev.type === "activity"] });
  const [openKey, setOpenKey] = useState(null);
  const cards = data || [];
  const current = openKey === null ? (cards[0] && cards[0].key) : openKey;
  return html`<div class="stack s">
    <div class="note"><${Icon} n="info" /><div>Hal keeps notes for each job site: what works there and what to avoid. Some notes cover
      every employer that uses the same application system, such as Workday. Built-in notes come with Hal; you can switch any of
      them off. <button type="button" class="btn quiet small" onClick=${() => openDialog("report", { site: "", platform: "" })}>Tell Hal about a problem</button></div></div>
    ${error && html`<p class="small bad-text" role="alert">${error.message}</p>`}
    ${loading && !data && html`<p class="small sub" role="status">Loading the notes...</p>`}
    ${data && !cards.length && html`<${Empty}>No notes yet.</${Empty}>`}
    ${cards.map(site => html`<${SiteCard} key=${site.key} site=${site} open=${current === site.key}
      onToggle=${() => setOpenKey(current === site.key ? "" : site.key)} reload=${reload} />`)}
  </div>`;
}
