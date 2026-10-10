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
const routes = { queue: viewQueue, applications: viewApplications, todos: viewTodos, alerts: viewAlerts, job: viewJob };
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
      <span class="kbd">A</span> apply, <span class="kbd">D</span> discard, <span class="kbd">Enter</span> details.</p></div></div>
    ${data.jobs.length ? `<div class="grid">${data.jobs.map(jobCard).join("")}</div>` :
      `<div class="card empty"><h2>The queue is empty</h2><p>${esc(w?.detail || "Hal is looking for more jobs.")}</p></div>`}`;
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
        <div class="c">${k === "applying" ? esc(stageName(j.stage)) : esc(ago(j.changed))}</div></div>`).join("") || `<p class="muted small-t">None</p>`}</div>`;
    }).join("")}</div>
    ${closed.length ? `<div class="section"><h4>Closed</h4><div class="card">${closed.map(j => `<div class="todo" data-id="${esc(j.id)}" style="cursor:pointer">
      <div class="body"><div class="t">${esc(j.company)}: ${esc(j.title)}</div></div><span class="chip ${STATUS_CHIP[j.status]}">${esc(j.status)}</span></div>`).join("")}</div></div>` : ""}`;
  $$("[data-id]", main).forEach(el => el.onclick = () => location.hash = "#/job/" + enc(el.dataset.id));
}
const stageName = s => ({ documents: "Reviewing documents", form: "Reviewing the form", filled: "Ready to submit", submitted: "Submitted" }[s] || "Not started");

// One application --------------------------------------------------------------
const STEPS = [["documents", "Documents"], ["form", "Application form"], ["submitted", "Submitted"], ["next", "Interview and tests"]];
function currentStep(d) {
  const st = d.job.status, stage = d.application.stage;
  if (["assessment", "interview", "offer"].includes(st)) return "next";
  if (["applied", "rejected", "withdrawn"].includes(st) || stage === "submitted") return "submitted";
  if (stage === "form" || stage === "filled") return "form";
  return "documents";
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
    <div class="split">
      <div class="card pad">
        <div class="tabs">${tabs.map(([k, n]) => `<button class="tab ${state.tab === k ? "on" : ""}" data-tab="${k}">${n}</button>`).join("")}</div>
        <div id="editor"></div>
        <div class="bar"><button data-reset class="ghost">Reset to Hal's draft</button><span class="grow"></span>
          <button data-save>Save</button><button class="primary" data-approve>Approve documents</button></div>
      </div>
      <div class="card pad"><div class="tabs"><button class="tab on" id="prev-label">Preview</button></div><div id="preview"></div></div>
    </div>`;
  const editor = $("#editor");
  const draw = () => {
    $$("[data-tab]", pane).forEach(b => b.classList.toggle("on", b.dataset.tab === state.tab));
    if (state.tab === "letter") editor.innerHTML = `<textarea class="tall" data-k="letter">${esc(docs.letter)}</textarea>
      <p class="small-t muted">Plain text; blank lines separate paragraphs. Saved as the PDF on the right and pasted into any cover-letter box.</p>`;
    if (state.tab === "cv") editor.innerHTML = `<label class="f">Title line</label><input type="text" data-k="cv_title" value="${esc(docs.cv_title)}">
      <label class="f">Summary</label><textarea data-k="cv_summary">${esc(docs.cv_summary)}</textarea>
      <p class="small-t muted">Projects and skills are ordered for this job automatically, using only what is in cv.json.</p>`;
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
    $("#preview").innerHTML = file ? `<iframe class="pdf" src="${fileUrl(file)}"></iframe>` :
      `<div class="empty"><p>No PDF yet. Save to render it.</p></div>`;
  };
  $$("[data-tab]", pane).forEach(b => b.onclick = () => { state.tab = b.dataset.tab; draw(); });
  const save = async approve => {
    const r = await guard(() => api(`job/${enc(id)}/docs`, { docs, approve }), approve ? "Documents approved" : "Saved and re-rendered");
    d.files = r.files;
    if (approve) { state.stepView = null; viewJob(id); } else draw();
  };
  $("[data-save]", pane).onclick = () => save(false);
  $("[data-approve]", pane).onclick = () => save(true);
  $("[data-reset]", pane).onclick = async () => {
    if (!await confirmBox("Reset to Hal's draft?", "Your edits to these documents will be lost.", "Reset")) return;
    const r = await guard(() => api(`job/${enc(id)}/docs-reset`, {}));
    Object.assign(docs, r.docs); draw();
  };
  draw();
}

function fieldControl(f) {
  const v = f.value;
  const opts = f.options || [];
  if (f.kind === "file") return `<div class="small-t">${f.skip ? '<span class="muted">Left empty</span>' : esc(v ? v.split("/").pop() : "Nothing to upload")}</div>`;
  if (f.type === "password") return `<div class="small-t muted">Typed by you in Hal's window</div>`;
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
  if (!form || (!form.fields.length && !form.filled)) {
    pane.innerHTML = `<div class="card pad">
      <h3 style="margin-top:0">Let Hal fill in the application</h3>
      <p class="muted">Hal opens the form at <a href="${esc(d.apply_url)}" target="_blank" rel="noopener">${esc(d.apply_url.slice(0, 90))}</a>
      in its own browser window, reads every question, and proposes an answer from your profile, these documents and the local model.
      You check everything here before anything is entered, and nothing is sent until you press Submit.</p>
      ${form && !form.fields.length ? `<div class="progress error">No form was found on ${esc(form.url)}. If the site needs a login or a few clicks,
        do that in Hal's browser window, then press "Read the page in Hal's window".</div>` : ""}
      <div class="bar"><button class="primary" data-open ${running ? "disabled" : ""}>Read the form</button>
      <button data-here ${running ? "disabled" : ""}>Read the page in Hal's window</button>
      <span class="grow"></span><button class="ghost" data-manual>I applied myself</button></div></div>`;
    $("[data-open]", pane).onclick = () => startAction(id, "form-open", { here: false });
    $("[data-here]", pane).onclick = () => startAction(id, "form-open", { here: true });
    $("[data-manual]", pane).onclick = () => markApplied(id);
    return;
  }
  const need = form.fields.filter(f => (f.value === null || f.value === "" || (Array.isArray(f.value) && !f.value.length)) && !f.skip);
  const needReq = need.filter(f => f.required);
  pane.innerHTML = `
    <div class="card pad" style="margin-bottom:16px">
      <div class="bar" style="margin:0">
        <div class="grow"><b>${form.step > 1 ? `Step ${form.step}: ` : ""}${esc(form.title || "Application form")}</b><div class="small-t muted">${esc(form.url)}</div></div>
        <span class="chip ${needReq.length ? "bad" : "good"}">${needReq.length ? needReq.length + " required left" : "All required answered"}</span>
        ${need.length - needReq.length ? `<span class="chip warn">${need.length - needReq.length} optional empty</span>` : ""}
        ${form.captcha ? `<span class="chip warn" title="The site may ask you to solve a captcha when you submit">captcha on page</span>` : ""}
      </div>
      ${form.model ? `<p class="small-t muted" style="margin:10px 0 0">Hal's model could not run: ${esc(form.model)}. It will answer the rest when you press "Ask Hal".</p>` : ""}
    </div>
    <div class="split" style="grid-template-columns: minmax(0, 3fr) minmax(0, 2fr)">
      <div class="card pad"><div class="fields">${form.fields.map(f => {
        const empty = need.includes(f);
        return `<div class="field ${f.error ? "err" : empty && f.required ? "need" : ""}" data-hid="${esc(f.hid)}">
          <div><div class="q">${esc(f.label || "(unlabelled " + f.kind + ")")}${f.required ? '<span class="req">*</span>' : ""}</div>
            <div class="meta"><span class="chip ${SOURCE_CHIP(f.source)}">${esc(f.source)}</span></div></div>
          <div>${fieldControl(f)}${f.note ? `<div class="note">${esc(f.note)}</div>` : ""}${f.error ? `<div class="error">Could not fill: ${esc(f.error)}</div>` : ""}</div></div>`;
      }).join("")}</div>
      <div class="bar"><button data-again class="ghost" ${running ? "disabled" : ""}>Read again</button>
        ${need.length ? `<button data-draft ${running ? "disabled" : ""}>Ask Hal to answer the rest</button>` : ""}
        <span class="grow"></span><button data-save>Save</button>
        <button class="primary" data-fill ${running ? "disabled" : ""}>Approve and fill</button></div></div>
      <div class="card pad">
        ${form.filled ? `<h4 class="muted small-t" style="margin:0 0 8px">FILLED FORM ${esc(ago(form.filled.at)).toUpperCase()}</h4>
          <div class="shot-wrap"><img class="shot" src="${fileUrl(form.filled.screenshot)}" alt="The filled form"></div>
          <div class="bar"><button data-show>Show browser window</button><span class="grow"></span>
            ${form.filled.next ? `<button class="primary" data-next ${running ? "disabled" : ""}>Next step</button>`
              : `<button class="good" data-submit ${running || app.stage !== "filled" ? "disabled" : ""}>Submit application</button>`}</div>
          <p class="small-t muted">${form.filled.next ? "This form has more steps. Hal moves on by itself when a step fills without problems."
            : `Submit presses the form's own submit button. Or submit it yourself in Hal's window and press "I applied myself".`}</p>`
        : `<h4 class="muted small-t" style="margin:0 0 8px">NEXT</h4><p class="muted">Check each answer, change anything that is not right,
          then press <b>Approve and fill</b>. Hal enters the values in the browser window and shows you a picture of the result here.</p>
          <div class="bar"><button data-show>Show browser window</button></div>`}
        ${form.submit ? `<div class="section"><h4>After submitting</h4>
          <div class="progress ${form.submit.submitted ? "done" : "error"}">${form.submit.submitted ? "The site confirmed the application" : "No confirmation seen"}</div>
          <div class="shot-wrap"><img class="shot" src="${fileUrl(form.submit.screenshot)}" alt="The page after submitting"></div></div>` : ""}
        <div class="bar"><span class="grow"></span><button class="ghost" data-manual>I applied myself</button></div>
      </div></div>`;
  const collect = () => form.fields.map(f => {
    const row = $(`[data-hid="${CSS.escape(f.hid)}"]`, pane);
    let value = f.value, edited = false;
    const el = row && $("[data-v]", row);
    if (el) value = el.type === "checkbox" ? el.checked : el.value;
    if (row && $$("[data-multi]", row).length) value = $$("[data-multi]", row).filter(x => x.checked).map(x => x.value);
    if (JSON.stringify(value) !== JSON.stringify(f.value)) edited = true;
    return { hid: f.hid, value, skip: f.skip, edited };
  });
  const save = () => api(`job/${enc(id)}/form-save`, { fields: collect() });
  $("[data-save]", pane).onclick = () => guard(save, "Saved");
  $("[data-again]", pane).onclick = () => startAction(id, "form-open", { here: true });
  const draft = $("[data-draft]", pane);
  draft && (draft.onclick = async () => { await save(); startAction(id, "form-draft", {}); });
  $("[data-fill]", pane).onclick = async () => {
    const left = collect().filter((f, i) => form.fields[i].required && !form.fields[i].skip && (f.value === "" || f.value === null || (Array.isArray(f.value) && !f.value.length)));
    if (left.length && !await confirmBox("Some required questions are empty", `${left.length} required field(s) have no answer. The site will likely refuse the form. Fill anyway?`, "Fill anyway")) return;
    await save(); startAction(id, "form-fill", {});
  };
  $$("[data-show]", pane).forEach(b => b.onclick = () => guard(() => api(`job/${enc(id)}/form-show`, {}), "Brought Hal's window to the front"));
  const next = $("[data-next]", pane);
  next && (next.onclick = () => startAction(id, "form-next", {}));
  const submit = $("[data-submit]", pane);
  submit && (submit.onclick = async () => {
    if (!await confirmBox(`Submit your application to ${d.job.company}?`, "Hal will press the form's submit button. This sends the application and cannot be undone.", "Submit application", "good")) return;
    startAction(id, "submit", {});
  });
  $$("[data-manual]", pane).forEach(b => b.onclick = () => markApplied(id));
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
      <div class="card pad" style="margin-top:16px"><h4 class="muted small-t" style="margin:0 0 8px">MESSAGE TO THE HIRING MANAGER</h4>
        <p class="small-t muted">To: ${esc(d.docs.message_to)}</p><textarea readonly>${esc(d.docs.message)}</textarea>
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
  $("[data-note]", pane).onclick = async () => {
    const text = $("#note").value.trim(); if (!text) return;
    await guard(() => api(`job/${enc(id)}/note`, { text })); viewJob(id);
  };
  bindTodos(pane, () => viewJob(id));
}

// To do ------------------------------------------------------------------------
function todoRow(t) {
  return `<div class="todo ${t.done ? "done" : ""} ${t.priority <= 1 && !t.done ? "urgent" : ""}" data-todo="${t.id}">
    <input type="checkbox" ${t.done ? "checked" : ""} aria-label="Done">
    <div class="body"><div class="t">${esc(t.title)}</div>
      <div class="small-t muted">${[t.detail && esc(t.detail), t.due && "due " + esc(t.due),
        t.job_id && state.route !== "job" ? `<a href="#/job/${enc(t.job_id)}">${esc(t.company || "job")}</a>` : "",
        t.url ? `<a href="${esc(t.url)}" target="_blank" rel="noopener">open email</a>` : ""].filter(Boolean).join(" &middot; ")}</div></div>
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
