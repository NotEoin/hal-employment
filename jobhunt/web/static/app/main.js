/* Boot: apply this viewer's theme, ask /session who is here, then draw the app, or the screen that
   says to open Hal from its shortcut. A session that ends later (any 401) switches to that screen. */
import { html, render, useEffect } from "./core/html.js";
import { store, useStore, useApi, readPrefs, setTheme, openSetup } from "./core/store.js";
import { get } from "./core/api.js";
import { startEvents } from "./core/events.js";
import { followHash, parse, ROUTES } from "./core/router.js";
import { App } from "./shell/app.js";
import { NoSession, NotAnswering } from "./shell/no-session.js";
import { loadStatus } from "./shell/status.js";

function countsFrom(home) {
  if (home.counts) return home.counts;
  return { jobs: (home.new_matches || []).length };
}

function SignedIn() {
  const home = useApi("/home");
  useEffect(() => {
    if (!home.data) return;
    store.set({ counts: countsFrom(home.data), needs: (home.data.needs || []).length });
  }, [home.data]);
  return html`<${App} />`;
}

function Root({ failed }) {
  const session = useStore(s => s.session);
  if (failed) return html`<${NotAnswering} />`;
  return session ? html`<${SignedIn} />` : html`<${NoSession} />`;
}

async function boot() {
  const prefs = readPrefs();
  setTheme(prefs.theme || "system");
  const mount = document.getElementById("app");
  let session = null;
  try {
    session = await get("/session");
  } catch (err) {
    render(html`<${Root} failed=${err.code !== "signed_out"} />`, mount);
    return;
  }
  if (!prefs.theme && session.theme) setTheme(session.theme);
  const hash = (location.hash || "").slice(1);
  const first = parse(hash);
  store.set({
    session,
    simpleNav: prefs.simpleNav !== undefined ? !!prefs.simpleNav : !!session.simple_menu,
    route: ROUTES[hash.split("-")[0]] ? hash : "home",
  });
  if (first.page === "setup") openSetup(first.arg || 0);
  else if (!hash && session.setup_done === false) openSetup(0);
  window.addEventListener("hashchange", followHash);
  window.addEventListener("popstate", followHash);
  startEvents();
  loadStatus();
  render(html`<${Root} />`, mount);
}

boot();
