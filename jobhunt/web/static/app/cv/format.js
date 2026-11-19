/* Bold, italic and links on a CV line, written as the marks the server keeps (`cvstudio/inline.py`):
   `**bold**`, `*italic*` and `[words](https://...)`. `FormatBox` is a line's text box with its Bold, Italic and
   Link buttons (and Ctrl B, Ctrl I), shown while the box or its buttons have focus, so a page of many lines
   is not a page of buttons.

   A button works on the line's words, not its characters: the line is read into words with their styles
   (cv/marks.js `chars`, the rules of `inline.py`), the selection is trimmed of spaces and widened to the words
   it touches, Bold makes them all bold (or takes it away when they all already are), and the whole line is
   written again in canonical form (`toText`), so marks already in the line never end up as stray stars.
   After a change the same words stay selected. The buttons keep
   the focus in the text box when clicked, so the selection stays in view. */
import { html, useState } from "../core/html.js";
import { Icon } from "../ui/icons.js";
import { chars, runsOf, safeLink, toText } from "./marks.js";

const WORD = /[\p{L}\p{N}]/u;
const SPACE = /\s/;
const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;
const SCHEME = /^(https?:\/\/|mailto:)/i;

/** The selection [start, end] of `words` trimmed of spaces at its edges and widened to whole words. */
export function wordsAt(words, start, end) {
  while (start < end && SPACE.test(words[start])) start++;
  while (end > start && SPACE.test(words[end - 1])) end--;
  while (start > 0 && WORD.test(words[start - 1])) start--;
  while (end < words.length && WORD.test(words[end])) end++;
  return [start, end];
}

/* A selection of the typed line as a range of its words' characters (`chars`): every character it touches. */
function wordRange(cs, start, end) {
  let from = cs.findIndex(c => c.to > start);
  if (from < 0) from = cs.length;
  const to = Math.max(from, cs.filter(c => c.from < end).length);
  return wordsAt(cs.map(c => c.ch).join(""), from, to);
}

/* The characters written back as a line in canonical form, with the words `from` to `to` selected in it. */
function written(cs, from, to) {
  const text = toText(runsOf(cs));
  const back = chars(text);
  return { text, start: from < back.length ? back[from].from : text.length,
    end: to > from && to <= back.length ? back[to - 1].to : text.length };
}

/** The line with the words at [start, end] made bold or italic, or that taken away when they all already are:
    { text, start, end }, where start and end select the same words afterwards. It works on the line's words
    and their styles, then writes the whole line again, so marks already there never end up as stray stars. */
export function toggleMark(text, start, end, kind) {
  const cs = chars(text);
  const [from, to] = wordRange(cs, start, end);
  if (from >= to) return { text, start, end };
  const on = !cs.slice(from, to).every(c => c[kind]);
  return written(cs.map((c, k) => (k >= from && k < to ? { ...c, [kind]: on } : c)), from, to);
}

/* True when every "(" in the address has its ")" after it, as in "en.example.org/wiki/Avery_(writer)". */
function balanced(url) {
  let depth = 0;
  for (const ch of url) {
    if (ch === "(") depth++;
    else if (ch === ")" && --depth < 0) return false;
  }
  return depth === 0;
}

/** The address as a link the CV keeps: https:// added to a bare address, mailto: to an email; "" when it
    cannot be one (it has spaces or control characters). Parentheses that do not pair are written as %28 and
    %29, the same address to a browser: a raw ")" would end the link early ("[w](https://example.org/a)b)"
    reads as a link to ".../a" and the words "b)"), and a lone "(" would leave the whole mark as text. */
export function linkAddress(typed) {
  const url = (typed || "").trim();
  if (!url || SPACE.test(url)) return "";
  const full = SCHEME.test(url) ? url : EMAIL.test(url) ? "mailto:" + url : "https://" + url;
  return safeLink(balanced(full) ? full : full.replace(/\(/g, "%28").replace(/\)/g, "%29"));
}

/** The line with the words at [start, end] made a link to `url` (with no words selected, the address's own
    words are added there): { text, start, end } as for toggleMark. */
export function addLink(text, start, end, url) {
  const cs = chars(text);
  const [from, to] = wordRange(cs, start, end);
  if (from < to) return written(cs.map((c, k) => (k >= from && k < to ? { ...c, link: url } : c)), from, to);
  const added = [...url.replace(SCHEME, "")].map(ch => ({ ch, bold: false, italic: false, link: url }));
  return written([...cs.slice(0, from), ...added, ...cs.slice(from)], from, from + added.length);
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
