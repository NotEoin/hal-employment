/* Small building blocks every page uses, as in the design: switches,
   segmented choices, tabs, page heads, the match score, point chips, chips, notes, empty states and bars.
   Each carries its ARIA role, so colour is never the only signal. */
import { html, cx, useRef } from "../core/html.js";
import { Icon } from "./icons.js";

const SCORE_STRONG = 80;  // bands as the scoring rules define them
const SCORE_GOOD = 65;
const METER_STEPS = 5;

/** An on/off switch with role="switch"; `label` names it for screen readers. */
export function Switch({ on, onChange, label, disabled }) {
  return html`<button type="button" class="switch" role="switch" aria-checked=${on ? "true" : "false"} aria-label=${label}
    disabled=${disabled} onClick=${() => !disabled && onChange(!on)}></button>`;
}

/** A segmented choice (role="radiogroup") over `options`, plain strings or [value, words] pairs. */
export function Seg({ value, options, onChange, label }) {
  const opts = options.map(o => (Array.isArray(o) ? o : [o, o]));
  return html`<div class="seg" role="radiogroup" aria-label=${label}>${opts.map(([v, words]) => html`<button type="button"
    role="radio" aria-checked=${value === v ? "true" : "false"} class=${value === v ? "on" : ""}
    onClick=${() => onChange(v)}>${words}</button>`)}</div>`;
}

/** The match score with its five-step meter and band words ("Match 84 out of 100: Strong"). */
export function Score({ score }) {
  const c = score >= SCORE_STRONG ? ["strong", "Strong"] : score >= SCORE_GOOD ? ["good", "Good"] : ["fair", "Worth a look"];
  const lit = Math.max(1, Math.round(score / (100 / METER_STEPS)));
  return html`<div class=${"score " + c[0]} role="img" aria-label=${"Match " + score + " out of 100: " + c[1]}><b>${score}</b>
    <div class="meter">${Array.from({ length: METER_STEPS }, (_, i) => html`<i class=${i < lit ? "on" : ""}></i>`)}</div>
    <span>${c[1]}</span></div>`;
}

/** One scoring point as a chip: [label, points]. */
export function PointChip({ p }) {
  return html`<span class=${cx("chip w", p[1] >= 0 ? "why" : "bad")}><span class="pt">${p[1] > 0 ? "+" : ""}${p[1]}</span> ${p[0]}</span>`;
}

/** A chip; `tone` is one of good, warn, bad, info, why, hal, line. */
export function Chip({ tone, wrap, children }) {
  return html`<span class=${cx("chip", tone, wrap && "w")}>${children}</span>`;
}

/** A page head: the h1 that gets focus on each route change, an eyebrow, a sub line and buttons. */
export function Head({ eyebrow, title, sub, children }) {
  return html`<div class="phead"><div>${eyebrow && html`<div class="eyebrow">${eyebrow}</div>`}<h1 class="h1" tabindex="-1">${title}</h1>
    ${sub && html`<p class="sub">${sub}</p>`}</div>${children && html`<div class="row">${children}</div>`}</div>`;
}

/** Tabs (role="tablist"): [id, words, count] each; Left and Right move between them. */
export function Tabs({ tabs, value, onChange, label }) {
  const ref = useRef(null);
  const onKey = e => {
    if (e.key !== "ArrowRight" && e.key !== "ArrowLeft" && e.key !== "Home" && e.key !== "End") return;
    e.preventDefault();
    const i = tabs.findIndex(t => t[0] === value);
    const n = tabs.length;
    const j = e.key === "Home" ? 0 : e.key === "End" ? n - 1 : (i + (e.key === "ArrowRight" ? 1 : -1) + n) % n;
    onChange(tabs[j][0]);
    const btns = ref.current && ref.current.querySelectorAll('[role="tab"]');
    if (btns && btns[j]) btns[j].focus();
  };
  return html`<div class="tabs" role="tablist" aria-label=${label} ref=${ref} onKeyDown=${onKey}>${tabs.map(t => html`<button
    type="button" role="tab" aria-selected=${value === t[0] ? "true" : "false"} tabindex=${value === t[0] ? "0" : "-1"}
    class=${cx("tab", value === t[0] && "on")} onClick=${() => onChange(t[0])}>${t[1]}${t[2] != null && t[2] !== ""
      && html`<span class="count">${t[2]}</span>`}</button>`)}</div>`;
}

/** A note box with an icon; `tone` is "warn" or "lock" for the other two looks. */
export function Note({ icon = "info", tone, children }) {
  return html`<div class=${cx("note", tone)}><${Icon} n=${icon} /><div>${children}</div></div>`;
}

/** The words shown where a list has nothing in it. */
export function Empty({ children }) {
  return html`<div class="empty">${children}</div>`;
}

/** A progress bar filled to `pct` (0 to 100); `tone` "warn" turns it amber. */
export function Bar({ pct, tone, label }) {
  const v = Math.max(0, Math.min(100, Math.round(pct || 0)));
  return html`<div class=${cx("bar", tone)} role="progressbar" aria-label=${label} aria-valuemin="0" aria-valuemax="100"
    aria-valuenow=${v}><i style=${"width:" + v + "%"}></i></div>`;
}
