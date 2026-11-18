"""Hal's copy of the CV as files to keep or send: PDF, Word, plain text, JSON Resume and the website's cv.json.

The PDF comes from the printer (Hal's simple drawn PDF when no browser can print). The Word file is
written with the standard library as minimal WordprocessingML using only the built-in styles Heading 1,
List Bullet and Normal, so Word, LibreOffice and Google Docs all open it and it can be edited and uploaded
again as a new main CV; keeping an uploaded Word file's own design is cut for now. A line's bold and italic
are runs with `w:b` and `w:i`, and its links real hyperlinks (a relationship each, in the built-in Hyperlink
character style), so the file reads back with the same marks. File names come from the `cvdocs.file_name`
setting through `core.naming`, which strips accents only in the name.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape

from jobhunt.cvstudio import inline, printer, render

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
FORMATS = {"pdf": ("application/pdf", ".pdf"), "docx": (DOCX_MIME, ".docx"),
           "txt": ("text/plain; charset=utf-8", ".txt"), "jsonresume": ("application/json", ".json"),
           "website": ("application/json", ".json")}
JSON_RESUME_SCHEMA = "https://raw.githubusercontent.com/jsonresume/resume-schema/v1.0.0/schema.json"
MONTHS = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct",
                                         "nov", "dec"))}
NAME_HALF_POINTS = 32         # the name at 16 pt in the Word file
FIXED_ZIP_TIME = (2026, 1, 1, 0, 0, 0)   # the same CV always gives the same bytes
_XML_BAD = re.compile("[\x00-\x08\x0b\x0c\x0e-\x1f]")
FIRST_LINK_ID = 3             # rId1 is the styles part and rId2 the numbering part


class UnknownFormat(ValueError):
    """Not one of the export formats."""


def file_name(fmt: str) -> str:
    """The download's name: the `cvdocs.file_name` template with this format's extension."""
    from jobhunt.core import naming

    if fmt not in FORMATS:
        raise UnknownFormat(f"Hal cannot export a CV as {fmt!r}.")
    base = naming.file_name("cv")
    stem = base[:-4] if base.lower().endswith(".pdf") else base
    suffix = FORMATS[fmt][1]
    return f"{stem}{'.website' if fmt == 'website' else ''}{suffix}"


def fold_for_simple_pdf(text: str) -> str:
    """What Hal's simple PDF can hold of `text` (it draws ASCII and Latin-1 letters only)."""
    from jobhunt.cvstudio import pdf as cvpdf

    return cvpdf._drawable(text)


def cv_pdf(doc: Any, type_: Any, look_id: str, profile: dict, out: Path, *, layout: dict | None = None,
           why: str = "", simple: bool = True) -> dict:
    """Prints a CV to `out` with the printer, falling back to the simple PDF; the printer's result.

    With `simple` False there is no fallback, so `printer.PrintError` when no browser can print (a page count
    must be the look's own). No page count is too many: a CV over its page limit is the person's choice and
    is printed whole (2.3.0 refused one more than a page over its type's rule).
    """
    from jobhunt.cvstudio import pdf as cvpdf

    v = render.view(doc, type_, profile, layout=layout)
    html = render.html_of(v, look_id)
    first = v.sections[0].title if v.sections else ""
    fallback = (lambda path: cvpdf.draw_view(v, path)) if simple else None
    return printer.to_pdf(html, out, expect_name=v.name, max_pages=0, expect_text=[first], fallback=fallback,
                          fold=fold_for_simple_pdf, why=why)


# ---------------------------------------------------------------------------------------------- Word

def _t(text: str) -> str:
    return escape(_XML_BAD.sub("", text or ""))


def _attr(text: str) -> str:
    """`_t` for a value inside double quotes."""
    return escape(_XML_BAD.sub("", text or ""), {'"': "&quot;"})


class _Links:
    """The web addresses a Word file links to, each with its relationship id."""

    def __init__(self) -> None:
        self.ids: dict[str, str] = {}

    def id_of(self, url: str) -> str:
        """The relationship id for `url`, given the first time it is asked for."""
        return self.ids.setdefault(url, f"rId{FIRST_LINK_ID + len(self.ids)}")


def _run(r: inline.Run, size: int | None = None) -> str:
    """One Word run: its words in bold, italic or the Hyperlink style as `r` says, at `size` half-points."""
    props = (('<w:rStyle w:val="Hyperlink"/>' if r.link else "") + ("<w:b/>" if r.bold else "")
             + ("<w:i/>" if r.italic else "") + (f'<w:sz w:val="{size}"/>' if size else ""))
    rpr = f"<w:rPr>{props}</w:rPr>" if props else ""
    return f'<w:r>{rpr}<w:t xml:space="preserve">{_t(r.text)}</w:t></w:r>'


def _props(style: str) -> str:
    return f'<w:pPr><w:pStyle w:val="{style}"/></w:pPr>' if style != "Normal" else ""


def _para(text: str, style: str = "Normal", *, bold: bool = False, size: int | None = None) -> str:
    return f"<w:p>{_props(style)}{_run(inline.Run(text, bold=bold), size)}</w:p>"


def _line_para(text: str, links: _Links, style: str = "Normal") -> str:
    """A paragraph of a CV line, its marks as bold and italic runs and its links as hyperlinks."""
    body: list[str] = []
    runs = inline.runs(text)
    i = 0
    while i < len(runs):
        j = i
        while j < len(runs) and runs[j].link == runs[i].link:
            j += 1
        parts = "".join(_run(r) for r in runs[i:j])
        body.append(f'<w:hyperlink r:id="{links.id_of(runs[i].link)}" w:history="1">{parts}</w:hyperlink>'
                    if runs[i].link else parts)
        i = j
    return f"<w:p>{_props(style)}{''.join(body)}</w:p>"


def _docx_body(v: render.View, links: _Links) -> str:
    out = [_para(v.name, bold=True, size=NAME_HALF_POINTS)]
    if v.headline:
        out.append(_para(v.headline))
    if v.contact:
        out.append(_para(" | ".join(v.contact)))
    for s in v.sections:
        out.append(_para(s.title, "Heading1"))
        if s.text:
            out.append(_line_para(s.text, links))
        for e in s.entries:
            if s.kind in ("skills", "languages") and e.items and not e.bullets:
                out.append(_para(f"{e.heading}: {e.items}" if e.heading else e.items))
                continue
            top = " | ".join(x for x in (e.heading, e.dates) if x)
            if top:
                out.append(_para(top, bold=True))
            for line in (e.org, e.meta):
                if line:
                    out.append(_para(line))
            if e.text:
                out.append(_line_para(e.text, links))
            if e.items:
                out.append(_para(e.items))
            out += [_line_para(text, links, "ListBullet") for _, text in e.bullets]
            if e.modules:
                out.append(_para(f"Selected modules: {e.modules}"))
        if s.also:
            out.append(_para(f"Also: {s.also}"))
    return "".join(out)


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'
R = 'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"'
HYPERLINK = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/hyperlink"
DOCX_PARTS = {
    "[Content_Types].xml": (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.'
        'wordprocessingml.document.main+xml"/>'
        '<Override PartName="/word/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.'
        'wordprocessingml.styles+xml"/>'
        '<Override PartName="/word/numbering.xml" ContentType="application/vnd.openxmlformats-officedocument.'
        'wordprocessingml.numbering+xml"/></Types>'),
    "_rels/.rels": (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
        'officeDocument" Target="word/document.xml"/></Relationships>'),
    "word/styles.xml": (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:styles {W}>'
        '<w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Calibri" w:hAnsi="Calibri" w:cs="Calibri"/>'
        '<w:sz w:val="21"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="60"/></w:pPr>'
        '</w:pPrDefault></w:docDefaults>'
        '<w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/></w:style>'
        '<w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/>'
        '<w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:spacing w:before="240" w:after="80"/>'
        '<w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/><w:sz w:val="26"/></w:rPr></w:style>'
        '<w:style w:type="paragraph" w:styleId="ListBullet"><w:name w:val="List Bullet"/>'
        '<w:basedOn w:val="Normal"/><w:pPr><w:numPr><w:numId w:val="1"/></w:numPr>'
        '<w:ind w:left="360" w:hanging="360"/></w:pPr></w:style>'
        '<w:style w:type="character" w:styleId="Hyperlink"><w:name w:val="Hyperlink"/>'
        '<w:rPr><w:color w:val="0563C1"/><w:u w:val="single"/></w:rPr></w:style></w:styles>'),
    "word/numbering.xml": (
        f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:numbering {W}>'
        '<w:abstractNum w:abstractNumId="0"><w:lvl w:ilvl="0"><w:start w:val="1"/><w:numFmt w:val="bullet"/>'
        '<w:lvlText w:val="&#8226;"/><w:lvlJc w:val="left"/><w:pPr><w:ind w:left="360" w:hanging="360"/>'
        '</w:pPr></w:lvl></w:abstractNum><w:num w:numId="1"><w:abstractNumId w:val="0"/></w:num>'
        '</w:numbering>'),
}


def _document_rels(links: _Links) -> str:
    """The document's relationships: its styles and numbering parts, then one per web address it links to."""
    rels = ['<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'styles" Target="styles.xml"/>',
            '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
            'numbering" Target="numbering.xml"/>']
    # `inline.safe_link` already refuses control characters; this keeps the part valid XML whatever comes in
    rels += [f'<Relationship Id="{rid}" Type="{HYPERLINK}" Target="{_attr(url)}" TargetMode="External"/>'
             for url, rid in links.ids.items()]
    return ('<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            + "".join(rels) + "</Relationships>")


def to_docx(v: render.View) -> bytes:
    """A Word file of the CV, with the same words and order as the printed page."""
    links = _Links()
    document = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?><w:document {W} {R}><w:body>'
                f'{_docx_body(v, links)}<w:sectPr><w:pgSz w:w="11906" w:h="16838"/>'
                '<w:pgMar w:top="1020" w:right="1020" w:bottom="1020" w:left="1020" w:header="0" w:footer="0" '
                'w:gutter="0"/></w:sectPr></w:body></w:document>')
    parts = [*DOCX_PARTS.items(), ("word/_rels/document.xml.rels", _document_rels(links)),
             ("word/document.xml", document)]
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, text in parts:
            z.writestr(zipfile.ZipInfo(name, FIXED_ZIP_TIME), text.encode("utf-8"))
    return buf.getvalue()


# ---------------------------------------------------------------------------------------------- JSON Resume

def _iso(when: str) -> str:
    """"Sep 2023" as "2023-09", "2023" as "2023"; "" for "Present" or anything else."""
    m = re.fullmatch(r"\s*(?:([A-Za-z]{3})[a-z]*\.?\s+)?(\d{4})\s*", when or "")
    if not m:
        return ""
    month = MONTHS.get((m.group(1) or "").lower())
    return f"{m.group(2)}-{month:02d}" if month else m.group(2)


def _dated(e: Any, out: dict) -> dict:
    for key, value in (("startDate", _iso(e.start)), ("endDate", _iso(e.end))):
        if value:
            out[key] = value
    return out


def to_jsonresume(doc: Any, profile: dict) -> dict:
    """The CV as JSON Resume 1.0.0: basics from the profile; work from experience and volunteering."""
    links = profile.get("links") or {}
    basics: dict[str, Any] = {"name": render._p(profile, "name"), "label": doc.headline or ""}
    for key in ("email", "phone"):
        if render._p(profile, key):
            basics[key] = render._p(profile, key)
    city = render._p(profile, "location") or render._p(profile, "city")
    if city:
        basics["location"] = {"city": city, "countryCode": "GB"}
    basics["profiles"] = [{"network": k.title() if k != "linkedin" else "LinkedIn", "url": links[k]}
                          for k in render.LINK_ORDER if links.get(k)]
    out: dict[str, Any] = {"$schema": JSON_RESUME_SCHEMA, "basics": basics, "work": [], "education": [],
                           "skills": [], "projects": []}
    for s in doc.sections:
        if not s.visible:
            continue
        shown = [e for e in s.entries if not e.hidden]
        if s.kind == "summary" and s.text:
            basics["summary"] = inline.plain(s.text)
        for e in shown:
            highlights = [inline.plain(b.text) for b in e.bullets if b.text]
            if s.kind in ("experience", "volunteering"):
                out["work"].append(_dated(e, {"name": e.org, "position": e.heading, "location": e.location,
                                              "summary": inline.plain(e.text), "highlights": highlights}))
            elif s.kind == "education":
                out["education"].append(_dated(e, {"institution": e.org, "studyType": e.heading, "score": e.grade,
                                                   "courses": [m.name for m in e.modules]}))
            elif s.kind == "skills":
                out["skills"].append({"name": e.heading, "keywords": list(e.items)})
            elif s.kind == "projects":
                out["projects"].append({"name": e.heading, "description": inline.plain(e.text) or e.meta,
                                        "highlights": highlights, **({"url": e.link} if e.link else {})})
    return out


# ---------------------------------------------------------------------------------------------- one call

def export(fmt: str, doc: Any, type_: Any, look_id: str, profile: dict, *, layout: dict | None = None,
           work_dir: Path | None = None) -> tuple[bytes, str, str]:
    """(bytes, file name, media type) of the CV in one format."""
    from jobhunt.cvstudio import model

    if fmt not in FORMATS:
        raise UnknownFormat(f"Hal cannot export a CV as {fmt!r}. Choose PDF, Word, text, JSON Resume or website.")
    mime = FORMATS[fmt][0]
    if fmt == "pdf":
        from jobhunt.core import paths

        folder = work_dir or paths.cache_dir() / "cv-print"
        html = render.render_cv_html(doc, type_, look_id, profile, for_print=True, layout=layout)
        out = folder / f"{hashlib.sha256(html.encode('utf-8')).hexdigest()[:20]}-{look_id}.pdf"
        if not out.exists():
            cv_pdf(doc, type_, look_id, profile, out, layout=layout, why="You exported your CV")
        data = out.read_bytes()
    elif fmt == "docx":
        data = to_docx(render.view(doc, type_, profile, layout=layout))
    elif fmt == "txt":
        data = model.plain_text(doc, profile).encode("utf-8")
    elif fmt == "jsonresume":
        data = (json.dumps(to_jsonresume(doc, profile), indent=2) + "\n").encode("utf-8")
    else:
        data = (json.dumps(model.to_v1(doc, profile), indent=2) + "\n").encode("utf-8")
    return data, file_name(fmt), mime
