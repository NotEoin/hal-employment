// Hal Employment's browser helper. It asks Hal for work, carries each command out in a job's tab, and
// reports the result. Only tabs Hal opened for a job, tabs those open, and tabs the applicant hands to Hal
// with the toolbar button are ever touched. There is no submit command: Hal never presses Submit, and the
// person presses it on the site.
//
// Protocol 1: the helper is paired once on Hal's /pair page in this browser (pair.js relays the
// one-time code), and keeps {base, helper_id, key, install_id} in storage. Every request goes to that base,
// the address the pairing page was opened at, so a laptop talks through its own tunnel port. Only APIs that
// Firefox also has are used (tabs, scripting, storage, alarms, runtime).

const PROTOCOL = 1;
const VERSION = chrome.runtime.getManifest().version;
const JSON_HEADERS = { "X-Hal": "1", "Content-Type": "application/json" };
const UNPAIRED_WAIT_MS = 5000;    // how often an unpaired helper looks for a pairing
const REFUSED_WAIT_MS = 30000;    // Hal refused the key (forgotten in Settings): ask again much later
const POLL_TIMEOUT_MS = 25000;    // Hal holds a poll for 20 s
const ADOPT_TRIES = 60;           // finding a handed-over tab's job may ask the AI; Hal answers every 2 s

// Where Hal is, and how to prove who this helper is; null until the helper is paired.
async function link() {
  const { pairing } = await chrome.storage.local.get("pairing");
  if (!pairing || !pairing.base || !pairing.key) return null;
  return { base: pairing.base, helper_id: pairing.helper_id,
           headers: { ...JSON_HEADERS, "X-Hal-Key": `${pairing.helper_id}:${pairing.key}` } };
}
const SEARCH = "https://duckduckgo.com/?q=";
const sleep = ms => new Promise(r => setTimeout(r, ms));

// Job tabs, newest last, kept in session storage because the worker is stopped when idle.
let writing = Promise.resolve();
function withJobs(change) {
  writing = writing.then(async () => {
    const { jobs = {} } = await chrome.storage.session.get("jobs");
    await change(jobs);
    await chrome.storage.session.set({ jobs });
  });
  return writing;
}

async function tabFor(job) {
  await writing;
  const { jobs = {} } = await chrome.storage.session.get("jobs");
  for (const id of [...(jobs[job] || [])].reverse()) {
    try { return await chrome.tabs.get(id); } catch { /* closed */ }
  }
  return null;
}

chrome.tabs.onCreated.addListener(tab => {
  if (tab.openerTabId == null) return;
  // A form that opens in a new tab belongs to the job whose tab opened it.
  withJobs(jobs => Object.values(jobs).forEach(ids => ids.includes(tab.openerTabId) && ids.push(tab.id)));
});
// A page the browser loaded ahead of time swaps in under a new tab id; the job keeps the tab.
chrome.tabs.onReplaced.addListener((added, removed) => {
  withJobs(jobs => Object.keys(jobs).forEach(job => { jobs[job] = jobs[job].map(t => t === removed ? added : t); }));
});
chrome.tabs.onRemoved.addListener(id => {
  withJobs(jobs => Object.keys(jobs).forEach(job => { jobs[job] = jobs[job].filter(t => t !== id); }));
});

async function loaded(tabId, timeout = 45000) {
  const until = Date.now() + timeout;
  let tab = await chrome.tabs.get(tabId);
  while (tab.status !== "complete" && Date.now() < until) {
    await sleep(250);
    tab = await chrome.tabs.get(tabId);
  }
  // Adzuna links show a holding page that hands over to the advertiser a few seconds later.
  const holding = () => /adzuna\./.test(new URL(tab.url).host) && tab.url.includes("/land/");
  for (let i = 0; i < 60 && holding(); i++) {
    await sleep(250);
    tab = await chrome.tabs.get(tabId);
  }
  await sleep(1200);
  return tab;
}

// A page swapped out while the helper reaches into it loses its frame ("Frame with ID 0 was removed", Totaljobs,
// testers 2026-11-05); the page that replaced it is tried once, when it has loaded.
const FRAME_GONE = /frame with id \d+ was removed|no frame with id|frame was removed/i;

async function inTab(tabId, method, arg) {
  for (let attempt = 0; ; attempt++) {
    try {
      await chrome.scripting.executeScript({ target: { tabId }, files: ["page.js", "content.js"] });
      const [out] = await chrome.scripting.executeScript({
        target: { tabId }, func: (m, a) => window.halPage[m](a), args: [method, arg ?? null] });
      return out.result;
    } catch (e) {
      if (attempt || !FRAME_GONE.test(String(e && e.message))) throw e;
      await loaded(tabId);
    }
  }
}

// The page-world block, the same as BLOCK_JS in jobhunt/browser.py. For the whole of each command
// that acts on a page, a submit the page or a press would make is cancelled and noted, as are the form's own
// send methods, unless the submitter is the vetted Next control marked `data-hal-allow`: many multi-page
// forms move on with a real submit. Taken away when the command ends, so the person's own Submit works.
// Each command holds the block: two commands in one tab (a watch's read beside a fill) share it, and it goes
// only when the last lets go, so the first to end never takes it from under the other.
function halBlock() {
  if (window.__halUnblock) { window.__halHolders = (window.__halHolders || 1) + 1; return true; }
  const proto = HTMLFormElement.prototype, submit = proto.submit, request = proto.requestSubmit;
  const cancelled = [];
  const allowed = el => Boolean(el && el.hasAttribute && el.hasAttribute('data-hal-allow'));
  const onSubmit = e => {
    if (allowed(e.submitter)) return;
    e.preventDefault();
    e.stopImmediatePropagation();
    cancelled.push('the page tried to send the form');
  };
  window.addEventListener('submit', onSubmit, true);
  proto.submit = function () { cancelled.push('a script on the page tried to send the form'); };
  proto.requestSubmit = function (by) {
    if (allowed(by)) return request.call(this, by);
    cancelled.push('a script on the page asked to send the form');
  };
  window.__halHolders = 1;
  window.__halUnblock = () => {
    if (--window.__halHolders > 0) return [];   // the last holder reports what was cancelled
    window.removeEventListener('submit', onSubmit, true);
    proto.submit = submit;
    proto.requestSubmit = request;
    delete window.__halUnblock;
    delete window.__halHolders;
    return cancelled;
  };
  return true;
}
function halUnblock() { return window.__halUnblock ? window.__halUnblock() : []; }

// Runs `work` with the block on in the tab's page world, and adds what it cancelled to the result.
async function blocked(tabId, work) {
  await chrome.scripting.executeScript({ target: { tabId }, world: "MAIN", func: halBlock });
  let result;
  try {
    result = await work();
  } finally {
    const [left] = await chrome.scripting.executeScript({ target: { tabId }, world: "MAIN", func: halUnblock })
      .catch(() => [null]);  // the tab moved on, taking its block with it
    const cancelled = (left && left.result) || [];
    if (cancelled.length && result && typeof result === "object") {
      result.blocked = cancelled;
      result.url = (await chrome.tabs.get(tabId).catch(() => ({}))).url || "";
    }
  }
  return result;
}

// Hal's own window, holding every application tab when the helper asks for one ("separate"),
// so they never crowd the window the applicant is working in. Its id lasts as long as the browser runs, so
// every form until then goes into the same window; it is made again if it was closed.
async function halWindow() {
  const { halWindow: id } = await chrome.storage.session.get("halWindow");
  if (id != null) {
    try { return await chrome.windows.get(id); } catch { /* closed */ }
  }
  return null;
}

// The Hal page the person used last, which a "This window" form opens beside; null when none is open.
async function halTab() {
  const at = await link();
  if (!at) return null;
  const tabs = (await chrome.tabs.query({})).filter(t => (t.url || "").startsWith(at.base + "/"));
  tabs.sort((a, b) => (b.lastAccessed || 0) - (a.lastAccessed || 0));
  return tabs[0] || null;
}

// A form's new tab. Hal's window comes to the front with it: opened behind the applicant's window, it
// looked as if no form had opened (testers, 2026-11-03); `front` false (Preferences) leaves it behind. "This
// window" puts it beside Hal's page and in that
// page's tab group, as the setting says; it opened wherever the browser last had focus (testers, 2026-11-04).
async function newTab(url, separate, front = true) {
  if (!separate) {
    const hal = await halTab();
    if (!hal) return chrome.tabs.create({ url, active: false });
    const tab = await chrome.tabs.create({ url, active: false, windowId: hal.windowId, index: hal.index + 1,
                                           openerTabId: hal.id });
    if (hal.groupId !== undefined && hal.groupId !== -1) {
      await chrome.tabs.group({ groupId: hal.groupId, tabIds: [tab.id] }).catch(() => {});  // a group closed meanwhile
    }
    return tab;
  }
  const own = await halWindow();
  if (own) {
    const tab = await chrome.tabs.create({ url, windowId: own.id, active: true });
    if (front) await chrome.windows.update(own.id, { focused: true });
    return tab;
  }
  const made = await chrome.windows.create({ url, focused: front, type: "normal" });
  await chrome.storage.session.set({ halWindow: made.id });
  return made.tabs[0];
}

// The job's tab, loaded. With `front`, it is brought forward first, in Hal's window or the applicant's own
// ("This window"): a tab behind others is not drawn, so its dropdowns never open and its clicks never settle.
// Looks that only read (a watch every 3 s) leave the tabs as they are, so two watched jobs never flip the
// window between their tabs, nor put a tab Hal is filling behind another mid-command.
async function jobTab(job, front = false) {
  const tab = await tabFor(job);
  if (!tab) throw new Error("gone");
  if (front && !tab.active) await chrome.tabs.update(tab.id, { active: true });
  return loaded(tab.id);
}
const fileName = path => String(path).split(/[\\/]/).pop();

function base64(buffer) {
  const bytes = new Uint8Array(buffer);
  let text = "";
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(text);
}

const COMMANDS = {
  async open({ job, url, window = "separate", front = true }) {
    let tab = await tabFor(job);
    if (tab) await chrome.tabs.update(tab.id, { url });
    else {
      tab = await newTab(url, window === "separate", front);
      await withJobs(jobs => { jobs[job] = [tab.id]; });
    }
    await loaded(tab.id);
    return {};
  },
  async goto({ job, url }) {
    const tab = await jobTab(job);
    await chrome.tabs.update(tab.id, { url });
    await loaded(tab.id);
    return {};
  },
  async read({ job, patterns, front = true }) {
    const tab = await jobTab(job, front);
    return blocked(tab.id, () => inTab(tab.id, "read", patterns));
  },
  async press({ job, kind, pattern, name, page, guard }) {
    const tab = await jobTab(job, true);
    const out = await blocked(tab.id, async () => {
      const pressed = await inTab(tab.id, "press", { kind, pattern, name, page, guard });
      await sleep(1500);
      return pressed;
    });
    await jobTab(job);
    return out;
  },
  async type_code({ job, code, boxes, guard }) {
    const tab = await jobTab(job, true);
    return blocked(tab.id, () => inTab(tab.id, "typeCode", { code, boxes, guard }));
  },
  async fill({ job, fields, paths, guard }) {
    // Files come through the worker: a page may not fetch from the helper itself.
    const files = {};
    const at = await link();
    for (const path of paths) {
      const where = `/api/v2/helpers/file?job_id=${encodeURIComponent(job)}&path=${encodeURIComponent(path)}`;
      const reply = await fetch(`${at.base}${where}`, { headers: at.headers });
      if (!reply.ok) throw new Error(`could not read ${fileName(path)}`);
      files[path] = { name: fileName(path), type: reply.headers.get("Content-Type") || "application/pdf",
                      data: base64(await reply.arrayBuffer()) };
    }
    const tab = await jobTab(job, true);
    return blocked(tab.id, () => inTab(tab.id, "fill", { fields, files, guard }));
  },
  async mark({ job, fields, panel }) {
    const tab = await jobTab(job);
    const out = await inTab(tab.id, "mark", { fields, panel });
    const left = fields.filter(f => f.state !== "ok").length;
    await chrome.action.setBadgeBackgroundColor({ tabId: tab.id, color: left ? "#d4a72c" : "#2da44e" });
    await chrome.action.setBadgeText({ tabId: tab.id, text: left ? String(left) : "\u2713" });
    return out;
  },
  async show({ job }) {
    const tab = await tabFor(job);
    if (!tab) return { shown: false };
    await chrome.tabs.update(tab.id, { active: true });
    await chrome.windows.update(tab.windowId, { focused: true });
    return { shown: true };
  },
  async search({ queries, pause }) {
    // Searches run in Hal's window when there is one, out of the applicant's way.
    const own = await halWindow();
    const tab = await chrome.tabs.create({ url: "about:blank", active: false, ...(own ? { windowId: own.id } : {}) });
    const pages = [];
    let refused = false;
    try {
      for (const [i, query] of queries.entries()) {
        if (i) await sleep(pause * 1000);
        await chrome.tabs.update(tab.id, { url: SEARCH + encodeURIComponent(query) });
        await loaded(tab.id);
        let results = [];
        for (let tries = 0; tries < 16 && !results.length; tries++) {
          results = await inTab(tab.id, "results");
          if (!results.length) await sleep(500);
        }
        if (!results.length) {
          const [check] = await chrome.scripting.executeScript({
            target: { tabId: tab.id }, func: () => document.documentElement.innerHTML.includes("anomaly") });
          refused = check.result;
          if (refused) break;
        }
        pages.push(results);
      }
    } finally {
      await chrome.tabs.remove(tab.id).catch(() => {});
    }
    return { pages, refused };
  },
};

async function jobOf(tabId) {
  await writing;
  const { jobs = {} } = await chrome.storage.session.get("jobs");
  return Object.keys(jobs).find(job => jobs[job].includes(tabId)) || null;
}

async function post(path, body = {}) {
  const at = await link();
  if (!at) throw new Error("the helper is not connected to Hal yet; press Connect in Hal's setup");
  const reply = await fetch(`${at.base}/api/v2/helpers/${path}`,
    { method: "POST", headers: at.headers, body: JSON.stringify(body) });
  const out = await reply.json().catch(() => ({}));
  if (!reply.ok) throw new Error((out.error && (out.error.message || out.error)) || `Hal answered ${reply.status}`);
  return out;
}

// The panel Hal draws in a job's tab asks for things through here, where the helper can be reached.
chrome.runtime.onMessage.addListener((message, sender) => {
  const ask = message && message.hal;
  if (!ask || !sender.tab) return;
  (async () => {
    const job = await jobOf(sender.tab.id);
    if (!job) return;
    // No opener: a tab the job's tab opens is taken for the next page of its form.
    const at = { index: sender.tab.index + 1, windowId: sender.tab.windowId };
    const hal = await link();
    if (!hal) return;
    if (ask.cmd === "fill") {
      await chrome.action.setBadgeText({ tabId: sender.tab.id, text: "..." });
      await post("fill-empty", { job_id: job });
    } else if (ask.cmd === "open") {
      await chrome.tabs.create({ url: `${hal.base}/#app-${encodeURIComponent(job)}`, ...at });
    } else if (ask.cmd === "doc" && /^(cv|letter)$/.test(ask.which)) {
      await chrome.tabs.create({ url: `${hal.base}/doc/${encodeURIComponent(job)}/${ask.which}`, ...at });
    }
  })().catch(e => console.warn("Hal:", e.message));
});

// The toolbar button (Alt+Shift+F): the applicant hands Hal the application in this tab.
async function handTab(tab) {
  const say = async (text, title) => {
    await chrome.action.setBadgeBackgroundColor({ tabId: tab.id, color: text === "!" ? "#cf222e" : "#57606a" });
    await chrome.action.setBadgeText({ tabId: tab.id, text });
    await chrome.action.setTitle({ tabId: tab.id, title });
  };
  try {
    // The button pressed on Hal's own page, or on a browser page no extension may read, used to end in a bare
    // "!" (testers, 2026-11-03); each now says what the button is for.
    const hal = await link();
    if (!hal) {
      await say("!", "Hal's helper is not connected yet. In Hal, open Settings, then Browser helper, and press Connect.");
      return;
    }
    if (!/^https?:/.test(tab.url || "")) {
      await say("!", "Hal cannot read this browser page. Press this button on an employer's application page.");
      return;
    }
    if (new URL(tab.url).origin === hal.base) {
      await say("", "This is Hal itself. Press Apply with Hal on a job, or press this button on an employer's application page.");
      return;
    }
    await say("...", "Hal is filling this application");
    const [read] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: () => ({
      url: location.href, title: document.title,
      heading: (document.querySelector("h1")?.innerText || "").trim().slice(0, 200),
      site: document.querySelector("meta[property='og:site_name']")?.content || "",
      text: (document.body?.innerText || "").slice(0, 20000) }) });
    // Hal fills the tab once it is registered under its job, so the job is found first, then filled.
    let reply = await post("adopt", { tab: read.result });
    for (let i = 0; reply.started && i < ADOPT_TRIES; i++) reply = await post("adopt", { action_id: reply.action_id, tab: read.result });
    if (!reply.job_id) throw new Error("Hal took too long to find the job");
    await withJobs(jobs => {
      for (const job of Object.keys(jobs)) jobs[job] = jobs[job].filter(id => id !== tab.id);
      jobs[reply.job_id] = [...(jobs[reply.job_id] || []), tab.id];
    });
    await chrome.action.setTitle({ tabId: tab.id, title: `Hal: ${reply.title} at ${reply.company}` });
  } catch (e) {
    await say("!", `Hal could not take this tab: ${e.message}`);
  }
}
chrome.action.onClicked.addListener(handTab);

// Commands are carried out side by side, each in its own job's tab: one at a time, a slow page in
// one application held up every other, and a quick "show the form" timed out queued behind it
// (2026-10-15). The helper never sends two actions for one job at once.
const MOST = 4;
let running = 0;

async function carryOut(command) {
  running++;
  let result;
  try {
    result = { id: command.id, ok: true, value: await COMMANDS[command.cmd](command.args) };
  } catch (e) {
    result = { id: command.id, ok: false, error: String(e.message || e) };
  } finally {
    running--;
  }
  const at = await link();
  if (!at) return;
  await fetch(`${at.base}/api/v2/helpers/result`,
    { method: "POST", headers: at.headers, body: JSON.stringify(result) }).catch(() => {});
}

// One poll; returns how long to wait before the next, in milliseconds.
async function pollOnce() {
  const at = await link();
  if (!at) return UNPAIRED_WAIT_MS;
  const body = { helper_id: at.helper_id, version: VERSION, protocol: PROTOCOL, tabs: running };
  let reply;
  try {
    reply = await fetch(`${at.base}/api/v2/helpers/poll`, {
      method: "POST", headers: at.headers, body: JSON.stringify(body), signal: AbortSignal.timeout(POLL_TIMEOUT_MS) });
  } catch {
    return UNPAIRED_WAIT_MS;  // Hal is not running, or the wait timed out
  }
  if (reply.status === 401 || reply.status === 403) return REFUSED_WAIT_MS;
  const out = await reply.json().catch(() => ({}));
  chrome.runtime.getPlatformInfo(() => {});  // counts as activity, so the worker is kept running
  if (out.reload) {
    // Hal was updated: re-read this folder's files, which git and Hal's start refreshed.
    chrome.runtime.reload();
    return REFUSED_WAIT_MS;
  }
  for (const command of out.commands || []) carryOut(command);
  return 0;
}

let looping = false;
async function loop() {
  if (looping) return;
  looping = true;
  try {
    for (;;) {
      while (running >= MOST) await sleep(100);
      const wait = await pollOnce().catch(() => UNPAIRED_WAIT_MS);
      if (wait) await sleep(wait);
    }
  } finally {
    looping = false;
  }
}

// Pairing: pair.js found a one-time code on Hal's own /pair page in this browser. The base is
// that page's origin, so a laptop pairs with its tunnel's address.
async function pairWith(code, page, installId) {
  const base = new URL(page).origin;
  const reply = await fetch(`${base}/api/v2/helpers/pair`, { method: "POST", headers: JSON_HEADERS, body: JSON.stringify({
    code, browser: null, version: VERSION, protocol: PROTOCOL, extension_id: chrome.runtime.id }) });
  const out = await reply.json().catch(() => ({}));
  if (!reply.ok) throw new Error((out.error && out.error.message) || `Hal answered ${reply.status}`);
  await chrome.storage.local.set({ pairing: { base, helper_id: out.helper_id, key: out.key, install_id: installId || null } });
  loop();
}

chrome.runtime.onMessage.addListener((message, sender, reply) => {
  const ask = message && message.halPair;
  if (!ask || !sender.tab || !/^http:\/\/(127\.0\.0\.1|localhost)(:\d+)?\/pair$/.test(sender.url.split(/[?#]/)[0])) return false;
  pairWith(String(ask.code), sender.url, ask.install_id).then(() => reply({ ok: true }), e => reply({ ok: false, error: e.message }));
  return true;  // the reply comes later
});

chrome.runtime.onStartup.addListener(loop);
chrome.runtime.onInstalled.addListener(loop);
chrome.alarms.create("hal", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(loop);
loop();
