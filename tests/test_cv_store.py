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

    def test_the_import_warning_goes_once_the_missing_text_is_back(self):
        _, doc = store.current()
        doc.extras["not_found"] = ["Volunteer at a made-up archive", "Fell running"]
        store.new_version(doc, origin="imported", what="Imported", by="baseline")
        back = edited(store.current()[1], "Volunteer at a made-up archive, two years.")
        store.new_version(back, origin="manual", what="You put it back", by="you")
        self.assertEqual(store.current()[1].extras["not_found"], ["Fell running"])

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


class HomeAndSearch(unittest.TestCase):
    """What My CV adds to Home and to Ctrl K."""

    def setUp(self):
        self.enterContext(cv_home())
        self.con = db.connect()
        self.addCleanup(self.con.close)

    def test_home_asks_you_to_check_text_the_import_did_not_find(self):
        from jobhunt.cvstudio import home

        self.assertEqual(home.needs(self.con), [])
        self.assertEqual(self.con.execute("SELECT count(*) FROM cv_versions").fetchone()[0], 0)
        _, doc = store.current()
        doc.extras["not_found"] = ["Volunteer at a made-up archive"]
        store.new_version(doc, origin="imported", what="Imported", by="baseline")
        need = home.needs(self.con)
        self.assertEqual([(n["title"], n["act"]["route"]) for n in need],
                         [("1 piece of your old CV is not in Hal's copy", "cv-versions")])

    def test_ctrl_k_finds_the_versions_and_the_route_dialog(self):
        from jobhunt.cvstudio import search

        self.assertEqual([r["label"] for r in search.search(self.con, "version")], ["CV versions"])
        self.assertEqual(search.search(self.con, "how your cv")[0]["action"], "dialog:route")


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

    def test_a_line_you_reword_is_kept_to_learn_your_writing_from_and_a_new_one_is_not(self):
        for text in ("Made-up profile line.", "A made-up profile line, in my own words."):
            doc = self.c.get("/api/v2/cv/document").json()
            doc.pop("version_id")
            doc.pop("n")
            doc["sections"][0]["text"] = text
            self.c.request("PUT", "/api/v2/cv/document", {"doc": doc, "what": "You wrote a profile"})
        con = db.connect()
        try:
            rows = [tuple(r) for r in con.execute("SELECT kind, draft, final FROM writing_edits")]
        finally:
            con.close()
        self.assertEqual(rows, [("cv_line", "Made-up profile line.", "A made-up profile line, in my own words.")])

    def test_at_a_glance_shows_the_scanner_score_and_the_types_checks(self):
        # the glance card stayed empty and the type showed as its id once the checks were in
        cv = self.c.get("/api/v2/cv").json()
        checked = self.c.get("/api/v2/cv/checks").json()
        self.assertEqual(cv["type"]["name"], "Graduate, any subject")   # nobody chose one: Hal's suggestion
        self.assertEqual(cv["glance"]["scanner"], checked["scanner"]["score"])
        self.assertEqual((cv["glance"]["type_pass"], cv["glance"]["type_total"]),
                         (checked["type"]["passed"], checked["type"]["total"]))

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


def add_job(job_id: str = "made-up:1", title: str = "Archivist", status: str = "ready") -> None:
    """A made-up saved job, so a per-job version has a job to belong to."""
    con = db.connect()
    try:
        con.execute("INSERT OR IGNORE INTO jobs (id, source, title, company, url, first_seen, last_seen, status) "
                    "VALUES (?, 'made-up', ?, 'Made-up Trust', 'https://example.org/1', '2026-10-01', "
                    "'2026-10-01', ?)", (job_id, title, status))
        con.commit()
    finally:
        con.close()


class SeveralCVs(unittest.TestCase):
    """Each CV keeps its own history, and the lead CV is what callers that name none get."""

    def setUp(self):
        self.enterContext(cv_home())
        with at(DAY0):
            self.first, self.doc = store.current()
        self.main = store.doc()["id"]

    def other(self, name: str = "Museum roles", words: str = "Museum guide who tells good stories.") -> str:
        return store.create(name, edited(self.doc, words), origin="uploaded", what="Your file: museum.pdf",
                            by="baseline")

    def test_each_cv_numbers_its_own_versions_from_one(self):
        store.new_version(edited(self.doc, "Archive volunteer."), origin="manual", what="Edit", by="you")
        museum = self.other()
        self.assertEqual([v["n"] for v in store.versions(doc_id=museum)], [1])
        store.new_version(edited(self.doc, "Guide for families."), origin="manual", what="Edit", by="you",
                          doc_id=museum)
        self.assertEqual([v["n"] for v in store.versions(doc_id=museum)], [2, 1])
        self.assertEqual([v["n"] for v in store.versions(doc_id=self.main)], [2, 1])
        self.assertEqual(store.current(doc_id=museum)[1].sections[0].text, "Guide for families.")

    def test_a_job_version_takes_no_number_of_its_cv(self):
        add_job()
        job_v = store.new_version(edited(self.doc, "For the trust."), origin="tailored", what="Adjusted",
                                  by="you", parent=self.first, job_id="made-up:1")
        self.assertEqual((store.version(job_v)["n"], store.version(job_v)["doc_id"]), (None, self.main))
        second = store.new_version(edited(self.doc, "Next."), origin="manual", what="Edit", by="you")
        self.assertEqual(store.version(second)["n"], 2)
        self.assertNotIn(job_v, [v["id"] for v in store.versions()])

    def test_a_new_cv_leads_and_the_first_starred_in_your_order_is_the_lead(self):
        store.new_version(edited(self.doc, "Kept."), origin="manual", what="Edit", by="you")
        museum = self.other()
        self.assertEqual(store.lead_id(), museum, "a new upload is used as an upload always was")
        self.assertEqual([d["id"] for d in store.starred()], [museum, self.main])
        store.reorder([self.main, museum])
        self.assertEqual((store.lead_id(), store.current()[0]), (self.main, store.doc(doc_id=self.main)
                                                                  ["current_version_id"]))
        store.star(self.main, False)
        self.assertEqual(store.lead_id(), museum)

    def test_the_last_star_stays_and_archiving_hands_it_on(self):
        store.new_version(edited(self.doc, "Kept."), origin="manual", what="Edit", by="you")
        museum = self.other()
        store.star(self.main, False)
        with self.assertRaisesRegex(CVError, "stays in use"):
            store.star(museum, False)
        store.archive(museum)
        self.assertEqual([(d["id"], d["starred"]) for d in store.documents()], [(self.main, True)])
        self.assertEqual([d["id"] for d in store.documents(archived=True)], [museum])
        with self.assertRaisesRegex(CVError, "only CV in use"):
            store.archive(self.main)
        store.archive(museum, False)
        self.assertEqual({d["id"] for d in store.documents()}, {self.main, museum})
        self.assertEqual([v["n"] for v in store.versions(doc_id=museum)], [1], "an archived CV keeps its history")

    def test_a_cv_archived_while_it_is_turned_on_is_not_turned_on_in_the_archive(self):
        """`star` read the CV before taking the write lock, so a CV another request archived in between was turned
        on while in the archive (2026-11-18)."""
        museum = self.other()
        store.new_version(edited(self.doc, "Kept."), origin="manual", what="Edit", by="you", doc_id=self.main)
        store.archive(self.main, False)
        store.star(self.main, True)
        store.star(museum, False)
        real, done = store.writing, []

        @contextlib.contextmanager
        def other_request_first(con):
            if not done:
                done.append(True)
                store.archive(museum)       # another request, on its own connection, just before the lock
            with real(con):
                yield

        with mock.patch.object(store, "writing", other_request_first), \
                self.assertRaisesRegex(CVError, "in the archive"):
            store.star(museum, True)
        row = store.doc(doc_id=museum)
        self.assertEqual((bool(row["archived"]), row["starred"]), (True, 0))

    def test_an_out_of_date_order_says_to_reload_in_plain_words(self):
        museum = self.other()
        store.archive(self.main, False)
        store.star(self.main, True)
        with self.assertRaises(CVError) as caught:
            store.reorder([museum])
        self.assertEqual(str(caught.exception), store.STALE_ORDER)

    def test_a_cv_can_be_renamed_but_not_to_nothing(self):
        self.assertEqual(store.rename(self.main, "  Heritage   roles ")["name"], "Heritage roles")
        with self.assertRaisesRegex(CVError, "name:"):
            store.rename(self.main, " ")
        with self.assertRaises(store.NoSuchCV):
            store.rename("cv-nothere", "Anything")

    def test_your_first_own_cv_sends_the_untouched_starter_to_the_archive(self):
        museum = self.other()
        self.assertEqual([d["id"] for d in store.documents()], [museum])
        self.assertEqual([d["id"] for d in store.documents(archived=True)], [self.main])

    def test_a_file_that_made_a_cv_finds_that_cv(self):
        fid = store.add_file(b"%PDF made-up", "Museum CV.pdf", role="baseline")
        museum = store.create("Museum CV", edited(self.doc, "Guide."), origin="uploaded", what="Your file",
                              by="baseline", file_id=fid)
        self.assertEqual(store.cv_for_file(fid), museum)
        self.assertEqual(store.doc(doc_id=museum)["baseline_file_id"], fid)
        self.assertIsNone(store.cv_for_file("f-nothing"))

    def test_the_ais_reading_takes_the_upload_versions_place_and_nothing_counted_from_the_old_one_stays(self):
        museum = self.other()
        upload_v = store.doc(doc_id=museum)["current_version_id"]
        con = db.connect()
        try:
            con.execute("UPDATE cv_versions SET pages = 1, pages_key = 'old-page', scanner_score = 70 WHERE id = ?",
                        (upload_v,))
            con.execute("INSERT INTO cv_checks (version_id, look_id, at) VALUES (?, 'classic', ?)", (upload_v, DAY0))
            con.commit()
        finally:
            con.close()
        later = store.new_version(edited(self.doc, "Guide, after the upload."), origin="manual", what="Edit",
                                  by="you", doc_id=museum)
        self.assertTrue(store.replace_reading(upload_v, edited(self.doc, "Guide, as the AI read it.")))
        self.assertEqual(store.version(upload_v)["n"], 1, "the same version, with its number")
        self.assertIn("as the AI read it", stored(upload_v))
        self.assertEqual(store.doc(doc_id=museum)["current_version_id"], later, "a version made since stays in use")
        con = db.connect()
        try:
            row = con.execute("SELECT pages, pages_key, scanner_score FROM cv_versions WHERE id = ?",
                              (upload_v,)).fetchone()
            checks = con.execute("SELECT count(*) FROM cv_checks WHERE version_id = ?", (upload_v,)).fetchone()[0]
        finally:
            con.close()
        self.assertEqual((tuple(row), checks), ((None, None, None), 0))
        with self.assertRaises(CVError):
            store.replace_reading(later, self.doc)
        self.assertFalse(store.replace_reading("v9-gone", self.doc))


def rows(sql: str, *args) -> list[tuple]:
    """The rows a query answers, as tuples."""
    con = db.connect()
    try:
        return [tuple(r) for r in con.execute(sql, args)]
    finally:
        con.close()


def run(sql: str, *args) -> None:
    """Runs one write and commits it."""
    con = db.connect()
    try:
        con.execute(sql, args)
        con.commit()
    finally:
        con.close()


class DeleteCV(unittest.TestCase):
    """Deleting an archived CV for good: everything of its own goes, what applications sent stays."""

    def setUp(self):
        self.enterContext(cv_home())
        self.enterContext(mock.patch.object(store, "type_in_use", return_value="technical_grad"))
        with at(DAY0):
            _, self.doc = store.current()
        self.main = store.doc()["id"]
        store.new_version(edited(self.doc, "Archive volunteer."), origin="manual", what="Edit", by="you")
        self.fid = store.add_file(b"%PDF made-up museum CV", "Museum CV.pdf", role="baseline")
        self.museum = store.create("Museum CV", edited(self.doc, "Museum guide."), origin="uploaded",
                                   what="Your file: Museum CV.pdf", by="baseline", file_id=self.fid)
        self.v1 = store.doc(doc_id=self.museum)["current_version_id"]
        self.kept = paths.data_dir() / store.file_row(self.fid)["rel"]

    def archived(self) -> None:
        store.archive(self.museum)

    def test_an_archived_cv_goes_with_everything_of_its_own(self):
        from jobhunt.cvstudio import choose, suggest

        add_job()
        job_v = store.new_version(edited(self.doc, "Museum guide for archives."), origin="tailored",
                                  what="For one job", by="hal", job_id="made-up:1", doc_id=self.museum)
        run("INSERT INTO cv_checks (version_id, look_id, at) VALUES (?, 'classic', ?)", self.v1, DAY0)
        run("INSERT INTO cv_parse_reports (version_id, file_id, found, items) VALUES (?, ?, 'a CV', '[]')",
            self.v1, self.fid)
        run("""INSERT INTO cv_suggestions (id, doc_id, based_on, target, op, kind, title, why, trigger, evidence,
               afters, status, fingerprint, created) VALUES ('s-1', ?, ?, 's:summary/text', 'replace', 'Wording',
               'Shorter', 'Reads better', '{}', '[]', '[]', 'pending', 'fp', ?)""", self.museum, self.v1, DAY0)
        run("INSERT INTO cv_preferences (kind, target, text, created, doc_id) VALUES ('lock_line', 't', 'x', ?, ?)",
            DAY0, self.museum)
        run("INSERT INTO meta (k, v) VALUES (?, ?)", suggest.META_REVIEWED.format(doc=self.museum), self.v1)
        choose._VECTORS[(self.v1, "made-up-model")] = [1.0]
        self.archived()
        gone = store.delete(self.museum)
        self.assertEqual((gone["name"], gone["versions"], gone["files"]), ("Museum CV", 2, 1))
        for table, col in (("cv_versions", "doc_id"), ("cv_suggestions", "doc_id"), ("cv_preferences", "doc_id")):
            self.assertEqual(rows(f"SELECT * FROM {table} WHERE {col} = ?", self.museum), [], table)
        for table in ("cv_checks", "cv_parse_reports"):
            self.assertEqual(rows(f"SELECT * FROM {table} WHERE version_id IN (?, ?)", self.v1, job_v), [], table)
        self.assertEqual(rows("SELECT * FROM meta WHERE k = ?", suggest.META_REVIEWED.format(doc=self.museum)), [])
        self.assertEqual(rows("SELECT * FROM cv_files WHERE id = ?", self.fid), [])
        self.assertFalse(self.kept.exists(), "the kept file went with its last CV")
        self.assertNotIn((self.v1, "made-up-model"), choose._VECTORS)
        with self.assertRaises(store.NoSuchCV):
            store.doc(doc_id=self.museum)
        self.assertEqual([d["id"] for d in store.documents(archived=None)], [self.main])
        self.assertEqual([v["n"] for v in store.versions(doc_id=self.main)], [2, 1], "the other CV is untouched")

    def test_a_cv_in_use_is_refused_and_keeps_everything(self):
        with self.assertRaises(CVError) as caught:
            store.delete(self.museum)
        self.assertEqual(str(caught.exception), store.NOT_ARCHIVED)
        self.assertEqual(store.doc(doc_id=self.museum)["current_version_id"], self.v1)
        self.assertTrue(self.kept.exists())
        with self.assertRaises(store.NoSuchCV):
            store.delete("cv-nothere")

    def test_a_kept_file_another_cv_uses_stays(self):
        store.create("Museum CV copy", edited(self.doc, "Museum guide."), origin="uploaded", what="Your file",
                     by="baseline", file_id=self.fid)
        self.archived()
        self.assertEqual(store.delete(self.museum)["files"], 0)
        self.assertTrue(self.kept.exists())
        self.assertEqual(len(rows("SELECT * FROM cv_files WHERE id = ?", self.fid)), 1)

    def test_an_application_being_prepared_on_it_opens_and_has_a_cv_in_use_again(self):
        from jobhunt.apply import queries

        add_job()
        run("INSERT INTO applications (job_id, stage, updated) VALUES ('made-up:1', 'documents', ?)", DAY0)
        run("INSERT INTO job_cvs (job_id, base_version_id, level, changes) VALUES ('made-up:1', ?, 'no', '[]')",
            self.v1)
        self.archived()
        gone = store.delete(self.museum)
        self.assertEqual(gone["redraft"], ["Archivist"])
        self.assertEqual(rows("SELECT * FROM job_cvs"), [])
        self.assertEqual(rows("SELECT docs_need_redraft FROM applications"), [(1,)])
        self.assertIn((f"Documents for Archivist need making again, because {store.REDRAFT_WHY}",),
                      rows("SELECT text FROM activity WHERE job_id = 'made-up:1'"))
        con = db.connect()
        self.addCleanup(con.close)
        page = queries.application(con, "made-up:1")
        self.assertEqual(page["docs"]["cv"]["base"]["doc_id"], self.main, "built on the CV in use now")
        self.assertIsNone(page["docs"]["cv"]["version_n"])

    def test_an_approved_application_not_sent_refuses_and_names_the_job(self):
        add_job(title="Collections Assistant")
        run("INSERT INTO job_cvs (job_id, base_version_id, level, changes, approved) "
            "VALUES ('made-up:1', ?, 'no', '[]', ?)", self.v1, DAY0)
        self.archived()
        with self.assertRaises(CVError) as caught:
            store.delete(self.museum)
        self.assertIn("Collections Assistant at Made-up Trust", str(caught.exception))
        self.assertEqual(len(rows("SELECT * FROM job_cvs")), 1)
        self.assertEqual(store.doc(doc_id=self.museum)["current_version_id"], self.v1)

    def test_a_sent_application_keeps_its_documents(self):
        from jobhunt.apply import queries

        add_job(status="applied")
        job_v = store.new_version(edited(self.doc, "Museum guide for archives."), origin="tailored",
                                  what="For one job", by="hal", job_id="made-up:1", doc_id=self.museum)
        run("""INSERT INTO applications (job_id, stage, updated, approved_at, sent_at, cv_version_id, cv_sent_as)
               VALUES ('made-up:1', 'submitted', ?, ?, ?, ?, 'hal_copy')""", DAY0, DAY0, DAY0, job_v)
        run("INSERT INTO job_cvs (job_id, base_version_id, version_id, level, changes, approved) "
            "VALUES ('made-up:1', ?, ?, 'no', '[]', ?)", self.v1, job_v, DAY0)
        run("INSERT INTO files (id, root, rel, kind, job_id, created) VALUES ('file-1', 'applications', "
            "'0001 Made-up Trust/CV.pdf', 'cv_pdf', 'made-up:1', ?)", DAY0)
        run("INSERT INTO app_documents (job_id, kind, file_id, cv_version_id, approved_at, updated) "
            "VALUES ('made-up:1', 'cv_pdf', 'file-1', ?, ?, ?)", job_v, DAY0, DAY0)
        self.archived()
        gone = store.delete(self.museum)
        self.assertEqual((gone["redraft"], gone["kept_sent"]), ([], 1))
        self.assertEqual(rows("SELECT file_id, cv_version_id, approved_at FROM app_documents"),
                         [("file-1", None, DAY0)])
        self.assertEqual(len(rows("SELECT * FROM files WHERE id = 'file-1'")), 1)
        self.assertEqual(rows("SELECT sent_at, cv_version_id, cv_sent_as, docs_need_redraft FROM applications"),
                         [(DAY0, None, "hal_copy", 0)])
        con = db.connect()
        self.addCleanup(con.close)
        self.assertEqual(queries.application(con, "made-up:1")["docs"]["cv"]["sent_as"], "hal_copy")


class SeveralCVRoutes(unittest.TestCase):
    """The CV list and its changes through the real server; `?cv=` picks the CV every route acts on."""

    def setUp(self):
        self.hal = self.enterContext(HalServer())
        self.c = self.hal.signed_in()
        self.main = self.c.get("/api/v2/cv").json()["cv"]["id"]
        doc = self.c.get("/api/v2/cv/document").json()
        doc.pop("version_id")
        doc.pop("n")
        self.c.request("PUT", "/api/v2/cv/document", {"doc": doc, "what": "Kept the starter"})
        self.museum = store.create("Museum roles", model.from_dict(doc), origin="uploaded", what="Your file",
                                   by="baseline")

    def test_the_list_star_rename_order_archive_and_restore(self):
        listed = self.c.get("/api/v2/cv/cvs").json()
        self.assertEqual(([d["id"] for d in listed["cvs"]], listed["lead"]), ([self.museum, self.main], self.museum))
        q = f"?cv={self.museum}"
        renamed = self.c.request("PUT", "/api/v2/cv/name" + q, {"name": "Heritage roles"}).json()
        self.assertEqual(renamed["cvs"][0]["name"], "Heritage roles")
        ordered = self.c.request("PUT", "/api/v2/cv/order", {"ids": [self.main, self.museum]}).json()
        self.assertEqual(ordered["lead"], self.main)
        self.c.request("PUT", "/api/v2/cv/star" + q, {"on": False})
        refused = self.c.request("PUT", f"/api/v2/cv/star?cv={self.main}", {"on": False})
        self.assertEqual(refused.status, 400)
        self.assertIn("stays in use", refused.json()["error"]["message"])
        archived = self.c.request("PUT", "/api/v2/cv/archive" + q, {"on": True}).json()
        self.assertEqual([d["id"] for d in archived["archived"]], [self.museum])
        back = self.c.request("PUT", "/api/v2/cv/archive" + q, {"on": False}).json()
        self.assertEqual(sorted(d["id"] for d in back["cvs"]), sorted([self.main, self.museum]))
        self.assertEqual(self.c.request("PUT", "/api/v2/cv/star", {"on": True}).status, 400)

    def test_delete_answers_404_for_no_cv_409_for_one_in_use_and_the_list_once_gone(self):
        q = f"?cv={self.museum}"
        self.assertEqual(self.c.request("DELETE", "/api/v2/cv?cv=cv-nothere").status, 404)
        refused = self.c.request("DELETE", "/api/v2/cv" + q)
        self.assertEqual((refused.status, refused.json()["error"]["message"]), (409, store.NOT_ARCHIVED))
        self.c.request("PUT", "/api/v2/cv/archive" + q, {"on": True})
        gone = self.c.request("DELETE", "/api/v2/cv" + q)
        self.assertEqual(gone.status, 200, gone.text)
        self.assertEqual(([d["id"] for d in gone.json()["cvs"]], gone.json()["archived"], gone.json()["lead"]),
                         ([self.main], [], self.main))
        self.assertEqual(gone.json()["deleted"], {"name": "Museum roles", "redraft": [], "kept_sent": 0})
        self.assertIn(("Deleted Museum roles, with 1 version",), rows("SELECT text FROM activity"))
        self.assertEqual(self.c.get("/api/v2/cv" + q).status, 404)

    def test_every_tab_follows_the_chosen_cv(self):
        q = f"?cv={self.main}"
        self.assertEqual(self.c.get("/api/v2/cv").json()["cv"]["id"], self.museum)
        header = self.c.get("/api/v2/cv" + q).json()
        self.assertEqual((header["cv"]["id"], header["version"]["n"], header["count"]), (self.main, 2, 2))
        self.assertEqual([v["n"] for v in self.c.get("/api/v2/cv/versions" + q).json()], [2, 1])
        self.assertEqual([v["n"] for v in self.c.get(f"/api/v2/cv/versions?cv={self.museum}").json()], [1])
        layout = self.c.get("/api/v2/cv/layout" + q).json()
        layout["sections"][1]["title"] = "Learning"
        self.c.request("PUT", "/api/v2/cv/layout" + q, layout)
        self.assertEqual([v["n"] for v in self.c.get("/api/v2/cv/versions" + q).json()], [3, 2, 1])
        self.assertEqual([v["n"] for v in self.c.get(f"/api/v2/cv/versions?cv={self.museum}").json()], [1])
        self.assertEqual(self.c.get("/api/v2/cv/checks" + q).json()["version_id"],
                         self.c.get("/api/v2/cv/document" + q).json()["version_id"])
        self.assertEqual(self.c.get("/api/v2/cv?cv=cv-nothere").status, 404)


if __name__ == "__main__":
    unittest.main()
