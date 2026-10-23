"""The printer: Edge on Windows whatever browser the person uses, Playwright's Chromium on Linux,
never a snap or flatpak browser, Hal's simple PDF with an Activity row when browsers fail, and a check on
every PDF made.

Most tests use fake platforms and a fake browser that copies a drawn PDF, so they need no browser. The
tests that print for real need `HAL_TEST_BROWSER=1` (docs/dev/testing.md).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support import people
from support.ui import browser_tests_on
from test_cv_render import ACCENTED, made_up_cv, profile_of, type_for

from jobhunt import cvpdf, cvscore, db
from jobhunt.core.platform import found
from jobhunt.core.platform.windows import CREATE_NO_WINDOW, WindowsPlatform
from jobhunt.cvstudio import export, model, pdfread, printer, render

GOLDENS = Path(__file__).parent / "goldens" / "scanner"
SCANNER_TOLERANCE = 0.05    # scores of the fixed CVs are asserted within a tolerance
SAFE_LOOKS = ("classic", "modern", "compact")
OPERA = r"C:\Users\robin\AppData\Local\Programs\Opera\opera.exe"
EDGE = r"C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
WIN_ENV = {"LOCALAPPDATA": r"C:\Users\robin\AppData\Local", "ProgramFiles": r"C:\Program Files",
           "ProgramFiles(x86)": r"C:\Program Files (x86)", "APPDATA": r"C:\Users\robin\AppData\Roaming"}


def drawn_pdf(person: people.Person, name: str | None = None) -> Path:
    """A real PDF of a made-up CV, drawn by Hal's simple printer."""
    v = render.view(made_up_cv(person.key), type_for(person.key), profile_of(person, name))
    return cvpdf.draw_view(v, Path(tempfile.mkdtemp()) / "drawn.pdf")


class FakeBrowser:
    """Stands in for a headless browser: remembers each command and leaves `pdf` where --print-to-pdf says."""

    def __init__(self, pdf: Path | None, *, hang: bool = False) -> None:
        self.pdf, self.hang = pdf, hang
        self.started: list[list[str]] = []
        self.flags: list[int] = []
        self.killed = 0

    def popen(self, args, **kw):
        self.started.append(list(args))
        self.flags.append(kw.get("creationflags", 0))
        target = next(a.split("=", 1)[1] for a in args if a.startswith("--print-to-pdf="))
        if self.pdf is not None and not self.hang:
            shutil.copyfile(self.pdf, target)
        proc = mock.Mock(pid=4242)
        proc.wait.side_effect = (subprocess.TimeoutExpired(args, printer.PRINT_TIMEOUT_S) if self.hang
                                 else lambda timeout=None: 0)
        proc.poll.return_value = None if self.hang else 0
        return proc


class FakeLinux:
    """A Linux platform layer with chosen browsers and a browser that must never start."""

    name = "linux"

    def __init__(self, browsers, test: unittest.TestCase) -> None:
        self._browsers, self.test = browsers, test

    def browsers(self):
        return list(self._browsers)

    def popen_hidden(self, args, **kw):
        self.test.fail(f"a browser was started: {args}")

    def kill_tree(self, proc) -> None:
        self.test.fail("nothing should need stopping")


class NoBrowsersHere:
    """A platform layer with no browser at all."""

    name = "linux"

    def browsers(self):
        return []


def fallback_rows() -> list[str]:
    con = db.connect()
    try:
        return [r[0] for r in con.execute("SELECT text FROM activity WHERE kind = 'cv.printed_simple' ORDER BY id")]
    finally:
        con.close()


class Printing(unittest.TestCase):
    def setUp(self) -> None:
        self.person = people.HUMANITIES
        self.doc = made_up_cv(self.person.key)
        self.type = type_for(self.person.key)
        self.profile = profile_of(self.person)
        self.view = render.view(self.doc, self.type, self.profile)
        self.page = render.html_of(self.view, "classic")
        self.out = Path(tempfile.mkdtemp()) / "cv.pdf"

    def to_pdf(self, plat, **kw) -> dict:
        return printer.to_pdf(self.page, self.out, expect_name=self.profile["name"], max_pages=2,
                              expect_text=["Profile"], fallback=lambda p: cvpdf.draw_view(self.view, p), plat=plat,
                              **kw)

    def test_printing_on_windows_uses_edge_whatever_browser_you_use(self):
        fake = FakeBrowser(drawn_pdf(self.person))
        files = {OPERA, EDGE}
        win = WindowsPlatform(reg=lambda hive, key: None, exists=lambda p: p in files, env=WIN_ENV,
                              popen=fake.popen, run=lambda *a, **k: subprocess.CompletedProcess(a, 0, "", ""))
        self.assertIn("opera", [b.id for b in win.browsers()])
        done = self.to_pdf(win)
        self.assertEqual((done["how"], done["fallback_reason"]), ("edge", None))
        (args,) = fake.started
        self.assertEqual(args[0], EDGE)
        for flag in ("--headless", "--disable-gpu", "--no-first-run", "--no-default-browser-check",
                     "--no-pdf-header-footer"):
            self.assertIn(flag, args)
        self.assertTrue(any(a.startswith("--user-data-dir=") for a in args))
        self.assertTrue(args[-1].startswith("file:///") and args[-1].endswith("/cv.html"))
        self.assertTrue(fake.flags[0] & CREATE_NO_WINDOW)

    def test_a_snap_or_flatpak_browser_is_never_used_to_print(self):
        snap = found("chromium", Path("/snap/bin/chromium"), sandboxed=True)
        flatpak = found("chrome", Path("/usr/bin/flatpak"), sandboxed=True, args=("run", "com.google.Chrome"))
        plat = FakeLinux([snap, flatpak], self)
        self.assertEqual(printer.printers(plat, playwright=False)[0], [])
        done = self.to_pdf(plat, playwright=False)
        self.assertEqual(done["how"], printer.SIMPLE)
        self.assertIn("snap or flatpak", done["fallback_reason"])

    def test_an_ordinary_chromium_prints_when_playwright_is_missing(self):
        fake = FakeBrowser(drawn_pdf(self.person))
        plat = FakeLinux([found("firefox", Path("/usr/bin/firefox")), found("chromium", Path("/usr/bin/chromium"))],
                         self)
        plat.popen_hidden = fake.popen
        self.assertEqual(self.to_pdf(plat, playwright=False)["how"], "chromium")
        self.assertEqual(fake.started[0][0], "/usr/bin/chromium")

    def test_printing_falls_back_to_the_simple_pdf_and_says_why(self):
        before = len(fallback_rows())
        done = self.to_pdf(FakeLinux([], self), playwright=False)
        self.assertEqual((done["how"], done["pages"]), (printer.SIMPLE, 1))
        self.assertIn("Marnie Quillfeather", pdfread.text(self.out))
        rows = fallback_rows()[before:]
        self.assertEqual(rows, ["Made the PDF with Hal's simple printer because the browser printer failed: "
                                "no browser that can print was found"])

    def test_a_browser_that_hangs_is_stopped_and_the_simple_pdf_is_made(self):
        fake = FakeBrowser(None, hang=True)
        plat = FakeLinux([found("chrome", Path("/usr/bin/google-chrome"))], self)
        plat.popen_hidden = fake.popen
        stopped = []
        plat.kill_tree = stopped.append
        done = self.to_pdf(plat, playwright=False)
        self.assertEqual(len(stopped), 1)
        self.assertIn(f"took longer than {printer.PRINT_TIMEOUT_S} seconds", done["fallback_reason"])

    def test_every_printed_pdf_is_checked(self):
        # a browser that prints someone else's CV is caught, and the simple PDF is made instead
        fake = FakeBrowser(drawn_pdf(people.CHANGER))
        plat = FakeLinux([found("chromium", Path("/usr/bin/chromium"))], self)
        plat.popen_hidden = fake.popen
        done = self.to_pdf(plat, playwright=False)
        self.assertEqual(done["how"], printer.SIMPLE)
        self.assertIn("does not hold 'Marnie Quillfeather'", done["fallback_reason"])
        good = drawn_pdf(self.person)
        pages = printer.check(good, expect_name="Marnie Quillfeather", max_pages=2, expect_text=["Profile"])
        self.assertEqual(pages, 1)
        with self.assertRaisesRegex(printer.CheckFailed, "does not hold"):
            printer.check(good, expect_name="Someone Else", max_pages=2)
        broken = self.out.with_name("broken.pdf")
        broken.write_bytes(b"%PDF-1.4 not really\n")
        with self.assertRaisesRegex(printer.CheckFailed, "does not open"):
            printer.check(broken, expect_name="Marnie Quillfeather", max_pages=2)
        with self.assertRaises(printer.PrintError):
            printer.to_pdf(self.page, self.out, expect_name="x", max_pages=2, plat=FakeLinux([], self),
                           playwright=False)

    def test_the_simple_pdf_keeps_an_accented_name_its_fonts_can_draw(self):
        name = "Zo\u00eb M\u00fcller"
        view = render.view(self.doc, self.type, profile_of(self.person, name))
        done = printer.to_pdf(render.html_of(view, "classic"), self.out, expect_name=name, max_pages=2,
                              fallback=lambda p: cvpdf.draw_view(view, p), fold=export.fold_for_simple_pdf,
                              plat=FakeLinux([], self), playwright=False)
        self.assertEqual(done["how"], printer.SIMPLE)
        self.assertIn(name, pdfread.text(self.out))


@unittest.skipUnless(browser_tests_on(), "browser tests need HAL_TEST_BROWSER=1 and Playwright")
class RealPrinting(unittest.TestCase):
    def test_accented_names_print_in_every_look(self):
        for person in people.PEOPLE:
            profile = profile_of(person, ACCENTED)
            doc, type_ = made_up_cv(person.key), type_for(person.key)
            for look in render.looks():
                with self.subTest(person=person.key, look=look.id):
                    out = Path(tempfile.mkdtemp()) / "cv.pdf"
                    done = export.cv_pdf(doc, type_, look.id, profile, out)
                    self.assertEqual((done["how"], done["fallback_reason"]), ("playwright", None))
                    squeezed = "".join(pdfread.text(out).split())
                    self.assertIn("".join(ACCENTED.split()), squeezed)
                    self.assertLessEqual(done["pages"], 2)


    def test_the_scanner_reads_each_safe_look_as_it_did_when_recorded(self):
        """Gate for the looks: today's parsing checks of cvscore on three people x three safe looks.

        `HAL_RECORD_SCANNER=1` rewrites the goldens, only in a commit meant to change a look.
        """
        record = os.environ.get("HAL_RECORD_SCANNER") == "1"
        for person in people.PEOPLE:
            doc, profile = made_up_cv(person.key), profile_of(person)
            for look in SAFE_LOOKS:
                with self.subTest(person=person.key, look=look):
                    out = Path(tempfile.mkdtemp()) / "cv.pdf"
                    export.cv_pdf(doc, type_for(person.key), look, profile, out)
                    checks = cvscore.parsing(model.to_v1(doc, profile), out)
                    got = {"group": round(cvscore._group_score(checks), 3),
                           "checks": {c["name"]: round(c["score"], 3) for c in checks}}
                    golden = GOLDENS / f"{person.key}-{look}.json"
                    if record:
                        golden.parent.mkdir(parents=True, exist_ok=True)
                        golden.write_text(json.dumps(got, indent=2, sort_keys=True) + "\n", encoding="ascii")
                    want = json.loads(golden.read_text(encoding="ascii"))
                    self.assertEqual(sorted(got["checks"]), sorted(want["checks"]))
                    for name, score in want["checks"].items():
                        self.assertAlmostEqual(got["checks"][name], score, delta=SCANNER_TOLERANCE, msg=name)
                    self.assertAlmostEqual(got["group"], want["group"], delta=SCANNER_TOLERANCE)


if __name__ == "__main__":
    unittest.main()
