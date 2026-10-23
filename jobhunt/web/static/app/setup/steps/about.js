/* Setup guide step 3: About you.

   The fields come from GET /setup/about as they are saved now, marked "From your CV" where they match the
   CV Hal read. Each field saves a moment after the person stops typing, and only a field the person
   changed in this step is sent (with the value the step showed), so going through the step again never
   resets a value changed in Settings, and right to work keeps its saved value. Pay is read leniently and
   the step says what Hal understood ("Hal reads this as 22,000 pounds a year"). The private questions
   open the same write-only form as Settings. */
import { html, useState, useEffect, useRef } from "../../core/html.js";
import { get, put } from "../../core/api.js";
import { openDialog, toast } from "../../core/store.js";
import { Icon } from "../../ui/icons.js";

const SAVE_AFTER_MS = 600;    // a pause in typing; long enough not to save every letter
const PAY = "goal.min_pay";
const HELP = { [PAY]: "Type it however you like." };
const WIDE = { [PAY]: "width:140px" };

function Field({ f, value, words, onChange }) {
  const id = "about-" + f.key.replace(/\W/g, "-");
  const lab = html`<label class="lab" for=${id}>${f.label} ${f.from_cv && html`<span class="chip why">From your CV</span>`}</label>`;
  const control = f.choices
    ? html`<select id=${id} class="input" style="width:auto;max-width:100%" value=${value} onChange=${e => onChange(e.target.value, true)}>
        ${f.choices.map(([v, w]) => html`<option value=${v} selected=${v === value}>${w}</option>`)}</select>`
    : html`<input id=${id} class="input" style=${WIDE[f.key] || "width:280px;max-width:100%"} value=${value ?? ""}
        type=${f.kind === "email" ? "email" : f.kind === "phone" ? "tel" : "text"} onInput=${e => onChange(e.target.value, false)}
        onBlur=${e => onChange(e.target.value, true)} />`;
  return html`<div class="setting"><div>${lab}${HELP[f.key] && html`<div class="small sub">${HELP[f.key]}</div>`}</div>
    <div class="ctl">${control}${words && html`<span class="small" role="status">${words}</span>`}</div></div>`;
}

/** The step's body. */
export function Step() {
  const [fields, setFields] = useState(null);
  const [values, setValues] = useState({});
  const [words, setWords] = useState({});
  const [open, setOpen] = useState(false);
  const seen = useRef({});
  const timers = useRef({});
  const valuesRef = useRef(values);
  valuesRef.current = values;

  useEffect(() => {
    get("/setup/about").then(d => {
      const v = {};
      for (const f of d.fields) v[f.key] = f.key === PAY ? (f.value ? String(f.value) : "") : f.value;
      seen.current = { ...v };
      setValues(v);
      setWords(Object.fromEntries(d.fields.filter(f => f.display).map(f => [f.key, f.display])));
      setFields(d.fields);
    }).catch(err => toast(err.message));
    return () => Object.values(timers.current).forEach(clearTimeout);
  }, []);

  const save = async key => {
    const value = valuesRef.current[key];
    try {
      const r = await put("/setup/about/fields", { values: { [key]: value }, seen: { [key]: seen.current[key] } });
      const out = r.saved[key];
      if (!out) return;
      if (out.ok) seen.current[key] = value;
      if (key === PAY || !out.ok) setWords(w => ({ ...w, [key]: out.words }));
      else setWords(w => ({ ...w, [key]: "" }));
    } catch (err) { setWords(w => ({ ...w, [key]: err.message })); }
  };
  const change = (key, value, now) => {
    setValues(v => ({ ...v, [key]: value }));
    valuesRef.current = { ...valuesRef.current, [key]: value };
    clearTimeout(timers.current[key]);
    if (now) save(key);
    else timers.current[key] = setTimeout(() => save(key), SAVE_AFTER_MS);
  };

  const fromCv = fields && fields.some(f => f.from_cv);
  return html`<h1 class="h1">About you</h1>
    <p class="sub">${fromCv ? "Filled in from your CV. Check and correct anything." : "Check and correct anything. Hal uses these on every form."}</p>
    <div class="card about" style="margin-top:16px">${fields ? fields.map(f => html`<${Field} f=${f} value=${values[f.key]}
      words=${words[f.key]} onChange=${(v, now) => change(f.key, v, now)} />`) : html`<p class="loading">Loading...</p>`}</div>
    <section class="card soft" style="margin-top:16px"><button type="button" class="row" style="width:100%;background:none;border:0;padding:0;cursor:pointer;color:inherit;text-align:left"
        aria-expanded=${open ? "true" : "false"} onClick=${() => setOpen(!open)}><${Icon} n="lock" /><b style="flex:1">Private questions (optional)</b><${Icon} n=${open ? "chevU" : "chevD"} /></button>
      ${open && html`<p class="small" style="margin:10px 0">Many forms ask about disability, adjustments for interviews, and caring for someone. Employers must ask these for fairness monitoring, and "Prefer not to say" is always allowed. If you answer here, Hal types your answers into those questions only. They stay on this computer, are never sent to the AI, and Hal shows only "saved" afterwards.</p>
        <div class="row"><button type="button" class="btn small" onClick=${() => openDialog("private")}>Answer them</button>
          <span class="small sub">Or leave them: Hal will answer "Prefer not to say".</span></div>`}</section>`;
}
export default Step;
