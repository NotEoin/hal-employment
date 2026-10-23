"""The CV store: versions that never change, the bin, the "employers get" line, kept files,
and the My CV routes over them. Every CV here is a made-up person's.
"""
import contextlib
import os
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - the temporary home, before any jobhunt import
from support.api import HalServer

from jobhunt import db
from jobhunt.core import clock, paths
from jobhunt.core.settings import store as settings_store
from jobhunt.cvstudio import model, store
from jobhunt.cvstudio.model import Change, CVError

DAY0 = "2026-10-01T09:00:00Z"


@contextlib.contextmanager
def cv_home():
    """A fresh temporary home with an empty CV store, for one test."""
    with tempfile.TemporaryDirectory(prefix="hal-cv-") as tmp, paths.set_override(Path(tmp)):
        settings_store.drop_cache()
        paths.ensure()
        try:
            yield Path(tmp)
        finally:
            settings_store.drop_cache()


def at(when: str):
    """Fixes Hal's clock at `when` for the length of a `with` block."""
    return mock.patch.dict(os.environ, {clock.ENV_FAKE_NOW: when})


def edited(doc: model.CVDoc, words: str) -> model.CVDoc:
    """The document with its profile paragraph reworded."""
    return model.apply(doc, Change("replace", "s:summary/text", words))


def stored(vid: str) -> str:
    con = db.connect()
    try:
        return con.execute("SELECT data FROM cv_versions WHERE id = ?", (vid,)).fetchone()[0]
    finally:
        con.close()


class Versions(unittest.TestCase):
    def setUp(self):
        self.enterContext(cv_home())

    def test_the_first_use_makes_a_starting_cv_from_the_settings(self):
        vid, doc = store.current()
        self.assertEqual(store.version(vid)["n"], 1)
        self.assertEqual(doc.sections[0].id, "summary")
        self.assertEqual(store.versions()[0]["what"], "A starting CV made from your settings")

    def test_versions_never_change_and_any_can_be_used_again(self):
        first, doc = store.current()
        second = store.new_version(edited(doc, "Archive volunteer with a First."), origin="manual",
                                   what="You edited your CV", by="you")
        third = store.new_version(edited(doc, "Researcher who writes clearly."), origin="manual",
                                  what="You edited your CV again", by="you")
        before = {v: stored(v) for v in (first, second, third)}
        store.use(second)
        self.assertEqual(store.current()[0], second)
        self.assertEqual(store.current()[1].sections[0].text, "Archive volunteer with a First.")
        store.use(first)
        fourth = store.new_version(edited(store.current()[1], "Back to basics."), origin="manual",
                                   what="You edited your CV", by="you")
        self.assertEqual({v: stored(v) for v in (first, second, third)}, before)
        self.assertEqual(store.version(fourth)["parent_id"], first)
        self.assertEqual([v["n"] for v in store.versions()], [4, 3, 2, 1])
        self.assertEqual([v["current"] for v in store.versions()], [True, False, False, False])

    def test_a_version_made_for_one_job_never_becomes_the_main_cv(self):
        main, doc = store.current()
        con = db.connect()
        try:
            con.execute("INSERT INTO jobs (id, source, title, company, url, first_seen, last_seen, status) "
                        "VALUES ('made-up:1', 'made-up', 'Archivist', 'Made-up Trust', 'https://example.org/1', "
                        "'2026-10-01', '2026-10-01', 'ready')")
            con.commit()
        finally:
            con.close()
        job_v = store.new_version(edited(doc, "For the trust."), origin="tailored", what="Adjusted for this job",
                                  by="hal", job_id="made-up:1")
        self.assertEqual(store.current()[0], main)
        self.assertNotIn(job_v, [v["id"] for v in store.versions()])
        with self.assertRaises(CVError):
            store.use(job_v)

    def test_a_document_with_problems_is_refused_with_its_reason(self):
        _, doc = store.current()
        doc.sections[1].title = ""
        with self.assertRaisesRegex(CVError, "has no title"):
            store.new_version(doc, origin="manual", what="Broken", by="you")
        with self.assertRaisesRegex(CVError, "by:"):
            store.new_version(store.current()[1], origin="manual", what="x", by="someone")

    def test_two_saves_at_once_get_two_numbers(self):
        _, doc = store.current()
        errors = []

        def save(words):
            try:
                store.new_version(edited(doc, words), origin="manual", what=words, by="you")
            except Exception as e:  # collected so the test can say what went wrong
                errors.append(e)

        threads = [threading.Thread(target=save, args=(f"Version {i}",)) for i in range(4)]
        for t in threads:
            t.start()
        for t in threads:
            t.join(timeout=30)
        self.assertEqual(errors, [])
        self.assertEqual(sorted(v["n"] for v in store.versions()), [1, 2, 3, 4, 5])


class Bin(unittest.TestCase):
    def setUp(self):
        self.enterContext(cv_home())
        with at(DAY0):
            self.first, doc = store.current()
            self.second = store.new_version(edited(doc, "Second."), origin="manual", what="Second", by="you")
            self.third = store.new_version(edited(doc, "Third."), origin="manual", what="Third", by="you")

    def test_the_bin_keeps_versions_for_30_days(self):
        with at(DAY0):
            store.bin_version(self.second)
        with at("2026-10-30T09:00:00Z"):                      # 29 days on: still there, and can come back
            listed = {v["id"]: v for v in store.versions()}
            self.assertTrue(listed[self.second]["binned"])
            store.restore(self.second)
            self.assertFalse({v["id"]: v for v in store.versions()}[self.second]["binned"])
            store.bin_version(self.second)
        with at("2026-11-28T09:00:00Z"):                      # 29 days after the second binning
            self.assertIn(self.second, [v["id"] for v in store.versions()])
        with at("2026-11-30T09:00:00Z"):                      # 31 days after it: gone
            self.assertNotIn(self.second, [v["id"] for v in store.versions()])
            self.assertEqual([v["n"] for v in store.versions()], [3, 1])

    def test_the_version_in_use_and_the_first_cannot_be_binned(self):
        with self.assertRaisesRegex(CVError, "in use"):
            store.bin_version(self.third)
        with self.assertRaisesRegex(CVError, "first version"):
            store.bin_version(self.first)

    def test_a_binned_version_cannot_be_used_until_restored(self):
        store.bin_version(self.second)
        with self.assertRaisesRegex(CVError, "in the bin"):
            store.use(self.second)
        store.restore(self.second)
        store.use(self.second)
        self.assertEqual(store.current()[0], self.second)


class EmployersGet(unittest.TestCase):
    def setUp(self):
        self.home = self.enterContext(cv_home())

    def test_the_employers_get_line_matches_what_is_sent(self):
        _, doc = store.current()
        self.assertEqual(store.doc()["send"], "hal_copy")
        self.assertEqual(store.employers_get()[0], "Employers get: Hal's copy of your CV, version 1.")
        store.new_version(edited(doc, "Newer."), origin="manual", what="Newer", by="you")
        self.assertEqual(store.employers_get()[0], "Employers get: Hal's copy of your CV, version 2.")
        with self.assertRaisesRegex(CVError, "no file of yours"):
            store.set_send("original")
        with self.assertRaisesRegex(CVError, "no file of yours"):
            store.set_route("as_is")
        original = b"%PDF-1.4\n% a made-up CV\n%%EOF\n"
        fid = store.add_file(original, "Ada-Example-CV.pdf", role="baseline")
        store.set_baseline_file(fid)
        store.set_route("as_is")
        self.assertEqual(store.doc()["send"], "original")
        self.assertEqual(store.employers_get(), ("Employers get: your original file, unchanged.",
                                                 "Adjusting for each job is off while you send your original."))
        self.assertEqual(store.file_bytes(fid), original)
        store.set_route("improve")
        self.assertEqual(store.doc()["send"], "hal_copy")
        self.assertIn("version 2", store.employers_get()[0])

    def test_a_kept_file_is_read_only_and_checked_on_every_read(self):
        data = b"PK\x03\x04 a made-up Word file"
        fid = store.add_file(data, "cv.docx", role="baseline")
        self.assertEqual(store.add_file(data, "again.docx", role="baseline"), fid)
        row = store.file_row(fid)
        path = paths.data_dir() / row["rel"]
        self.assertTrue(row["rel"].startswith("cv/files/") and row["rel"].endswith(".docx"))
        self.assertFalse(os.access(path, os.W_OK) and os.stat(path).st_mode & 0o222)
        os.chmod(path, 0o600)
        path.write_bytes(b"changed")
        with self.assertRaisesRegex(CVError, "changed on disk"):
            store.file_bytes(fid)


class Layout(unittest.TestCase):
    def setUp(self):
        self.enterContext(cv_home())

    def test_sections_order_names_limits_and_locked_lines_make_one_version(self):
        _, doc = store.current()
        doc = model.apply(doc, Change("insert", "s:education", {"heading": "BA History"}))
        vid = store.new_version(doc, origin="manual", what="Added a degree", by="you")
        entry = store.get(vid).sections[1].entries[-1]
        entry_t = f"s:education/e:{entry.id}"
        doc = model.apply(store.get(vid), Change("insert", entry_t, "Dissertation: canal towns"))
        vid = store.new_version(doc, origin="manual", what="Added a line", by="you")
        line = store.get(vid).sections[1].entries[-1].bullets[0]
        sections = store.layout()["sections"]
        sections.insert(1, sections.pop(4))               # skills before education
        sections[1].update(title="Key skills", max=3, lock=True)
        sections[-1]["show"] = False
        new = store.set_layout(sections, [f"{entry_t}/b:{line.id}"])
        out = store.layout()
        self.assertEqual([s["id"] for s in out["sections"]][:2], ["summary", "skills"])
        self.assertEqual(out["sections"][1], {"id": "skills", "kind": "skills", "title": "Key skills", "show": True,
                                              "lock": True, "max": 3})
        self.assertFalse(out["sections"][-1]["show"])
        self.assertEqual(out["locked_lines"], [f"{entry_t}/b:{line.id}"])
        self.assertTrue(store.get(new).sections[2].entries[-1].bullets[0].locked)
        self.assertIsNone(store.set_layout(out["sections"], out["locked_lines"]))
        with self.assertRaisesRegex(CVError, "every section once"):
            store.set_layout(out["sections"][1:], [])


class Routes(unittest.TestCase):
    """The My CV routes through the real server."""

    def setUp(self):
        self.hal = self.enterContext(HalServer())
        self.c = self.hal.signed_in()

    def test_the_header_document_versions_and_compare_answer(self):
        cv = self.c.get("/api/v2/cv").json()
        self.assertEqual(cv["employers_get"], "Employers get: Hal's copy of your CV, version 1.")
        self.assertEqual((cv["route"], cv["send"], cv["version"]["n"]), ("improve", "hal_copy", 1))
        doc = self.c.get("/api/v2/cv/document").json()
        first = doc.pop("version_id")
        doc.pop("n")
        doc["sections"][0]["text"] = "Made-up profile line."
        saved = self.c.request("PUT", "/api/v2/cv/document", {"doc": doc, "what": "You wrote a profile"}).json()
        self.assertEqual(saved["n"], 2)
        versions = self.c.get("/api/v2/cv/versions").json()
        self.assertEqual([(v["n"], v["current"]) for v in versions], [(2, True), (1, False)])
        diff = self.c.get(f"/api/v2/cv/compare?a={first}&b={saved['version_id']}").json()["changes"]
        self.assertEqual([(c["kind"], c["after"]) for c in diff], [("Reworded", "Made-up profile line.")])
        used = self.c.post(f"/api/v2/cv/versions/{first}/use").json()
        self.assertEqual(used["version"]["n"], 1)
        self.assertEqual(self.c.request("DELETE", f"/api/v2/cv/versions/{saved['version_id']}").json(), {"ok": True})
        self.assertTrue(self.c.get("/api/v2/cv/versions").json()[0]["binned"])
        self.assertEqual(self.c.post(f"/api/v2/cv/versions/{saved['version_id']}/restore").json(), {"ok": True})

    def test_a_bad_document_or_route_is_a_400_naming_the_field(self):
        doc = self.c.get("/api/v2/cv/document").json()
        doc["sections"][0]["visible"] = "yes"
        bad = self.c.request("PUT", "/api/v2/cv/document", {"doc": doc})
        self.assertEqual(bad.status, 400)
        self.assertIn("cv.sections[0].visible", bad.json()["error"]["message"])
        bad = self.c.request("PUT", "/api/v2/cv/send", {"send": "original"})
        self.assertEqual(bad.status, 400)
        self.assertIn("Add your CV file first", bad.json()["error"]["message"])
        self.assertEqual(self.c.post("/api/v2/cv/versions/v99-nothere/use").status, 404)

    def test_the_layout_route_saves_the_sections_card(self):
        out = self.c.get("/api/v2/cv/layout").json()
        out["sections"][1]["title"] = "Education and training"
        saved = self.c.request("PUT", "/api/v2/cv/layout", out).json()
        self.assertTrue(saved["version_id"])
        self.assertEqual(self.c.get("/api/v2/cv/layout").json()["sections"][1]["title"], "Education and training")
        con = db.connect()
        try:
            rows = con.execute("SELECT text, why FROM activity WHERE area = 'cv'").fetchall()
        finally:
            con.close()
        self.assertIn(("Changed your CV's sections (version 2)", "You changed it on My CV"), [tuple(r) for r in rows])


if __name__ == "__main__":
    unittest.main()
