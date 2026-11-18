"""Your record's CVs as My CVs, against a real server in a temporary home: "Use this for applications" on a CV
of the record makes it one of My CVs, in use, and turning it off takes it out of use.

The CV files are the made-up ones of `tests/fixtures/cv` (Avery Quillon, Marnie Quillfeather, a scanned CV);
no AI is used, so the CV reader keeps its rules' reading.
"""
import threading
import time
import unittest
from pathlib import Path
from unittest import mock

from support.actions import warm_document_reader
from support.api import HalServer
from test_cv_parse import upload
from test_sections_routes import ended, form

from jobhunt import record
from jobhunt.core import paths, runner
from jobhunt.cvstudio import parse, store

FIXTURES = Path(__file__).parent / "fixtures" / "cv"
WAIT_S = 20


def fixture(name: str) -> bytes:
    """The bytes of one made-up CV file."""
    return (FIXTURES / name).read_bytes()


class RecordCvRoutes(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        warm_document_reader()

    def setUp(self):
        self.hal = self.enterContext(HalServer())
        folder = Path(paths.record_default())
        folder.mkdir(parents=True, exist_ok=True)
        self.enterContext(mock.patch.object(record, "VAULT", folder, create=True))
        self.addCleanup(record._CACHE.clear)
        self.addCleanup(self.idle)
        self.c = self.hal.signed_in()

    def idle(self):
        """Waits until no action runs, so no read outlives its test."""
        deadline = time.monotonic() + WAIT_S
        while runner._LIVE and time.monotonic() < deadline:
            time.sleep(0.05)
        self.assertEqual(list(runner._LIVE), [], "every read a test starts has ended before it finishes")

    def to_record(self, name: str) -> int:
        """`name` added to Your record's CVs and read; its source's id."""
        status, out = form(self.hal, self.c, "/cv", [(name, fixture(name), "")])
        self.assertEqual(status, 200, out)
        ended(out["action_id"])
        return out["added"][0]["id"]

    def to_my_cvs(self, name: str) -> str:
        """`name` uploaded on My CVs (which also adds it to the record) and read; its CV's id."""
        status, out = upload(self.hal, self.c, name, fixture(name))
        self.assertEqual(status, 200, out)
        cv = ended(out["action_id"])["result"]["cv"]
        self.idle()
        return cv

    def listed(self) -> dict[str, dict]:
        """The record's CVs by name."""
        return {s["name"]: s for s in self.c.get("/api/v2/record/sources?section=cv").json()}

    def use(self, source_id: int, on: bool):
        return self.c.post(f"/api/v2/record/sources/{source_id}/use", {"on": on})

    def starred(self) -> list[str]:
        """The names of the CVs in use, in the person's order."""
        return [c["name"] for c in self.c.get("/api/v2/cv/cvs").json()["cvs"] if c["starred"]]

    def test_turning_a_record_cv_on_reads_it_into_a_new_cv_in_use_last_and_off_takes_it_out(self):
        self.to_my_cvs("Marnie-Quillfeather-CV.txt")
        avery = self.to_record("Avery-Quillon-CV.md")
        before = self.listed()
        self.assertEqual({n: (s["can_use"], s["in_use"]) for n, s in before.items()},
                         {"Marnie-Quillfeather-CV.txt": (True, True), "Avery-Quillon-CV.md": (True, False)},
                         "a file from a My CVs upload shows in use")
        self.assertIsNone(before["Avery-Quillon-CV.md"]["my_cv"])
        on = self.use(avery, True).json()
        self.assertIsNone(on["cv"])
        made = ended(on["action_id"])
        self.assertEqual(made["state"], "done", made)
        self.idle()
        self.assertEqual(self.starred(), ["Marnie-Quillfeather-CV", "Avery-Quillon-CV"], "a CV turned on goes last")
        now = self.listed()["Avery-Quillon-CV.md"]
        self.assertEqual((now["in_use"], now["my_cv"]["id"]), (True, made["result"]["cv"]))
        off = self.use(avery, False)
        self.assertEqual((off.status, off.json()["in_use"]), (200, False))
        self.assertEqual(self.starred(), ["Marnie-Quillfeather-CV"])
        marnie = self.listed()["Marnie-Quillfeather-CV.txt"]["id"]
        refused = self.use(marnie, False)
        self.assertEqual(refused.status, 400)
        self.assertIn("stays in use", refused.json()["error"]["message"])
        self.assertNotRegex(refused.json()["error"]["message"].lower(), r"\bstar", "no star word on screen")

    def test_turning_on_a_file_that_made_a_cv_uses_that_cv_and_brings_it_back_from_the_archive(self):
        first = self.to_my_cvs("Marnie-Quillfeather-CV.txt")
        self.to_my_cvs("Avery-Quillon-CV.md")
        self.assertEqual(self.c.request("PUT", f"/api/v2/cv/archive?cv={first}", {"on": True}).status, 200)
        source = self.listed()["Marnie-Quillfeather-CV.txt"]
        self.assertEqual((source["in_use"], source["my_cv"]["id"], bool(source["my_cv"]["archived"])),
                         (False, first, True))
        on = self.use(source["id"], True).json()
        self.assertEqual((on["action_id"], on["cv"], on["in_use"]), (None, first, True), "no second copy is made")
        cvs = self.c.get("/api/v2/cv/cvs").json()
        self.assertEqual(([c["name"] for c in cvs["cvs"]], cvs["archived"]),
                         (["Avery-Quillon-CV", "Marnie-Quillfeather-CV"], []))

    def test_one_file_read_from_my_cvs_and_your_record_at_once_makes_one_cv(self):
        """Turning a CV on in Your record while My CVs read the same file started a second reading, and the file
        became two identical CVs (2026-11-18)."""
        entered, release = threading.Event(), threading.Event()
        rules = parse.read_rules

        def held(lines):
            entered.set()
            release.wait(WAIT_S)
            return rules(lines)

        self.to_my_cvs("Marnie-Quillfeather-CV.txt")
        with mock.patch.object(parse, "read_rules", held):
            status, out = upload(self.hal, self.c, "Avery-Quillon-CV.md", fixture("Avery-Quillon-CV.md"))
            self.assertEqual(status, 200, out)
            self.assertTrue(entered.wait(WAIT_S), "My CVs is reading the file")
            source = self.listed()["Avery-Quillon-CV.md"]
            self.assertIsNone(source["my_cv"])
            pressed = self.use(source["id"], True)
            self.assertEqual((pressed.status, pressed.json()["action_id"]), (200, out["action_id"]),
                             "the press follows the reading under way")
            release.set()
            ended(out["action_id"])
        self.idle()
        names = [c["name"] for c in self.c.get("/api/v2/cv/cvs").json()["cvs"]]
        self.assertEqual(sorted(names), ["Avery-Quillon-CV", "Marnie-Quillfeather-CV"])

    def test_a_reading_that_ends_after_another_made_the_cv_uses_that_cv(self):
        """The duplicate check ran only when a request started, so a reading that started just before another
        ended made a second CV of the same file (2026-11-18)."""
        first = self.to_my_cvs("Avery-Quillon-CV.md")
        self.to_my_cvs("Marnie-Quillfeather-CV.txt")
        self.assertEqual(self.c.request("PUT", f"/api/v2/cv/archive?cv={first}", {"on": True}).status, 200)
        file_id = store.doc(doc_id=first)["baseline_file_id"]
        late = runner.start("cv.parse", subject="late reading",
                            params={"file_id": file_id, "file": "Avery-Quillon-CV.md", "last": True})
        done = ended(late)
        self.assertEqual((done["state"], done["result"]["cv"]), ("done", first), done["error"])
        self.idle()
        cvs = self.c.get("/api/v2/cv/cvs").json()
        self.assertEqual(([c["name"] for c in cvs["cvs"]], cvs["archived"]),
                         (["Marnie-Quillfeather-CV", "Avery-Quillon-CV"], []),
                         "no second CV; the one there is back in use, last")

    def test_a_scanned_cv_or_a_letter_cannot_be_used_and_says_why(self):
        scanned = self.to_record("Scanned-CV.pdf")
        refused = self.use(scanned, True)
        self.assertEqual((refused.status, refused.json()["error"]["message"]), (400, parse.PICTURE_WORDS))
        status, out = form(self.hal, self.c, "/letter", [("letter.md", b"# Dear Ms Fenwick\n\nI write to...\n", "")],
                           {"doc_kind": "cover_letter"})
        self.assertEqual(status, 200, out)
        ended(out["action_id"])
        letter = out["added"][0]
        self.assertEqual((letter["can_use"], letter["in_use"], letter["my_cv"]), (False, False, None))
        self.assertEqual(self.use(letter["id"], True).status, 400)
        self.assertEqual(self.use(scanned, "yes").status, 400)

    def preview(self, source_id: int) -> dict:
        reply = self.c.get(f"/api/v2/record/sources/{source_id}/preview")
        self.assertEqual(reply.status, 200, reply.text)
        return reply.json()

    def test_a_readable_cv_is_previewed_from_a_quick_reading_and_is_not_added_to_my_cvs(self):
        avery = self.to_record("Avery-Quillon-CV.pdf")
        shown = self.preview(avery)
        base = f"/record/sources/{avery}"
        self.assertEqual(shown, {"version_id": None, "url": base + "/preview.html", "why": "",
                                 "original_url": base + "/original"})
        with mock.patch.object(parse, "read_rules", wraps=parse.read_rules) as reads:
            page = self.c.get("/api/v2" + shown["url"])
            self.c.get("/api/v2" + shown["url"])
        self.assertEqual(page.status, 200)
        self.assertIn("Experience", page.text)
        self.assertEqual(reads.call_count, 0, "the reading is kept by the file's SHA-256")
        self.assertEqual(self.c.get(f"/api/v2{base}/fonts/nothing.woff2").status, 404)
        listed = self.c.get("/api/v2/cv/cvs").json()["cvs"]
        self.assertEqual([c["file"] for c in listed], [None], "nothing added to My CVs")

    def test_a_cv_in_my_cvs_previews_its_version_in_use(self):
        cv = self.to_my_cvs("Marnie-Quillfeather-CV.txt")
        source = self.listed()["Marnie-Quillfeather-CV.txt"]
        version = self.c.get(f"/api/v2/cv?cv={cv}").json()["version"]["id"]
        self.assertEqual(self.preview(source["id"])["version_id"], version)

    def test_a_file_hal_cannot_read_says_why_and_offers_the_original(self):
        scanned = self.to_record("Scanned-CV.pdf")
        shown = self.preview(scanned)
        self.assertEqual((shown["url"], shown["version_id"], shown["why"]), (None, None, parse.PICTURE_WORDS))
        original = self.c.get("/api/v2" + shown["original_url"])
        self.assertEqual((original.status, original.body), (200, fixture("Scanned-CV.pdf")))
        page = self.c.get(f"/api/v2/record/sources/{scanned}/preview.html")
        self.assertEqual(page.status, 400)


if __name__ == "__main__":
    unittest.main()
