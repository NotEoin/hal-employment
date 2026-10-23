"""Printing CV and letter pages to PDF with a headless browser Hal controls, never the person's own browser.

On Windows the printer is Microsoft Edge, which every Windows 10 and 11 has, whatever browser the person
uses. Elsewhere it is Playwright's Chromium, then a Chromium-family browser installed as an ordinary
program. A snap or flatpak browser is never used: it cannot read the temporary folder the page is written
to. The page and its fonts are written to a new temporary folder and loaded as a `file:` address, so no
sign-in is involved; each run gets a fresh browser profile, which avoids Edge refusing a profile in use.

When no browser can print, or its PDF fails the check, the caller's `fallback` (Hal's simple drawn PDF)
makes the file and an Activity row says why. Every PDF is checked: it opens, has 1 to
`max_pages` + 1 pages, and its text holds the name and the expected words.
"""
from __future__ import annotations

import importlib.util
import logging
import re
import shutil
import subprocess
import tempfile
import unicodedata
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import pdfread, render

log = logging.getLogger(__name__)

PRINT_TIMEOUT_S = 60          # a CV prints in about a second
# Browsers whose headless --print-to-pdf ends by itself; Brave 1.8x waited until the 60 s timeout (2026-10-23).
HEADLESS_OK = ("chromium", "chrome", "edge")
SIMPLE = "simple"
FALLBACK_WORDS = "Made the PDF with Hal's simple printer because the browser printer failed: {reason}"


class PrintError(RuntimeError):
    """No PDF could be made; the message says why in plain words."""


class CheckFailed(PrintError):
    """A PDF was made but is not right (no pages, too many, or the name is missing)."""


@dataclass(frozen=True)
class Printer:
    """One way to print: `how` is "edge", "playwright" or "chromium"."""

    how: str
    name: str
    exe: Path | None = None


def _platform() -> Any:
    from ..core import platform

    return platform.current()


def _has_playwright() -> bool:
    return importlib.util.find_spec("playwright") is not None


def printers(plat: Any = None, *, playwright: bool | None = None) -> tuple[list[Printer], str]:
    """The printers to try, in order, and why there are none when the list is empty."""
    plat = plat or _platform()
    if getattr(plat, "name", "") == "windows":
        return [Printer("edge", "Microsoft Edge", Path(plat.edge()))], ""
    if _has_playwright() if playwright is None else playwright:
        return [Printer("playwright", "Playwright's Chromium")], ""
    out = []
    skipped = []
    for b in plat.browsers():
        if b.family != "chromium":
            continue
        if b.sandboxed or b.args:
            skipped.append(b.name)
            continue
        if b.id in HEADLESS_OK:
            out.append(Printer("chromium", b.name, Path(b.exe)))
    if out:
        return out, ""
    if skipped:
        return [], (f"the only browsers here ({', '.join(skipped)}) are snap or flatpak installs, "
                    "which cannot print Hal's pages")
    return [], "no browser that can print was found"


def headless_args(exe: Path, profile_dir: Path, pdf: Path, page: Path) -> list[str]:
    """The headless print command for Edge or Chromium."""
    return [str(exe), "--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
            f"--user-data-dir={profile_dir}", "--no-pdf-header-footer", f"--print-to-pdf={pdf}", page.as_uri()]


def _print_headless(printer: Printer, page: Path, pdf: Path, plat: Any) -> None:
    profile = page.parent / "profile"
    profile.mkdir()
    proc = plat.popen_hidden(headless_args(printer.exe, profile, pdf, page),
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        code = proc.wait(timeout=PRINT_TIMEOUT_S)
    except subprocess.TimeoutExpired:
        plat.kill_tree(proc)
        raise PrintError(f"{printer.name} took longer than {PRINT_TIMEOUT_S} seconds") from None
    if code not in (0, None):
        raise PrintError(f"{printer.name} stopped with code {code}")
    if not pdf.exists() or pdf.stat().st_size == 0:
        raise PrintError(f"{printer.name} made no PDF")


def _print_playwright(page: Path, pdf: Path) -> None:
    from playwright.sync_api import Error, sync_playwright

    ms = PRINT_TIMEOUT_S * 1000
    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True, timeout=ms)
            try:
                tab = browser.new_page()
                tab.set_default_timeout(ms)
                tab.goto(page.as_uri(), wait_until="load")
                tab.evaluate("() => document.fonts.ready.then(() => true)")
                tab.pdf(path=str(pdf), format="A4", print_background=True, prefer_css_page_size=True)
            finally:
                browser.close()
    except Error as e:
        first = (str(e).strip().splitlines() or ["unknown error"])[0]
        raise PrintError(f"Playwright's Chromium could not print ({first[:160]})") from e


def _norm(text: str) -> str:
    """Text for comparing: accents kept, case and spacing ignored."""
    return re.sub(r"\s+", " ", unicodedata.normalize("NFC", text or "")).strip().casefold()


def check(pdf: Path, *, expect_name: str, max_pages: int, expect_text: Iterable[str] = (),
          fold: Callable[[str], str] | None = None) -> int:
    """The page count of a good PDF; `CheckFailed` says what is wrong."""
    try:
        pages = pdfread.pages(pdf)
    except Exception as e:
        raise CheckFailed(f"the PDF does not open ({type(e).__name__})") from e
    most = (max_pages or 0) + 1 if max_pages else None
    if pages < 1:
        raise CheckFailed("the PDF does not open, or has no pages")
    if most and pages > most:
        raise CheckFailed(f"the PDF has {pages} pages, and this CV should have 1 to {most}")
    # Line breaks and spaces are ignored: on a short page PDFium can read Chromium's text back one letter per
    # line (poppler reads the same file correctly; Chromium 1243, pypdfium2 4, checked 2026-10-23).
    squeezed = re.sub(r"\s+", "", _norm(pdfread.text(pdf)))
    for want in [expect_name, *expect_text]:
        shown = fold(want) if fold else want
        if shown and re.sub(r"\s+", "", _norm(shown)) not in squeezed:
            raise CheckFailed(f"the PDF's text does not hold {shown!r}")
    return pages


def _record_fallback(reason: str, why: str) -> None:
    try:
        from ..core import activity

        activity.record("cv", "cv.printed_simple", FALLBACK_WORDS.format(reason=reason), why or "Your CV was printed",
                        icon="file", tone="warn")
    except Exception:
        log.exception("could not record the simple-printer note")


def to_pdf(html: str, out: Path, *, expect_name: str, max_pages: int, expect_text: Iterable[str] = (),
           fallback: Callable[[Path], Any] | None = None, fold: Callable[[str], str] | None = None,
           why: str = "", plat: Any = None, playwright: bool | None = None) -> dict:
    """Prints `html` to `out`: {path, pages, how, fallback_reason}.

    `fallback(out)` draws the simple PDF when every browser fails; `fold` turns the expected words into what
    the simple PDF can hold (it drops letters its fonts lack). Without a fallback a failure raises
    `PrintError`.
    """
    out = Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)
    expect_text = [t for t in expect_text if t]
    plat = plat or _platform()
    found, reason = printers(plat, playwright=playwright)
    reasons = [reason] if reason else []
    for printer in found:
        try:
            # a browser killed at the timeout may still hold its profile; the system cleans its temp folder
            with tempfile.TemporaryDirectory(prefix="hal-print-", ignore_cleanup_errors=True) as tmp:
                folder = Path(tmp)
                page = folder / "cv.html"
                page.write_text(html, encoding="utf-8")
                (folder / "fonts").mkdir()
                for f in render.font_files():
                    shutil.copyfile(f, folder / "fonts" / f.name)
                made = folder / "out.pdf"
                if printer.how == "playwright":
                    _print_playwright(page, made)
                else:
                    _print_headless(printer, page, made, plat)
                pages = check(made, expect_name=expect_name, max_pages=max_pages, expect_text=expect_text)
                shutil.move(str(made), out)
            return {"path": str(out), "pages": pages, "how": printer.how, "fallback_reason": None}
        except PrintError as e:
            log.warning("printing with %s failed: %s", printer.name, e)
            reasons.append(str(e))
    why_not = "; ".join(reasons) or "no browser that can print was found"
    if fallback is None:
        raise PrintError(why_not)
    fallback(out)
    pages = check(out, expect_name=expect_name, max_pages=max_pages, expect_text=expect_text, fold=fold)
    _record_fallback(why_not, why)
    return {"path": str(out), "pages": pages, "how": SIMPLE, "fallback_reason": why_not}
