"""Web pages of the Projects section: one page or online document (`page`) and a portfolio site (`portfolio`).

`PageReader` reads one page as a project: its title, a date, the medium and tools it names, the person's role
as the page states it, its pictures' alt text, and a summary written from the page's words only. A Google Doc
or Slides link is read through its export address, and any other link is fetched with `ingest.web`.

`PortfolioReader` reads a portfolio's index (a Behance, Dribbble or ArtStation profile, a personal site's
projects page, any page that lists project links) into candidate pieces, kept in the source's details. The
person picks which pieces to add (`meta.picked`, from the page's piece picker), and each picked piece becomes a
child source of kind `web_page`, read by `PageReader`.

Design decisions, each with its reason:

- Nothing is guessed about what the page is: the person added it to Projects, so it is a project, and the
  AI only summarises its words (sections plan, 2026-11-09). Tools and media are matched by name against a
  table (`NAMED`: the code tools of `sections.stack` and the media and programs of other fields), so a tool
  on a card is always a word the page says.
- A piece is a link on the same site that is deeper than the index or follows a portfolio site's own pattern
  for a piece (`PIECE_PATHS`); the site's menus, sign-in and social links are never pieces.
- A single page is fetched by itself, without a crawl, as `ingest.web` treats a single document: robots.txt
  rules a crawl, not a page the person pointed Hal at. Every request has `web.TIMEOUT_S` and a size cap.
- The page's ETag and a hash of its words are kept in its details, so the refresh can tell whether it changed
  without reading it again (`check`).
"""
from __future__ import annotations

import hashlib
import logging
import re
import urllib.error
import urllib.parse
from dataclasses import dataclass
from html.parser import HTMLParser

from jobhunt.ingest import web
from jobhunt.sections import readers, repo, stack

log = logging.getLogger(__name__)

PIECES_MAX = 60            # pieces offered from one index; a bigger list is a shop, not a portfolio
ALT_MAX = 20               # pictures' alt text kept from one page
ROLE_CHARS = 80            # a role is a few words; a longer match is a sentence
SUMMARY_CHARS = 600        # without the AI, the first paragraph stands in for a summary, cut to a few sentences
# Media and programs of fields beyond code, matched as whole words (case-insensitive) in a page's text.
MEDIA = ("Photoshop", "Illustrator", "InDesign", "After Effects", "Premiere Pro", "Lightroom", "Figma", "Sketch",
         "Blender", "Maya", "Cinema 4D", "ZBrush", "Substance Painter", "Unreal Engine", "Unity", "Procreate",
         "AutoCAD", "Revit", "SketchUp", "Rhino", "SolidWorks", "Fusion 360", "Final Cut Pro", "DaVinci Resolve",
         "Logic Pro", "Ableton Live", "Pro Tools", "Canva", "Excel", "Tableau", "Power BI", "SPSS", "Stata", "NVivo",
         "QGIS", "ArcGIS", "LaTeX", "WordPress", "Squarespace", "Webflow",
         "oil on canvas", "acrylic", "watercolour", "watercolor", "charcoal", "gouache", "ink", "linocut",
         "screen printing", "risograph", "ceramics", "textiles", "photography", "illustration", "animation",
         "typography", "3D modelling", "3D modeling", "video", "installation", "sculpture", "printmaking")
NAMED = tuple(dict.fromkeys([*sorted(set(stack.TOOLS.values()), key=str.lower), *MEDIA]))
ROLE = re.compile(r"^\s*(?:[-*]\s*)?(?:\*\*)?(?:my\s+role|role)(?:\*\*)?\s*[:\-]\s*(?:\*\*)?(.+?)\s*$", re.I | re.M)
# Paths of a single piece on the portfolio sites people use most.
PIECE_PATHS = re.compile(r"/(?:gallery/\d+|shots/\d+|artwork/[\w-]+|projects?/[\w-]+|work/[\w-]+|portfolio/[\w-]+)",
                         re.I)
NOT_PIECES = re.compile(r"/(?:about|contact|login|signin|sign-in|signup|join|search|tags?|category|categories|"
                        r"feed|rss|privacy|terms|cart|shop)(?:/|$)", re.I)


class _Html(HTMLParser):
    """A page's title, published date, pictures' alt text and links with their words."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.title = ""
        self.published = ""
        self.alts: list[str] = []
        self.links: list[tuple[str, str]] = []
        self._in_title = False
        self._href: str | None = None
        self._words: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        a = {k: v or "" for k, v in attrs}
        if tag == "title":
            self._in_title = True
        elif tag == "img" and a.get("alt", "").strip():
            alt = " ".join(a["alt"].split())
            if self._href is not None:
                self._words.append(alt)
            if alt not in self.alts and len(self.alts) < ALT_MAX:
                self.alts.append(alt)
        elif tag == "meta" and a.get("property", a.get("name", "")).lower() in ("article:published_time",
                                                                               "date", "dc.date"):
            self.published = self.published or a.get("content", "")
        elif tag == "time" and a.get("datetime"):
            self.published = self.published or a["datetime"]
        elif tag == "a" and a.get("href"):
            self._href, self._words = a["href"], []

    def handle_endtag(self, tag: str) -> None:
        if tag == "title":
            self._in_title = False
        elif tag == "a" and self._href is not None:
            self.links.append((self._href, " ".join(" ".join(self._words).split())))
            self._href = None

    def handle_data(self, data: str) -> None:
        if self._in_title:
            self.title += data
        elif self._href is not None:
            self._words.append(data)


@dataclass
class Fetched:
    """One page as fetched: where it ended up, its words as Markdown, what the HTML says, and its ETag."""

    url: str
    title: str
    text: str
    html: _Html
    etag: str = ""

    @property
    def hash(self) -> str:
        """A hash of the page's words, which says whether it changed when it has no ETag."""
        return hashlib.sha256(self.text.encode("utf-8")).hexdigest()


def fetch(url: str) -> Fetched:
    """One page or online document, read now; ValueError with plain words when it cannot be read."""
    try:
        url = web.canonical(web._with_scheme(url))
    except ValueError as e:
        raise ValueError(web.NOT_A_LINK) from e
    if web.export_url(url):
        page = web.fetch([url], page_cap=1)[0]
        if page.error:
            raise ValueError(page.error)
        return Fetched(page.url, page.title, page.text, _Html())
    try:
        r = web._open(url)
    except (urllib.error.URLError, TimeoutError, OSError) as e:
        raise ValueError(web._unreachable(url, e)) from e
    if r.status >= 400:
        raise ValueError(web._status_words(r.status))
    if r.type not in ("text/html", "application/xhtml+xml", "text/plain"):
        raise ValueError(web.NOT_A_PAGE)
    body = r.text()
    parsed = _Html()
    if r.type != "text/plain":
        parsed.feed(body)
        parsed.close()
    doc = web._read_html(body) if r.type != "text/plain" else None
    title = " ".join((doc.title if doc else parsed.title).split())
    return Fetched(web.canonical(r.url), title, doc.text if doc else body.strip(), parsed,
                   (r.headers.get("ETag") or "") if r.headers else "")


def check(url: str, etag: str, digest: str) -> tuple[bool, str, str]:
    """Whether a page changed since it was read with this ETag and hash: (changed, etag, hash) now. A page that
    cannot be fetched counts as unchanged, so a refresh offline never drops it."""
    try:
        got = fetch(url)
    except ValueError as e:
        log.info("could not check %s: %s", url, e)
        return False, etag, digest
    if etag and got.etag == etag:
        return False, etag, digest
    return got.hash != digest, got.etag, got.hash


def named(text: str) -> list[str]:
    """The tools and media `NAMED` that the text names as whole words, in the order of `NAMED`."""
    out = []
    for name in NAMED:
        if re.search(rf"(?<![\w.+#-]){re.escape(name)}(?![\w+#-])", text, re.I):
            out.append(name)
    return out


def _date(published: str, text: str) -> str:
    m = re.match(r"((?:19|20)\d\d)-(\d\d)", published or "")
    return f"{m.group(1)}-{m.group(2)}" if m else readers.date_in(text)


def _line_with(text: str, words: str) -> str:
    want = words.lower()
    return next((ln.strip() for ln in text.splitlines() if want in ln.lower()), words)


def page_reading(got: Fetched, title_given: str = "") -> readers.Reading:
    """A page as a project: its note text, summary, project and skill facts, and details for the refresh."""
    title = title_given or got.title or readers.title_in(got.text, got.url)
    role = ""
    if m := ROLE.search(got.text):
        role = m.group(1).strip(" *")[:ROLE_CHARS]
    tools = named(got.text + "\n" + "\n".join(got.html.alts))
    date = _date(got.html.published, got.text)
    summary, ai_facts = repo.ai_reading(title, [(got.url, got.text)])
    first = next((p.strip() for p in got.text.split("\n\n") if p.strip() and not p.lstrip().startswith("#")), "")
    summary = summary or first[:SUMMARY_CHARS]
    lines = [f"# {title}", "", f"- Link: {got.url}"]
    lines += [f"- Date: {date}"] if date else []
    lines += [f"- Role: {role}"] if role else []
    lines += [f"- Tools and media named: {', '.join(tools)}"] if tools else []
    if summary:
        lines += ["", summary]
    if got.html.alts:
        lines += ["", "## Pictures", ""] + [f"- {a}" for a in got.html.alts]
    lines += ["", "## The page", "", got.text.strip()]
    text = "\n".join(lines).strip() + "\n"

    def fact(fields: dict, field_name: str, quote: str, line: int | None = None) -> readers.Fact:
        return readers.Fact("project", {"name": title, **fields}, readers.Quote(field_name, quote, got.url, line))

    facts = [fact({"summary": summary} if summary else {}, "name", f"# {title}"), fact({"link": got.url}, "link",
                                                                                      f"- Link: {got.url}")]
    if role:
        facts.append(fact({"role": role}, "role", _line_with(got.text, role)))
    if tools:
        facts.append(fact({"tools": tools}, "tools", f"- Tools and media named: {', '.join(tools)}"))
    if date:
        facts.append(fact({"period": date}, "period", f"- Date: {date}"))
    facts += [fact({}, f["kind"], f["quote"], f["line"]) for f in ai_facts]
    facts += [readers.Fact("skill", {"name": t}, readers.Quote("name", _line_with(got.text, t), got.url))
              for t in tools if t.lower() in got.text.lower()]
    return readers.Reading(title=title, date=date, text=text, summary=summary, facts=facts,
                           details={"tools": tools, "etag": got.etag, "hash": got.hash, "last": date})


def pieces(got: Fetched, cap: int = PIECES_MAX) -> list[dict]:
    """The candidate pieces an index lists: [{"url", "title"}], same site, deeper than the index or shaped like a
    piece, no menus or sign-in pages, each once."""
    base = urllib.parse.urlsplit(got.url)
    folder = base.path.rstrip("/") + "/"
    out: dict[str, str] = {}
    for href, words in got.html.links:
        try:
            url = web.canonical(urllib.parse.urljoin(got.url, href))
        except ValueError:      # mailto:, javascript: and tel: links are not pieces
            continue
        p = urllib.parse.urlsplit(url)
        if p.netloc != base.netloc or url == got.url or NOT_PIECES.search(p.path) or web._asset(url):
            continue
        if not (p.path.startswith(folder) and p.path != folder) and not PIECE_PATHS.search(p.path):
            continue
        name = words or p.path.rstrip("/").rsplit("/", 1)[-1].replace("-", " ")
        if url not in out or (words and not out[url]):
            out[url] = name
        if len(out) >= cap:
            break
    return [{"url": u, "title": t} for u, t in out.items()]


class PageReader:
    """The `page` reader: one web page or online document as a project."""

    name = "page"

    def read(self, source: readers.Source) -> readers.Reading:
        """The page's reading (`page_reading`); ValueError with plain words when it cannot be fetched."""
        return page_reading(fetch(source.locator), source.title)


class PortfolioReader:
    """The `portfolio` reader: a portfolio's index, its pieces offered, and each piece picked as a child."""

    name = "portfolio"

    def read(self, source: readers.Source) -> readers.Reading:
        """The index's pieces in the note and details, and the picked ones as `web_page` children."""
        got = fetch(source.locator)
        found = pieces(got)
        picked = [str(u) for u in (source.meta or {}).get("picked") or []]
        title = source.title or got.title or readers.title_in(got.text, got.url)
        lines = [f"# {title}", "", f"{len(found)} pieces listed at {got.url}.", ""]
        lines += [f"- {p['title']}: {p['url']}" + (" (added)" if p["url"] in picked else "") for p in found]
        return readers.Reading(title=title, text="\n".join(lines) + "\n",
                               children=[readers.Child("web_page", "link", u) for u in picked],
                               details={"pieces": found, "etag": got.etag, "hash": got.hash})


readers.register(PageReader())
readers.register(PortfolioReader())
