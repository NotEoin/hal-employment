/* Routing: hash routes of the form "#<page>" or "#<page>-<arg>" (the arg is everything after the
   first "-"; job ids are encodeURIComponent-ed, since they contain ":").

   ROUTES lists every page from the start. Pages load on first visit, so one page that fails to load
   shows an error card instead of stopping the whole app. A page module exports its component under
   `name`. The rail, the palette and deep links in pop-ups and emails all use this table. */
import { store, openSetup } from "./store.js";

export const APP_NAME = "Hal Employment";

export const ROUTES = {
  home: { title: "Home", name: "Home", load: () => import("../pages/home.js") },
  jobs: { title: "Jobs", name: "Jobs", load: () => import("../pages/jobs.js") },
  job: { title: "Job", name: "JobPage", load: () => import("../pages/job.js"), parent: "jobs" },
  apps: { title: "Applications", name: "Apps", load: () => import("../pages/apps.js") },
  app: { title: "Application", name: "AppPage", load: () => import("../pages/app.js"), parent: "apps" },
  inbox: { title: "Messages", name: "Messages", load: () => import("../pages/messages.js") },
  cv: { title: "My CV", name: "CvStudio", load: () => import("../pages/cv.js") },
  docs: { title: "Documents", name: "Docs", load: () => import("../pages/docs.js") },
  record: { title: "Your record", name: "Record", load: () => import("../pages/record.js") },
  activity: { title: "Activity", name: "Activity", load: () => import("../pages/activity.js") },
  memory: { title: "What Hal remembers", name: "Memory", load: () => import("../pages/memory.js") },
  settings: { title: "Settings", name: "Settings", load: () => import("../pages/settings.js") },
};

/** Every deep link the app knows, with example args for the ones that take an id (used by the tests). */
export const ROUTE_EXAMPLES = [
  "home", "jobs", "jobs-saved", "jobs-hidden", "jobs-where", "job-<id>", "apps", "app-<id>", "inbox", "cv", "cv-sugg",
  "cv-style", "cv-checks", "cv-versions", "docs", "docs-<id>", "docs-details", "record", "activity", "memory",
  "memory-learned", "memory-sites", "memory-answers", "settings", "settings-<group>", "setup", "setup-<step>",
];

/** Splits a route ("job-reed%3A123") into its page and decoded arg; unknown pages become Home. */
export function parse(route) {
  const raw = String(route || "").replace(/^#/, "");
  const i = raw.indexOf("-");
  const page = i < 0 ? raw : raw.slice(0, i);
  let arg = i < 0 ? "" : raw.slice(i + 1);
  try { arg = decodeURIComponent(arg); } catch (err) { /* a hand-typed "%" stays as typed */ }
  if (page === "setup") return { page: "setup", arg };
  return ROUTES[page] ? { page, arg } : { page: "home", arg: "" };
}

/** The route for a page and an optional arg, encoding the arg ("job", "reed:1" -> "job-reed%3A1"). */
export function route(page, arg) { return arg ? page + "-" + encodeURIComponent(arg) : page; }

/** The top-level page a route belongs to, for the rail ("job-..." -> "jobs"). */
export function railPage(r) {
  const { page } = parse(r);
  return (ROUTES[page] && ROUTES[page].parent) || page;
}

/** The window title for a route, with the count of things that need you in front. */
export function titleFor(r, needs = 0) {
  const { page } = parse(r);
  const name = ROUTES[page] ? ROUTES[page].title : "Setup guide";
  return (needs > 0 ? "(" + needs + ") " : "") + name + " - " + APP_NAME;
}

function writeHash(r, replace) {
  try {
    if (replace) history.replaceState(null, "", "#" + r);
    else history.pushState(null, "", "#" + r);
  } catch (err) { /* a sandboxed frame may refuse; the page still works */ }
}

/** Goes to a route: "#setup" or "setup-<step>" opens the guide over the current page. */
export function go(r, { replace = false } = {}) {
  const { page, arg } = parse(r);
  if (page === "setup") { openSetup(arg || 0); return; }
  const raw = String(r).replace(/^#/, "");
  const next = ROUTES[raw.split("-")[0]] ? raw : "home";
  store.set(st => ({ route: next, railOpen: false, dialog: null, setup: { ...st.setup, open: false } }));
  writeHash(next, replace);
  window.scrollTo(0, 0);
}

/** Follows the address bar (Back, Forward, a typed hash) without adding to the history. */
export function followHash() {
  const r = (location.hash || "").slice(1);
  if (!r) return;
  const { page, arg } = parse(r);
  if (page === "setup") { openSetup(arg || 0); return; }
  const next = ROUTES[r.split("-")[0]] ? r : "home";
  store.set(st => ({ route: next, railOpen: false, dialog: null, setup: { ...st.setup, open: false } }));
}

const loaded = new Map();
/** The page's module (cached after the first load). */
export function loadPage(page) {
  if (!loaded.has(page)) loaded.set(page, ROUTES[page].load());
  return loaded.get(page);
}

