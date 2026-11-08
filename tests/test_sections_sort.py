"""The review of a sort: proposals kept for several sittings, paged and counted, changed by the person, and only the
accepted ones made sources, each read by its own section's reader.

The made-up dumps are `test_sections_sorter`'s: Avery Quillon (software) and Marnie Quillfeather (history).
"""
import json
import time
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.api import HalServer
from support.fake_ai import fake_ai
from test_ingest import home, put
from test_sections_routes import form
from test_sections_sorter import AVERY_CV, CERTIFICATE, history_dump, software_dump

from jobhunt import record
from jobhunt.core import paths, runner
from jobhunt.profile import store as profile_store
from jobhunt.sections import pipeline, sorter, sorts

NONE = {"sort_pick": json.dumps({"choice": "none"})}
WAIT_S = 30
BIG = 10_000                   # the size of dump the review table must stay quick for


def sorted_folder(con, folder: Path) -> int:
    """A sort of `folder`, run in this thread; its id."""
    sort_id = sorts.create(con, "folder", [str(folder)], folder.name)
    sorts.run(con, sort_id)
    return sort_id


def add_and_read(con, sort_id: int) -> list[int]:
    """What the accept action does, in this thread: the accepted rows made sources, then read; their ids."""
    ids = sorts.add_accepted(con, sort_id)
    for source_id in ids:
        pipeline.read(con, source_id)
    sorts.star_projects(con, sort_id)
    sorts.rematch(con, sort_id)
    return ids


def sections_of(con) -> list[tuple[str, str]]:
    """Every source in the record as (section, kind of document), sorted."""
    return sorted(tuple(r) for r in con.execute("SELECT section, doc_kind FROM record_sources"))


def ended(action_id: str) -> dict:
    """The action once it has ended."""
    deadline = time.monotonic() + WAIT_S
    while time.monotonic() < deadline:
        found = runner.wait(action_id, timeout=1)
        if found["state"] not in ("queued", "running"):
            return found
    raise AssertionError(f"action {action_id} did not end")


class Review(unittest.TestCase):
    def education(self, con, qualification: str, subject: str) -> int:
        """A degree on a made-up CV; its card's id."""
        return profile_store.add_found(con, "education", {"qualification": qualification, "subject": subject,
                                                          "institution": "University of Nowhere Fen"},
                                       {"source_rel": "Sources/CVs/CV.md", "doc_type": "cv"})

    def test_only_accepted_rows_become_sources_each_read_in_its_section(self):
        with home() as (con, _vault, files), fake_ai(NONE):
            self.education(con, "BSc", "Computing")
            sort_id = sorted_folder(con, software_dump(files))
            counted = {s["id"]: s["proposed"] for s in sorts.counts(con, sort_id)["sections"]}
            self.assertEqual((counted["cv"], counted["lecture"], counted["project"]), (2, 3, 1))
            self.assertEqual(sections_of(con), [], "nothing is in the record before the person accepts")
            self.assertEqual(sorts.accept(con, sort_id, "cv"), {"accepted": 2, "need_card": 0})
            add_and_read(con, sort_id)
            self.assertEqual(sections_of(con), [("cv", "cv"), ("cv", "cv")])
            self.assertTrue(con.execute("SELECT 1 FROM profile_evidence WHERE source_id IS NOT NULL").fetchone())
            letter = sorts.rows(con, sort_id, section="letter")["rows"][0]
            sorts.change_row(con, letter["id"], {"ticked": False})
            sorts.accept(con, sort_id)
            add_and_read(con, sort_id)
            got = sections_of(con)
            self.assertNotIn(("letter", "cover_letter"), got, "an unticked row stays out")
            self.assertIn(("coursework", "dissertation"), got)
            self.assertIn(("project", "repo"), got)
            self.assertEqual(sorts.counts(con, sort_id)["proposed"], 1, "only the unticked letter is left")
            left = sorts.rows(con, sort_id, state="left_out")["rows"]
            self.assertTrue(any("private" in r["why"] for r in left))

    def test_a_row_waits_for_its_course_and_finds_it_once_a_cv_gives_one(self):
        with home() as (con, _vault, files), fake_ai(NONE):
            sort_id = sorted_folder(con, history_dump(files))
            out = sorts.accept(con, sort_id)
            self.assertGreater(out["need_card"], 0, "coursework and lecture notes wait for their course")
            essay = next(r for r in sorts.rows(con, sort_id, section="coursework")["rows"] if r["name"].endswith(
                "essay.md"))
            self.assertTrue(essay["needs_card"])
            degree = self.education(con, "BA", "History")
            self.assertGreater(sorts.rematch(con, sort_id), 0)
            essay = sorts.rows(con, sort_id, section="coursework")["rows"][0]
            self.assertEqual(essay["target_id"], degree)
            self.assertEqual(sorts.accept(con, sort_id, "coursework")["need_card"], 0)

    def test_a_change_is_the_persons_and_must_fit_its_section(self):
        with home() as (con, _vault, files), fake_ai(NONE):
            sort_id = sorted_folder(con, history_dump(files))
            cv = sorts.rows(con, sort_id, section="cv")["rows"][0]
            with self.assertRaisesRegex(ValueError, "Coursework takes"):
                sorts.change_row(con, cv["id"], {"section": "coursework", "doc_kind": "cv"})
            moved = sorts.change_row(con, cv["id"], {"section": "document", "doc_kind": "other"})
            self.assertEqual((moved["section"], moved["by"]), ("document", "you"))
            left = sorts.rows(con, sort_id, state="left_out")["rows"][0]
            back = sorts.change_row(con, left["id"], {"section": "document"})
            self.assertEqual((back["state"], back["ticked"]), ("proposed", True))
            with self.assertRaisesRegex(ValueError, "showcase"):
                sorts.change_row(con, back["id"], {"star": True})

    def test_sorting_again_keeps_what_was_accepted(self):
        with home() as (con, _vault, files), fake_ai(NONE):
            folder = files / "Inbox"
            put(folder, "Avery Quillon CV.md", AVERY_CV)
            sort_id = sorted_folder(con, folder)
            sorts.accept(con, sort_id)
            add_and_read(con, sort_id)
            put(folder, "First aid certificate.md", CERTIFICATE)
            sorts.run(con, sort_id)
            states = sorted((r["name"], r["state"]) for state in ("added", "proposed")
                            for r in sorts.rows(con, sort_id, state=state)["rows"])
            self.assertEqual(states, [("Avery Quillon CV.md", "added"), ("First aid certificate.md", "proposed")])

    def test_a_ten_thousand_file_sort_is_counted_and_paged(self):
        with home() as (con, _vault, _files):
            sort_id = sorts.create(con, "folder", ["/made-up"], "made-up")
            found = sorter.Sorted(proposals=[
                sorter.Proposal(Path(f"/made-up/week {n} notes.md"), f"made-up/week {n} notes.md", "upload").place(
                    "lecture" if n % 2 else "document", "notes" if n % 2 else "other", "Made up.") for n in range(BIG)])
            sorts.save(con, sort_id, found)
            sorts.set_state(con, sort_id, "ready")
            counted = {s["id"]: (s["proposed"], s["needs_card"]) for s in sorts.counts(con, sort_id)["sections"]}
            self.assertEqual(counted, {"lecture": (BIG // 2, BIG // 2), "document": (BIG // 2, 0)})
            page = sorts.rows(con, sort_id, section="document", offset=sorts.PAGE)
            self.assertEqual((len(page["rows"]), page["total"]), (sorts.PAGE, BIG // 2))
            self.assertEqual(sorts.tick(con, sort_id, False, "lecture"), BIG // 2)
            self.assertEqual(sorts.accept(con, sort_id), {"accepted": BIG // 2, "need_card": 0})


class Routes(unittest.TestCase):
    def setUp(self):
        self.hal = self.enterContext(HalServer())
        self.folder = Path(paths.record_default())
        self.folder.mkdir(parents=True, exist_ok=True)
        self.enterContext(mock.patch.object(record, "VAULT", self.folder, create=True))
        self.addCleanup(record._CACHE.clear)
        self.enterContext(fake_ai(NONE))
        self.c = self.hal.signed_in()

    def test_a_folder_is_sorted_reviewed_and_accepted_through_the_api(self):
        dump = software_dump(self.hal.home / "Files")
        started = self.c.post("/api/v2/record/sort", {"folders": [str(dump)]})
        self.assertEqual(started.status, 200, started.text)
        self.assertEqual(ended(started.json()["action_id"])["state"], "done")
        sort_id = started.json()["sort"]["id"]
        page = self.c.get(f"/api/v2/record/sort/{sort_id}?section=cv").json()
        self.assertEqual((page["state"], page["total"]), ("ready", 2))
        self.assertIn("education", page["cards"])
        first = page["rows"][0]
        changed = self.c.request("PUT", f"/api/v2/record/sort-rows/{first['id']}", {"ticked": False})
        self.assertEqual((changed.status, changed.json()["ticked"]), (200, False))
        accepted = self.c.post(f"/api/v2/record/sort/{sort_id}/accept", {"section": "cv"}).json()
        self.assertEqual(accepted["accepted"], 1)
        self.assertEqual(ended(accepted["action_id"])["state"], "done")
        listed = self.c.get("/api/v2/record/sources?section=cv").json()
        self.assertEqual([s["state"] for s in listed], ["read"])
        self.assertEqual(self.c.get("/api/v2/record/sources?section=lecture").json(), [])

    def test_a_zip_is_unpacked_and_sorted_and_a_folder_from_afar_is_refused(self):
        import io
        import zipfile

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("Old laptop/Avery Quillon CV.md", AVERY_CV)
        status, out = form(self.hal, self.c, "", [("old laptop.zip", buf.getvalue(), "")])
        self.assertEqual(status, 404, "the sources route is not the sort's")
        status, out = sort_upload(self.hal, self.c, [("old laptop.zip", buf.getvalue())])
        self.assertEqual(status, 200, out)
        self.assertEqual(ended(out["action_id"])["state"], "done")
        page = self.c.get(f"/api/v2/record/sort/{out['sort']['id']}").json()
        self.assertEqual([(r["rel"], r["section"]) for r in page["rows"]],
                         [("old laptop/Old laptop/Avery Quillon CV.md", "cv")])
        self.assertEqual(self.c.post("/api/v2/record/sort", {"folders": ["/no/such/folder"]}).status, 400)
        self.assertEqual(self.c.post("/api/v2/record/sort", {"folders": [str(self.folder)]}).status, 400,
                         "the record itself is never sorted into itself")


def sort_upload(hal, client, files: list[tuple[str, bytes]]):
    """POSTs `files` to the sort's upload as the page does."""
    import http.client
    import uuid

    from support.api import TIMEOUT_S

    boundary = uuid.uuid4().hex
    body = b"".join(f"--{boundary}\r\nContent-Disposition: form-data; name=\"file{i}\"; filename=\"{name}\"\r\n"
                    f"Content-Type: application/octet-stream\r\n\r\n".encode() + data + b"\r\n"
                    for i, (name, data) in enumerate(files)) + f"--{boundary}--\r\n".encode()
    con = http.client.HTTPConnection("127.0.0.1", hal.port, timeout=TIMEOUT_S)
    try:
        con.request("POST", "/api/v2/record/sort/upload", body=body, headers={
            "Host": f"127.0.0.1:{hal.port}", "X-Hal": "1", "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Cookie": "; ".join(f"{k}={v}" for k, v in client.jar.items())})
        r = con.getresponse()
        return r.status, json.loads(r.read())
    finally:
        con.close()


if __name__ == "__main__":
    unittest.main()
