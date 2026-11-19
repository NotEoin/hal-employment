/* Bold, italic and links of a CV line on screen, read by the same rules as the server's `cvstudio/inline.py`
   (keep the two in step): a pair of stars counts only when it hugs words, three stars at most, no pair across
   a line break, `\*`, `\[` and `\]` are text, and only http, https and mailto addresses make links.
   `Marked` shows a line as the CV shows it, wherever a line's words reach the page outside the editor
   (suggestion cards, what Hal changed for a job, the changes between versions); `plainWords` gives its words.
   `chars` and `toText` let the editor (cv/format.js) change the styles of words and write the line again, as
   `inline.runs` and `inline.to_text` do on the server.
   Every word is Preact text, never HTML, so nothing in a line can become markup. */
import { html } from "../core/html.js";

const STAR = "*";
const ESCAPABLE = "*[]";
const MAX_STARS = 3;          // bold and italic together, as `inline.MAX_STARS`
const SCHEMES = /^(https?:\/\/|mailto:)/i;
const WORD = /[\p{L}\p{N}]/u;
const SPACE = /\s/;
const CONTROL = /[\x00-\x1f\x7f]/;   // never in an address, as `inline.CONTROL`
const GAP = /(\[[^\]]+\])/;     // Hal's gaps ("[how many?]"), as the cards have always marked them

/** The address when it is an http, https or mailto link with no spaces or control characters, else "". */
export function safeLink(url) {
  if (CONTROL.test(url || "")) return "";
  const u = (url || "").trim();
  return u && !SPACE.test(u) && SCHEMES.test(u) ? u : "";
}

function closing(s, i, open, close) {
  let depth = 0;
  for (let k = i; k < s.length; k++) {
    if (s[k] === "\\" && ESCAPABLE.includes(s[k + 1] || "\n")) { k++; continue; }
    if (s[k] === open) depth++;
    else if (s[k] === close && --depth === 0) return k;
  }
  return -1;
}

function linkAt(s, i) {
  const end = closing(s, i, "[", "]");
  if (end < 0 || s[end + 1] !== "(") return null;
  const stop = closing(s, end + 1, "(", ")");
  if (stop < 0) return null;
  const words = s.slice(i + 1, end), raw = s.slice(end + 2, stop), url = safeLink(raw);
  if (!url || url !== raw || !words.trim()) return null;
  return { words, url, next: stop + 1 };
}

// Each character of the words keeps where it came from in the typed line (`from` to `to`), so the editor can
// turn a selection of words into a selection of the line and back.
function tokens(s, links, offset) {
  const toks = [];
  let buf = [];
  const flush = () => { if (buf.length) { toks.push({ kind: "text", chars: buf }); buf = []; } };
  const put = (ch, from, to) => buf.push({ ch, from: offset + from, to: offset + to });
  let i = 0;
  while (i < s.length) {
    const ch = s[i];
    if (ch === "\\" && i + 1 < s.length && ESCAPABLE.includes(s[i + 1])) { put(s[i + 1], i, i + 2); i += 2; continue; }
    if (ch === STAR) {
      let j = i;
      while (j < s.length && s[j] === STAR) j++;
      const before = i ? s[i - 1] : "", after = j < s.length ? s[j] : "";
      let opens = false, closes = false;
      if (j - i <= MAX_STARS) {
        opens = !!after && !SPACE.test(after) && !WORD.test(before);
        closes = !!before && !SPACE.test(before) && !WORD.test(after);
      }
      if (opens || closes) { flush(); toks.push({ kind: "stars", text: s.slice(i, j), at: offset + i, opens, closes }); }
      else for (let k = i; k < j; k++) put(STAR, k, k + 1);
      i = j;
      continue;
    }
    if (ch === "[" && links) {
      const found = linkAt(s, i);
      if (found) {
        flush();
        toks.push({ kind: "link", inner: lineChars(found.words, false, offset + i + 1), url: found.url });
        i = found.next;
        continue;
      }
    }
    put(ch, i, i + 1);
    i++;
  }
  flush();
  return toks;
}

// An opening and a closing star run pair when they have the same count or either is three stars (`inline._fits`).
const fits = (opener, closer) => opener.length === closer.length || opener.length === MAX_STARS || closer.length === MAX_STARS;

/* Pairs nest but never cross, as `inline._pair_up`: a closing run takes the nearest opening run that fits, and
   opening runs left inside it stay text. An opening run gives up its stars from its end, a closing run from its
   start, so the stars left over keep their places. */
function pairUp(toks) {
  toks.forEach((closer, j) => {
    if (closer.kind !== "stars" || !closer.closes) return;
    while (closer.text) {
      let k = j - 1;
      while (k >= 0 && !(toks[k].kind === "stars" && toks[k].opens && toks[k].text && fits(toks[k].text, closer.text))) k--;
      if (k < 0) break;
      const opener = toks[k];
      const use = closer.text.length >= 2 && opener.text.length >= 2 ? 2 : 1;
      for (const t of toks.slice(k + 1, j)) {
        if (use === 2) t.bold = true; else t.italic = true;
        if (t.kind === "stars") t.opens = t.closes = false;
      }
      opener.text = opener.text.slice(0, -use);
      closer.text = closer.text.slice(use);
      closer.at += use;
    }
  });
}

function lineChars(s, links, offset) {
  const toks = tokens(s, links, offset);
  pairUp(toks);
  const out = [];
  for (const t of toks) {
    const bold = !!t.bold, italic = !!t.italic;
    if (t.kind === "link") out.push(...t.inner.map(c => ({ ...c, bold: c.bold || bold, italic: c.italic || italic, link: t.url })));
    else if (t.kind === "stars") out.push(...[...t.text].map((ch, k) => ({ ch, from: t.at + k, to: t.at + k + 1, bold, italic, link: "" })));
    else out.push(...t.chars.map(c => ({ ...c, bold, italic, link: "" })));
  }
  return out;
}

/** Every character of the line's words with its style and the part of the typed line it came from:
    { ch, bold, italic, link, from, to }. */
export function chars(text) {
  const out = [];
  let offset = 0;
  String(text || "").split("\n").forEach((line, n) => {
    if (n) { out.push({ ch: "\n", bold: false, italic: false, link: "", from: offset - 1, to: offset }); }
    out.push(...lineChars(line, true, offset));
    offset += line.length + 1;
  });
  return out;
}

/** Characters (as `chars` gives them) joined into runs of one style each. */
export function runsOf(cs) {
  const out = [];
  for (const c of cs) {
    const last = out[out.length - 1];
    if (last && last.bold === c.bold && last.italic === c.italic && last.link === c.link) last.text += c.ch;
    else out.push({ text: c.ch, bold: c.bold, italic: c.italic, link: c.link });
  }
  return out;
}

/** The line as runs of one style each: { text, bold, italic, link }; a line break is a plain run of its own. */
export const runs = text => runsOf(chars(text));

/** The line's words without its marks; a link gives its words, never its address. */
export const plainWords = text => chars(text).map(c => c.ch).join("");

// ---------------------------------------------------------------------------------------- writing marks

/* Runs with the spaces at either end of a bold, italic or linked stretch moved outside it, as `inline._tidy`;
   a link's brackets end a bold or italic stretch too. */
function tidy(rs) {
  const cs = rs.flatMap(r => [...r.text].map(ch => ({ ch, bold: r.bold, italic: r.italic, link: r.link })));
  for (const [slot, off] of [["link", ""], ["bold", false], ["italic", false]]) {
    let k = 0;
    while (k < cs.length) {
      if (cs[k][slot] === off) { k++; continue; }
      let end = k;
      while (end < cs.length && cs[end][slot] === cs[k][slot] && cs[end].link === cs[k].link) end++;
      for (let m = k; m < end && SPACE.test(cs[m].ch); m++) cs[m][slot] = off;
      for (let m = end - 1; m >= k && SPACE.test(cs[m].ch); m--) cs[m][slot] = off;
      k = end;
    }
  }
  return runsOf(cs);
}

const escaped = (text, escape) => (escape ? [...text].map(ch => (ESCAPABLE.includes(ch) ? "\\" + ch : ch)).join("") : text);

function writeStyled(rs, escape) {
  const marks = { b: "**", i: "*" };
  const out = [], stack = [];
  // the style that lasts longer opens first, so it closes last: "***a** and **b***", not stray stars
  [...rs, { text: "", bold: false, italic: false }].forEach((r, k) => {
    const want = new Set([r.bold && "b", r.italic && "i"].filter(Boolean));
    let keep = stack.findIndex(x => !want.has(x));
    if (keep < 0) keep = stack.length;
    while (stack.length > keep) out.push(marks[stack.pop()]);
    const lasts = x => { let n = 0; while (n + k < rs.length && rs[n + k][x === "b" ? "bold" : "italic"]) n++; return n; };
    for (const x of ["b", "i"].sort((p, q) => lasts(q) - lasts(p))) {
      if (want.has(x) && !stack.includes(x)) { stack.push(x); out.push(marks[x]); }
    }
    out.push(escaped(r.text, escape));
  });
  return out.join("");
}

function write(rs, escape) {
  const out = [];
  let i = 0;
  while (i < rs.length) {
    let j = i;
    while (j < rs.length && rs[j].link === rs[i].link) j++;
    const words = writeStyled(rs.slice(i, j), escape);
    out.push(rs[i].link ? "[" + words + "](" + rs[i].link + ")" : words);
    i = j;
  }
  return out.join("");
}

const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);

/** Runs written as a line with marks that `runs` reads back the same, as `inline.to_text`: formatting that
    cannot be written that way (a style change inside a word) is dropped, never left as stray stars. */
export function toText(rs) {
  const want = tidy(rs);
  const tries = [want, want, tidy(want.map(r => ({ ...r, bold: false, italic: false }))),
    tidy(want.map(r => ({ ...r, bold: false, italic: false, link: "" })))];
  for (let n = 0; n < tries.length; n++) {
    const out = write(tries[n], n > 0);
    if (same(tidy(runs(out)), tries[n])) return out;
  }
  return escaped(want.map(r => r.text).join(""), true);
}

function words(text, gaps) {
  return gaps ? text.split(GAP).map(p => (GAP.test(p) ? html`<span class="ph">${p}</span>` : p)) : text;
}

/** A CV line as the CV shows it: bold, italic and links drawn, every word escaped; with `gaps`, Hal's gaps
    ("[how many?]") are marked too. A link opens in a new tab. */
export function Marked({ text, gaps = false }) {
  return runs(text).map(r => {
    let part = words(r.text, gaps);
    if (r.italic) part = html`<em>${part}</em>`;
    if (r.bold) part = html`<strong>${part}</strong>`;
    return r.link ? html`<a href=${r.link} target="_blank" rel="noopener noreferrer">${part}</a>` : part;
  });
}
