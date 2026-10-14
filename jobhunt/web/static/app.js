// Hal Employment: single-page interface over the jobhunt JSON API.
"use strict";

const $ = (sel, root = document) => root.querySelector(sel);
const $$ = (sel, root = document) => [...root.querySelectorAll(sel)];
const esc = s => String(s ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
const enc = encodeURIComponent;
const main = $("#main");

const state = { route: "", last: null, lastAlert: null, focus: 0, queue: [], job: null, tab: "letter", stepView: null };

// API --------------------------------------------------------------------------
async function api(path, body) {
  const opts = body === undefined ? {} : { method: "POST", headers: { "Content-Type": "application/json", "X-Hal": "1" }, body: JSON.stringify(body) };
  const res = await fetch("/api/" + path, opts);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || res.statusText);
  return data;
}
const fileUrl = p => "/files?path=" + enc(p) + "&t=" + Date.now();

// Small helpers ----------------------------------------------------------------
function toast(text, level = "") {
  const el = document.createElement("div");
  el.className = "toast " + level;
  el.textContent = text;
  $("#toasts").append(el);
  setTimeout(() => el.remove(), level === "urgent" ? 9000 : 4500);
}
async function guard(fn, okText) {
  try { const r = await fn(); if (okText) toast(okText); return r; }
  catch (e) { toast(e.message, "error"); throw e; }
}
function ago(iso) {
  if (!iso) return "";
  const s = (Date.now() - new Date(iso)) / 1000;
  if (s < 90) return "just now";
  if (s < 3600) return Math.round(s / 60) + " min ago";
  if (s < 86400) return Math.round(s / 3600) + " h ago";
  return Math.round(s / 86400) + " d ago";
}
function md(text) {
  // Minimal Markdown: headings, lists, bold, paragraphs. Input is escaped first.
  const out = [];
  let list = null;
  for (const raw of esc(text).split("\n")) {
    const line = raw.replace(/\*\*(.+?)\*\*/g, "<b>$1</b>");
    const li = line.match(/^\s*(?:[-*]|\d+\.)\s+(.*)/);
    if (li) { if (!list) { list = []; } list.push("<li>" + li[1] + "</li>"); continue; }
    if (list) { out.push("<ul>" + list.join("") + "</ul>"); list = null; }
    const h = line.match(/^(#{1,4})\s+(.*)/);
    if (h) out.push(`<h${Math.min(h[1].length + 1, 4)}>${h[2]}</h${Math.min(h[1].length + 1, 4)}>`);
    else if (line.trim()) out.push("<p>" + line + "</p>");
  }
  if (list) out.push("<ul>" + list.join("") + "</ul>");
  return out.join("");
}
function modal(html, onReady) {
  const m = $("#modal");
  m.innerHTML = `<div class="card">${html}</div>`;
  m.hidden = false;
  const close = () => { m.hidden = true; m.innerHTML = ""; };
  m.onclick = e => { if (e.target === m) close(); };
  onReady && onReady(m, close);
  return close;
}
function confirmBox(title, text, okLabel, cls = "primary") {
  return new Promise(resolve => {
    modal(`<h3>${esc(title)}</h3><p class="muted">${esc(text)}</p>
      <div class="bar"><span class="grow"></span><button data-x>Cancel</button><button class="${cls}" data-ok>${esc(okLabel)}</button></div>`,
      (m, close) => {
        $("[data-x]", m).onclick = () => { close(); resolve(false); };
        $("[data-ok]", m).onclick = () => { close(); resolve(true); };
        $("[data-ok]", m).focus();
      });
  });
}
function flagClass(f) { return /^(ELIGIBILITY|REAPPLICATION)/.test(f) ? "bad" : ""; }
function scoreRing(s) { const v = Math.max(0, Math.min(100, Math.round(s || 0))); return `<div class="score" style="--s:${v}"><span>${s == null ? "-" : v}</span></div>`; }
const STATUS_CHIP = { applying: "info", applied: "", assessment: "warn", interview: "warn", offer: "good", rejected: "bad", withdrawn: "", ready: "info", skipped: "" };

// Router -----------------------------------------------------------------------
const routes = { queue: viewQueue, applications: viewApplications, todos: viewTodos, alerts: viewAlerts, stats: viewStats, cv: viewCv, job: viewJob };
async function route() {
  const [name, ...rest] = (location.hash.slice(2) || "queue").split("/");
  state.route = name;
  $$(".nav").forEach(a => a.classList.toggle("on", a.dataset.route === name || (name === "job" && a.dataset.route === "applications")));
  const view = routes[name] || viewQueue;
  try { await view(rest.map(decodeURIComponent).join("/")); }
  catch (e) { main.innerHTML = `<div class="empty"><h2>Something went wrong</h2><p>${esc(e.message)}</p></div>`; }
}
window.addEventListener("hashchange", () => { state.stepView = null; route(); });

// Queue ------------------------------------------------------------------------
async function viewQueue() {
  const data = await api("queue");
  state.queue = data.jobs;
  state.focus = Math.min(state.focus, Math.max(0, data.jobs.length - 1));
  const w = state.last?.worker;
  main.innerHTML = `
    <div class="head"><div><h1>Your queue</h1>
      <p>${data.jobs.length} of ${data.target} ready${data.reserve ? `, ${data.reserve} more in reserve` : ""}.
      Hal tops it up as you go. <span class="kbd">J</span> <span class="kbd">K</span> move,
      <span class="kbd">A</span> apply, <span class="kbd">D</span> discard, <span class="kbd">Enter</span> details.
      Apply sends Hal to fill the form in; nothing is submitted until you approve it.</p></div>
      ${data.jobs.length ? `<div class="bar" style="margin:0"><button class="primary" data-all>Apply to all ${data.jobs.length}</button></div>` : ""}</div>
    ${data.jobs.length ? `<div class="grid">${data.jobs.map(jobCard).join("")}</div>` :
      `<div class="card empty"><h2>The queue is empty</h2><p>${esc(w?.detail || "Hal is looking for more jobs.")}</p></div>`}`;
  const all = $("[data-all]", main);
  all && (all.onclick = async () => {
    if (!await confirmBox(`Apply to all ${data.jobs.length}?`, "Hal opens each application, answers its questions and fills the form in. Each one then waits for you to check the filled form and approve it; nothing is submitted before that.", "Start")) return;
    await guard(() => api("queue/apply", { count: data.jobs.length }), "Hal has started on them");
    location.hash = "#/applications";
  });
  $$(".job", main).forEach((el, i) => {
    el.onclick = e => { if (!e.target.closest("button")) { state.focus = i; openDrawer(state.queue[i].id); } };
    $("[data-apply]", el).onclick = () => applyJob(state.queue[i]);
    $("[data-discard]", el).onclick = () => discardJob(state.queue[i]);
  });
  paintFocus();
}
function jobCard(j) {
  const strong = j.fit.filter(f => f.strength === "strong").length;
  const serious = j.flags.filter(f => flagClass(f));
  const chips = [
    j.location && `<span class="chip">${esc(j.location.slice(0, 40))}</span>`,
    j.salary && `<span class="chip good">${esc(j.salary)}</span>`,
    j.fit.length && `<span class="chip info">${strong}/${j.fit.length} strong matches</span>`,
    serious.length && `<span class="chip bad" title="${esc(serious.join("\n"))}">${esc(serious[0].split(":")[0].toLowerCase())}</span>`,
    j.flags.length - serious.length > 0 && `<span class="chip warn">${j.flags.length - serious.length} to check</span>`,
  ].filter(Boolean).join("");
  return `<article class="card job" tabindex="0">
    <div class="job-top">${scoreRing(j.score)}<div><div class="company">${esc(j.company)}</div><h3>${esc(j.title)}</h3></div></div>
    <div class="chips">${chips}</div>
    <p class="role">${esc(j.role || j.score_notes || "")}</p>
    <div class="job-actions"><button data-discard>Discard</button><button class="primary" data-apply>Apply</button></div>
  </article>`;
}
function paintFocus() {
  $$(".job", main).forEach((el, i) => el.classList.toggle("focus", i === state.focus));
  const el = $$(".job", main)[state.focus];
  el && el.scrollIntoView({ block: "nearest", behavior: "smooth" });
}
async function applyJob(j) {
  await guard(() => api(`job/${enc(j.id)}/apply`, {}));
  location.hash = "#/job/" + enc(j.id);
}
function discardJob(j) {
  const reasons = ["Not interested", "Too senior", "Wrong location", "Not my field", "Company", "Duplicate"];
  modal(`<h3>Discard ${esc(j.company)}?</h3><p class="muted">${esc(j.title)}</p>
    <div class="reasons">${reasons.map(r => `<button class="small" data-r="${esc(r)}">${esc(r)}</button>`).join("")}</div>
    <input type="text" id="reason" placeholder="Or type a reason (optional)">
    <div class="bar"><span class="grow"></span><button data-x>Cancel</button><button class="primary" data-ok>Discard</button></div>`,
    (m, close) => {
      const go = async reason => {
        close();
        const card = $$(".job", main)[state.queue.indexOf(j)];
        card && card.classList.add("leaving");
        await guard(() => api(`job/${enc(j.id)}/discard`, { reason }), `Discarded ${j.company}`);
        setTimeout(viewQueue, 180);
      };
      $$("[data-r]", m).forEach(b => b.onclick = () => go(b.dataset.r));
      $("[data-x]", m).onclick = close;
      $("[data-ok]", m).onclick = () => go($("#reason", m).value);
      $("#reason", m).onkeydown = e => { if (e.key === "Enter") go(e.target.value); };
      $("[data-ok]", m).focus();
    });
}
async function openDrawer(id) {
  const d = await api("job/" + enc(id));
  const j = d.job, s = d.summary;
  const bg = document.createElement("div"); bg.className = "drawer-bg";
  const dr = document.createElement("aside"); dr.className = "drawer";
  dr.innerHTML = `
    <div class="job-top">${scoreRing(j.score)}<div><div class="company">${esc(j.company)}</div><h2>${esc(j.title)}</h2>
      <div class="chips"><span class="chip">${esc(j.location || "")}</span>${j.salary ? `<span class="chip good">${esc(j.salary)}</span>` : ""}
      <span class="chip">${esc(j.source)}</span><span class="chip">seen ${esc(ago(j.first_seen))}</span></div></div></div>
    <div class="bar"><button data-discard>Discard</button><button class="primary" data-apply>Apply</button>
      <span class="grow"></span><a href="${esc(j.url)}" target="_blank" rel="noopener"><button class="ghost">Open advert</button></a></div>
    ${s.flags.length ? `<div class="section"><h4>Check before sending</h4><ul class="flags">${s.flags.map(f => `<li class="${flagClass(f)}">${esc(f)}</li>`).join("")}</ul></div>` : ""}
    ${s.role ? `<div class="section"><h4>Role</h4><p>${esc(s.role)}</p></div>` : ""}
    ${s.company ? `<div class="section"><h4>Company</h4><p>${esc(s.company)}</p>${s.company_check.length ? `<p class="small-t muted">${s.company_check.map(esc).join("<br>")}</p>` : ""}</div>` : ""}
    ${s.fit.length ? `<div class="section"><h4>Fit</h4><table><tr><th>Requirement</th><th>Evidence</th><th></th></tr>
      ${s.fit.map(f => `<tr><td>${esc(f.requirement)}</td><td>${esc(f.evidence)}</td><td><span class="chip ${f.strength === "strong" ? "good" : "warn"}">${esc(f.strength)}</span></td></tr>`).join("")}</table>
      ${s.gaps ? `<p class="small-t muted">Gaps: ${esc(s.gaps)}</p>` : ""}</div>` : ""}
    <div class="section"><h4>Advert</h4><div class="advert">${esc(j.description || "No advert text was fetched.")}</div></div>`;
  const close = () => { bg.remove(); dr.remove(); document.removeEventListener("keydown", onKey, true); };
  const onKey = e => { if (e.key === "Escape") { e.stopPropagation(); close(); } };
  document.addEventListener("keydown", onKey, true);
  bg.onclick = close;
  $("[data-apply]", dr).onclick = () => { close(); applyJob(j); };
  $("[data-discard]", dr).onclick = () => { close(); discardJob(j); };
  document.body.append(bg, dr);
}
document.addEventListener("keydown", e => {
  if (state.route !== "queue" || !$("#modal").hidden || $(".drawer") || /INPUT|TEXTAREA|SELECT/.test(document.activeElement.tagName)) return;
  const j = state.queue[state.focus];
  if (e.key === "j" || e.key === "ArrowDown") { state.focus = Math.min(state.focus + 1, state.queue.length - 1); paintFocus(); }
  else if (e.key === "k" || e.key === "ArrowUp") { state.focus = Math.max(state.focus - 1, 0); paintFocus(); }
  else if (e.key === "a" && j) applyJob(j);
  else if (e.key === "d" && j) discardJob(j);
  else if (e.key === "Enter" && j) openDrawer(j.id);
  else return;
  e.preventDefault();
});

// Applications board -----------------------------------------------------------
async function viewApplications() {
  const { jobs } = await api("applications");
  const cols = [["applying", "In progress"], ["applied", "Applied"], ["assessment", "Assessment"], ["interview", "Interview"], ["offer", "Offer"]];
  const closed = jobs.filter(j => ["rejected", "withdrawn"].includes(j.status));
  main.innerHTML = `<div class="head"><div><h1>Applications</h1><p>Everything you have chosen from the queue, by stage.</p></div></div>
    <div class="board">${cols.map(([k, name]) => {
      const list = jobs.filter(j => j.status === k);
      return `<div class="col"><h4>${name}<span>${list.length}</span></h4>${list.map(j => `
        <div class="card mini" data-id="${esc(j.id)}"><div class="t">${esc(j.title)}</div><div class="c">${esc(j.company)}</div>
        <div class="c">${k === "applying" ? standing(j) : esc(ago(j.changed))}</div></div>`).join("") || `<p class="muted small-t">None</p>`}</div>`;
    }).join("")}</div>
    ${closed.length ? `<div class="section"><h4>Closed</h4><div class="card">${closed.map(j => `<div class="todo" data-id="${esc(j.id)}" style="cursor:pointer">
      <div class="body"><div class="t">${esc(j.company)}: ${esc(j.title)}</div></div><span class="chip ${STATUS_CHIP[j.status]}">${esc(j.status)}</span></div>`).join("")}</div></div>` : ""}`;
  $$("[data-id]", main).forEach(el => el.onclick = () => location.hash = "#/job/" + enc(el.dataset.id));
}
// Where an application in progress stands: what Hal is doing, or what it is waiting on.
function standing(j) {
  const a = state.last?.actions?.[j.id];
  if (a && a.state === "running") return `<span class="chip info">Hal: ${esc(a.message || a.name)}</span>`;
  const chip = { ready: ["good", "Ready for you to approve"], needs: ["warn", "Needs you"], stuck: ["bad", "Stuck"], working: ["", "Not finished"] }[j.state];
  return chip ? `<span class="chip ${chip[0]}" title="${esc(j.message || "")}">${chip[1]}</span>` : "Not started";
}

// One application --------------------------------------------------------------
const STEPS = [["documents", "Documents"], ["form", "Form and approval"], ["submitted", "Submitted"], ["next", "Interview and tests"]];
function currentStep(d) {
  const st = d.job.status, stage = d.application.stage;
  if (["assessment", "interview", "offer"].includes(st)) return "next";
  if (["applied", "rejected", "withdrawn"].includes(st) || stage === "submitted") return "submitted";
  return "form";
}
async function viewJob(id) {
  const d = await api("job/" + enc(id));
  state.job = d;
  const step = state.stepView || currentStep(d);
  const reached = STEPS.findIndex(s => s[0] === currentStep(d));
  const j = d.job;
  main.innerHTML = `
    <div class="head"><div>
      <a href="#/applications" class="muted small-t" style="text-decoration:none">&larr; Applications</a>
      <h1>${esc(j.title)}</h1>
      <p>${esc(j.company)} &middot; ${esc(j.location || "")} ${j.salary ? "&middot; " + esc(j.salary) : ""}
      <span class="chip ${STATUS_CHIP[j.status] || ""}">${esc(j.status)}</span></p></div>
      <div class="bar" style="margin:0"><a href="${esc(j.url)}" target="_blank" rel="noopener"><button class="ghost">Advert</button></a>
      ${j.status === "applying" ? `<button class="ghost" data-back>Back to queue</button>` : ""}</div></div>
    <div class="steps">${STEPS.map(([k, name], i) => `<div class="step ${k === step ? "on" : ""} ${i < reached ? "done" : ""}" data-step="${k}">
      <span class="n">${i < reached ? "&#10003;" : i + 1}</span>${name}</div>`).join("")}</div>
    <div id="action"></div>
    <div id="pane"></div>`;
  $$("[data-step]", main).forEach(el => el.onclick = () => { state.stepView = el.dataset.step; viewJob(id); });
  const back = $("[data-back]", main);
  back && (back.onclick = async () => { await guard(() => api(`job/${enc(id)}/restore`, {}), "Returned to the queue"); location.hash = "#/queue"; });
  paintAction(d.action);
  ({ documents: paneDocs, form: paneForm, submitted: paneAfter, next: paneAfter })[step](d);
}
function paintAction(a) {
  const el = $("#action");
  if (!el) return;
  if (!a) { el.innerHTML = ""; return; }
  const cls = a.state === "running" ? "" : a.state;
  el.innerHTML = `<div class="progress ${cls}">${a.state === "running" ? '<span class="spinner"></span>' : ""}
    <span><b>${esc(a.name)}</b>${a.message ? ": " + esc(a.message) : a.state === "running" ? "..." : ""}</span></div>`;
}

function paneDocs(d) {
  const docs = d.docs, s = d.summary, id = d.job.id;
  const tabs = [["letter", "Cover letter"], ["cv", "CV"], ["message", "Message"], ["answers", "Answers"]];
  const pane = $("#pane");
  pane.innerHTML = `
    ${s.flags.length ? `<div class="card pad" style="margin-bottom:16px"><h4 style="margin:0 0 8px" class="muted small-t">CHECK BEFORE SENDING</h4>
      <ul class="flags">${s.flags.map(f => `<li class="${flagClass(f)}">${esc(f)}</li>`).join("")}</ul></div>` : ""}
    ${!s.has_pack ? `<div class="progress error">No pack was drafted for this job; the documents are empty.</div>` : ""}
    <div class="split docs">
      <div class="card pad">
        <div class="tabs">${tabs.map(([k, n]) => `<button class="tab ${state.tab === k ? "on" : ""}" data-tab="${k}">${n}</button>`).join("")}</div>
        <div id="editor"></div>
        <div class="bar"><button data-reset class="ghost">Reset to Hal's draft</button><span class="grow"></span>
          <button class="primary" data-save>Save and re-render</button></div>
        <p class="small-t muted">Hal uploads these PDFs and pastes this text into the form. If you change them after the form is filled, press "Start again" on the form so it takes the new ones.</p>
      </div>
      <div class="card pad preview-card"><div class="tabs"><button class="tab on" id="prev-label">Preview</button><span class="grow"></span>
        <button class="small ghost" data-expand>Expand</button><a class="button small ghost" data-full target="_blank" rel="noopener">Open full size</a></div>
        <div id="preview"></div></div>
    </div>`;
  const editor = $("#editor");
  const draw = () => {
    $$("[data-tab]", pane).forEach(b => b.classList.toggle("on", b.dataset.tab === state.tab));
    if (state.tab === "letter") editor.innerHTML = `<textarea class="tall" data-k="letter">${esc(docs.letter)}</textarea>
      <p class="small-t muted">Plain text; blank lines separate paragraphs. Saved as the PDF on the right and pasted into any cover-letter box.</p>`;
    if (state.tab === "cv") {
      editor.innerHTML = `<p class="small-t muted" style="margin-top:0">${d.cv_edited || docs.cv
        ? `You have edited this application's CV, so changes to <a href="#/cv">your CV</a> no longer reach it.`
        : `Made by Hal from <a href="#/cv">your CV</a>: the most relevant projects and skills first, and a title line for this kind of role.
           Editing it here changes this application only.`}</p><div id="cv-builder"></div>
        ${docs.cv ? `<div class="bar"><button class="small ghost" data-cv-reset>Go back to Hal's version</button></div>` : ""}`;
      const draft = docs.cv || structuredClone(d.cv);
      cvEditor($("#cv-builder"), draft, { reorder: true, onChange: () => { docs.cv = draft; } });
      const reset = $("[data-cv-reset]", editor);
      reset && (reset.onclick = async () => {
        docs.cv = null; d.cv_edited = false;
        await save();
        const fresh = await api("job/" + enc(id));
        d.cv = fresh.cv; draw();
      });
    }
    if (state.tab === "message") editor.innerHTML = `<label class="f">To</label><input type="text" data-k="message_to" value="${esc(docs.message_to)}">
      <label class="f">Message</label><textarea class="tall" data-k="message">${esc(docs.message)}</textarea>
      <div class="bar"><button class="small" data-copy>Copy message</button></div>`;
    if (state.tab === "answers") editor.innerHTML = (docs.answers || []).map((a, i) => `<label class="f">${esc(a.question)}</label>
      <textarea data-ans="${i}">${esc(a.answer)}</textarea>`).join("") || `<p class="muted">No drafted answers.</p>`;
    $$("[data-k]", editor).forEach(el => el.oninput = () => { docs[el.dataset.k] = el.value; });
    $$("[data-ans]", editor).forEach(el => el.oninput = () => { docs.answers[+el.dataset.ans].answer = el.value; });
    const copy = $("[data-copy]", editor);
    copy && (copy.onclick = () => navigator.clipboard.writeText(docs.message).then(() => toast("Copied")));
    const file = state.tab === "cv" ? d.files.cv : d.files.letter;
    $("#prev-label").textContent = state.tab === "cv" ? "CV preview" : "Cover letter preview";
    $("#preview").innerHTML = file ? pdfPages(file) : `<div class="empty"><p>No PDF yet. Save to render it.</p></div>`;
    const full = $("[data-full]", pane);
    full.href = `/doc/${enc(id)}/${state.tab === "cv" ? "cv" : "letter"}`;
    full.hidden = $("[data-expand]", pane).hidden = !file;
    $("[data-expand]", pane).onclick = () => viewPdf(file);
  };
  $$("[data-tab]", pane).forEach(b => b.onclick = () => { state.tab = b.dataset.tab; draw(); });
  const save = async () => {
    const r = await guard(() => api(`job/${enc(id)}/docs`, { docs }), "Saved and re-rendered");
    d.files = r.files;
    draw();
  };
  $("[data-save]", pane).onclick = save;
  $("[data-reset]", pane).onclick = async () => {
    if (!await confirmBox("Reset to Hal's draft?", "Your edits to these documents will be lost.", "Reset")) return;
    const r = await guard(() => api(`job/${enc(id)}/docs-reset`, {}));
    Object.assign(docs, r.docs); draw();
  };
  draw();
}

// A document's pages drawn as images at the full width of their box, so they read at a usable size.
// Pages past the last fail to load and are dropped.
function pdfPages(file) {
  const t = Date.now();
  return `<div class="pages">${[1, 2, 3, 4].map(n => `<img src="/pages?path=${enc(file)}&page=${n}&t=${t}" alt="Page ${n}"
    onerror="this.remove()">`).join("")}</div>`;
}

// A document over the whole window; Escape or a click outside closes it.
function viewPdf(file) {
  const box = document.createElement("div");
  box.className = "viewer";
  box.innerHTML = pdfPages(file);
  const close = () => { box.remove(); document.removeEventListener("keydown", key); };
  const key = e => { if (e.key === "Escape") close(); };
  box.onclick = e => { if (e.target === box) close(); };
  document.addEventListener("keydown", key);
  document.body.append(box);
}

function fieldControl(f) {
  const v = f.value;
  const opts = f.options || [];
  if (f.kind === "file") return `<div class="small-t">${f.skip ? '<span class="muted">Left empty</span>' : esc(v ? v.split("/").pop() : "Nothing to upload")}</div>`;
  if (f.type === "password") return `<div class="small-t muted">Typed by you into the form</div>`;
  if (f.kind === "checkbox") return `<label class="toggle"><input type="checkbox" data-v ${v ? "checked" : ""}> ${v ? "Ticked" : "Not ticked"}</label>`;
  if (f.kind === "checkgroup") return `<div class="radios">${opts.map(o => `<label><input type="checkbox" data-multi value="${esc(o)}" ${(v || []).includes(o) ? "checked" : ""}> ${esc(o)}</label>`).join("")}</div>`;
  if ((f.kind === "radio" || f.kind === "select" || f.kind === "combobox") && opts.length)
    return `<select data-v><option value="">Leave empty</option>${opts.map(o => `<option ${o === v ? "selected" : ""}>${esc(o)}</option>`).join("")}</select>`;
  if (f.kind === "textarea" || (typeof v === "string" && v.length > 90))
    return `<textarea data-v>${esc(v || "")}</textarea>`;
  return `<input type="text" data-v value="${esc(v || "")}">`;
}
const SOURCE_CHIP = s => s.startsWith("profile") ? "info" : s.startsWith("documents") ? "good" : s === "hal" ? "warn" : s === "skipped" ? "" : "bad";

function paneForm(d) {
  const id = d.job.id, app = d.application, form = app.form;
  const pane = $("#pane");
  const running = d.action && d.action.state === "running";
  // Forms are driven in Hal's own browser window, or in a tab of yours through the extension.
  const st = state.last || {}, own = st.browser === "extension", where = st.where || "Hal's window";
  const offline = own && !st.extension ? `<div class="progress error">The Hal extension is not connected. Open Brave with the
    Hal Employment extension switched on; it links up within half a minute.</div>` : "";
  const controls = `<div class="bar"><button data-restart class="ghost" ${running ? "disabled" : ""}>Start again</button>
      <button data-here class="ghost" ${running ? "disabled" : ""} title="Use this after moving the form on yourself in ${esc(where)}">Carry on from this page</button>
      <button data-show>Show the form</button><span class="grow"></span><button class="ghost" data-manual>I applied myself</button></div>`;
  const bind = () => {
    $$("[data-restart]", pane).forEach(b => b.onclick = () => startAction(id, "auto", { here: false }));
    $$("[data-here]", pane).forEach(b => b.onclick = () => startAction(id, "auto", { here: true }));
    $$("[data-show]", pane).forEach(b => b.onclick = () => guard(() => api(`job/${enc(id)}/form-show`, {}), "Brought the form to the front"));
    $$("[data-manual]", pane).forEach(b => b.onclick = () => markApplied(id));
  };
  if (!form || (!form.fields.length && !form.filled)) {
    const stuck = form && form.state === "stuck";
    pane.innerHTML = `${offline}<div class="card pad">
      <h3 style="margin-top:0">${running ? "Hal is working on this application" : stuck ? "Hal could not reach the form" : "Let Hal fill in the application"}</h3>
      <p class="muted">Hal opens the form at <a href="${esc(d.apply_url)}" target="_blank" rel="noopener">${esc(d.apply_url.slice(0, 90))}</a>
      ${own ? "in a tab of your browser" : "in its own browser window"}, answers every question from your profile, your documents and the local model,
      and fills the form in. It then stops, with the filled form on screen, until you approve it. Nothing is submitted before that.</p>
      ${form && form.message && !running ? `<div class="progress ${stuck ? "error" : ""}">${esc(form.message)}</div>` : ""}
      ${controls}</div>`;
    bind();
    return;
  }
  const blank = f => (f.value === null || f.value === "" || f.value === false || (Array.isArray(f.value) && !f.value.length)) && !f.skip && f.type !== "password";
  const need = form.fields.filter(blank);
  const needReq = need.filter(f => f.required);
  const refused = form.fields.filter(f => f.error);
  const toRead = form.fields.filter(f => f.written || /unsure/.test(f.note || ""));
  const filled = app.stage === "filled" && form.filled;
  const does = form.press === "apply" ? "press Apply" : form.final ? "submit" : "continue";
  const tone = { ready: "done", needs: "", stuck: "error", working: "" }[form.state] ?? "";
  // What needs reading comes first: gaps, refusals, then anything Hal wrote in sentences.
  const rank = f => f.error ? 0 : blank(f) && f.required ? 1 : /unsure/.test(f.note || "") ? 2 : f.written ? 3 : 4;
  const ordered = form.fields.map((f, i) => [f, i]).sort((x, y) => rank(x[0]) - rank(y[0]) || x[1] - y[1]).map(x => x[0]);
  pane.innerHTML = `${offline}
    <div class="card pad" style="margin-bottom:16px">
      <div class="bar" style="margin:0">
        <div class="grow"><b>${form.step > 1 ? `Step ${form.step}: ` : ""}${esc(form.title || "Application form")}</b><div class="small-t muted">${esc(form.url)}</div></div>
        <span class="chip ${needReq.length ? "bad" : "good"}">${needReq.length ? needReq.length + " required left" : "All required answered"}</span>
        ${refused.length ? `<span class="chip bad">${refused.length} not accepted</span>` : ""}
        ${toRead.length ? `<span class="chip warn">${toRead.length} written by Hal to read</span>` : ""}
        ${form.captcha ? `<span class="chip warn" title="The site may ask you to pass a human check when the form is submitted">human check on page</span>` : ""}
      </div>
      ${form.message && !running ? `<div class="progress ${tone}" style="margin:12px 0 0">${esc(form.message)}</div>` : ""}
      ${form.stale ? `<div class="progress error" style="margin:12px 0 0">You changed the documents after this form was filled. Press "Start again" so the form takes the new ones.</div>` : ""}
      ${form.model ? `<p class="small-t muted" style="margin:10px 0 0">Hal's model could not run: ${esc(form.model)}.</p>` : ""}
    </div>
    <div class="split" style="grid-template-columns: minmax(0, 3fr) minmax(0, 2fr)">
      <div class="card pad"><div class="fields">${ordered.map(f => {
        const empty = need.includes(f);
        return `<div class="field ${f.error ? "err" : empty && f.required ? "need" : ""}" data-hid="${esc(f.hid)}">
          <div><div class="q">${esc(f.label || "(unlabelled " + f.kind + ")")}${f.required ? '<span class="req">*</span>' : ""}</div>
            <div class="meta"><span class="chip ${SOURCE_CHIP(f.source)}">${esc(f.source)}</span>${f.written ? ' <span class="chip warn">read this</span>' : ""}</div></div>
          <div>${fieldControl(f)}${f.note ? `<div class="note">${esc(f.note)}</div>` : ""}${f.help ? `<div class="note muted">${esc(f.help)}</div>` : ""}${f.error ? `<div class="error">The form says: ${esc(f.error)}</div>` : ""}</div></div>`;
      }).join("")}</div>
      ${(form.done || []).map(s => `<details class="section"><summary class="small-t muted">Step ${s.step}, already entered: ${esc(s.title)}</summary>
        <table>${s.fields.map(f => `<tr><td>${esc(f.label)}</td><td>${esc(f.kind === "file" ? String(f.value || "").split("/").pop() : Array.isArray(f.value) ? f.value.join(", ") : f.value === true ? "Ticked" : f.value ?? "")}</td></tr>`).join("")}</table></details>`).join("")}
      <div class="bar">${need.length ? `<button data-draft class="ghost" ${running ? "disabled" : ""}>Ask Hal to answer the rest</button>` : ""}
        <span class="grow"></span><button data-save>Save</button>
        <button data-fill ${running ? "disabled" : ""} title="Types your changes into the form without sending anything">Put my changes in the form</button></div></div>
      <div class="card pad">
        ${filled ? `<h4 class="muted small-t" style="margin:0 0 8px">THE FORM AS HAL FILLED IT, ${esc(ago(form.filled.at)).toUpperCase()}</h4>
          ${form.filled.screenshot ? `<div class="shot-wrap"><img class="shot" src="${fileUrl(form.filled.screenshot)}" alt="The filled form"></div>`
            : `<p class="muted">The form is in ${esc(where)}.</p>`}
          ${own && form.press !== "apply" ? `<div class="bar"><button class="primary" data-show>Show the form</button>
              ${Object.keys(d.files).map(k => `<a class="button ghost" href="/doc/${enc(id)}/${k}" target="_blank" rel="noopener">${k === "cv" ? "CV" : "Cover letter"}</a>`).join("")}</div>
            <p class="small-t muted">Check the outlined answers in the job's tab and change anything you like there. Then press
              ${form.final ? "Submit" : "Next"} on the form yourself. Hal fills each new page as it appears and marks the job applied
              when the site confirms it. Changes you make in the form are remembered for the next one.</p>`
          : `<div class="bar"><button class="primary" data-show>Show the form</button><span class="grow"></span>
            <button class="good" data-approve ${running ? "disabled" : ""}>Approve and ${does}</button></div>
          <p class="small-t muted">${form.press === "apply" ? `Approving presses Apply on the advert, which sends the CV stored in your account there.`
            : form.final ? `Look over the form in ${esc(where)}: it is filled in and nothing has been sent. Approving enters any changes you made here, then presses the form's own submit button. That cannot be undone.`
            : `This form has more pages. Approving moves it on; Hal stops again at the last page before anything is sent.`}</p>`}`
        : `<h4 class="muted small-t" style="margin:0 0 8px">${running ? "HAL IS FILLING THE FORM IN" : "NOT FILLED IN YET"}</h4>
          <p class="muted">${running ? "The answers on the left are going into the form now." : `Press "Start again" to have Hal fill the form in.`}</p>`}
        ${form.submit ? `<div class="section"><h4>After submitting</h4>
          <div class="progress ${form.submit.submitted ? "done" : "error"}">${form.submit.submitted ? "The site confirmed the application" : "No confirmation seen"}</div>
          ${form.submit.screenshot ? `<div class="shot-wrap"><img class="shot" src="${fileUrl(form.submit.screenshot)}" alt="The page after submitting"></div>` : ""}</div>` : ""}
        ${controls}
      </div></div>`;
  const collect = () => form.fields.map(f => {
    const row = $(`[data-hid="${CSS.escape(f.hid)}"]`, pane);
    let value = f.value, edited = false;
    const el = row && $("[data-v]", row);
    if (el) value = el.type === "checkbox" ? el.checked : el.value;
    if (row && $$("[data-multi]", row).length) value = $$("[data-multi]", row).filter(x => x.checked).map(x => x.value);
    if (JSON.stringify(value ?? "") !== JSON.stringify(f.value ?? "")) edited = true;
    return { hid: f.hid, value, skip: f.skip, edited };
  });
  const save = () => api(`job/${enc(id)}/form-save`, { fields: collect() });
  $("[data-save]", pane).onclick = () => guard(save, "Saved");
  const draft = $("[data-draft]", pane);
  draft && (draft.onclick = async () => { await save(); startAction(id, "form-draft", {}); });
  $("[data-fill]", pane).onclick = () => startAction(id, "form-fill", { fields: collect() });
  const approve = $("[data-approve]", pane);
  approve && (approve.onclick = async () => {
    const fields = collect();
    const left = fields.filter((f, i) => form.fields[i].required && !form.fields[i].skip && form.fields[i].type !== "password" && (f.value === "" || f.value === null || f.value === false || (Array.isArray(f.value) && !f.value.length)));
    const warn = left.length ? ` ${left.length} required question(s) have no answer here; unless you answered them in the form itself, the site will refuse it.` : "";
    const ask = form.final || form.press === "apply"
      ? [`Submit your application to ${d.job.company}?`, `Hal ${form.press === "apply" ? "presses Apply on the advert" : "presses the form's submit button"}. This sends the application and cannot be undone.${warn}`, "Submit application"]
      : ["Move on to the next page?", `Hal presses Next. Nothing is sent yet; it stops again before the application is submitted.${warn}`, "Continue"];
    if (!await confirmBox(ask[0], ask[1], ask[2], "good")) return;
    startAction(id, "approve", { fields });
  });
  bind();
}
async function startAction(id, action, body) {
  const a = await guard(() => api(`job/${enc(id)}/${action}`, body));
  if (state.last) state.last.actions[id] = a;
  paintAction(a);
  $$("#pane button").forEach(b => b.disabled = true);
}
async function markApplied(id) {
  if (!await confirmBox("Mark as applied?", "Use this when you sent the application yourself.", "Mark applied")) return;
  await guard(() => api(`job/${enc(id)}/applied`, { how: "submitted by hand" }), "Marked as applied");
  state.stepView = null; viewJob(id);
}

function paneAfter(d) {
  const id = d.job.id, app = d.application;
  const running = d.action && d.action.state === "running";
  const people = (app.contacts && app.contacts.people) || [];
  const statuses = ["applied", "assessment", "interview", "offer", "rejected", "withdrawn"];
  const pane = $("#pane");
  pane.innerHTML = `<div class="split">
    <div>
      <div class="card pad"><h4 class="muted small-t" style="margin:0 0 8px">STATUS</h4>
        <div class="bar" style="margin:0">${statuses.map(s => `<button class="small ${d.job.status === s ? "primary" : ""}" data-st="${s}">${s}</button>`).join("")}</div>
        <p class="small-t muted">Hal moves this along by itself when the employer emails; change it here if it gets it wrong.</p></div>
      <div class="card pad" style="margin-top:16px"><div class="bar" style="margin:0"><h4 class="muted small-t grow" style="margin:0">PREPARATION</h4>
        <button class="small" data-prep>${app.prep ? "Redraft" : "Draft preparation notes"}</button></div>
        <div class="md">${app.prep ? md(app.prep) : `<p class="muted">When an assessment or interview comes in, Hal drafts what to revise, likely questions,
          stories from your projects to tell, and questions to ask. You do the interview; Hal gets you ready for it.</p>`}</div></div>
    </div>
    <div>
      <div class="card pad"><h4 class="muted small-t" style="margin:0 0 8px">TO DO</h4>${d.todos.length ? d.todos.map(todoRow).join("") : `<p class="muted">Nothing for this job.</p>`}</div>
      <div class="card pad" style="margin-top:16px"><div class="bar" style="margin:0 0 8px"><h4 class="muted small-t grow" style="margin:0">MESSAGE TO THE HIRING MANAGER</h4>
        <button class="small" data-people ${running ? "disabled" : ""}>${app.contacts ? "Search again" : "Find who to message"}</button></div>
        ${people.map((p, i) => `<div class="person"><div><b>${esc(p.name)}</b> <span class="chip">${esc(p.source)}</span>
            ${p.sure ? "" : `<span class="chip warn" title="Hal is not sure this is the right person">check</span>`}</div>
          <div class="small-t muted">${esc([p.role, p.why].filter(Boolean).join(". "))}</div>
          <div class="bar" style="margin:4px 0 10px">${/^https:\/\//.test(p.url || "") ? `<a href="${esc(p.url)}" target="_blank" rel="noopener">Open profile</a>` : ""}
            ${p.email ? `<a href="mailto:${esc(p.email)}">${esc(p.email)}</a>` : ""}
            <span class="grow"></span><button class="small" data-copy-for="${i}">Copy message for ${esc(p.name.split(" ")[0])}</button></div></div>`).join("")}
        ${people.length ? "" : `<p class="small-t muted">${app.contacts ? "Hal found nobody to suggest." : "To: " + esc(d.docs.message_to)}</p>`}
        ${app.contacts && app.contacts.note ? `<p class="small-t muted">Note: ${esc(app.contacts.note)}.</p>` : ""}
        ${people.length ? `<p class="small-t muted">Found from the advert, your job inbox and public web search. Hal never opens LinkedIn; you send the message.</p>` : ""}
        <textarea readonly>${esc(d.docs.message)}</textarea>
        <div class="bar"><button class="small" data-copy>Copy</button></div></div>
      ${d.emails.length ? `<div class="card pad" style="margin-top:16px"><h4 class="muted small-t" style="margin:0 0 8px">EMAIL</h4>
        <ul class="timeline">${d.emails.map(e => `<li><b>${esc(e.kind)}</b> ${esc(e.date.slice(0, 10))} ${e.url ? `<a href="${esc(e.url)}" target="_blank" rel="noopener">${esc(e.subject)}</a>` : esc(e.subject)}</li>`).join("")}</ul></div>` : ""}
      <div class="card pad" style="margin-top:16px"><h4 class="muted small-t" style="margin:0 0 8px">HISTORY</h4>
        <div class="bar" style="margin:0 0 8px"><input type="text" id="note" placeholder="Add a note"><button class="small" data-note>Add</button></div>
        <ul class="timeline">${d.events.map(e => `<li><b>${esc(e.kind)}</b> ${esc(e.at.slice(0, 16).replace("T", " "))} ${esc(e.detail)}</li>`).join("")}</ul></div>
    </div></div>`;
  $$("[data-st]", pane).forEach(b => b.onclick = async () => {
    await guard(() => api(`job/${enc(id)}/status`, { status: b.dataset.st, note: "set in the web interface" }), "Status updated");
    viewJob(id);
  });
  $("[data-prep]", pane).onclick = () => startAction(id, "prep", {});
  $("[data-copy]", pane).onclick = () => navigator.clipboard.writeText(d.docs.message).then(() => toast("Copied"));
  $("[data-people]", pane).onclick = () => startAction(id, "contacts", {});
  $$("[data-copy-for]", pane).forEach(b => b.onclick = () => {
    const first = people[+b.dataset.copyFor].name.split(" ")[0];
    navigator.clipboard.writeText(d.docs.message.replace(/\[Hiring Manager(?:'s)? Name\]/i, first)).then(() => toast("Copied"));
  });
  $("[data-note]", pane).onclick = async () => {
    const text = $("#note").value.trim(); if (!text) return;
    await guard(() => api(`job/${enc(id)}/note`, { text })); viewJob(id);
  };
  bindTodos(pane, () => viewJob(id));
}

// Statistics -------------------------------------------------------------------
const dayMonth = iso => new Date(iso + "T12:00:00").toLocaleDateString("en-GB", { day: "numeric", month: "short" });

// Columns for applications sent each week. Only the peak and the current week carry
// a number; the rest are read from the axis, the hover readout or the table.
function weekChart(weeks) {
  const W = 720, H = 220, left = 28, right = 8, top = 18, bottom = 26;
  const peak = Math.max(...weeks.map(w => w.count));
  const step = Math.max(1, Math.ceil(peak / 4)), max = Math.max(step, Math.ceil(peak / step) * step);
  const band = (W - left - right) / weeks.length, bar = Math.min(18, band * 0.6), plot = H - top - bottom;
  const y = v => top + plot - (v / max) * plot;
  const ticks = Array.from({ length: max / step + 1 }, (_, i) => i * step);
  const labelled = new Set([weeks.length - 1, weeks.findIndex(w => w.count === peak)]);
  return `<svg class="chart" viewBox="0 0 ${W} ${H}" role="img" aria-label="Applications sent per week, last ${weeks.length} weeks">
    ${ticks.map(t => `<line class="grid" x1="${left}" x2="${W - right}" y1="${y(t)}" y2="${y(t)}"/>
      <text x="${left - 6}" y="${y(t) + 4}" text-anchor="end">${t}</text>`).join("")}
    ${weeks.map((w, i) => {
      const x = left + band * i + (band - bar) / 2, h = (w.count / max) * plot, r = Math.min(4, h);
      const mark = w.count ? `<path class="mark" d="M${x},${top + plot} v${-(h - r)} q0,${-r} ${r},${-r} h${bar - 2 * r} q${r},0 ${r},${r} v${h - r} z"/>` : "";
      return `<g><rect class="hit" tabindex="0" x="${left + band * i}" y="${top}" width="${band}" height="${plot}"
          data-tip="${w.count}|week of ${esc(dayMonth(w.start))}" aria-label="${w.count} sent in the week of ${esc(dayMonth(w.start))}"/>${mark}
        ${labelled.has(i) && w.count ? `<text class="value" x="${x + bar / 2}" y="${y(w.count) - 5}" text-anchor="middle">${w.count}</text>` : ""}
        <text x="${x + bar / 2}" y="${H - 8}" text-anchor="middle">${esc(dayMonth(w.start))}</text></g>`;
    }).join("")}</svg>`;
}

// CV builder -------------------------------------------------------------------
// Edits a CV in place. Values are bound by path ("projects.1.body"); skills are edited as a comma list.
// With `site`, each changed field offers the website's wording back; with `reorder`, projects and skills move.
const CV_LISTS = [["education", "Education", ["qualification", "grade", "meta", "detail"]],
                  ["experience", "Experience", ["role", "org", "dates", "body"]],
                  ["projects", "Projects", ["name", "stack", "body"]]];
const CV_LONG = new Set(["detail", "body"]);
function cvEditor(box, cv, { site = null, original = "the website's", reorder = false, onChange = () => {} } = {}) {
  const at = path => path.split(".").reduce((o, k) => o?.[k], cv);
  const put = (path, value) => {
    const keys = path.split("."), last = keys.pop();
    keys.reduce((o, k) => o[k], cv)[last] = value;
    onChange();
  };
  // The website's version of the same field, found by the entry's name rather than its place.
  const theirs = path => {
    if (!site) return undefined;
    const [section, index, field] = path.split(".");
    if (index === undefined) return site[section];
    const item = cv[section][+index];
    const match = Array.isArray(item) ? site[section].find(x => x[0] === item[0])
      : site[section].find(x => x._key === (item._key || item.name || item.qualification || item.role));
    return match ? match[field] : undefined;
  };
  const changed = path => {
    const was = theirs(path);
    return was !== undefined && JSON.stringify(was) !== JSON.stringify(at(path))
      ? `<button class="link small-t" data-revert="${path}">Changed · use ${original}</button>` : "";
  };
  const field = (path, label, long) => `<label class="f">${esc(label)} ${changed(path)}</label>` + (long
    ? `<textarea data-p="${path}" class="fit" rows="${Math.min(14, Math.max(2, Math.ceil(String(at(path) || "").length / 70)))}">${esc(at(path) || "")}</textarea>`
    : `<input type="text" data-p="${path}" value="${esc(at(path) || "")}">`);
  const mover = (section, i, n) => reorder ? `<button class="small ghost" data-move="${section}.${i}.-1" ${i ? "" : "disabled"} title="Move up">↑</button>
    <button class="small ghost" data-move="${section}.${i}.1" ${i < n - 1 ? "" : "disabled"} title="Move down">↓</button>` : "";
  const draw = () => {
    box.innerHTML = `<p class="small-t muted" style="margin:0">Words between ** and ** are printed in bold.</p>
      ${field("title", "Title line")}${field("summary", "Summary", true)}
      ${CV_LISTS.map(([section, name, fields]) => `<h4 class="cv-h">${name}</h4>${(cv[section] || []).map((item, i, all) => `
        <div class="cv-entry ${item.hidden ? "out" : ""}"><div class="cv-top"><b class="grow">${esc(item[fields[0]])}</b>${mover(section, i, all.length)}
          <label class="small-t"><input type="checkbox" data-hide="${section}.${i}" ${item.hidden ? "" : "checked"}> On the CV</label></div>
          ${item.hidden ? "" : fields.filter(f => f in item).map(f => field(`${section}.${i}.${f}`, f === "meta" ? "Place and dates" : f[0].toUpperCase() + f.slice(1), CV_LONG.has(f))).join("")}
        </div>`).join("")}`).join("")}
      ${field("moreProjects", "Further projects", true)}
      <h4 class="cv-h">Skills</h4>${(cv.skills || []).map((g, i, all) => `<div class="cv-skill"><div class="cv-top"><b class="grow">${esc(g[0])}</b>${mover("skills", i, all.length)}</div>
        <input type="text" data-skills="${i}" value="${esc(g[1].join(", "))}">${changed(`skills.${i}.1`)}</div>`).join("")}
      <h4 class="cv-h">Additional</h4>${(cv.additional || []).map((a, i) => field(`additional.${i}.1`, a[0], true)).join("")}
      ${field("closing", "Closing line")}`;
    $$("[data-p]", box).forEach(el => el.oninput = () => put(el.dataset.p, el.value));
    $$("[data-skills]", box).forEach(el => el.oninput = () =>
      put(`skills.${el.dataset.skills}.1`, el.value.split(",").map(x => x.trim()).filter(Boolean)));
    $$("[data-hide]", box).forEach(el => el.onchange = () => { put(el.dataset.hide + ".hidden", !el.checked); draw(); });
    $$("[data-revert]", box).forEach(el => el.onclick = e => { e.preventDefault(); put(el.dataset.revert, structuredClone(theirs(el.dataset.revert))); draw(); });
    $$("[data-move]", box).forEach(el => el.onclick = () => {
      const [section, i, by] = el.dataset.move.split(".").map((x, n) => n ? +x : x);
      const list = cv[section];
      [list[i], list[i + by]] = [list[i + by], list[i]];
      onChange(); draw();
    });
  };
  draw();
}

async function viewCv() {
  const d = await api("cv");
  const tab = state.cvTab || "edit";
  const from = d.origin === "built" ? "the CV Hal built from your material" : "your website's CV";
  main.innerHTML = `<div class="head"><div><h1>Your CV</h1>
      <p>Every application's CV is made from this one. For each job, Hal puts the most relevant projects and skills first and
      may change the title line; you can edit any one application's CV from its Documents step.</p></div></div>
    <div class="tabs">${[["edit", "Edit"], ["build", "Build from your material"]].map(([k, n]) =>
      `<button class="tab ${tab === k ? "on" : ""}" data-cvtab="${k}">${n}</button>`).join("")}</div>
    <div id="cv-pane"></div>`;
  $$("[data-cvtab]", main).forEach(b => b.onclick = () => { state.cvTab = b.dataset.cvtab; viewCv(); });
  (tab === "edit" ? cvEditTab : cvBuildTab)($("#cv-pane"), d, from);
}

function cvEditTab(pane, d, from) {
  const status = r => r.changes ? `${r.changes} change${r.changes > 1 ? "s" : ""} from ${from}` : `The same as ${from}`;
  pane.innerHTML = `<div class="split docs">
      <div class="card pad"><p class="small-t muted" style="margin-top:0">This starts from ${from}. What you change here goes only
        into Hal's applications${d.origin === "site" ? "; the website stays as it is" : ""}.</p><div id="builder"></div>
        <div class="bar"><span class="muted small-t grow" id="cv-status">${status(d)}</span>
          <button class="primary" data-save>Save and update applications</button></div></div>
      <div class="card pad preview-card"><div class="tabs"><button class="tab on">Preview</button><span class="grow"></span>
        <button class="small ghost" data-expand>Expand</button></div><div id="preview">${pdfPages(d.pdf)}</div></div>
    </div>`;
  cvEditor($("#builder"), d.cv, { site: d.site, original: d.origin === "built" ? "the built CV's" : "the website's" });
  $("[data-expand]", pane).onclick = () => viewPdf(d.pdf);
  $("[data-save]", pane).onclick = async () => {
    const r = await guard(() => api("cv", { cv: d.cv }), "Saved. Hal is updating the CVs of applications still to send.");
    $("#preview").innerHTML = pdfPages(r.pdf);
    $("#cv-status").textContent = status(r);
  };
}

const CV_OPTIONS = [["website", "Also read my website CV"], ["results", "Open entries with results, measured where my material measures them"],
                    ["two_pages", "Keep to two pages"], ["interests", "Include community work and interests"]];
const CV_FOCUS = { general: "Software engineering in general", backend: "Backend and distributed systems", ml: "Machine learning and applied AI", security: "Security engineering" };
function cvBuildTab(pane, d) {
  const o = d.options, b = d.built;
  const action = state.last?.actions?._cvbuild;
  const building = action && action.state === "running";
  pane.innerHTML = `<div class="split docs">
    <div class="card pad">
      <h3 style="margin-top:0">Your material</h3>
      <p class="small-t muted">Anything about you: old CVs, transcripts, project write-ups, references, notes. Text, PDF and Word files
        are read as plain text. Hal writes the CV from what these say and nothing else.</p>
      ${d.sources.length ? `<div class="sources">${d.sources.map(x => `<div class="source"><div class="grow"><b>${esc(x.name)}</b>
          <div class="small-t muted">${x.chars.toLocaleString()} characters · ${esc(x.preview.slice(0, 90))}</div></div>
          <button class="small ghost" data-remove="${esc(x.name)}">Remove</button></div>`).join("")}</div>`
        : `<p class="muted">Nothing added yet.</p>`}
      <label class="f">Name</label><input type="text" data-src-name placeholder="For example: transcript, dissertation summary, old CV">
      <label class="f">Text</label><textarea data-src-text rows="6" placeholder="Paste text here"></textarea>
      <div class="bar"><button data-add>Add text</button>
        <label class="button ghost">Upload files<input type="file" data-upload multiple accept=".txt,.md,.pdf,.docx,.json,.csv" hidden></label></div>
      <h3>Options</h3>
      ${CV_OPTIONS.map(([k, label]) => `<label class="check"><input type="checkbox" data-opt="${k}" ${o[k] ? "checked" : ""}> ${esc(label)}</label>`).join("")}
      <label class="f">Aim the title and summary at</label><select data-opt="focus">${Object.entries(CV_FOCUS).map(([k, n]) =>
        `<option value="${k}" ${o.focus === k ? "selected" : ""}>${esc(n)}</option>`).join("")}</select>
      <div class="opt-row"><label class="f">Projects on the CV<input type="number" data-opt="projects" min="1" max="6" value="${o.projects}"></label>
        <label class="f">Show module marks from<input type="number" data-opt="modules_min" min="0" max="100" value="${o.modules_min}"></label></div>
      <div class="bar"><span class="grow small-t muted" id="build-status">${building ? esc(action.message || "building") + "..." : ""}</span>
        <button class="primary" data-build ${building ? "disabled" : ""}>Build my CV</button></div>
    </div>
    <div class="card pad preview-card">
      ${b ? `<div class="tabs"><button class="tab on">Built ${esc(ago(b.at))}</button><span class="grow"></span>
          <button class="small ghost" data-expand>Expand</button></div>
        ${b.flags.length ? `<div class="flags-box"><b class="small-t">Check before using it</b><ul class="flags">${b.flags.map(f => `<li>${esc(f)}</li>`).join("")}</ul></div>` : ""}
        <div class="bar" style="margin:0 0 12px">${d.origin === "built"
          ? `<span class="chip good">In use as your CV</span><span class="grow"></span><button class="small ghost" data-origin="site">Go back to the website's CV</button>`
          : `<span class="grow small-t muted">Using it replaces the website's CV as the start of Hal's; the website stays as it is.</span>
             <button class="good" data-origin="built">Use this as my CV</button>`}</div>
        <div id="built-preview">${pdfPages(b.pdf)}</div>`
      : `<div class="empty"><h3>Nothing built yet</h3><p class="muted">Add your material, choose the options, then press "Build my CV".
          Building reads every source with the local model, so it takes a few minutes.</p></div>`}
    </div></div>`;
  const options = () => Object.fromEntries($$("[data-opt]", pane).map(el =>
    [el.dataset.opt, el.type === "checkbox" ? el.checked : el.type === "number" ? +el.value : el.value]));
  $$("[data-remove]", pane).forEach(el => el.onclick = async () => {
    if (!await confirmBox(`Remove ${el.dataset.remove}?`, "Hal will no longer build your CV from it.", "Remove")) return;
    await guard(() => api("cv/source/remove", { name: el.dataset.remove })); viewCv();
  });
  $("[data-add]", pane).onclick = async () => {
    const text = $("[data-src-text]", pane).value;
    await guard(() => api("cv/source", { name: $("[data-src-name]", pane).value || "notes", text }), "Added"); viewCv();
  };
  $("[data-upload]", pane).onchange = async e => {
    for (const file of e.target.files) {
      const data = await new Promise((ok, fail) => { const r = new FileReader(); r.onload = () => ok(r.result.split(",")[1]); r.onerror = fail; r.readAsDataURL(file); });
      await guard(() => api("cv/source", { name: file.name, data }), `Added ${file.name}`);
    }
    viewCv();
  };
  $("[data-build]", pane).onclick = async () => {
    const a = await guard(() => api("cv/build", { options: options() }));
    if (state.last) state.last.actions._cvbuild = a;
    $("[data-build]", pane).disabled = true;
    $("#build-status").textContent = "starting...";
    waitForBuild();
  };
  $$("[data-origin]", pane).forEach(el => el.onclick = async () => {
    const built = el.dataset.origin === "built";
    if (!await confirmBox(built ? "Use the built CV?" : "Go back to the website's CV?",
      built ? "Hal's CV will start from the built one. Edits you made to the website's CV in Hal are kept for if you switch back."
            : "Hal's CV will start from the website's again. Edits you made to the built CV are kept for if you switch back.",
      built ? "Use it" : "Go back")) return;
    await guard(() => api("cv/origin", { origin: el.dataset.origin }), "Done. Hal is updating the CVs of applications still to send.");
    viewCv();
  });
  const expand = $("[data-expand]", pane);
  expand && (expand.onclick = () => viewPdf(b.pdf));
  if (building) waitForBuild();
}
// Follows a CV build, which runs for minutes, until it ends.
async function waitForBuild() {
  if (state.cvWaiting) return;
  state.cvWaiting = true;
  try {
    for (;;) {
      await new Promise(r => setTimeout(r, 2000));
      const a = (await api("state")).actions._cvbuild;
      const el = $("#build-status");
      if (!a || a.state !== "running") {
        if (a) toast(a.state === "done" ? a.message : `The build stopped: ${a.message}`, a.state === "done" ? "" : "error");
        if (state.route === "cv") viewCv();
        return;
      }
      if (el) el.textContent = (a.message || "building") + "...";
    }
  } finally { state.cvWaiting = false; }
}

async function viewStats() {
  const s = await api("stats");
  const pct = s.total ? Math.round(100 * s.heard / s.total) : 0;
  const change = s.last7 - s.previous7;
  const tiles = [
    ["Sent today", s.today, s.today ? "applications" : "none yet today"],
    ["Sent, last 7 days", s.last7, `${change > 0 ? "+" : ""}${change} on the week before`],
    ["Sent in total", s.total, s.undated ? `${s.undated} without a sent date` : "all tracked"],
    ["Heard back", s.heard, `${pct}% of those sent`],
    ["Reply wait", s.reply_days === null ? "n/a" : `${Math.round(s.reply_days)} d`, "median to first reply"],
    ["Ready to apply", s.ready, `${s.applying} in progress`],
  ];
  const most = Math.max(1, ...s.standing.map(x => x.count));
  main.innerHTML = `<div class="head"><div><h1>Statistics</h1><p>What has gone out, what has come back, and what is still waiting.</p></div></div>
    <div class="tiles">${tiles.map(([k, v, d]) => `<div class="card tile"><div class="k">${esc(k)}</div><div class="v">${esc(v)}</div><div class="d">${esc(d)}</div></div>`).join("")}</div>
    <div class="card pad viz" id="weeks"><div class="bar" style="margin:0"><h3 class="grow">Applications sent per week</h3>
        <button class="small ghost" data-table>Show as table</button></div>
      <div data-chart>${weekChart(s.weeks)}</div>
      <table data-rows hidden><tr><th>Week starting</th><th class="num">Sent</th></tr>
        ${s.weeks.map(w => `<tr><td>${esc(dayMonth(w.start))}</td><td class="num">${w.count}</td></tr>`).join("")}</table></div>
    <div class="split" style="margin-top:16px">
      <div class="card pad"><h3 style="margin:0;font-size:14px">Where sent applications stand</h3>
        ${s.standing.length ? `<div class="hbars">${s.standing.map(x => `<div>${esc(x.label)}</div>
          <div class="track" title="${x.count} of ${s.total}"><div class="fill" style="width:${Math.round(78 * x.count / most)}%"></div><span class="n">${x.count}</span></div>`).join("")}</div>`
          : `<p class="muted">Nothing sent yet.</p>`}</div>
      <div class="card pad"><h3 style="margin:0 0 8px;font-size:14px">By source</h3>
        <table><tr><th>Source</th><th class="num">Sent</th><th class="num">Heard back</th><th class="num">Rejected</th></tr>
        ${s.sources.map(r => `<tr><td>${esc(r.source)}</td><td class="num">${r.sent}</td><td class="num">${r.heard}</td><td class="num">${r.rejected}</td></tr>`).join("")}</table></div>
    </div>
    <div class="card pad" style="margin-top:16px"><h3 style="margin:0 0 8px;font-size:14px">Waiting longest for a reply</h3>
      ${s.waiting.length ? `<table><tr><th>Company</th><th>Role</th><th class="num">Days</th></tr>
        ${s.waiting.map(w => `<tr><td><a href="#/job/${enc(w.id)}">${esc(w.company)}</a></td><td>${esc(w.title.slice(0, 90))}</td><td class="num">${w.days}</td></tr>`).join("")}</table>`
        : `<p class="muted">Nothing is waiting on a reply.</p>`}</div>`;
  const viz = $("#weeks"), tip = document.createElement("div");
  tip.className = "tip"; tip.hidden = true; viz.append(tip);
  $$(".hit", viz).forEach(hit => {
    const show = () => {
      const [count, week] = hit.dataset.tip.split("|"), box = hit.getBoundingClientRect(), frame = viz.getBoundingClientRect();
      tip.replaceChildren(Object.assign(document.createElement("b"), { textContent: count }), ` sent, ${week}`);
      tip.hidden = false;
      // Kept inside the card, so the readout for the newest week is not cut off.
      const half = tip.offsetWidth / 2, centre = box.left + box.width / 2 - frame.left;
      tip.style.left = `${Math.max(half + 8, Math.min(frame.width - half - 8, centre))}px`;
      tip.style.top = `${box.top - frame.top - 4}px`;
    };
    hit.onpointerenter = hit.onfocus = show;
    hit.onpointerleave = hit.onblur = () => { tip.hidden = true; };
  });
  $("[data-table]", viz).onclick = e => {
    const table = $("[data-rows]", viz).toggleAttribute("hidden");
    $("[data-chart]", viz).hidden = !table;
    e.target.textContent = table ? "Show as table" : "Show as chart";
  };
}

// To do ------------------------------------------------------------------------
function todoRow(t) {
  return `<div class="todo ${t.done ? "done" : ""} ${t.priority <= 1 && !t.done ? "urgent" : ""}" data-todo="${t.id}">
    <input type="checkbox" ${t.done ? "checked" : ""} aria-label="Done">
    <div class="body"><div class="t">${esc(t.title)}</div>
      <div class="small-t muted">${[t.detail && esc(t.detail), t.due && "due " + esc(t.due),
        t.job_id && state.route !== "job" ? `<a href="#/job/${enc(t.job_id)}">${esc(t.company || "job")}</a>` : "",
        t.url ? `<a href="${esc(t.url)}" target="_blank" rel="noopener">${t.kind === "message" ? "open profile" : "open email"}</a>` : ""].filter(Boolean).join(" &middot; ")}</div></div>
    <button class="ghost small x" data-del title="Delete">Delete</button></div>`;
}
function bindTodos(root, refresh) {
  $$("[data-todo]", root).forEach(el => {
    const id = el.dataset.todo;
    $("input", el).onchange = async e => { await guard(() => api(`todos/${id}/${e.target.checked ? "done" : "undo"}`, {})); refresh(); poll(); };
    $("[data-del]", el).onclick = async () => { await guard(() => api(`todos/${id}/delete`, {})); refresh(); poll(); };
  });
}
async function viewTodos() {
  const { todos } = await api("todos");
  const open = todos.filter(t => !t.done), done = todos.filter(t => t.done);
  main.innerHTML = `<div class="head"><div><h1>To do</h1><p>Invitations to tests and interviews go to the top as soon as the email arrives.</p></div></div>
    <form class="add" id="add"><input type="text" id="t-title" placeholder="Add a to-do" required><input type="date" id="t-due"><button class="primary">Add</button></form>
    <div class="card">${open.length ? open.map(todoRow).join("") : `<div class="empty"><h2>All clear</h2><p>Nothing waiting on you.</p></div>`}</div>
    ${done.length ? `<div class="section"><h4>Done recently</h4><div class="card">${done.map(todoRow).join("")}</div></div>` : ""}`;
  $("#add").onsubmit = async e => {
    e.preventDefault();
    await guard(() => api("todos", { title: $("#t-title").value, due: $("#t-due").value }));
    viewTodos(); poll();
  };
  bindTodos(main, viewTodos);
}

// Alerts -----------------------------------------------------------------------
async function viewAlerts() {
  const { alerts } = await api("alerts");
  main.innerHTML = `<div class="head"><div><h1>Alerts</h1><p>What Hal has noticed. Urgent ones also reach your phone and desktop.</p></div>
    <div class="bar" style="margin:0"><button data-notify class="ghost">Desktop notifications</button><button data-read>Mark all read</button></div></div>
    <div class="card">${alerts.length ? alerts.map(a => `<div class="alert ${esc(a.level)} ${a.read ? "" : "unread"}">
      <span class="lvl"></span><div style="flex:1"><div><b>${esc(a.title)}</b> <span class="small-t muted">${esc(ago(a.at))}</span></div>
      ${a.body ? `<div class="small-t muted" style="white-space:pre-line">${esc(a.body)}</div>` : ""}
      ${a.job_id ? `<a class="small-t" href="#/job/${enc(a.job_id)}">Open</a>` : ""}</div></div>`).join("")
      : `<div class="empty"><h2>No alerts yet</h2></div>`}</div>`;
  $("[data-read]").onclick = async () => { await guard(() => api("alerts/read", { all: true })); viewAlerts(); poll(); };
  $("[data-notify]").onclick = () => Notification.requestPermission().then(p => toast("Notifications " + p));
}

// Live state -------------------------------------------------------------------
async function poll() {
  let s;
  try { s = await api("state?since=" + (state.lastAlert ?? 0)); }
  catch { $("#worker-text").textContent = "Server unreachable"; $("#worker").className = "worker error"; return; }
  const prevActions = state.last?.actions || {};
  state.last = s;
  $("#b-queue").textContent = s.queue ? `${Math.min(s.queue, s.target)}/${s.target}` : "";
  $("#b-apps").textContent = s.applying || "";
  $("#b-todos").textContent = s.todos || "";
  $("#b-todos").classList.toggle("hot", s.urgent > 0);
  $("#b-alerts").textContent = s.unread || "";
  $("#b-alerts").classList.toggle("hot", s.unread > 0);
  const w = s.worker;
  $("#worker").className = "worker " + (w.activity === "working" ? "working" : w.activity === "error" ? "error" : "idle");
  $("#worker-text").textContent = w.detail || w.activity;
  $("#btn-pause").textContent = w.paused ? "Resume" : "Pause";
  const busy = w.activity === "working" || Object.values(s.actions).some(a => a.state === "running");
  $("#eye").classList.toggle("busy", busy);
  if (state.lastAlert !== null) {
    for (const a of s.new_alerts) {
      toast(a.title + (a.body ? ": " + a.body.split("\n")[0] : ""), a.level === "urgent" ? "urgent" : "");
      // An application the site has just confirmed changes the page being looked at.
      if (a.level === "good" && state.route === "job" && state.job && a.job_id === state.job.job.id) viewJob(a.job_id);
      if (document.hidden && "Notification" in window && Notification.permission === "granted" && a.level !== "info")
        new Notification(a.title, { body: a.body || "", tag: "hal-" + a.id });
    }
  }
  state.lastAlert = s.last_alert;
  // Refresh the open application when its background action finishes.
  if (state.route === "job" && state.job) {
    const id = state.job.job.id, now = s.actions[id], before = prevActions[id];
    if (now && now.state === "running") paintAction(now);
    if (now && before && before.state === "running" && now.state !== "running") {
      toast(now.name + ": " + now.message, now.state === "error" ? "error" : "");
      viewJob(id);
    }
  }
  const mail = s.actions._mail, mailBefore = prevActions._mail;
  if (mail && mailBefore && mailBefore.state === "running" && mail.state !== "running") toast(mail.message, mail.state === "error" ? "error" : "");
  if (state.route === "queue" && !$(".drawer") && $("#modal").hidden && state.queue.length < Math.min(s.queue, s.target)) viewQueue();
}

$("#btn-mail").onclick = () => guard(() => api("mail", {}), "Checking email...");
$("#btn-pause").onclick = () => guard(() => api("worker", { paused: !state.last?.worker?.paused })).then(poll);
$("#btn-theme").onclick = () => {
  const dark = document.documentElement.dataset.theme ? document.documentElement.dataset.theme === "dark"
    : matchMedia("(prefers-color-scheme: dark)").matches;
  document.documentElement.dataset.theme = dark ? "light" : "dark";
  try { localStorage.setItem("hal-theme", document.documentElement.dataset.theme); } catch {}
};
try { const t = localStorage.getItem("hal-theme"); if (t) document.documentElement.dataset.theme = t; } catch {}

route();
poll();
setInterval(poll, 4000);
