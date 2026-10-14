// Hal Employment's link to the local web helper. It asks the helper for work,
// carries each command out in a job's tab, and reports the result. Only tabs
// Hal opened for a job, tabs those open, and tabs the applicant hands to Hal
// with the toolbar button are ever touched.

const HAL = "http://127.0.0.1:8737";
// The helper's address: the default, unless storage names another (the tests run their own helper).
const hal = async () => (await chrome.storage.local.get("hal")).hal || HAL;
const HEADERS = { "X-Hal": "1", "Content-Type": "application/json" };
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

async function inTab(tabId, method, arg) {
  await chrome.scripting.executeScript({ target: { tabId }, files: ["page.js", "content.js"] });
  const [out] = await chrome.scripting.executeScript({
    target: { tabId }, func: (m, a) => window.halPage[m](a), args: [method, arg ?? null] });
  return out.result;
}

async function jobTab(job) {
  const tab = await tabFor(job);
  if (!tab) throw new Error("gone");
  return loaded(tab.id);
}

function base64(buffer) {
  const bytes = new Uint8Array(buffer);
  let text = "";
  for (let i = 0; i < bytes.length; i += 0x8000) text += String.fromCharCode(...bytes.subarray(i, i + 0x8000));
  return btoa(text);
}

const COMMANDS = {
  async open({ job, url }) {
    let tab = await tabFor(job);
    if (tab) await chrome.tabs.update(tab.id, { url });
    else {
      tab = await chrome.tabs.create({ url, active: false });
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
  async read({ job, patterns }) {
    return inTab((await jobTab(job)).id, "read", patterns);
  },
  async press({ job, kind, pattern }) {
    const out = await inTab((await jobTab(job)).id, "press", { kind, pattern });
    await sleep(1500);
    await jobTab(job);
    return out;
  },
  async fill({ job, fields, paths }) {
    // Files come through the worker: a page may not fetch from the helper itself.
    const files = {};
    for (const path of paths) {
      const reply = await fetch(`${await hal()}/files?path=${encodeURIComponent(path)}`);
      if (!reply.ok) throw new Error(`could not read ${path.split("/").pop()}`);
      files[path] = { name: path.split("/").pop(), type: reply.headers.get("Content-Type") || "application/pdf",
                      data: base64(await reply.arrayBuffer()) };
    }
    return inTab((await jobTab(job)).id, "fill", { fields, files });
  },
  async mark({ job, fields, panel }) {
    const tab = await jobTab(job);
    const out = await inTab(tab.id, "mark", { fields, panel });
    const left = fields.filter(f => f.state !== "ok").length;
    await chrome.action.setBadgeBackgroundColor({ tabId: tab.id, color: left ? "#d4a72c" : "#2da44e" });
    await chrome.action.setBadgeText({ tabId: tab.id, text: left ? String(left) : "✓" });
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
    const tab = await chrome.tabs.create({ url: "about:blank", active: false });
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
  const reply = await fetch(`${await hal()}/api/${path}`, { method: "POST", headers: HEADERS, body: JSON.stringify(body) });
  const out = await reply.json().catch(() => ({}));
  if (!reply.ok) throw new Error(out.error || `the helper answered ${reply.status}`);
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
    const at = { index: sender.tab.index + 1 };
    if (ask.cmd === "fill") {
      await chrome.action.setBadgeText({ tabId: sender.tab.id, text: "…" });
      await post(`job/${encodeURIComponent(job)}/page`);
    } else if (ask.cmd === "open") {
      await chrome.tabs.create({ url: `${await hal()}/#/job/${encodeURIComponent(job)}`, ...at });
    } else if (ask.cmd === "doc" && /^(cv|letter)$/.test(ask.which)) {
      await chrome.tabs.create({ url: `${await hal()}/doc/${encodeURIComponent(job)}/${ask.which}`, ...at });
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
    await say("…", "Hal is filling this application");
    const [read] = await chrome.scripting.executeScript({ target: { tabId: tab.id }, func: () => ({
      url: location.href, title: document.title,
      heading: (document.querySelector("h1")?.innerText || "").trim().slice(0, 200),
      site: document.querySelector("meta[property='og:site_name']")?.content || "",
      text: (document.body?.innerText || "").slice(0, 20000) }) });
    const found = await post("ext/adopt", read.result);
    await withJobs(jobs => {
      for (const job of Object.keys(jobs)) jobs[job] = jobs[job].filter(id => id !== tab.id);
      jobs[found.job] = [...(jobs[found.job] || []), tab.id];
    });
    await post(`job/${encodeURIComponent(found.job)}/adopt`, { url: read.result.url });
    await chrome.action.setTitle({ tabId: tab.id, title: `Hal: ${found.title} at ${found.company}` });
  } catch (e) {
    await say("!", `Hal could not take this tab: ${e.message}`);
  }
}
chrome.action.onClicked.addListener(handTab);

let looping = false;
async function loop() {
  if (looping) return;
  looping = true;
  try {
    for (;;) {
      let command;
      try {
        const reply = await fetch(`${await hal()}/api/ext/poll`, {
          method: "POST", headers: HEADERS, body: "{}", signal: AbortSignal.timeout(25000) });
        command = await reply.json();
      } catch {
        await sleep(5000);  // the helper is not running, or the wait timed out
        continue;
      }
      chrome.runtime.getPlatformInfo(() => {});  // counts as activity, so the worker is kept running
      if (!command.id) continue;
      let result;
      try {
        result = { id: command.id, ok: true, value: await COMMANDS[command.cmd](command.args) };
      } catch (e) {
        result = { id: command.id, ok: false, error: String(e.message || e) };
      }
      await fetch(`${await hal()}/api/ext/result`, { method: "POST", headers: HEADERS, body: JSON.stringify(result) })
        .catch(() => {});
    }
  } finally {
    looping = false;
  }
}

chrome.runtime.onStartup.addListener(loop);
chrome.runtime.onInstalled.addListener(loop);
chrome.alarms.create("hal", { periodInMinutes: 0.5 });
chrome.alarms.onAlarm.addListener(loop);
loop();
