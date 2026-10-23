/* Where Hal looks: job sites, careers pages, watched companies and email alerts.

   Each row is a row of GET /sources with its health. A site Hal skips for want of a free key says so in
   plain words and offers the two-step "Get a free key" guide; Hal works without keys, so the guide is an
   offer, never a stop. A site that failed shows its error and "Search it again". Built-in sites can only
   be switched off; sites the person added can also be removed. */
import { html, cx, useState } from "../core/html.js";
import { put, del, post } from "../core/api.js";
import { useApi, openDialog, toast, invalidate } from "../core/store.js";
import { go } from "../core/router.js";
import { Icon } from "../ui/icons.js";
import { Switch, Chip, Note } from "../ui/controls.js";

const ICONS = { Email: "mail", Company: "briefcase" };

/** When a site was last searched, in words. */
function lastWords(at) {
  if (!at) return "Not searched yet";
  const d = new Date(at);
  if (isNaN(d)) return "Last searched " + at;
  const time = d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
  const today = new Date().toDateString() === d.toDateString();
  return "Last searched " + (today ? time : d.toLocaleDateString("en-GB", { day: "numeric", month: "short" }) + " " + time);
}

/** The two steps to get a free key, then the box to paste it. */
function KeySteps({ src, onClose }) {
  const fix = src.fix;
  return html`<div class="card soft" style="margin:8px 0 4px">
    <b>Get a free ${src.name} key</b>
    <ol class="small" style="margin:8px 0">${(fix.steps || []).map(s => html`<li>${s}</li>`)}</ol>
    <div class="row">
      ${fix.url && html`<a class="btn small" href=${fix.url} target="_blank" rel="noopener noreferrer"><${Icon} n="link" c="s" />Open ${src.name}'s page</a>`}
      <button type="button" class="btn small primary" onClick=${() => openDialog("secret", { name: fix.secret, label: "A free " + src.name + " key" })}>Paste the key</button>
      <button type="button" class="btn quiet small" onClick=${onClose}>Not now</button>
    </div></div>`;
}

async function searchNow() {
  try { await post("/sources/search-now"); toast("Hal is searching all sites now. Watch it in Activity."); }
  catch (err) { toast(err.message); }
}

/** One source row. */
function Row({ src, onChange }) {
  const [open, setOpen] = useState(false);
  const flip = async on => {
    try { await put("/sources/" + encodeURIComponent(src.id), { on }); onChange(); toast(on ? "Hal will search this site again" : "Hal will skip this site"); }
    catch (err) { toast(err.message); }
  };
  const remove = async () => {
    try { await del("/sources/" + encodeURIComponent(src.id)); onChange(); toast("Removed " + src.name); }
    catch (err) { toast(err.message); }
  };
  const fix = src.fix;
  const press = () => {
    if (fix.secret) setOpen(!open);
    else if (fix.action) searchNow();
    else if (fix.route) go(fix.route);
  };
  const seen = src.seen ? ". " + src.seen + " jobs seen, " + src.kept + " kept" : "";
  return html`<div class="li" style="align-items:center;flex-wrap:wrap">
      <div class=${cx("ic", src.ok ? "" : "warn")}><${Icon} n=${ICONS[src.kind_label] || "globe"} /></div>
      <div class="grow" style="min-width:0;flex:1 1 180px"><b>${src.name}</b> <${Chip}>${src.kind_label}<//>
        <div class="small sub">${src.area ? src.area + ". " : ""}${lastWords(src.last)}${seen}.</div>
        ${src.missed && html`<div class="small sub">${src.missed}</div>`}
        ${!src.missed && src.error && html`<div class="small sub">Last time it said: ${src.error}</div>`}</div>
      ${fix && html`<button type="button" class="btn small" aria-expanded=${fix.secret ? (open ? "true" : "false") : undefined} onClick=${press}>${fix.label}</button>`}
      ${src.removable && html`<button type="button" class="btn quiet small" aria-label=${"Remove " + src.name} onClick=${remove}><${Icon} n="x" c="s" /></button>`}
      <${Switch} on=${src.on} label=${"Search " + src.name} onChange=${flip} />
      ${open && fix && fix.secret && html`<div style="flex-basis:100%"><${KeySteps} src=${src} onClose=${() => setOpen(false)} /></div>`}
    </div>`;
}

/** How job alert emails reach Hal, opened from the note at the top. */
function AlertsHelp() {
  return html`<div class="small" style="margin-top:8px">
    <p style="margin:0 0 6px">Sign up for job alerts on sites such as RateMyPlacement, Bright Network, Prospects, TARGETjobs, Gradcracker,
      Milkround, CharityJob, Guardian Jobs, W4MP, Civil Service Jobs, jobs.ac.uk and Find an apprenticeship, using the email address
      Hal reads. Hal turns each job in an alert into a match, scored against your goal.</p>
    <p style="margin:0">Ask for alerts only for the kinds of role you chose, so your matches stay useful.</p></div>`;
}

/** The Where Hal looks tab of Jobs. */
export function Sources() {
  const { data, error } = useApi("/sources", { on: [ev => ev.type === "activity" || ev.type === "setting"] });
  const [help, setHelp] = useState(false);
  const reload = () => invalidate("/sources");
  if (error) return html`<div class="card empty">${error.message}</div>`;
  if (!data) return html`<div class="card empty">Loading the sites Hal searches...</div>`;
  const times = (data.times || []).join(" and ");
  return html`
    <${Note}><div>${times ? "Hal searches these at " + times + ". " : ""}Add a careers page, a company to watch, or have job alerts emailed to the address Hal reads.
      <button type="button" class="btn quiet small" aria-expanded=${help ? "true" : "false"} onClick=${() => setHelp(!help)}>How do job alert emails work?</button>
      ${help && html`<${AlertsHelp} />`}</div><//>
    ${data.missed && data.missed.length > 0 && html`<div style="margin-top:10px"><${Note} icon="warn" tone="warn"><div><b>Hal works without these keys.</b> ${data.missed.join(" ")} Each key is free; with one, Hal also searches that site.</div><//></div>`}
    <div class="row" style="margin:12px 0">
      <button type="button" class="btn primary" onClick=${() => openDialog("addsite")}><${Icon} n="plus" c="s" />Add a job site or careers page</button>
      <button type="button" class="btn" onClick=${() => openDialog("addsite", { company: true })}>Watch a company</button>
      <button type="button" class="btn quiet" onClick=${searchNow}><${Icon} n="refresh" c="s" />Search now</button>
    </div>
    <div class="card">${data.sources.map(src => html`<${Row} key=${src.id} src=${src} onChange=${reload} />`)}</div>`;
}
