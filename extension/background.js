// Hal Employment's link to the local web helper. It asks the helper for work,
// carries each command out in a job tab Hal opened, and reports the result.
// Only tabs Hal opened for a job, and tabs those open, are ever touched.

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
