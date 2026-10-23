"""Cover letters as PDFs in the CV's style.

With a look, the letter is an HTML page in that look (`cvstudio.render.render_letter_html`), printed by the
same browser printer as the CV, so its header matches a CV printed in the same look; Hal's own PDF writer
(`pdfdraw.py`) is the printer's fallback. Without a look, it is drawn with that writer as before. The drawn
header (name, title, contact line, location) is exactly the one on the CV sent with the letter, then the
date, recipient, subject line and letter body. The caller passes that CV; without one the header comes
from the person's main CV, never from a file outside Hal.
"""
from datetime import date
from pathlib import Path

from . import cv, pdfdraw
from .cvpdf import _drawable

BODY_GAP = 7.0      # space between paragraphs
BLOCK_GAP = 14.0    # space between the address block, subject and body
NAME_TRACKING = 0.2  # the CV's name is set with this letter spacing


def _header(b, doc, data: dict) -> None:
    contact = data.get("contact") or {}
    doc.y -= b.NAME_SIZE
    doc.text(_drawable(b.NAME) + " ", b.ML, doc.y, b.F_BOLD, b.NAME_SIZE, b.INK, tracking=NAME_TRACKING)
    doc.y -= 11.5
    doc.text(data.get("title", "") + " ", b.ML, doc.y, b.F_REG, 9.5, b.META_INK)
    doc.y -= 11
    doc.link_line(b.contact_line(contact), b.ML, b.CONTACT_SIZE)
    doc.y -= 10.5
    doc.text((contact.get("location") or "") + " ", b.ML, doc.y, b.F_REG, b.CONTACT_SIZE, b.META_INK)
    doc.y -= 9
    doc.rule(b.ML, doc.y, b.CW, ink=0.55, thickness=0.7)
    doc.y -= 22


def _day(when: date) -> str:
    return f"{when.day} {when:%B %Y}"


def render(body: str, out: Path, recipient: list[str], subject: str, when: date | None = None,
           cv_sent: dict | None = None, look: str | None = None) -> Path:
    """Writes a one-page cover letter PDF and returns its path.

    Args:
        body: Letter text from the greeting to the sign-off; paragraphs separated by blank lines.
        out: Destination PDF path.
        recipient: Address lines, top to bottom.
        subject: Subject line, set in bold above the greeting.
        when: Date shown on the letter; today by default.
        cv_sent: The CV sent with the letter, whose header the letter repeats; the main CV by default.
        look: A CV look id: print the letter in that look with the browser printer.

    Raises:
        ValueError: the letter runs past one page.
    """
    if look is None:
        return draw(body, out, recipient, subject, when, cv_sent)
    from .cvstudio import export, printer
    from .cvstudio import render as pages

    when = when or date.today()
    title = (cv.printable(cv_sent) if cv_sent is not None else cv.load()).get("title", "")
    profile = pages.profile_from_settings()
    page = pages.render_letter_html(body, profile, recipient, look, when=_day(when), subject=subject, headline=title)
    done = printer.to_pdf(page, out, expect_name=profile["name"], max_pages=0, expect_text=[subject],
                          fallback=lambda path: draw(body, path, recipient, subject, when, cv_sent),
                          fold=export.fold_for_simple_pdf, why="Your cover letter was printed")
    if done["pages"] > 1:
        raise ValueError(f"letter runs to {done['pages']} pages; shorten it to one")
    return Path(done["path"])


def draw(body: str, out: Path, recipient: list[str], subject: str, when: date | None = None,
         cv_sent: dict | None = None) -> Path:
    """Draws the letter with Hal's own PDF writer; `render` without a look, and the printer's fallback."""
    b = pdfdraw
    data = _drawable(cv.printable(cv_sent) if cv_sent is not None else cv.load())
    body, recipient, subject = _drawable(body), _drawable(recipient), _drawable(subject)
    paragraphs = [p.strip() for p in body.strip().split("\n\n") if p.strip()]

    doc = b.Doc()
    _header(b, doc, data)
    when = when or date.today()
    doc.paragraph(_day(when), size=b.BODY_SIZE, lead=b.BODY_LEAD)
    doc.y -= BODY_GAP
    for line in recipient:
        doc.paragraph(line, size=b.BODY_SIZE, lead=b.BODY_LEAD)
    doc.y -= BLOCK_GAP
    doc.paragraph(f"**{subject}**", size=b.BODY_SIZE, lead=b.BODY_LEAD)
    doc.y -= BODY_GAP
    for i, para in enumerate(paragraphs):
        # The sign-off keeps its line breaks ("Kind regards," then the name).
        for line in para.split("\n") if i == len(paragraphs) - 1 else [para.replace("\n", " ")]:
            doc.paragraph(line, size=b.BODY_SIZE, lead=b.BODY_LEAD)
        doc.y -= BODY_GAP

    pages = doc.finish()
    if len(pages) > 1:
        raise ValueError(f"letter runs to {len(pages)} pages; shorten it to one")
    out.parent.mkdir(parents=True, exist_ok=True)
    b.OUT = out
    b.write_pdf(pages, doc.links, title="Cover letter")
    return out
