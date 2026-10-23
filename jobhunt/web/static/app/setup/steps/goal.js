/* Setup guide step 4: What are you looking for?

   The quick questions start from the goal as it is now, so running the step again never wipes a change
   made in Settings. A career area is a one-press starting point for the roles. Each change asks POST
   /goal/quiz, which uses no AI and saves nothing, and shows "Here is what I understood"; the person
   corrects any line above it, and only "Yes, save this" writes the goal, as one change with its own
   version. The chat with Hal is not offered: it is not built yet. */
import { html, cx, useState, useEffect, useRef } from "../../core/html.js";
import { get, post } from "../../core/api.js";
import { toast, invalidate } from "../../core/store.js";
import { Icon } from "../../ui/icons.js";
import { Seg } from "../../ui/controls.js";

const STAGES = [["student", "Student"], ["new_grad", "New graduate"], ["early", "Early career (1 to 3 years)"],
  ["experienced", "Experienced"], ["changing", "Changing career"], ["school_leaver", "School leaver"]];
const STUDY = [["at_uni", "Yes, at university or college"], ["finished", "No, I have finished"],
  ["none", "I did not go to university"]];
const KINDS = [["graduate_job", "Graduate and entry-level jobs"], ["graduate_scheme", "Graduate schemes"],
  ["internship", "Paid internships"], ["placement", "Placement years"], ["insight", "Insight weeks and spring weeks"],
  ["apprenticeship", "Apprenticeships"], ["experienced", "Jobs that ask for experience"], ["part_time", "Part-time jobs"],
  ["fixed_term", "Fixed-term jobs"]];
const PATTERNS = [["office", "Office"], ["hybrid", "A mix"], ["remote", "Home"], ["any", "Any"]];
const ASK_AFTER_MS = 400;   // ask once typing pauses, not on every key

/** A row of on/off choice buttons over [value, words] pairs. */
function Picks({ label, options, chosen, onToggle }) {
  return html`<div class="ctl" role="group" aria-label=${label}>${options.map(([v, words]) => html`<button type="button"
    class=${cx("pick", chosen.includes(v) && "on")} aria-pressed=${chosen.includes(v) ? "true" : "false"}
    onClick=${() => onToggle(v)}>${words}</button>`)}</div>`;
}

const toggled = (list, v) => (list.includes(v) ? list.filter(x => x !== v) : [...list, v]);

/** The step's body. */
export function Step() {
  const [a, setA] = useState(null);
  const [starters, setStarters] = useState([]);
  const [newRole, setNewRole] = useState("");
  const [understood, setUnderstood] = useState(null);
  const [problem, setProblem] = useState(null);
  const [saved, setSaved] = useState(false);
  const first = useRef(null);
  useEffect(() => {
    get("/goal").then(g => {
      const v = g.values;
      setA({ stage: v["goal.stage"], study_status: v["goal.study_status"], study_end: v["goal.study_end"] || "",
        kinds: v["goal.kinds"] || [], roles: v["goal.roles"] || [], starter: "", places: (v["goal.places"] || []).join(", "),
        pattern: v["goal.pattern"], min_pay: v["goal.min_pay"] ? String(v["goal.min_pay"]) : "" });
    }).catch(err => toast(err.message));
    get("/goal/starters").then(setStarters).catch(() => setStarters([]));
  }, []);
  useEffect(() => {
    if (!a) return undefined;
    const t = setTimeout(async () => {
      try {
        const u = await post("/goal/quiz", { answers: a });
        setUnderstood(u); setProblem(null);
        // a new stage brings its own kinds of role: show them as chosen, so the picks match the card
        if (!a.kinds.length && u.understood.kinds.length) setA(cur => ({ ...cur, kinds: u.understood.kinds }));
      }
      catch (err) { setProblem({ field: err.field, text: err.message }); }
    }, ASK_AFTER_MS);
    return () => clearTimeout(t);
  }, [a]);
  if (!a) return html`<h1 class="h1">What are you looking for?</h1><p class="sub">Loading your goal...</p>`;
  const set = patch => { setA({ ...a, ...patch }); setSaved(false); };
  const start = s => set({ starter: a.starter === s.id ? "" : s.id, roles: a.starter === s.id ? a.roles : s.roles });
  const addRole = () => { const r = newRole.trim(); if (r && !a.roles.includes(r)) set({ roles: [...a.roles, r] }); setNewRole(""); };
  const save = async () => {
    try {
      await post("/goal/understood", { understood: understood.understood });
      invalidate("/goal"); invalidate("/home");
      setSaved(true);
      toast("Saved your goal. Hal searches with it from now on.");
    } catch (err) { setProblem({ field: err.field, text: err.message }); }
  };
  const err = f => problem && problem.field === f && html`<p class="small bad-text" role="alert">${problem.text}</p>`;
  return html`<h1 class="h1">What are you looking for?</h1>
    <p class="sub">Answer a few quick questions. Hal shows what it understood, and you check it before it is saved.</p>
    <div class="card" style="margin-top:16px">
      <div class="setting"><div class="lab">Career stage</div><div class="ctl">
        <select class="input" style="width:auto" aria-label="Career stage" value=${a.stage} ref=${first}
          onChange=${e => set({ stage: e.target.value, kinds: [] })}>${STAGES.map(([v, w]) => html`<option value=${v}>${w}</option>`)}</select></div></div>
      <div class="setting"><div class="lab">Are you studying now?</div><div class="ctl">
        <select class="input" style="width:auto" aria-label="Are you studying now?" value=${a.study_status}
          onChange=${e => set({ study_status: e.target.value })}>${STUDY.map(([v, w]) => html`<option value=${v}>${w}</option>`)}</select></div></div>
      ${a.study_status !== "none" && html`<div class="setting"><div><div class="lab">When your course ends or ended</div><div class="small sub">A year such as 2026, or a month such as 2026-06.</div></div>
        <div class="ctl"><input class="input" style="width:140px" aria-label="When your course ends or ended" value=${a.study_end}
          onInput=${e => set({ study_end: e.target.value })} />${err("study_end")}</div></div>`}
      <div class="setting"><div><div class="lab">Kinds of role</div><div class="small sub">Unpaid roles stay hidden unless you show them in Settings.</div></div>
        <${Picks} label="Kinds of role" options=${KINDS} chosen=${a.kinds} onToggle=${v => set({ kinds: toggled(a.kinds, v) })} />${err("kinds")}</div>
      ${starters.length > 0 && html`<div class="setting"><div><div class="lab">Start from a career area</div><div class="small sub">One press fills in example roles. Change them below.</div></div>
        <div class="ctl" role="group" aria-label="Career areas">${starters.map(s => html`<button type="button" class=${cx("pick", a.starter === s.id && "on")}
          aria-pressed=${a.starter === s.id ? "true" : "false"} onClick=${() => start(s)}>${s.name}</button>`)}</div></div>`}
      <div class="setting"><div class="lab">Roles you want</div><div class="ctl">
        ${a.roles.map(r => html`<button type="button" class="pick on" aria-pressed="true" aria-label=${"Remove " + r} onClick=${() => set({ roles: a.roles.filter(x => x !== r) })}>${r} <${Icon} n="x" c="s" /></button>`)}
        <input class="input" style="width:200px" placeholder="Add a role" aria-label="Add a role" value=${newRole}
          onInput=${e => setNewRole(e.target.value)} onKeyDown=${e => { if (e.key === "Enter") addRole(); }} />
        <button type="button" class="btn small" disabled=${!newRole.trim()} onClick=${addRole}>Add</button>${err("roles")}</div></div>
      <div class="setting"><div class="lab">Places</div><div class="ctl"><input class="input" style="width:260px;max-width:100%" aria-label="Places"
        placeholder="Your first choice first" value=${a.places} onInput=${e => set({ places: e.target.value })} />${err("places")}</div></div>
      <div class="setting"><div class="lab">Office or home</div><div class="ctl"><${Seg} label="Office or home" value=${a.pattern} options=${PATTERNS}
        onChange=${v => set({ pattern: v })} /></div></div>
      <div class="setting"><div><div class="lab">Lowest pay you would accept</div><div class="small sub">Type it however you like.</div></div>
        <div class="ctl"><input class="input" style="width:140px" aria-label="Lowest pay" value=${a.min_pay} onInput=${e => set({ min_pay: e.target.value })} />
          ${problem && problem.field === "min_pay" ? html`<span class="small" role="alert">Hal could not read that. Try 24000 or 24k.</span>`
            : understood && understood.understood.min_pay ? html`<span class="small">Hal reads this as ${new Intl.NumberFormat("en-GB", { style: "currency", currency: "GBP", maximumFractionDigits: 0 }).format(understood.understood.min_pay)} a year</span>` : ""}</div></div>
    </div>
    ${understood && html`<section class="card whyc" style="margin-top:18px" aria-live="polite"><h2 class="h3">Here is what I understood</h2>
      <dl class="kv" style="margin-top:10px">${understood.rows.map(([k, v]) => html`<dt>${k}</dt><dd>${v}</dd>`)}</dl>
      <div class="row" style="margin-top:12px">
        <button type="button" class="btn primary small" disabled=${saved || !!problem} onClick=${save}><${Icon} n="check" c="s" />${saved ? "Saved" : "Yes, save this"}</button>
        <button type="button" class="btn small" onClick=${() => first.current && first.current.focus()}><${Icon} n="edit" c="s" />Change something</button></div></section>`}`;
}
export default Step;
