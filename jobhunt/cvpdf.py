"""Hal's own CV layout, for applications: one column, bullets, and marks that read in order.

It draws with the website's PDF builder (tools/build-cv-pdf.py), so type and the
plain-text guarantees match the website's CV, and changes what applicant tracking
systems and recruiters read badly: body text is 10pt, the strongest module marks
sit on one line as "Module mark" rather than in a grid of bars, an entry written
as "- " lines is drawn as bullets with a hanging indent, and there is no
"References available on request". The website's own PDF is untouched.
"""
import json
from pathlib import Path

from . import cv

BULLET_INDENT = 10.0
BODY, LEAD = 10.0, 12.6  # careers services advise no smaller than 10pt; the website's CV uses 9.2


def _bullets(text: str) -> list[str] | None:
    """The bullets of an entry written as "- " lines, or None for a paragraph."""
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    if lines and all(line.startswith("- ") for line in lines):
        return [line[2:].strip() for line in lines]
    return None


def _body(b, doc, text: str) -> None:
    items = _bullets(text)
    if items is None:
        doc.paragraph(text, size=BODY, lead=LEAD)
        return
    for item in items:
        lines = b.wrap(item, b.CW - BULLET_INDENT, BODY)
        doc.need(len(lines) * LEAD)
        for i, line in enumerate(lines):
            doc.y -= BODY
            if i == 0:
                doc.text("- ", b.ML + 1.5, doc.y, b.F_REG, BODY)
            doc.line_runs(line, b.ML + BULLET_INDENT, doc.y, BODY, pad=True)
            doc.y -= LEAD - BODY
        doc.y -= 1.0


def _height(b, text: str) -> float:
    items = _bullets(text)
    if items is None:
        return len(b.wrap(text, b.CW, BODY)) * LEAD
    return sum(len(b.wrap(i, b.CW - BULLET_INDENT, BODY)) * LEAD + 1.0 for i in items)


def _entry(b, doc, title: str, meta: str, body: str, size=None, grade=None) -> None:
    size = size or b.HEAD_SIZE
    head = f"{title}, {grade}" if grade else title
    # A short entry is never split across pages; a long one keeps its heading with its first lines.
    tall = _height(b, body)
    doc.need(size + 3 + b.META_SIZE + 5 + (tall if tall <= 10 * LEAD else 3 * LEAD))
    doc.heading(head, size=size)
    doc.meta(meta)
    doc.y -= 1
    _body(b, doc, body)


def _marks(b, doc, entry: dict) -> None:
    """The strongest module marks on one line, best first, so each mark extracts beside its module."""
    rows = [r for g in entry.get("modules") or [] for r in g["rows"]]
    floor = entry.get("pdfModulesMin")
    rows = sorted((r for r in rows if floor is None or r[1] >= floor), key=lambda r: -r[1])
    if rows:
        doc.y -= 1
        doc.paragraph("**Selected modules (out of 100):** " + "; ".join(f"{n} {m}" for n, m in rows),
                      size=BODY, lead=LEAD, gap=1.0)


ORDER = ("education", "projects", "experience", "skills", "additional")


def render(variant: dict, out_dir: Path) -> Path:
    """Writes a CV variant as Hal's layout to out_dir and returns the PDF's path."""
    b = cv._pdf_builder()
    data = cv.printable(variant)
    # Bullets are lines of text; the line breaks between them are this layout's, not characters drawn.
    b.check_ascii(json.loads(json.dumps(data).replace("\\n", " ")))
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf = out_dir / "Firstname-Lastname-CV.pdf"
    doc = b.Doc()
    contact = data["contact"]

    doc.y -= b.NAME_SIZE
    doc.text(b.NAME + " ", b.ML, doc.y, b.F_BOLD, b.NAME_SIZE, b.INK)
    doc.y -= 11.5
    doc.text(data["title"] + " ", b.ML, doc.y, b.F_REG, 9.5, b.META_INK)
    doc.y -= 11
    sep = (" | ", None)
    doc.link_line([(b.EMAIL, f"mailto:{b.EMAIL}"), sep, (b.PHONE, None), sep,
                   (contact["linkedin"], f"https://{contact['linkedin']}"), sep,
                   (contact["github"], f"https://{contact['github']}"), sep,
                   (contact["site"], f"https://{contact['site']}")], b.ML, b.CONTACT_SIZE)
    doc.y -= 10.5
    doc.text(contact["location"] + " ", b.ML, doc.y, b.F_REG, b.CONTACT_SIZE, b.META_INK)
    doc.y -= 9
    doc.rule(b.ML, doc.y, b.CW, ink=0.55, thickness=0.7)
    doc.y -= 13
    if data.get("summary"):
        doc.paragraph(data["summary"], size=BODY, lead=LEAD)

    def heading(title: str) -> None:
        # The website's spacing assumes a line of small type above each heading; body text needs more room.
        doc.y -= 6
        doc.section(title)

    for section in data.get("order") or ORDER:
        if section == "education" and data.get("education"):
            heading("Education")
            for i, e in enumerate(data["education"]):
                if i:
                    doc.y -= 5
                _entry(b, doc, e["qualification"], e["meta"], e["detail"], grade=e.get("grade"))
                _marks(b, doc, e)
        elif section == "projects" and data.get("projects"):
            heading("Projects")
            # The most relevant projects in full; the rest by name, so the page stays one page.
            shown = data["projects"][:int(data.get("pdfProjects") or len(data["projects"]))]
            for i, p in enumerate(shown):
                if i:
                    doc.y -= 7
                _entry(b, doc, p["name"], p["stack"], p["body"], size=10.4)
            rest = [p["name"] for p in data["projects"][len(shown):]]
            more = " ".join(x for x in ((f"**Also:** {'; '.join(rest)}." if rest else ""), data.get("moreProjects", "")) if x)
            if more:
                doc.y -= 4
                doc.paragraph(more, size=BODY, lead=LEAD)
        elif section == "experience" and data.get("experience"):
            heading("Experience")
            for i, x in enumerate(data["experience"]):
                if i:
                    doc.y -= 6
                _entry(b, doc, f"{x['role']}, {x['org']}", x["dates"], x["body"], size=10.4)
        elif section == "skills" and data.get("skills"):
            heading("Skills")
            for group, items in data["skills"]:
                doc.paragraph(f"**{group}:** " + ", ".join(items), size=BODY, lead=LEAD, gap=1.5)
        elif section == "additional" and data.get("additional"):
            heading("Additional")
            for label, text in data["additional"]:
                doc.paragraph(f"**{label}:** {text}", size=BODY, lead=LEAD, gap=1.5)

    pages = doc.finish()
    b.add_footers(pages)
    b.OUT = pdf
    try:
        b.write_pdf(pages, doc.links)
    except ValueError:
        # The builder prints the path relative to the website's folder, which this one is not in.
        if not pdf.exists():
            raise
    return pdf
