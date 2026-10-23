/* "What kind of CV is this?" on My CV, Type and look: Hal's suggested type with its
   reason, every type as a card, comparing the CV under two types (no AI), copying a type and editing a copy
   as text. A mistake in a copy is shown with the line it is on, as the server reports it. The tips of the
   chosen type include what counts for paid internships and placement years. */
import { html, cx, useState } from "../core/html.js";
import { post, put } from "../core/api.js";
import { useApi, toast } from "../core/store.js";
import { Icon } from "../ui/icons.js";

const TYPES = "/cv/types";
const SOURCE_ROWS = 18;

function Outline({ o }) {
  return html`<div class="card soft" style="min-width:0"><b class="h3">${o.name}</b><p class="small sub" style="margin:4px 0 8px">${o.pages}</p>
    <ol class="small" style="margin:0;padding-left:18px">${o.sections.map(s => html`<li key=${s.kind + s.title}>${s.title}
      <span class="sub">${s.entries === 1 ? "1 entry" : s.entries + " entries"}${s.limit ? ", at most " + s.limit : ""}</span></li>`)}</ol>
    ${o.missing.length > 0 && html`<p class="small sub" style="margin:8px 0 0">Not on your CV yet: ${o.missing.join(", ")}.</p>`}</div>`;
}

function Compare({ types, a, onClose }) {
  const others = types.filter(t => t.id !== a);
  const [b, setB] = useState(others.length ? others[0].id : "");
  const { data, error } = useApi(b ? TYPES + "/compare?a=" + encodeURIComponent(a) + "&b=" + encodeURIComponent(b) : null);
  return html`<section class="card" style="margin-top:12px"><div class="row"><h3 class="h3" style="margin:0;flex:1">Your CV under two types</h3>
      <select class="input" style="width:auto" aria-label="Compare with" value=${b} onChange=${e => setB(e.target.value)}>
        ${others.map(t => html`<option value=${t.id}>${t.name}</option>`)}</select>
      <button class="btn quiet small" onClick=${onClose}>Close</button></div>
    <p class="small sub">A type changes the order, the section names and the limits. Your words stay the same.</p>
    ${error ? html`<p class="small">${error.message}</p>` : data ? html`<div class="grid2 even"><${Outline} o=${data.a} /><${Outline} o=${data.b} /></div>
      <div class="row" style="margin-top:8px"><a class="btn quiet small" href=${data.a_url} target="_blank" rel="noopener">Preview as ${data.a.name}</a>
      <a class="btn quiet small" href=${data.b_url} target="_blank" rel="noopener">Preview as ${data.b.name}</a></div>` : html`<p class="small sub">Laying out your CV...</p>`}</section>`;
}

function SourceEditor({ id, onDone }) {
  const { data } = useApi(TYPES + "/" + encodeURIComponent(id) + "/source");
  const [text, setText] = useState(null);
  const [problem, setProblem] = useState("");
  if (!data) return html`<p class="small sub">Opening the type...</p>`;
  const value = text === null ? data.toml : text;
  const save = async () => {
    try { await put(TYPES + "/" + encodeURIComponent(id) + "/source", { toml: value }); setProblem(""); toast("Saved your CV type"); onDone(); }
    catch (err) { setProblem(err.message); }
  };
  return html`<section class="card" style="margin-top:12px"><h3 class="h3" style="margin-top:0">Change ${data.name}</h3>
    <p class="small sub">Each line sets one thing: the order of sections, their names and limits, how Hal writes, and which checks count.</p>
    <textarea class="input mono" rows=${SOURCE_ROWS} aria-label=${"Settings of " + data.name} spellcheck="false"
      value=${value} onInput=${e => setText(e.target.value)}></textarea>
    ${problem && html`<div class="note warn" role="alert" style="margin-top:8px"><${Icon} n="warn" /><div>${problem}</div></div>`}
    <div class="row" style="margin-top:8px"><button class="btn primary small" onClick=${save}>Save</button>
      <button class="btn quiet small" onClick=${onDone}>Close</button></div></section>`;
}

/** The type picker. */
export function TypePicker() {
  const { data, error, reload } = useApi(TYPES);
  const [comparing, setComparing] = useState(false);
  const [editing, setEditing] = useState("");
  if (error) return html`<p class="small">${error.message}</p>`;
  if (!data) return html`<p class="sub">Loading CV types...</p>`;
  const current = data.types.find(t => t.id === data.current.id) || data.types[0];
  const choose = async t => {
    try {
      await put("/cv/type", { id: t.id });
      toast("CV type changed to " + t.name + ". Hal will suggest changes to match; nothing changes until you accept.");
      reload();
    } catch (err) { toast(err.message); }
  };
  const copy = async () => {
    try {
      const made = await post(TYPES + "/" + encodeURIComponent(current.id) + "/copy", {});
      await put("/cv/type", { id: made.id });
      toast("Made \"" + made.name + "\", a copy of this type you can change");
      await reload();
      setEditing(made.id);
    } catch (err) { toast(err.message); }
  };
  return html`<section>
    <div class="row"><h2 class="h2" style="margin:0">What kind of CV is this?</h2>
      <span class="sub small">The CV type sets what goes in, how Hal writes it and how Hal checks it. The look is separate, below.</span></div>
    <div class="note" style="margin-top:12px"><${Icon} n="spark" /><div><b>Hal suggests: ${data.suggested.name}.</b> ${data.suggested.why}
      <button class="btn quiet small" onClick=${() => setComparing(!comparing)}>Compare with another type</button></div></div>
    ${comparing && html`<${Compare} types=${data.types} a=${current.id} onClose=${() => setComparing(false)} />`}
    <div class="cols" style="margin-top:12px">${data.types.map(t => html`<button key=${t.id} class=${cx("choice", t.id === current.id && "on")}
        aria-pressed=${t.id === current.id ? "true" : "false"} onClick=${() => t.id !== current.id && choose(t)}>
      <b class="h3">${t.name}</b><span class="small sub">${t.fits}</span><span class="small"><b>Order:</b> ${t.order}</span>
      <span class="small"><b>Length:</b> ${t.pages}</span><span class="small sub">${t.note}</span></button>`)}</div>
    ${current.tips.length > 0 && html`<div class="card soft" style="margin-top:12px"><h3 class="h3" style="margin-top:0">Tips for ${current.name}</h3>
      <ul class="small" style="margin:0;padding-left:18px">${current.tips.map(tip => html`<li key=${tip}>${tip}</li>`)}</ul></div>`}
    <div class="row" style="margin-top:12px"><button class="btn small" onClick=${copy}><${Icon} n="copy" c="s" />Copy this type to change it</button>
      ${!current.builtin && html`<button class="btn quiet small" onClick=${() => setEditing(editing ? "" : current.id)}><${Icon} n="edit" c="s" />Change ${current.name}</button>`}</div>
    ${editing && html`<${SourceEditor} key=${editing} id=${editing} onDone=${() => { setEditing(""); reload(); }} />`}
  </section>`;
}
