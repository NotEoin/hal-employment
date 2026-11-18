/* Bold, italic and links of a CV line on screen, read by the same rules as the server's `cvstudio/inline.py`
   (keep the two in step): a pair of stars counts only when it hugs words, three stars at most, no pair across
   a line break, `\*`, `\[` and `\]` are text, and only http, https and mailto addresses make links.
   `Marked` shows a line as the CV shows it, wherever a line's words reach the page outside the editor
   (suggestion cards, what Hal changed for a job, the changes between versions); `plainWords` gives its words.
   Every word is Preact text, never HTML, so nothing in a line can become markup. */
import { html } from "../core/html.js";

const STAR = "*";
const ESCAPABLE = "*[]";
const MAX_STARS = 3;          // bold and italic together, as `inline.MAX_STARS`
const SCHEMES = /^(https?:\/\/|mailto:)/i;
const WORD = /[\p{L}\p{N}]/u;
const SPACE = /\s/;
const GAP = /(\[[^\]]+\])/;     // Hal's gaps ("[how many?]"), as the cards have always marked them

/** The address when it is an http, https or mailto link with no spaces, else "". */
export function safeLink(url) {
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

function tokens(s, links) {
  const toks = [];
  let buf = "";
  const flush = () => { if (buf) { toks.push({ kind: "text", text: buf }); buf = ""; } };
  let i = 0;
  while (i < s.length) {
    const ch = s[i];
    if (ch === "\\" && i + 1 < s.length && ESCAPABLE.includes(s[i + 1])) { buf += s[i + 1]; i += 2; continue; }
    if (ch === STAR) {
      let j = i;
      while (j < s.length && s[j] === STAR) j++;
      const before = i ? s[i - 1] : "", after = j < s.length ? s[j] : "";
      let opens = false, closes = false;
      if (j - i <= MAX_STARS) {
        opens = !!after && !SPACE.test(after) && !WORD.test(before);
        closes = !!before && !SPACE.test(before) && !WORD.test(after);
      }
      if (opens || closes) { flush(); toks.push({ kind: "stars", text: s.slice(i, j), opens, closes }); }
      else buf += s.slice(i, j);
      i = j;
      continue;
    }
    if (ch === "[" && links) {
      const found = linkAt(s, i);
      if (found) { flush(); toks.push({ kind: "link", inner: lineRuns(found.words, false), url: found.url }); i = found.next; continue; }
    }
    buf += ch;
    i++;
  }
  flush();
  return toks;
}

function pairUp(toks) {
  toks.forEach((closer, j) => {
    if (closer.kind !== "stars" || !closer.closes) return;
    while (closer.text) {
      let k = j - 1;
      while (k >= 0 && !(toks[k].kind === "stars" && toks[k].opens && toks[k].text)) k--;
      if (k < 0) break;
      const opener = toks[k];
      const use = closer.text.length >= 2 && opener.text.length >= 2 ? 2 : 1;
      for (const t of toks.slice(k + 1, j)) {
        if (use === 2) t.bold = true; else t.italic = true;
        if (t.kind === "stars") t.opens = t.closes = false;
      }
      opener.text = opener.text.slice(0, -use);
      closer.text = closer.text.slice(use);
    }
  });
}

function merge(rs) {
  const out = [];
  for (const r of rs) {
    if (!r.text) continue;
    const last = out[out.length - 1];
    if (last && last.bold === r.bold && last.italic === r.italic && last.link === r.link) last.text += r.text;
    else out.push({ ...r });
  }
  return out;
}

function lineRuns(s, links = true) {
  const toks = tokens(s, links);
  pairUp(toks);
  const out = [];
  for (const t of toks) {
    if (t.kind === "link") out.push(...t.inner.map(r => ({ text: r.text, bold: r.bold || !!t.bold, italic: r.italic || !!t.italic, link: t.url })));
    else out.push({ text: t.text, bold: !!t.bold, italic: !!t.italic, link: "" });
  }
  return merge(out);
}

/** The line as runs of one style each: { text, bold, italic, link }; a line break is a plain run of its own. */
export function runs(text) {
  const out = [];
  String(text || "").split("\n").forEach((line, n) => {
    if (n) out.push({ text: "\n", bold: false, italic: false, link: "" });
    out.push(...lineRuns(line));
  });
  return merge(out);
}

/** The line's words without its marks; a link gives its words, never its address. */
export const plainWords = text => runs(text).map(r => r.text).join("");

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
