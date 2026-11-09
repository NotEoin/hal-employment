/* The app shell: rail, status line, the page for the current route, the setup guide, dialogs and
   toasts.

   Pages load on first visit. On every route change the window title follows the page, focus moves to
   the page's h1 so a screen reader announces it, and the page scrolls to the top. */
import { html, cx, useEffect, useState, useErrorBoundary } from "../core/html.js";
import { useStore, store, openDialog } from "../core/store.js";
import { parse, loadPage, titleFor, wideRoute, ROUTES } from "../core/router.js";
import { installKeys } from "../core/keys.js";
import { Icon } from "../ui/icons.js";
import { DialogHost } from "../ui/dialog.js";
import { Toasts } from "../ui/toasts.js";
import { Rail } from "./rail.js";
import { Status } from "./status.js";

function focusHeading() {
  if (store.s.dialog || store.s.setup.open) return;  // a dialog or the guide keeps its focus
  const h = document.querySelector("#main h1");
  if (!h) return;
  if (!h.hasAttribute("tabindex")) h.setAttribute("tabindex", "-1");
  h.focus({ preventScroll: true });
}

function PageError({ error }) {
  console.error(error);
  return html`<div class="phead"><div><h1 class="h1" tabindex="-1">This page did not load</h1>
    <p class="sub">Reload the window to try again.</p></div></div>
    <button type="button" class="btn" onClick=${() => location.reload()}><${Icon} n="refresh" c="s" />Reload</button>`;
}

function Guarded({ Comp, arg }) {
  const [error] = useErrorBoundary();
  if (error) return html`<${PageError} error=${error} />`;
  return html`<${Comp} arg=${arg} />`;
}

/** The page for the current route, loaded on first visit. */
export function PageFrame({ route }) {
  const { page, arg } = parse(route);
  const [mod, setMod] = useState(null);
  const [failed, setFailed] = useState(null);
  useEffect(() => {
    let live = true;
    setFailed(null);
    loadPage(page).then(m => { if (live) setMod({ page, m }); }, err => { if (live) setFailed(err); });
    return () => { live = false; };
  }, [page]);
  const ready = mod && mod.page === page;
  useEffect(() => { if (ready) focusHeading(); }, [route, ready]);
  if (failed) return html`<${PageError} error=${failed} />`;
  if (!ready) return html`<p class="loading">Loading...</p>`;
  const Comp = mod.m[ROUTES[page].name] || mod.m.default;
  if (!Comp) return html`<${PageError} error=${new Error(page + " has no " + ROUTES[page].name)} />`;
  return html`<${Guarded} Comp=${Comp} arg=${arg} key=${route} />`;
}

function SetupHost() {
  const [Setup, setSetup] = useState(null);
  useEffect(() => { import("../setup/setup.js").then(m => setSetup(() => m.Setup), err => console.error(err)); }, []);
  return Setup ? html`<${Setup} />` : null;
}

/** The whole app. */
export function App() {
  const s = useStore(st => ({ route: st.route, railOpen: st.railOpen, setupOpen: st.setup.open, needs: st.needs,
                              busy: !!(st.status && st.status.busy) }));
  useEffect(() => installKeys(), []);
  useEffect(() => { document.title = titleFor(s.setupOpen ? "setup" : s.route, s.needs); }, [s.route, s.needs, s.setupOpen]);
  return html`
    <a class="skip" href="#main" onClick=${e => { e.preventDefault(); focusHeading(); }}>Skip to content</a>
    <div class="app">
      <${Rail} />
      <div class="main">
        <div class="mtop"><button type="button" class="iconbtn" aria-label="Menu" onClick=${() => store.set({ railOpen: true })}><${Icon}
          n="menu" /></button><span class=${cx("eye sm", s.busy && "busy")} aria-hidden="true"></span>Hal Employment<span class="sp"></span>
          <button type="button" class="iconbtn" aria-label="Search or jump" onClick=${() => openDialog("palette")}><${Icon} n="search" /></button></div>
        <${Status} />
        <main class=${cx("page", wideRoute(s.route) && "full")} id="main"><${PageFrame} route=${s.route} /></main>
      </div>
    </div>
    ${s.railOpen && html`<div class="scrim" style="z-index:44" onClick=${() => store.set({ railOpen: false })}></div>`}
    ${s.setupOpen && html`<${SetupHost} />`}
    <${DialogHost} />
    <${Toasts} />`;
}
