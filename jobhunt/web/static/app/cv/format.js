/* Bold, italic and links on a CV line, written as the marks the server keeps (`cvstudio/inline.py`):
   `**bold**`, `*italic*` and `[words](https://...)`. `FormatBox` is a line's text box with its Bold, Italic and
   Link buttons (and Ctrl B, Ctrl I), shown while the box or its buttons have focus, so a page of many lines
   is not a page of buttons.

   The marks go round whole words, with no spaces just inside them, because the server only counts a pair that
   hugs words: a selection is trimmed of spaces and widened to the words it touches. Pressing Bold on words
   that are already bold takes the marks away. After a change the same words stay selected. The buttons keep
   the focus in the text box when clicked, so the selection stays in view. */
import { html, useState } from "../core/html.js";
import { Icon } from "../ui/icons.js";

const MARKS = { bold: 2, italic: 1 };   // stars on each side
const WORD = /[\p{L}\p{N}]/u;
const SPACE = /\s/;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const SCHEME = /^(https?:\/\/|mailto:)/i;

/** The selection [start, end] trimmed of spaces and stars at its edges and widened to whole words. */
export function wordsAt(text, start, end) {
  while (start < end && (SPACE.test(text[start]) || text[start] === "*")) start++;
  while (end > start && (SPACE.test(text[end - 1]) || text[end - 1] === "*")) end--;
  while (start > 0 && WORD.test(text[start - 1])) start--;
  while (end < text.length && WORD.test(text[end])) end++;
  return [start, end];
}

function starsAround(text, start, end) {
  let before = 0, after = 0;
  while (start - before > 0 && text[start - before - 1] === "*") before++;
  while (end + after < text.length && text[end + after] === "*") after++;
  return Math.min(before, after);
}

/** The text with the words at [start, end] made bold or italic, or that taken away when they already are:
    { text, start, end }, where start and end select the same words afterwards. */
export function toggleMark(text, start, end, kind) {
  const n = MARKS[kind];
  [start, end] = wordsAt(text, start, end);
  const around = starsAround(text, start, end);
  const has = kind === "bold" ? around >= 2 : around === 1 || around === 3;
  if (has) {
    return { text: text.slice(0, start - n) + text.slice(start, end) + text.slice(end + n), start: start - n,
      end: end - n };
  }
  const mark = "*".repeat(n);
  return { text: text.slice(0, start) + mark + text.slice(start, end) + mark + text.slice(end), start: start + n,
    end: end + n };
}

/** The address as a link the CV keeps: https:// added to a bare address, mailto: to an email; "" when it
    cannot be one (it has spaces). */
export function linkAddress(typed) {
  const url = (typed || "").trim();
  if (!url || SPACE.test(url)) return "";
  if (SCHEME.test(url)) return url;
  return EMAIL.test(url) ? "mailto:" + url : "https://" + url;
}

/** The text with the words at [start, end] made a link to `url`: { text, start, end } as for toggleMark. */
export function addLink(text, start, end, url) {
  [start, end] = wordsAt(text, start, end);
  const words = text.slice(start, end) || url.replace(SCHEME, "");
  const out = text.slice(0, start) + "[" + words + "](" + url + ")" + text.slice(end);
  return { text: out, start: start + 1, end: start + 1 + words.length };
}

function selectSoon(id, start, end) {
  requestAnimationFrame(() => {
    const el = document.getElementById(id);
    if (el) { el.focus(); el.setSelectionRange(start, end); }
  });
}

/** A text box for one line of `name` (as the screen reader says it, "line 2 of Ticket clerk") with Bold,
    Italic and Link; `onText(text)` gets every change, typed or made with a button. */
export function FormatBox({ id, label, name, value, rows = 2, onText }) {
  const [linking, setLinking] = useState(null);   // the selection the Link form will act on
  const [address, setAddress] = useState("");
  const [problem, setProblem] = useState("");
  const box = () => document.getElementById(id);
  const apply = out => { onText(out.text); selectSoon(id, out.start, out.end); };
  const mark = kind => { const el = box(); if (el) apply(toggleMark(el.value, el.selectionStart, el.selectionEnd, kind)); };
  const keys = e => {
    if (!(e.ctrlKey || e.metaKey) || e.altKey || e.shiftKey) return;
    const k = e.key.toLowerCase();
    if (k === "b" || k === "i") { e.preventDefault(); mark(k === "b" ? "bold" : "italic"); }
  };
  const startLink = () => {
    const el = box();
    if (!el) return;
    setLinking([el.selectionStart, el.selectionEnd]); setAddress(""); setProblem("");
    requestAnimationFrame(() => { const a = document.getElementById(id + "-link"); if (a) a.focus(); });
  };
  const saveLink = () => {
    const url = linkAddress(address);
    if (!url) { setProblem("Type a web address with no spaces, such as example.org/my-work."); return; }
    const el = box();
    apply(addLink(el ? el.value : value, linking[0], linking[1], url));
    setLinking(null);
  };
  const keep = e => e.preventDefault();   // a click keeps the focus, and the selection, in the text box
  const linkKeys = e => {
    if (e.key === "Enter") { e.preventDefault(); saveLink(); }
    if (e.key === "Escape") { e.preventDefault(); e.stopPropagation(); setLinking(null); selectSoon(id, linking[0], linking[1]); }
  };
  return html`<div class=${"fmt" + (linking ? " fmt-open" : "")}>
    <textarea class="input" rows=${rows} id=${id} aria-label=${label} value=${value}
      onInput=${e => onText(e.target.value)} onKeyDown=${keys}></textarea>
    <div class="fmt-bar row tight" role="group" aria-label=${"Formatting for " + name}>
      <button type="button" class="btn quiet small" aria-label=${"Bold, " + name} title="Bold (Ctrl B)"
        onMouseDown=${keep} onClick=${() => mark("bold")}><b>Bold</b></button>
      <button type="button" class="btn quiet small" aria-label=${"Italic, " + name} title="Italic (Ctrl I)"
        onMouseDown=${keep} onClick=${() => mark("italic")}><i>Italic</i></button>
      <button type="button" class="btn quiet small" aria-label=${"Link, " + name} aria-expanded=${linking ? "true" : "false"}
        onMouseDown=${keep} onClick=${startLink}><${Icon} n="link" c="s" />Link</button>
      <span class="small sub fmt-hint">Select words first.</span></div>
    ${linking && html`<div class="fmt-link row tight">
      <label class="lab" for=${id + "-link"}>Link to</label>
      <input class="input grow" id=${id + "-link"} value=${address} placeholder="example.org/my-work" inputmode="url"
        onInput=${e => { setAddress(e.target.value); setProblem(""); }} onKeyDown=${linkKeys} />
      <button type="button" class="btn small" onClick=${saveLink}>Add link</button>
      <button type="button" class="btn quiet small" onClick=${() => { setLinking(null); selectSoon(id, linking[0], linking[1]); }}>Cancel</button>
      ${problem && html`<span class="small warn" role="alert">${problem}</span>`}</div>`}</div>`;
}
