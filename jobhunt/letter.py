"""Cover letters as PDFs in the CV's style, using the portfolio site's PDF builder.

The header (name, title, contact line, location) is drawn exactly as on the CV,
then the date, recipient, subject line and letter body.
"""
import json
from datetime import date
from pathlib import Path

from .cv import CV_JSON, _pdf_builder

BODY_GAP = 7.0      # space between paragraphs
BLOCK_GAP = 14.0    # space between the address block, subject and body


def _header(b, doc, data) -> None:
    contact = data["contact"]
    doc.y -= b.NAME_SIZE
    doc.text(b.NAME + " ", b.ML, doc.y, b.F_BOLD, b.NAME_SIZE, b.INK, tracking=0.2)
    doc.y -= 11.5
    doc.text(data["title"] + " ", b.ML, doc.y, b.F_REG, 9.5, b.META_INK)
    doc.y -= 11
    sep = (" | ", None)
    doc.link_line([
        (b.EMAIL, f"mailto:{b.EMAIL}"), sep, (b.PHONE, None), sep,
        (contact["linkedin"], f"https://{contact['linkedin']}"), sep,
        (contact["github"], f"https://{contact['github']}"), sep,
        (contact["site"], f"https://{contact['site']}"),
    ], b.ML, b.CONTACT_SIZE)
    doc.y -= 10.5
    doc.text(contact["location"] + " ", b.ML, doc.y, b.F_REG, b.CONTACT_SIZE, b.META_INK)
    doc.y -= 9
    doc.rule(b.ML, doc.y, b.CW, ink=0.55, thickness=0.7)
    doc.y -= 22


def render(body: str, out: Path, recipient: list[str], subject: str, when: date | None = None) -> Path:
    """Writes a one-page cover letter PDF and returns its path.

    Args:
        body: Letter text from the greeting to the sign-off; paragraphs separated by blank lines.
        out: Destination PDF path.
        recipient: Address lines, top to bottom.
        subject: Subject line, set in bold above the greeting.
        when: Date shown on the letter; today by default.
    """
    b = _pdf_builder()
    data = json.loads(CV_JSON.read_text())
    paragraphs = [p.strip() for p in body.strip().split("\n\n") if p.strip()]
    lines = [line for para in paragraphs for line in para.split("\n")]
    b.check_ascii({"body": lines, "recipient": recipient, "subject": subject}, "letter")

    doc = b.Doc()
    _header(b, doc, data)
    when = when or date.today()
    doc.paragraph(f"{when.day} {when:%B %Y}", size=b.BODY_SIZE, lead=b.BODY_LEAD)
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
    try:
        b.write_pdf(pages, doc.links, title="Cover letter")
    except ValueError:
        # write_pdf prints the path relative to the site root, which fails outside it.
        if not out.exists():
            raise
    return out
