/* The rail: the app's main menu, in groups, with counts.

   The simple menu shows SIMPLE_MAIN and puts the rest under More. */
import { html, cx, useState, useEffect, useRef } from "../core/html.js";
import { useStore, openDialog } from "../core/store.js";
import { go, railPage } from "../core/router.js";
import { Icon } from "../ui/icons.js";

export const NAV = [
  [null, [["home", "Home", "home"], ["jobs", "Jobs", "briefcase"], ["apps", "Applications", "columns"], ["inbox", "Messages", "inbox"]]],
  ["Your materials", [["cv", "My CV", "file"], ["docs", "Documents", "folder"], ["record", "Your record", "book"]]],
  ["Hal", [["activity", "Activity", "pulse"], ["memory", "What Hal remembers", "bulb"]]],
];
export const SETTINGS_ITEM = ["settings", "Settings", "sliders"];
export const SIMPLE_MAIN = ["home", "jobs", "apps", "cv", "docs"];
const HOT = new Set(["apps", "inbox"]);  // counts that mean "this needs you" are red, the rest grey
const NARROW = "(max-width: 860px)";     // where app.css turns the rail into a slide-in menu

function useNarrow() {
  const mq = typeof matchMedia === "function" ? matchMedia(NARROW) : null;
  const [narrow, setNarrow] = useState(!!(mq && mq.matches));
  useEffect(() => {
    if (!mq) return undefined;
    const f = () => setNarrow(mq.matches);
    mq.addEventListener("change", f);
    return () => mq.removeEventListener("change", f);
  }, []);
  return narrow;
}

function initials(name) {
  return String(name || "").split(/\s+/).filter(Boolean).slice(0, 2).map(w => w[0].toUpperCase()).join("");
}

/** The left-hand menu. */
export function Rail() {
  const s = useStore(st => ({ route: st.route, railOpen: st.railOpen, simpleNav: st.simpleNav, counts: st.counts,
                              session: st.session, busy: !!(st.status && st.status.busy) }));
  const [more, setMore] = useState(false);
  const narrow = useNarrow();
  const ref = useRef(null);
  useEffect(() => {
    if (narrow && s.railOpen && ref.current) { const b = ref.current.querySelector(".nav"); if (b) b.focus(); }
  }, [narrow, s.railOpen]);

  const top = railPage(s.route);
  const item = ([r, label, icon]) => {
    const n = (s.counts || {})[r] || 0;
    return html`<button type="button" class=${cx("nav", top === r && "on")} aria-current=${top === r ? "page" : null}
      onClick=${() => go(r)}><${Icon} n=${icon} />${label}${n > 0 && html`<span class=${cx("count", HOT.has(r) && "hot")}>${n}</span>`}</button>`;
  };
  const all = NAV.flatMap(([, items]) => items);
  const rest = [...all.filter(i => !SIMPLE_MAIN.includes(i[0])), SETTINGS_ITEM];
  const restCount = rest.reduce((t, i) => t + ((s.counts || {})[i[0]] || 0), 0);
  const me = s.session && s.session.first_name;

  // off screen on a narrow window, the closed menu is inert so Tab does not wander into it
  return html`<nav class=${cx("rail", s.railOpen && "open")} aria-label="Main" ref=${ref}
    inert=${narrow && !s.railOpen ? true : undefined}>
    <div class="brand"><span class=${cx("eye", s.busy && "busy")} aria-hidden="true"></span><div>Hal Employment<small>Your job search assistant</small></div></div>
    <button type="button" class="jump" onClick=${() => openDialog("palette")}><${Icon} n="search" c="s" />Search or jump<kbd>Ctrl K</kbd></button>
    ${s.simpleNav
      ? html`${all.filter(i => SIMPLE_MAIN.includes(i[0])).map(item)}
        <button type="button" class="nav" aria-expanded=${more ? "true" : "false"} onClick=${() => setMore(!more)}><${Icon}
          n=${more ? "chevU" : "chevD"} />More${!more && restCount > 0 && html`<span class="count hot">${restCount}</span>`}</button>
        ${more && rest.map(item)}`
      : NAV.map(([g, items]) => html`${g && html`<div class="navgroup">${g}</div>`}${items.map(item)}`)}
    <div class="railfoot">
      ${!s.simpleNav && item(SETTINGS_ITEM)}
      <button type="button" class="nav" onClick=${() => openDialog("keys")}><${Icon} n="help" />Help and shortcuts</button>
      ${s.session && s.session.remote && html`<div class="rchip"><${Icon} n="globe" c="s" />On ${s.session.remote_host || "the computer Hal runs on"}, from this computer</div>`}
      ${me && html`<div class="me"><span class="avatar" aria-hidden="true">${initials(me)}</span><div>${me}</div></div>`}
    </div>
  </nav>`;
}
