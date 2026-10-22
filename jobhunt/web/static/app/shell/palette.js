/* Search or jump (Ctrl K): pages answer at once, and /search adds jobs, applications, settings and
   actions as the person types. Up and Down move the choice, Enter takes it. */
import { html, useState, useEffect } from "../core/html.js";
import { closeDialog, openDialog, openSetup, toast } from "../core/store.js";
import { get, post } from "../core/api.js";
import { go } from "../core/router.js";
import { Icon } from "../ui/icons.js";
import { NAV, SETTINGS_ITEM } from "./rail.js";

const SEARCH_DELAY_MS = 150;  // waits for a pause in typing so each key press is not a request
const MAX_ITEMS = 9;
const KIND_ICON = { Page: "chevR", Job: "briefcase", Application: "columns", Setting: "sliders", Settings: "sliders", Action: "play" };

const PAGES = [...NAV.flatMap(([, items]) => items), SETTINGS_ITEM].map(([r, label]) => ({ label, kind: "Page", route: r }));
const LOCAL_ACTIONS = [{ label: "Open the setup guide", kind: "Action", action: "setup" }];

/** Does what a palette item says: go to its route, open its dialog or start its action. */
export async function choose(item) {
  if (item.route) { go(item.route); return; }
  const a = item.action || "";
  if (a === "setup") { closeDialog(); openSetup(0); return; }
  if (a.startsWith("dialog:")) { openDialog(a.slice("dialog:".length)); return; }
  closeDialog();
  try { await post(a); toast(item.label + ": started"); } catch (err) { toast(err.message); }
}

/** The palette's contents, drawn inside the dialog host. */
export function Palette() {
  const [q, setQ] = useState("");
  const [found, setFound] = useState([]);
  const [at, setAt] = useState(0);
  useEffect(() => {
    const text = q.trim();
    if (!text) { setFound([]); return undefined; }
    let live = true;
    const t = setTimeout(() => {
      get("/search?q=" + encodeURIComponent(text)).then(r => { if (live) setFound(Array.isArray(r) ? r : []); },
                                                        () => { if (live) setFound([]); });
    }, SEARCH_DELAY_MS);
    return () => { live = false; clearTimeout(t); };
  }, [q]);
  const needle = q.trim().toLowerCase();
  const local = [...PAGES, ...LOCAL_ACTIONS].filter(i => i.label.toLowerCase().includes(needle));
  const seen = new Set(local.map(i => i.label));
  const list = [...local, ...found.filter(i => !seen.has(i.label))].slice(0, MAX_ITEMS);
  const pick = Math.min(at, Math.max(0, list.length - 1));
  const onKey = e => {
    if (e.key === "ArrowDown") { e.preventDefault(); setAt((pick + 1) % Math.max(1, list.length)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setAt((pick - 1 + list.length) % Math.max(1, list.length)); }
    else if (e.key === "Enter" && list[pick]) { e.preventDefault(); choose(list[pick]); }
  };
  return html`<input class="input" placeholder="Search jobs, pages, settings or actions" aria-label="Search or jump"
      value=${q} onInput=${e => { setQ(e.target.value); setAt(0); }} onKeyDown=${onKey} />
    <ul aria-label="Results">${list.map((i, k) => html`<li key=${i.kind + i.label}><button type="button" class=${k === pick ? "on" : ""}
      onClick=${() => choose(i)}><${Icon} n=${KIND_ICON[i.kind] || "chevR"} c="s" />${i.label} <small>${i.kind}</small></button></li>`)}
      ${!list.length && html`<li class="empty">Nothing matches.</li>`}</ul>`;
}
Palette.label = "Search or jump";
