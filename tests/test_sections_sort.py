"""The review of a sort: proposals kept for several sittings, paged and counted, changed by the person, and only the
accepted ones made sources, each read by its own section's reader.

The made-up dumps are `test_sections_sorter`'s: Avery Quillon (software) and Marnie Quillfeather (history).
"""
import json
import shutil
import time
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.api import HalServer
from support.fake_ai import fake_ai
from test_ingest import home, put
from test_sections_routes import form
from test_sections_sorter import AVERY_CV, CERTIFICATE, history_dump, software_dump

from jobhunt import db, record
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
            sorts.tick(con, sort_id, True, "lecture")
            sorts.accept(con, sort_id)
            add_and_read(con, sort_id)
            got = sections_of(con)
            self.assertNotIn(("letter", "cover_letter"), got, "an unticked row stays out")
            self.assertIn(("coursework", "dissertation"), got)
            self.assertIn(("project", "repo"), got)
            self.assertEqual(sorts.counts(con, sort_id)["proposed"], 1, "only the unticked letter is left")
            left = sorts.rows(con, sort_id, state="left_out")["rows"]
            self.assertTrue(any("private" in r["why"] for r in left))

    def test_accepting_a_sort_makes_a_missing_record_folder(self):
        # regression: accepting a sort on a fresh install where setup was skipped failed in `record.sync` with
        # "no employment record at ..." (2026-11-09)
        with home() as (con, vault, files), fake_ai(NONE):
            sort_id = sorted_folder(con, software_dump(files))
            shutil.rmtree(vault)
            self.assertEqual(sorts.accept(con, sort_id, "cv")["accepted"], 2)
            self.assertTrue((vault / "Start Here.md").is_file())
            add_and_read(con, sort_id)
            record.sync()

    def test_lecture_material_starts_unticked_and_the_rest_ticked(self):
        with home() as (con, _vault, files), fake_ai(NONE):
            sort_id = sorted_folder(con, software_dump(files))
            ticked = {s["id"]: (s["ticked"], s["proposed"]) for s in sorts.counts(con, sort_id)["sections"]}
            self.assertEqual(ticked["lecture"], (0, 3), "lecture notes only teach Hal what was studied")
            self.assertEqual(ticked["cv"], (2, 2))

    def test_the_read_time_is_measured_from_recent_reads_else_a_stated_default(self):
        with home() as (con, _vault, _files):
            self.assertEqual(sorts.read_estimate(con), {"seconds": sorts.READ_S_DEFAULT, "measured": False})
            with con:
                for n, (started, ended, done) in enumerate((("2026-11-09T09:00:00Z", "2026-11-09T09:01:00Z", 6),
                                                            ("2026-11-09T10:00:00Z", "2026-11-09T10:00:30Z", 4))):
                    con.execute("INSERT INTO actions (id, kind, state, created, started, ended, done_n) VALUES "
                                "(?, 'record.sort_accept', 'done', ?, ?, ?, ?)", (f"a{n}", started, started, ended,
                                                                                    done))
            self.assertEqual(sorts.read_estimate(con), {"seconds": 9.0, "measured": True}, "90 s for 10 files")

    def test_a_step_of_several_sections_is_listed_ticked_and_accepted_together(self):
        with home() as (con, _vault, files), fake_ai(NONE):
            sort_id = sorted_folder(con, software_dump(files))
            papers = "cv,letter"
            listed = {r["section"] for r in sorts.rows(con, sort_id, section=papers)["rows"]}
            self.assertEqual(listed, {"cv", "letter"})
            self.assertEqual(sorts.tick(con, sort_id, False, ["letter"]), 1)
            self.assertEqual(sorts.accept(con, sort_id, ["cv", "letter"]), {"accepted": 2, "need_card": 0})
            with self.assertRaises(LookupError):
                sorts.rows(con, sort_id, section="cv,nowhere")

    def test_home_counts_the_sorts_waiting_to_be_checked(self):
        from jobhunt.sections import home as sections_home

        with home() as (con, _vault, files), fake_ai(NONE):
            self.assertEqual(sections_home.sections(con), {"counts": {"record": 0}})
            sort_id = sorted_folder(con, software_dump(files))
            self.assertEqual(sorts.waiting(con), [sort_id])
            self.assertEqual(sections_home.sections(con), {"counts": {"record": 1}})
            with con:
                con.execute("UPDATE record_sort_rows SET state = 'left_out' WHERE sort_id = ?", (sort_id,))
            self.assertEqual(sorts.waiting(con), [], "a sort with nothing left to check is not counted")

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

    def test_one_project_in_two_folders_of_a_sort_is_one_row(self):
        # the same project in a repos folder and a cloud drive came out as two projects, as copies were only
        # compared inside each folder sorted (2026-11-09)
        readme = "# Wrenbot\n\n" + "Wrenbot plans river boat rotas from a shared calendar and tide tables. " * 12
        with home() as (con, _vault, files), fake_ai(NONE):
            older, newer = files / "drive" / "personal projects" / "Wrenbot", files / "repos" / "Wrenbot"
            put(older, "README.md", readme)
            put(older, "wrenbot.py", "def plan():\n    return []\n")
            put(newer, "README.md", readme + "\nNow with a web page.\n")
            put(newer, "wrenbot.py", "def plan():\n    return ['tide']\n")
            put(newer, "web.py", "def page():\n    return 'rota'\n")
            sort_id = sorts.create(con, "folder", [str(files / "drive"), str(files / "repos")], "two folders")
            sorts.run(con, sort_id)
            rows = con.execute("SELECT rel, state FROM record_sort_rows WHERE sort_id = ? AND section = 'project'",
                               (sort_id,)).fetchall()
            self.assertEqual(sorted(state for _rel, state in rows), ["copy", "proposed"], rows)

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
        self.assertEqual([c["title"] for c in page["courses"]], ["BSc Computing"])
        course = page["courses"][0]["key"]
        self.assertEqual(self.c.post(f"/api/v2/record/sort/{sort_id}/courses", {}).status, 400)
        self.assertEqual(self.c.post(f"/api/v2/record/sort/{sort_id}/courses", {"key": "Nowhere"}).status, 404)
        added = self.c.post(f"/api/v2/record/sort/{sort_id}/courses", {"key": course, "subject": "Computer Science"})
        self.assertEqual((added.status, added.json()["card"]["title"]), (200, "BSc Computer Science"), added.text)
        first = page["rows"][0]
        changed = self.c.request("PUT", f"/api/v2/record/sort-rows/{first['id']}", {"ticked": False})
        self.assertEqual((changed.status, changed.json()["ticked"]), (200, False))
        self.assertEqual(self.c.post(f"/api/v2/record/sort-rows/{first['id']}/keep", {}).status, 400,
                         "a row Hal keeps is no older copy")
        self.assertEqual(self.c.post("/api/v2/record/sort-rows/999999/keep", {}).status, 404)
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


class OldWaysIn(unittest.TestCase):
    """Every older way of adding files now sorts them, or adds a source in its section; none reads a file in."""

    def setUp(self):
        self.hal = self.enterContext(HalServer())
        self.folder = Path(paths.record_default())
        self.folder.mkdir(parents=True, exist_ok=True)
        self.enterContext(mock.patch.object(record, "VAULT", self.folder, create=True))
        self.addCleanup(record._CACHE.clear)
        self.enterContext(fake_ai(NONE))

    def sources(self) -> int:
        """How many sources the record holds."""
        with closing(db.connect()) as con:
            return con.execute("SELECT count(*) FROM record_sources").fetchone()[0]

    def test_the_old_folder_route_sorts_and_reads_nothing_in(self):
        dump = history_dump(self.hal.home / "Files")
        c = self.hal.signed_in()
        out = c.post("/api/v2/record/ingest", {"folders": [{"path": str(dump), "origin": "mine"}]})
        self.assertEqual(out.status, 200, out.text)
        self.assertEqual(ended(out.json()["action_id"])["state"], "done")
        page = c.get(f"/api/v2/record/sort/{out.json()['sort_id']}").json()
        self.assertGreater(page["counts"]["proposed"], 0)
        self.assertEqual(c.get("/api/v2/record/ingest").json()["state"], "none", "the bulk read never ran")
        self.assertEqual(self.sources(), 0)
        self.assertFalse((self.folder / "Sources").exists())

    def test_the_local_key_sorts_a_folder_and_accepts_it_without_a_window(self):
        from jobhunt.web import auth

        put(self.hal.home / "Files", "Avery Quillon CV.md", AVERY_CV)
        c = self.hal.client()
        key = {"Authorization": f"Bearer {auth.local_api_key()}"}
        self.assertEqual(c.get("/api/v2/record/sort").status, 401)
        out = c.post("/api/v2/record/sort", {"folders": [str(self.hal.home / "Files")]}, headers=key).json()
        self.assertEqual(ended(out["action_id"])["state"], "done")
        accepted = c.post(f"/api/v2/record/sort/{out['sort']['id']}/accept", {}, headers=key).json()
        self.assertEqual(ended(accepted["action_id"])["state"], "done")
        self.assertEqual([s["section"] for s in c.get("/api/v2/record/sources", headers=key).json()], ["cv"])

    def test_an_upload_to_the_old_route_is_sorted_and_a_zips_escape_refused(self):
        import io
        import zipfile

        from test_evidence_routes import upload

        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w") as z:
            z.writestr("Year 1/Avery Quillon CV.md", AVERY_CV)
            z.writestr("../../escape.md", "# Out")
        status, out = upload(self.hal, self.hal.signed_in(),
                             {"uni.zip": buf.getvalue(), "award.md": CERTIFICATE.encode()})
        self.assertEqual(status, 200, out)
        self.assertEqual(ended(out["action_id"])["state"], "done")
        self.assertTrue(any("outside the zip" in p for p in out["problems"]))
        self.assertFalse((self.hal.home / "escape.md").exists())
        rows = self.hal.signed_in().get(f"/api/v2/record/sort/{out['sort']['id']}").json()["rows"]
        self.assertEqual(sorted((r["name"], r["section"]) for r in rows),
                         [("Avery Quillon CV.md", "cv"), ("award.md", "document")])
        self.assertEqual(self.sources(), 0)
        self.assertFalse((self.folder / "Inbox").exists(), "nothing goes straight into the record's Inbox")

    def test_pasted_links_become_project_sources_by_their_address(self):
        from jobhunt.sections import actions as section_actions

        c = self.hal.signed_in()
        self.assertEqual(c.post("/api/v2/record/links", {"urls": []}).status, 400)
        with mock.patch.object(section_actions, "queue", return_value="a1") as queue:
            out = c.post("/api/v2/record/links", {"urls": "https://github.com/averyq https://github.com/averyq/tide "
                                                          "https://example.org/zine"})
        self.assertEqual(out.status, 200, out.text)
        self.assertEqual(len(queue.call_args.args[0]), 3)
        with closing(db.connect()) as con:
            kinds = [r[0] for r in con.execute("SELECT doc_kind FROM record_sources WHERE section = 'project' "
                                               "ORDER BY id")]
        self.assertEqual(kinds, ["github_account", "repo_url", "web_page"])


class DropFolder(unittest.TestCase):
    def test_new_files_in_the_drop_folder_get_proposals_and_are_never_read_in(self):
        from jobhunt.ingest import sources as ingest_sources
        from jobhunt.sections import sort_actions

        with home() as (con, _vault, _files), fake_ai(NONE):
            drop = ingest_sources.drop_folder()
            put(drop, "Avery Quillon CV.md", AVERY_CV)
            sort_id = sort_actions.queue_inbox()
            self.assertIsNotNone(sort_id)
            runner.idle(WAIT_S)
            self.assertEqual([r["name"] for r in sorts.rows(con, sort_id, section="cv")["rows"]],
                             ["Avery Quillon CV.md"])
            self.assertTrue((drop / "Avery Quillon CV.md").exists(), "the file stays where the person put it")
            self.assertEqual(sections_of(con), [])
            self.assertIsNone(sort_actions.queue_inbox(), "a file proposed once is not proposed again")
            put(drop, "First aid certificate.md", CERTIFICATE)
            again = sort_actions.queue_inbox()
            runner.idle(WAIT_S)
            self.assertEqual([r["name"] for r in sorts.rows(con, again)["rows"]], ["First aid certificate.md"])


class CommandLine(unittest.TestCase):
    """`jobhunt sort`, `jobhunt sources` and `jobhunt ingest` through a stand-in for the running Hal."""

    def run_cli(self, fn, replies: dict) -> tuple[list, str]:
        """Runs `fn` with each call to Hal answered from `replies` by (method, path start); (calls, printed)."""
        import contextlib
        import io

        from jobhunt.cli import record as cli_record
        from jobhunt.cli import sort as cli_sort

        calls = []

        def hal(method, path, body=None):
            calls.append((method, path, body))
            return 200, next(v for (m, start), v in replies.items() if m == method and path.startswith(start))

        out = io.StringIO()
        with mock.patch.object(cli_record, "_hal", hal), mock.patch.object(cli_sort.time, "sleep"), \
                contextlib.redirect_stdout(out):
            fn()
        return calls, out.getvalue()

    def test_sort_proposes_and_accept_all_adds_what_is_ticked(self):
        from jobhunt.cli import sort as cli_sort

        ready = {"id": 7, "label": "Avery stuff", "state": "ready", "counts": {
            "sections": [{"id": "cv", "label": "CVs", "proposed": 2, "ticked": 2, "needs_card": 0}],
            "proposed": 2, "left_out": 1, "copy": 1, "accepted": 0, "added": 0, "failed": 0, "total": 4}}
        calls, printed = self.run_cli(lambda: cli_sort.sort(["Avery stuff"]), {
            ("POST", "/api/v2/record/sort"): {"sort": {"id": 7}, "action_id": "a1"},
            ("GET", "/api/v2/record/sort/7"): ready})
        self.assertEqual(Path(calls[0][2]["folders"][0]).name, "Avery stuff")
        self.assertIn("CVs: 2 proposed, 2 ticked", printed)
        self.assertIn("Nothing is added yet", printed)
        calls, printed = self.run_cli(lambda: cli_sort.sort([], accept_all=True), {
            ("GET", "/api/v2/record/sort/7"): {**ready, "counts": {**ready["counts"], "added": 2}},
            ("GET", "/api/v2/record/sort"): {"sorts": [ready]},
            ("POST", "/api/v2/record/sort/7/accept"): {"accepted": 2, "need_card": 0, "action_id": "a2"}})
        self.assertEqual([c[:2] for c in calls][:2], [("GET", "/api/v2/record/sort"),
                                                      ("POST", "/api/v2/record/sort/7/accept")])
        self.assertIn("Added: 2", printed)

    def test_ingest_of_folders_now_sorts_them(self):
        from jobhunt.cli import record as cli_record

        ready = {"id": 3, "label": "Mixed", "state": "ready", "counts": {
            "sections": [], "proposed": 0, "left_out": 0, "copy": 0, "accepted": 0, "added": 0, "failed": 0,
            "total": 0}}
        calls, printed = self.run_cli(lambda: cli_record.ingest(["Mixed"], mine=["Mine"]), {
            ("POST", "/api/v2/record/ingest"): {"action_id": "a1", "sort_id": 3},
            ("GET", "/api/v2/record/sort/3"): ready})
        self.assertEqual([Path(f).name for f in calls[0][2]["folders"]], ["Mixed", "Mine"])
        self.assertIn("adds nothing yet", printed)

    def test_sources_lists_adds_a_file_and_reads_again(self):
        from jobhunt.cli import sort as cli_sort

        listed = [{"id": 4, "section_label": "CVs", "title": "Avery Quillon", "doc_kind_label": "CV",
                   "state_words": "Read"}]
        _, printed = self.run_cli(lambda: cli_sort.sources("list", [], section="cv"),
                                  {("GET", "/api/v2/record/sources?section=cv"): listed})
        self.assertIn("    4  CVs: Avery Quillon (CV, Read)", printed)
        cv = Path(self.enterContext(__import__("tempfile").TemporaryDirectory())) / "CV.md"
        cv.write_text(AVERY_CV, encoding="utf-8")
        sent = []
        with mock.patch.object(cli_sort, "_form", lambda path, files, fields: sent.append((path, files)) or (
                200, {"added": [{"id": 5, "title": "Avery Quillon"}], "duplicates": []})):
            _, printed = self.run_cli(lambda: cli_sort.sources("add", ["cv", str(cv)]), {})
        self.assertEqual((sent[0][0], sent[0][1][0][0]), ("/api/v2/record/sources/cv", "CV.md"))
        self.assertIn("Added Avery Quillon (source 5)", printed)
        calls, _ = self.run_cli(lambda: cli_sort.sources("add", ["project", "https://example.org/zine"],
                                                         kind="web_page"),
                                {("POST", "/api/v2/record/sources/project/link"): {"added": [], "duplicates": []}})
        self.assertEqual(calls[0][2], {"locator": "https://example.org/zine", "doc_kind": "web_page"})
        calls, _ = self.run_cli(lambda: cli_sort.sources("read", ["5"]),
                                {("POST", "/api/v2/record/sources/5/reread"): {"action_id": "a3"}})
        self.assertEqual(calls[0][:2], ("POST", "/api/v2/record/sources/5/reread"))


if __name__ == "__main__":
    unittest.main()
