"""CVs and cover letters as HTML pages, the one source for the in-app preview and the printed PDF.

`render_cv_html` turns a schema 2 document, its CV type and a look into a page with plain semantic markup
(`header`, `section`, `h2`, `ul`, a class per part). Looks differ only by their CSS (`looks/<id>/cv.css`),
except that a look whose `look.toml` says `columns = 2` puts the side sections in an `aside`, and that a
personal look (`personal_look`, made from checked measures by `measures.py`) also sets how an entry is
written (stacked, or one line), where its dates go and the contact line's separator. The type
decides order and titles when the page is drawn, so one version can be shown under two types without
changing it. Its entry and line limits are advice on the Checks tab (`types.advice`), never a cut: the page
shows every line and entry the person has not hidden, up to the person's own limit for a section. The name
and contact line come from the profile, never from the CV body. A contact link shows its short address
(`github.com/name`, as CVs print it) and links to the full one (`contact_parts`).

Fonts are linked by the relative address `fonts/<file>`: the printer copies `FONT_DIR` next to the page it
prints, and the preview route serves the same files under the preview's own address, so the preview and
the printed page are the same bytes. Every text value is escaped; the page holds no script, and its own
Content-Security-Policy allows only its inline style and its fonts.

A line's bold, italic and links (a bullet, an entry's paragraph, a section's text) are drawn by `inline.html`
as `strong`, `em` and `a`, so the printed PDF keeps real links; every other field is shown as typed, with its
stars. The fonts hold no italic face: the browser slants the regular one, which keeps the folder at four files.
"""
from __future__ import annotations

import functools
import html
import re
import tomllib
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jobhunt.cvstudio import inline, measures

LOOKS_DIR = Path(__file__).with_name("looks")
FONT_DIR = Path(__file__).resolve().parents[1] / "web" / "static" / "app" / "fonts" / "cv"
DEFAULT_LOOK = "classic"
SIDE_KINDS = ("skills", "languages", "interests", "certifications", "achievements")
# file stem -> (family, weights); latin and latin-ext files exist for each. Bold uses the 600 file, so the
# folder holds four faces rather than six.
FONTS = {"source-sans-3-{s}-400": ("Source Sans 3", "400"), "source-sans-3-{s}-600": ("Source Sans 3", "500 900"),
         "source-serif-4-{s}-400": ("Source Serif 4", "400"), "source-serif-4-{s}-600": ("Source Serif 4", "500 900")}
# the unicode ranges fontsource gives its latin and latin-ext subsets
RANGES = {"latin": "U+0000-00FF, U+0131, U+0152-0153, U+02BB-02BC, U+02C6, U+02DA, U+02DC, U+0304, U+0308, "
                   "U+0329, U+2000-206F, U+20AC, U+2122, U+2191, U+2193, U+2212, U+2215, U+FEFF, U+FFFD",
          "latin-ext": "U+0100-02BA, U+02BD-02C5, U+02C7-02CC, U+02CE-02D7, U+02DD-02FF, U+0304, U+0308, U+0329, "
                       "U+1D00-1DBF, U+1E00-1E9F, U+1EF2-1EFF, U+2020, U+20A0-20AB, U+20AD-20C0, U+2113, "
                       "U+2C60-2C7F, U+A720-A7FF"}
POLICY = "default-src 'none'; style-src 'unsafe-inline'; font-src 'self' file:; img-src 'none'"
LINK_ORDER = ("linkedin", "website", "github")
WEB_SCHEME = "https://"        # what a link typed as "github.com/name" in your details opens
HAS_SCHEME = re.compile(r"^[a-z][a-z0-9+.-]*:", re.I)          # RFC 3986 section 3.1
LONG_START = re.compile(r"^(?:https?://)?(?:www\.)?", re.I)     # what a CV leaves off a shown address
PERSONAL_ORDER = 1000           # personal looks come after every built-in in the gallery


class UnknownLook(LookupError):
    """No look has this id."""


@dataclass(frozen=True)
class Look:
    """One look: a built-in from `looks/<id>/look.toml`, or a personal one made from measures (`personal_look`).

    A built-in's style is its folder's CSS; a personal look carries its own (`css`, `letter_css`), written by
    `measures.css`. `entry_form`, `dates` and `sep` are the measures that change the markup, not only the style.
    """

    id: str
    name: str
    safe: bool
    note: str
    fonts: tuple[str, ...]
    columns: int
    order: int
    personal: bool = False
    css: str = ""
    letter_css: str = ""
    entry_form: str = "stacked"     # "stacked": heading, place, text on lines of their own; "line": one line
    dates: str = "right"            # "right": at the right of the heading; "inline": in the line, after a comma
    sep: str = " | "                # between the contact line's parts

    def public(self) -> dict:
        """What `GET /cv/looks` sends."""
        return {"id": self.id, "name": self.name, "safe": self.safe, "note": self.note}


def personal_look(look_id: str, name: str, m: measures.Measures, *, note: str = "") -> Look:
    """A look made from a person's measures; its style is written by code from them, never taken from a file."""
    m = measures.validate(m.to_dict())
    family = {"sans": "Source Sans 3", "serif": "Source Serif 4"}
    return Look(look_id, name, True, note or "Your own layout", tuple(dict.fromkeys((family[m.font],
                family[m.heading_font]))), 1, PERSONAL_ORDER, personal=True, css=measures.css(m),
                letter_css=measures.letter_css(m), entry_form=m.entry_form, dates=m.dates, sep=measures.separator(m))


@functools.cache
def looks() -> tuple[Look, ...]:
    """Every look, in gallery order."""
    found = []
    for path in sorted(LOOKS_DIR.glob("*/look.toml")):
        data = tomllib.loads(path.read_text(encoding="utf-8"))
        if data["id"] != path.parent.name:
            raise ValueError(f"{path}: id {data['id']!r} does not match its folder")
        found.append(Look(data["id"], data["name"], bool(data["safe"]), data["note"], tuple(data.get("fonts", ())),
                          int(data.get("columns", 1)), int(data.get("order", 100))))
    return tuple(sorted(found, key=lambda look: look.order))


def look(look_id: str | None) -> Look:
    """The look with this id; `UnknownLook` names a missing one."""
    for found in looks():
        if found.id == (look_id or DEFAULT_LOOK):
            return found
    raise UnknownLook(f"There is no look called {look_id!r}.")


@functools.cache
def _css(name: str) -> str:
    return (LOOKS_DIR / name).read_text(encoding="utf-8")


@functools.cache
def font_css() -> str:
    """The @font-face rules for the bundled CV fonts, by relative address."""
    rules = []
    for stem, (family, weight) in FONTS.items():
        for subset, ranges in RANGES.items():
            rules.append(f'@font-face {{ font-family: "{family}"; font-style: normal; font-weight: {weight}; '
                         f'src: url("fonts/{stem.format(s=subset)}.woff2") format("woff2"); '
                         f"unicode-range: {ranges}; }}")
    return "\n".join(rules)


def font_files() -> list[Path]:
    """The font files a printed page links to."""
    return sorted(FONT_DIR.glob("*.woff2"))


# ----------------------------------------------------------------------------------------------- profile

def _p(profile: dict, key: str) -> str:
    """A profile value, given as `name` or as the setting key `you.name`."""
    value = profile.get(key, profile.get(f"you.{key}", ""))
    return str(value or "").strip()


def profile_from_settings() -> dict:
    """The person's name and contact details from the `you.*` settings, as the render functions take them."""
    from jobhunt.core import settings

    def get(key: str) -> str:
        try:
            return str(settings.get(key) or "")
        except KeyError:
            return ""

    return {"name": get("you.name"), "email": get("you.email"), "phone": get("you.phone"),
            "location": get("you.city"), "links": {k: get(f"you.links.{k}") for k in LINK_ORDER}}


def short_address(url: str) -> str:
    """A web address as a contact line shows it: no scheme, no leading `www.`, no closing slash."""
    return LONG_START.sub("", url.strip()).rstrip("/")


def _link_part(url: str) -> str:
    """One contact link as a line with marks: its short address, linked to the full one when it is a web
    address (an address typed without a scheme links to `WEB_SCHEME`); otherwise the words as typed."""
    url = url.strip()
    href = inline.safe_link(url if HAS_SCHEME.match(url) else WEB_SCHEME + url)
    if not href or href.lower().startswith("mailto:"):
        return inline.to_text([inline.Run(url)])
    return inline.to_text([inline.Run(short_address(url), link=href)])


def contact_parts(profile: dict) -> list[str]:
    """The contact line's parts, in order: email, phone, place, then links.

    Each part is a line with marks (`inline`): a link shows its short address and links to the full one, so
    the preview, the printed PDF and the Word file all keep a working link; other parts are plain words.
    """
    links = profile.get("links") or {}
    parts = [_p(profile, "email"), _p(profile, "phone"), _p(profile, "location") or _p(profile, "city")]
    out = [inline.to_text([inline.Run(x)]) for x in parts if x]
    return out + [_link_part(str(links.get(k) or "")) for k in LINK_ORDER if str(links.get(k) or "").strip()]


# ----------------------------------------------------------------------------------------------- the view

@dataclass
class ViewEntry:
    """One entry as it is shown: plain strings, already cut to the type's limits."""

    id: str
    heading: str
    dates: str
    org: str
    meta: str
    bullets: list[tuple[str, str]]     # (bullet id, text)
    text: str
    items: str
    modules: str


@dataclass
class ViewSection:
    """One section as it is shown."""

    id: str
    kind: str
    title: str
    text: str
    entries: list[ViewEntry]
    also: str


@dataclass
class View:
    """What a CV shows, in order, for one type: shared by the HTML page and Hal's simple printer."""

    name: str
    headline: str
    contact: list[str]                 # lines with marks (`contact_parts`)
    sections: list[ViewSection]


def _type_part(type_: Any, kind: str) -> dict:
    sections = getattr(type_, "sections", None) or {}
    part = sections.get(kind) or {}
    return part if isinstance(part, dict) else {}


def _int(value: Any) -> int | None:
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _mark(value: str) -> float | None:
    try:
        return float(str(value).strip().rstrip("%"))
    except ValueError:
        return None


def _modules(entry: Any, part: dict) -> str:
    """The module line the type allows: all, the strong ones from its `min_mark`, or none."""
    show = part.get("show_modules", "strong")
    modules = list(getattr(entry, "modules", None) or [])
    if show == "none" or not modules:
        return ""
    floor = entry.show_marks_from if entry.show_marks_from is not None else _int(part.get("min_mark"))
    if show == "strong" and floor is not None:
        modules = [m for m in modules if (_mark(m.mark) or 0) >= floor or not m.mark]
        modules.sort(key=lambda m: -(_mark(m.mark) or 0))
    return "; ".join(f"{m.name} {m.mark}".strip() for m in modules)


def _dates(entry: Any) -> str:
    start, end = (entry.start or "").strip(), (entry.end or "").strip()
    return f"{start} - {end}" if start and end else start or end


def _order(sections: list[Any], type_: Any, layout: dict | None) -> list[Any]:
    """The person's own order when they set one in Sections, else the type's, else the document's."""
    chosen = [s.get("id") for s in (layout or {}).get("sections") or [] if s.get("id")]
    if chosen:
        rank = {sid: i for i, sid in enumerate(chosen)}
        return sorted(sections, key=lambda s: rank.get(s.id, len(rank)))
    order = tuple(getattr(type_, "section_order", ()) or ())
    rank = {kind: i for i, kind in enumerate(order)}
    return sorted(sections, key=lambda s: rank.get(s.kind, len(rank)))


def view(doc: Any, type_: Any, profile: dict, *, layout: dict | None = None) -> View:
    """What `doc` shows under `type_`: visible sections in order, with their titles and the person's own
    entry limits (Sections); the type's limits cut nothing (see the module notes)."""
    own = {s.get("id"): s for s in (layout or {}).get("sections") or [] if s.get("id")}
    out: list[ViewSection] = []
    for s in _order(list(doc.sections), type_, layout):
        mine = own.get(s.id, {})
        if not s.visible or mine.get("show") is False:
            continue
        part = _type_part(type_, s.kind)
        title = mine.get("title") or part.get("title") or s.title
        limit = _int(mine.get("max")) or s.max_entries
        entries = []
        for e in [e for e in s.entries if not e.hidden][:limit or None]:
            heading = ", ".join(x for x in (e.heading, e.grade) if x)
            org = ", ".join(x for x in (e.org, e.location) if x)
            bullets = [(b.id, b.text) for b in e.bullets if (b.text or "").strip()]
            if e.style == "paragraph":
                bullets = []
            items = ", ".join(i for i in e.items if i)
            entries.append(ViewEntry(e.id, heading, _dates(e), org, e.meta or "", bullets,
                                     e.text if e.style == "paragraph" or not e.bullets else "", items,
                                     _modules(e, part) if s.kind == "education" else ""))
        if not (s.text or "").strip() and not entries and not s.also:
            continue
        out.append(ViewSection(s.id, s.kind, title, s.text or "", entries, s.also or ""))
    return View(_p(profile, "name"), doc.headline or "", contact_parts(profile), out)


# ----------------------------------------------------------------------------------------------- HTML

def _e(text: str) -> str:
    return html.escape(text or "", quote=True)


def _wanted(highlight: Iterable[str]) -> set[str]:
    """Ids to highlight, from bare ids or targets such as "s:education/e:ed-1/b:b2"."""
    out = set()
    for item in highlight or ():
        last = str(item).rstrip("/").split("/")[-1]
        out.add(last.split(":", 1)[-1] if ":" in last else last)
        out.add(str(item))
    return out


def _hl(text: str, part_id: str, hl: set[str], *, rich: bool = False) -> str:
    """The text escaped, its marks drawn when it is a line that may hold them, in a highlight when asked."""
    shown = inline.html(text) if rich else _e(text)
    return f'<mark class="hl">{shown}</mark>' if part_id in hl else shown


def _stacked(e: ViewEntry, hl: set[str], dates: str) -> list[str]:
    """An entry's heading (dates at its right, or after it with `dates` "inline"), then its place, details and
    text on lines of their own."""
    out = []
    if e.heading or e.dates:
        run = dates == "inline"
        shown = (f'<span class="cv-dates">{", " if run and e.heading else ""}{_e(e.dates)}</span>'
                 if e.dates else "")
        out.append(f'<div class="cv-top{" cv-run" if run else ""}"><h3>{_hl(e.heading, e.id, hl)}</h3>{shown}</div>')
    if e.org:
        out.append(f'<p class="cv-org">{_e(e.org)}</p>')
    if e.meta:
        out.append(f'<p class="cv-meta">{_e(e.meta)}</p>')
    if e.text:
        out.append(f'<p class="cv-text">{_hl(e.text, e.id, hl, rich=True)}</p>')
    return out


def _one_line(e: ViewEntry, hl: set[str], dates: str) -> str:
    """An entry's heading, place, details, dates and text as one line: "**Heading**, place, dates: text".

    Dates at the right are written first and floated there, so they sit on the line's first row.
    """
    right = dates == "right" and e.dates
    parts = [f"<h3>{_hl(e.heading, e.id, hl)}</h3>"] if e.heading else []
    parts += [f'<span class="cv-org">{_e(x)}</span>' for x in (e.org, e.meta) if x]
    if e.dates and not right:
        parts.append(f'<span class="cv-dates">{_e(e.dates)}</span>')
    line = ", ".join(parts)
    if e.text:
        line += (": " if line else "") + f'<span class="cv-text">{_hl(e.text, e.id, hl, rich=True)}</span>'
    first = f'<span class="cv-dates cv-right">{_e(e.dates)}</span>' if right else ""
    return f'<div class="cv-line">{first}{line}</div>'


def _entry_html(e: ViewEntry, hl: set[str], look_: Look | None = None) -> str:
    form, dates = (look_.entry_form, look_.dates) if look_ else ("stacked", "right")
    out = [f'<div class="cv-entry{" cv-one" if form == "line" else ""}" data-id="{_e(e.id)}">']
    if form == "line":
        if e.heading or e.dates or e.org or e.meta or e.text:
            out.append(_one_line(e, hl, dates))
    else:
        out += _stacked(e, hl, dates)
    if e.bullets:
        out.append("<ul>" + "".join(f'<li data-id="{_e(bid)}">{_hl(text, bid, hl, rich=True)}</li>'
                                    for bid, text in e.bullets) + "</ul>")
    if e.items:
        out.append(f'<p class="cv-items">{_e(e.items)}</p>')
    if e.modules:
        out.append(f'<p class="cv-modules"><b>Selected modules:</b> {_e(e.modules)}</p>')
    out.append("</div>")
    return "".join(out)


def _skills_html(s: ViewSection, hl: set[str]) -> str:
    """Skill groups as "Group: a, b, c" lines, which scanners read as one skill each."""
    rows = []
    for e in s.entries:
        label = f"<b>{_e(e.heading)}:</b> " if e.heading else ""
        rows.append(f'<p class="cv-items" data-id="{_e(e.id)}">{label}{_hl(e.items, e.id, hl)}</p>')
    return "".join(rows)


def _section_html(s: ViewSection, hl: set[str], look_: Look | None = None) -> str:
    body = []
    if s.text:
        body.append(f"<p>{_hl(s.text, s.id, hl, rich=True)}</p>")
    if s.kind in ("skills", "languages") and all(e.items and not e.bullets for e in s.entries):
        body.append(_skills_html(s, hl))
    else:
        body += [_entry_html(e, hl, look_) for e in s.entries]
    if s.also:
        body.append(f'<p class="cv-also"><b>Also:</b> {_e(s.also)}</p>')
    return (f'<section class="cv-sec cv-kind-{_e(s.kind)}" data-id="{_e(s.id)}"><h2>{_e(s.title)}</h2>'
            + "".join(body) + "</section>")


def _head_html(v: View, sep: str = " | ") -> str:
    contact = f'<span class="sep">{_e(sep)}</span>'.join(f"<span>{inline.html(x)}</span>" for x in v.contact)
    return (f'<header class="cv-head"><h1 class="cv-name">{_e(v.name)}</h1>'
            + (f'<p class="cv-headline">{_e(v.headline)}</p>' if v.headline else "")
            + (f'<p class="cv-contact">{contact}</p>' if contact else "") + "</header>")


def _page(title: str, look_: Look, body: str, *, letter: bool = False) -> str:
    if look_.personal:
        own = [look_.css] + ([look_.letter_css] if letter else [])
    else:
        own = [_css(f"{look_.id}/cv.css")] + ([_css(f"{look_.id}/letter.css")] if letter else [])
    style = "\n".join([font_css(), _css("base.css"), *own])
    return ("<!doctype html>\n"
            f'<html lang="en-GB"><head><meta charset="utf-8">'
            f'<meta http-equiv="Content-Security-Policy" content="{POLICY}">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{_e(title)}</title><style>\n{style}\n</style></head>"
            f'<body class="look-{_e(look_.id)}"><main class="page">{body}</main></body></html>\n')


def html_of(v: View, look_id: str | Look, *, highlight: Iterable[str] = ()) -> str:
    """The page for a view in one look, given by id or as a `Look`."""
    look_ = look_id if isinstance(look_id, Look) else look(look_id)
    hl = _wanted(highlight)

    def drawn(sections: list[ViewSection]) -> str:
        return "".join(_section_html(s, hl, look_) for s in sections)

    if look_.columns == 2:
        side = [s for s in v.sections if s.kind in SIDE_KINDS]
        main = [s for s in v.sections if s.kind not in SIDE_KINDS]
        sections = (f'<div class="cv-cols"><aside class="cv-side">{drawn(side)}</aside>'
                    f'<div class="cv-main">{drawn(main)}</div></div>')
    else:
        sections = drawn(v.sections)
    return _page(f"{v.name} CV".strip(), look_, _head_html(v, look_.sep) + sections)


def render_cv_html(doc: Any, type_: Any, look_id: str, profile: dict, *, highlight: Iterable[str] = (),
                   for_print: bool = False, layout: dict | None = None) -> str:
    """The CV page; `for_print` leaves out the highlights, so the printed page is the plain preview."""
    return html_of(view(doc, type_, profile, layout=layout), look_id, highlight=() if for_print else highlight)


def render_letter_html(text: str, profile: dict, recipient: list[str], look_id: str | Look, *, when: str = "",
                       subject: str = "", headline: str = "") -> str:
    """The cover letter page, with the same header as the CV in that look."""
    v = View(_p(profile, "name"), headline, contact_parts(profile), [])
    paragraphs = [p.strip() for p in (text or "").strip().split("\n\n") if p.strip()]
    body = "".join("<p>" + "<br>".join(_e(line) for line in p.split("\n")) + "</p>" for p in paragraphs)
    look_ = look_id if isinstance(look_id, Look) else look(look_id)
    parts = [_head_html(v, look_.sep)]
    if when:
        parts.append(f'<p class="letter-date">{_e(when)}</p>')
    if recipient:
        parts.append('<p class="letter-to">' + "<br>".join(_e(x) for x in recipient) + "</p>")
    if subject:
        parts.append(f'<p class="letter-subject">{_e(subject)}</p>')
    parts.append(f'<div class="letter-body">{body}</div>')
    return _page(f"{v.name} Cover letter".strip(), look_, "".join(parts), letter=True)
