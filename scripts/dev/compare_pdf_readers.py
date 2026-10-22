"""Compares Hal's PDF reader (pypdfium2, `jobhunt/cvstudio/pdfread.py`) with poppler on a folder of PDFs.

For the switch from poppler: run it on real application PDFs on your own computer. It
prints numbers only (word counts, differences, pages, body size, last-page fill), never any text, so the
output can be shared. Needs poppler's `pdftotext` and `pdfinfo` on the PATH.

    .venv/bin/python scripts/dev/compare_pdf_readers.py FOLDER     every *.pdf under FOLDER
"""
from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from jobhunt.cvstudio import pdfread

POPPLER_TIMEOUT_S = 60
WORD_TOLERANCE = 0.02      # the same words within 2% of characters
FILL_TOLERANCE = 0.05
FOOTER_ZONE, FILL_MARGINS = 40.0, 80.0   # as pdfread measures the last page


def _run(*args: str) -> str:
    return subprocess.run(args, capture_output=True, text=True, timeout=POPPLER_TIMEOUT_S).stdout


def poppler(pdf: Path) -> dict:
    """What poppler reads: words, pages and last-page fill."""
    pages_found = re.search(r"Pages:\s+(\d+)", _run("pdfinfo", str(pdf)))
    pages = int(pages_found.group(1)) if pages_found else 0
    fill = 0.0
    if pages:
        bbox = _run("pdftotext", "-bbox", "-f", str(pages), "-l", str(pages), str(pdf), "-")
        height = re.search(r'<page width="[\d.]+" height="([\d.]+)"', bbox)
        tops = [float(y) for y in re.findall(r'<word[^>]*yMax="([\d.]+)"', bbox)]
        if height and tops:
            h = float(height.group(1))
            fill = max([t for t in tops if t < h - FOOTER_ZONE] or [0.0]) / (h - FILL_MARGINS)
    return {"words": _run("pdftotext", str(pdf), "-").split(), "pages": pages, "fill": fill}


def compare(pdf: Path) -> dict:
    """The numbers for one PDF."""
    theirs = poppler(pdf)
    ours = pdfread.text(pdf).split()
    differ = (sum(1 for a, b in zip(ours, theirs["words"], strict=False) if a != b)
              + abs(len(ours) - len(theirs["words"])))
    return {"words": len(theirs["words"]), "differ": differ, "pages": (pdfread.pages(pdf), theirs["pages"]),
            "size": pdfread.body_size(pdf), "fill": (pdfread.last_page_fill(pdf), theirs["fill"])}


def main() -> int:
    """Prints one line per PDF and a total; exits 1 when any PDF is outside the tolerances."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("folder", type=Path)
    args = parser.parse_args()
    if not (shutil.which("pdftotext") and shutil.which("pdfinfo")):
        print("poppler is not installed (pdftotext, pdfinfo)")
        return 2
    found = sorted(args.folder.expanduser().rglob("*.pdf"))
    bad = 0
    for n, pdf in enumerate(found, 1):
        r = compare(pdf)
        ok = (r["differ"] <= WORD_TOLERANCE * max(1, r["words"]) and r["pages"][0] == r["pages"][1]
              and abs(r["fill"][0] - r["fill"][1]) <= FILL_TOLERANCE)
        bad += not ok
        print(f"{n:4d} {'ok  ' if ok else 'DIFF'} words {r['words']:5d} differ {r['differ']:4d} "
              f"pages {r['pages'][0]}/{r['pages'][1]} size {r['size'] or 0:.1f} "
              f"fill {r['fill'][0]:.2f}/{r['fill'][1]:.2f}")
    print(f"{len(found)} PDFs, {bad} outside the tolerances")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
