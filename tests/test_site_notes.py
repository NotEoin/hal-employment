"""Notes about job sites: built-in notes give exactly today's behaviour, the most specific note
wins, switches survive updates, failing notes are marked, sharing copies plain words, and the old sign-in
and effort state moves into the sites table. Every site, employer and person here is made up.
"""
from __future__ import annotations

import contextlib
import json
import re
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported
from support.api import HalServer

from jobhunt import autofill, browser, db, extension
from jobhunt.apply import guard
from jobhunt.core import paths
from jobhunt.core.settings import store
from jobhunt.core.settings.catalog import applying
from jobhunt.sites import actions, cards, notes
from jobhunt.sites import behaviour as hal

WORKABLE_FORM = "https://apply.workable.com/harbour-arts-trust/j/1A2B3C/apply/"


class Home(unittest.TestCase):
    """A fresh home with its own database for each test."""

    def setUp(self) -> None:
        tmp = self.enterContext(tempfile.TemporaryDirectory(prefix="hal-sites-"))
        self.home = Path(tmp).resolve()
        self.enterContext(paths.set_override(self.home))
        store.drop_cache()
        self.addCleanup(store.drop_cache)
        paths.ensure()
        self.con = self.enterContext(contextlib.closing(db.connect()))

    def add_job(self, job_id: str, url: str, company: str = "Harbour Arts Trust") -> None:
        self.con.execute("INSERT INTO jobs (id, source, company, title, url, status, first_seen, last_seen) "
                         "VALUES (?, 'manual', ?, 'Gallery Assistant', ?, 'applying', '2026-10-20', '2026-10-20')",
                         (job_id, company, url))
        self.con.commit()


class BuiltInNotes(Home):
    def test_with_only_built_in_notes_hal_behaves_as_today(self) -> None:
        def page(url: str, job_id: str | None = None) -> notes.NoteSet:
            return notes.for_page(url, job_id=job_id, con=self.con)

        # Addresses: Ashby and Lever forms, and the Greenhouse form on an employer's own careers page, each
        # with the address the old `browser.application_url` (before the engines read notes) gave.
        boards = [{"ats": "greenhouse", "name": "Harbour Arts Trust", "slug": "harbourarts"}]
        jobs = [({"id": "ashby:1", "company": "Fenwick Lane", "url": "https://jobs.ashbyhq.com/fenwick/7f3?src=x"},
                 "https://jobs.ashbyhq.com/fenwick/7f3/application"),
                ({"id": "ashby:2", "company": "Fenwick Lane",
                  "url": "https://jobs.ashbyhq.com/fenwick/7f3/application"},
                 "https://jobs.ashbyhq.com/fenwick/7f3/application"),
                ({"id": "lever:1", "company": "Moss Row", "url": "https://jobs.lever.co/mossrow/42/"},
                 "https://jobs.lever.co/mossrow/42/apply"),
                ({"id": "greenhouse:9", "company": "Harbour Arts Trust",
                  "url": "https://harbourarts.example.org/careers?gh_jid=5551234"},
                 "https://job-boards.greenhouse.io/embed/job_app?for=harbourarts&token=5551234"),
                ({"id": "greenhouse:9", "company": "Nobody Listed", "url": "https://other.example.org/c?gh_jid=77"},
                 "https://other.example.org/c?gh_jid=77"),
                ({"id": "manual:1", "company": "Harbour Arts Trust", "url": "https://harbourarts.example.org/c?gh_jid=5"},
                 "https://harbourarts.example.org/c?gh_jid=5"),
                ({"id": "manual:2", "company": "Quayside Books", "url": "https://quaysidebooks.example.org/jobs/2"},
                 "https://quaysidebooks.example.org/jobs/2"),
                ({"id": "manual:3", "company": "Quayside Books", "url": ""}, "")]
        with mock.patch("jobhunt.config.boards", return_value=boards):
            for job, today in jobs:
                with self.subTest(url=job["url"]):
                    self.assertEqual(hal.application_url(job, page(job["url"], job["id"])), today)
                    self.assertEqual(browser.application_url(job), today)

        # Page words: Apply buttons, thanks, already applied, closed, human checks, blocks and code walls.
        words = hal.page_words(page(WORKABLE_FORM))
        for key, old in [("apply", browser.APPLY_BUTTON), ("success", browser.SUCCESS), ("applied", browser.APPLIED),
                         ("closed", browser.CLOSED), ("bot_check", browser.BOT_CHECK), ("blocked", browser.BLOCKED),
                         ("code_wall", browser.CODE_WALL)]:
            self.assertEqual((words[key].pattern, words[key].flags), (old.pattern, old.flags), key)
        self.assertIs(words["next"], guard.NEXT_WORDS)
        self.assertIs(words["submit"], guard.FINAL_WORDS)

        # Closed adverts, including Greenhouse's error=true.
        surveys = [("https://boards.greenhouse.io/harbourarts/jobs/1?error=true", "Jobs", [], 200),
                   ("https://boards.greenhouse.io/harbourarts/jobs/1", "Gallery Assistant", [], 200),
                   ("https://quaysidebooks.example.org/x", "This job is no longer available", [], 200),
                   ("https://quaysidebooks.example.org/x", "Gallery Assistant", ["Position has been filled"], 200),
                   ("https://quaysidebooks.example.org/x", "Gallery Assistant", [], 404)]
        for url, title, heads, status in surveys:
            survey = {"url": url, "title": title, "headings": heads}
            self.assertEqual(hal.closed(survey, status, page(url)), browser._closed(survey, status), url)

        # Frames: which may hold the form, and the hosts of embedded forms.
        for frame in ["https://www.google.com/recaptcha/api2/anchor", "https://js.stripe.com/v3/", "about:blank",
                      "https://boards.greenhouse.io/embed/job_app?for=harbourarts", "https://widget.intercom.io/x",
                      "https://apply.workable.com/harbour-arts-trust/j/1/", "https://cookie-consent.example.org/"]:
            self.assertEqual(hal.frame_ok(frame, page(WORKABLE_FORM)), not browser.NOT_FORM_FRAME.search(frame), frame)
        # the one change the notes make on purpose: Google Forms are read
        self.assertTrue(hal.frame_ok("https://docs.google.com/forms/d/e/1FAIpQ/viewform", page(WORKABLE_FORM)))
        self.assertEqual(hal.form_hosts(page(WORKABLE_FORM)), browser.ATS_FRAME)

        # Cookie banners, lists, timing and holding pages.
        self.assertEqual([p.pattern for p in hal.cookie_patterns(page(WORKABLE_FORM))],
                         [p.pattern for p in browser.COOKIE_BUTTONS])
        self.assertTrue(hal.no_escape(page(WORKABLE_FORM)))
        self.assertEqual(hal.timing(page(WORKABLE_FORM)),
                         {"page_timeout_s": browser.NAV_TIMEOUT // 1000, "settle_s": 15,
                          "max_presses": browser.MAX_PRESSES, "fill_timeout_s": extension.FILL_WAIT})
        self.assertEqual(hal.wait_redirect("https://www.adzuna.co.uk/land/ad/123", page("https://www.adzuna.co.uk/land/ad/123")),
                         {"host": "adzuna.", "until": "host_changes", "max_s": 15.0})
        for url in ("https://www.adzuna.co.uk/jobs/details/123", WORKABLE_FORM):
            self.assertIsNone(hal.wait_redirect(url, page(url)), url)

        # Typing: dates and searched lists.
        for value in ("2026-10-20", "20/10/2026", "20-10-2026", "20 October 2026", "20 Oct 2026", "October 2026",
                      "Oct 2026", "10/2026", "next month", " 3/4/2026 "):
            self.assertEqual(hal.date_value(value, page(WORKABLE_FORM)), browser._date(value), value)
        for value in ("Leeds, West Yorkshire, United Kingdom", "Leeds", "Hebden Bridge", "Leeds,"):
            parts = [p.strip() for p in value.split(",") if p.strip()]
            today = list(dict.fromkeys([value, *parts, value.split()[0].strip(",")]))
            self.assertEqual(hal.search_terms(value, page(WORKABLE_FORM)), today, value)

        # How did you hear about this job, by where Hal found it.
        for source in [*autofill.HOW_HEARD, "manual", "careers-page"]:
            self.assertEqual(hal.how_heard(source, page(WORKABLE_FORM)),
                             autofill.HOW_HEARD.get(source, "Company careers page"), source)

        # Job boards.
        for url in ("https://www.indeed.co.uk/viewjob?jk=1", "https://www.reed.co.uk/jobs/2", "https://uk.talent.com/x",
                    "https://www.totaljobs.com/job/3", WORKABLE_FORM, "https://quaysidebooks.example.org/"):
            host = url.split("/")[2]
            self.assertEqual(hal.job_board(url, page(url)), any(a in host for a in applying.AGGREGATORS), url)

    def test_every_built_in_note_says_where_it_came_from_and_can_be_switched_off(self) -> None:
        listed = cards.listing(self.con)
        every = [n for c in listed for n in c["notes"]]
        self.assertEqual({n["id"] for n in every} >= {n.id for n in notes.seed_notes()}, True)
        for n in every:
            self.assertTrue(n["origin_label"].startswith("Built in"), n)
            self.assertTrue(n["on"])
        greenhouse = next(c for c in listed if c["key"] == "ats:greenhouse")
        self.assertEqual(greenhouse["name"], "All Greenhouse sites")
        self.assertIn("Built in (Greenhouse)", {n["origin_label"] for n in greenhouse["notes"]})

    def test_a_switched_off_built_in_note_stays_off_after_an_update(self) -> None:
        notes.switch("seed:workable-no-escape", False, con=self.con)
        self.assertFalse(hal.no_escape(notes.for_page(WORKABLE_FORM, con=self.con)))

        # An update ships better words and a changed action for the same note id.
        newer = self.home / "seed.toml"
        text = notes.SEED_PATH.read_text(encoding="utf-8")
        text = text.replace('act = { no_escape = true }', 'act = { no_escape = true, scroll_list = true }')
        text = text.replace("Close dropdown lists by clicking the form, never with Escape "
                            "(Escape closed Workable's form)",
                            "Close lists by clicking beside them, never with Escape")
        newer.write_text(text, encoding="utf-8")
        with mock.patch.object(notes, "SEED_PATH", newer):
            page = notes.for_page(WORKABLE_FORM, con=self.con)
            self.assertFalse(hal.no_escape(page))
            self.assertIn("seed:workable-no-escape", [n.id for n in page.off])
            note = next(n for c in cards.listing(self.con) for n in c["notes"] if n["id"] == "seed:workable-no-escape")
            self.assertEqual((note["on"], note["text"]),
                             (False, "Close lists by clicking beside them, never with Escape"))
            # switched back on, it acts as the update says
            notes.switch("seed:workable-no-escape", True, con=self.con)
            on = notes.for_page(WORKABLE_FORM, con=self.con).first("widget")
            self.assertEqual(on.act, {"no_escape": True, "scroll_list": True})

    def test_a_reworded_built_in_note_keeps_its_words_and_takes_the_updated_action(self) -> None:
        notes.edit("seed:lever-apply", "Lever: go straight to the form", con=self.con)
        page = notes.for_page("https://jobs.lever.co/mossrow/42", con=self.con)
        self.assertEqual((page.first("open_url").text, page.first("open_url").act),
                         ("Lever: go straight to the form", {"append": "/apply"}))

    def test_built_in_notes_cannot_be_deleted(self) -> None:
        with self.assertRaises(PermissionError):
            notes.delete("seed:lever-apply", con=self.con)


class Scopes(Home):
    def test_the_most_specific_note_wins_and_a_conflict_is_shown(self) -> None:
        notes.add("ats", "workable", "field_transform", {"label": "location"}, {"transform": "city_only"},
                  "In \"Location\", type only the city", con=self.con)
        mine = notes.add("site", "workable.com", "field_transform", {"label": "location"}, {"transform": "max_len:40"},
                         "In \"Location\", type at most 40 letters", con=self.con)
        job = notes.add("job", WORKABLE_FORM.rsplit("/apply", 1)[0], "field_transform", {"label": "^location$"},
                        {"transform": "digits_only"}, "Only digits here", con=self.con)
        page = notes.for_page(WORKABLE_FORM, con=self.con)
        self.assertEqual(page.first("field_transform", label="Location").id, job)
        self.assertEqual([n.id for n in page.for_field("Current location")], [mine, *[
            n.id for n in page.for_field("Current location") if n.scope_kind == "ats"]])
        self.assertIsNone(page.first("field_transform", label="Salary"))
        # another Workable employer gets the system-wide note, not this site's
        other = notes.for_page("https://apply.workable.com/moss-row/j/9/", con=self.con)
        self.assertEqual(other.first("field_transform", label="Location").text,
                         "In \"Location\", type at most 40 letters")
        elsewhere = notes.for_page("https://jobs.lever.co/mossrow/1", con=self.con)
        self.assertIsNone(elsewhere.first("field_transform", label="Location"))

        conflicts = notes.for_page("https://apply.workable.com/moss-row/j/9/", con=self.con).conflicts()
        self.assertEqual(len(conflicts), 1)
        self.assertEqual(conflicts[0]["used"], mine)
        self.assertIn("Hal uses \"In \"Location\", type at most 40 letters\" (workable.com) instead of "
                      "\"In \"Location\", type only the city\" (every Workable site)", conflicts[0]["words"])
        card = next(c for c in cards.listing(self.con) if c["key"] == "workable.com")
        self.assertEqual([c["used"] for c in card["conflicts"]], [mine])
        # a site's own timing over the built-in default is the point of the note, not a conflict
        notes.add("site", "workable.com", "timing", {}, {"settle_s": 30}, "This site is slow", con=self.con)
        page = notes.for_page(WORKABLE_FORM, con=self.con)
        self.assertEqual(hal.timing(page)["settle_s"], 30)
        self.assertEqual(hal.timing(page)["max_presses"], browser.MAX_PRESSES)
        self.assertFalse([c for c in page.conflicts() if c["kind"] == "timing"])

    def test_each_workday_employer_and_greenhouse_board_is_its_own_site(self) -> None:
        self.assertEqual(notes.site_key("https://fenwick.wd3.myworkdayjobs.com/en-GB/careers/job/1"),
                         "fenwick.wd3.myworkdayjobs.com")
        self.assertEqual(notes.site_key("https://mossrow.wd5.myworkdayjobs.com/x"), "mossrow.wd5.myworkdayjobs.com")
        self.assertEqual(notes.site_key("https://boards.greenhouse.io/harbourarts/jobs/1"),
                         "boards.greenhouse.io/harbourarts")
        self.assertEqual(notes.site_key("https://job-boards.greenhouse.io/embed/job_app?for=harbourarts&token=1"),
                         "boards.greenhouse.io/harbourarts")
        self.assertEqual(notes.site_key("https://secure.reed.co.uk/apply"), "reed.co.uk")
        notes.seen("https://fenwick.wd3.myworkdayjobs.com/x", account="signed_in", con=self.con)
        notes.seen("https://mossrow.wd5.myworkdayjobs.com/x", con=self.con)
        accounts = dict(self.con.execute("SELECT key, account FROM sites").fetchall())
        self.assertEqual(accounts, {"fenwick.wd3.myworkdayjobs.com": "signed_in",
                                    "mossrow.wd5.myworkdayjobs.com": "unknown"})

    def test_a_note_never_answers_a_private_question(self) -> None:
        with self.assertRaises(notes.NoteError):
            notes.add("site", "workable.com", "field_answer",
                      {"label": "Do you consider yourself to have a disability"},
                      {"value": "No"}, "Answer the disability question", con=self.con)

    def test_a_widget_note_has_no_enter_option(self) -> None:
        with self.assertRaises(notes.NoteError):
            notes.add("site", "workable.com", "widget", {}, {"press_enter": True}, "Press Enter after typing",
                      con=self.con)

    def test_a_note_hal_learned_waits_for_your_yes(self) -> None:
        self.add_job("manual:moss", "https://apply.workable.com/moss-row/j/9/")
        first = notes.propose("workable.com", "press_first", {}, {"control": "Apply for this job"},
                              "Press \"Apply for this job\" before looking for the form", ["manual:moss"])
        again = notes.propose("workable.com", "press_first", {}, {"control": "Apply for this job"},
                              "Press \"Apply for this job\" before looking for the form", ["manual:other"])
        self.assertEqual(first, again)
        self.assertIsNone(notes.for_page(WORKABLE_FORM, con=self.con).first("press_first"))
        card = next(c for c in cards.listing(self.con) if c["key"] == "workable.com")
        self.assertEqual([(n["proposed"], n["origin_label"]) for n in card["notes"] if n["id"] == first],
                         [(True, "Hal learned (waiting for you)")])
        notes.accept(first, con=self.con)
        self.assertEqual(notes.for_page(WORKABLE_FORM, con=self.con).first("press_first").id, first)
        row = self.con.execute("SELECT evidence FROM site_notes WHERE id = ?", (first,)).fetchone()
        self.assertEqual(json.loads(row["evidence"]), ["manual:moss", "manual:other"])


class Uses(Home):
    def test_a_note_that_keeps_failing_is_marked_may_be_out_of_date(self) -> None:
        self.add_job("manual:moss", "https://apply.workable.com/moss-row/j/9/")
        note_id = notes.add("site", "workable.com", "press_first", {}, {"control": "Apply for this job"},
                            "Press \"Apply for this job\" first", con=self.con)

        def flags() -> tuple[bool, int, int, int]:
            n = next(n for c in cards.listing(self.con) for n in c["notes"] if n["id"] == note_id)
            return n["may_be_out_of_date"], n["used"], n["worked"], len(self.con.execute(
                "SELECT 1 FROM site_events WHERE note_id = ?", (note_id,)).fetchall())

        notes.used(note_id, "manual:moss", ok=False)
        notes.used(note_id, "manual:moss", ok=False)
        notes.used(note_id, "manual:moss", ok=False)
        self.assertFalse(flags()[0], "three failures with no use before them is a note that never worked")
        for _ in range(3):
            notes.used(note_id, "manual:moss", ok=True)
        for _ in range(2):
            notes.used(note_id, "manual:moss", ok=False)
        self.assertFalse(flags()[0])
        notes.used(note_id, "manual:moss", ok=False, detail="no button called Apply for this job")
        self.assertEqual(flags(), (True, 9, 3, 9))
        event = self.con.execute("SELECT site, what, detail FROM site_events WHERE note_id = ? ORDER BY id DESC",
                                 (note_id,)).fetchone()
        self.assertEqual(tuple(event), ("workable.com", "note_failed", "no button called Apply for this job"))
        notes.used(note_id, "manual:moss", ok=True)
        self.assertFalse(flags()[0])
        self.assertTrue(notes.for_page(WORKABLE_FORM, con=self.con).first("press_first"), "a stale note stays on")

    def test_a_built_in_note_counts_its_uses_without_changing_its_words(self) -> None:
        self.add_job("manual:moss", "https://jobs.lever.co/mossrow/42")
        notes.used("seed:lever-apply", "manual:moss", ok=True)
        page = notes.for_page("https://jobs.lever.co/mossrow/42", con=self.con)
        self.assertEqual(page.first("open_url").text, "Open the application form directly, not the advert page")
        lever = next(n for c in cards.listing(self.con) for n in c["notes"] if n["id"] == "seed:lever-apply")
        self.assertEqual((lever["used"], lever["worked"]), (1, 1))

    def test_the_trace_says_which_note_and_from_when(self) -> None:
        note_id = notes.add("site", "civic-example.org.uk", "field_transform", {"label": "location"},
                            {"transform": "city_only"}, "In \"Location\", type only the city", con=self.con)
        page = notes.for_page("https://civic-example.org.uk/apply", con=self.con)
        note = page.first("field_transform", label="Location")
        self.assertEqual(note.id, note_id)
        day = notes.day_words(note.created)
        self.assertEqual(notes.because(note), f"because of your note from {day} (civic-example.org.uk)")
        self.assertEqual(hal.transform("Leeds, United Kingdom", note.act["transform"]), "Leeds")
        built_in = notes.for_page("https://jobs.lever.co/x/1", con=self.con).first("open_url")
        self.assertEqual(notes.because(built_in), "because of a built-in note for every Lever site")


class Sharing(Home):
    def test_share_this_note_copies_plain_words(self) -> None:
        note_id = notes.add("site", "civic-example.org.uk", "field_transform", {"label": "location"},
                            {"transform": "city_only"}, "In \"Location\", type only the city", con=self.con)
        self.assertEqual(notes.share_text(note_id, con=self.con),
                         "Hal note for civic-example.org.uk: In \"Location\", type only the city")
        self.assertEqual(notes.share_text("seed:workable-no-escape", con=self.con),
                         "Hal note for every site: Close dropdown lists by clicking the form, never with Escape "
                         "(Escape closed Workable's form)")
        answer = notes.add("site", "civic-example.org.uk", "field_answer", {"label": "how did you hear"},
                           {"value": "A friend"}, "Answer \"How did you hear\" with \"A friend\"", con=self.con)
        with self.assertRaises(PermissionError):
            notes.share_text(answer, con=self.con)


class Routes(unittest.TestCase):
    def setUp(self) -> None:
        self.hal = self.enterContext(HalServer())
        self.c = self.hal.signed_in()

    def test_notes_can_be_listed_switched_reworded_shared_and_deleted(self) -> None:
        r = self.c.post("/api/v2/sites/notes",
                        {"site": "civic-example.org.uk", "text": "Their form times out after 20 minutes"})
        self.assertEqual(r.status, 200, r.text)
        note_id = r.json()["id"]
        cards_now = self.c.get("/api/v2/sites").json()
        card = next(c for c in cards_now if c["key"] == "civic-example.org.uk")
        self.assertEqual([(n["text"], n["origin_label"], n["on"]) for n in card["notes"]],
                         [("Their form times out after 20 minutes", "You told Hal", True)])
        self.assertEqual(cards_now[-1]["name"], "Every site")
        quoted = note_id.replace(":", "%3A")
        r = self.c.request("PUT", f"/api/v2/sites/notes/{quoted}", {"on": False})
        self.assertEqual((r.status, r.json()["on"]), (200, False), r.text)
        r = self.c.request("PUT", f"/api/v2/sites/notes/{quoted}", {"text": "Save often: the form times out"})
        self.assertEqual(r.json()["text"], "Save often: the form times out")
        r = self.c.get(f"/api/v2/sites/notes/{quoted}/share")
        self.assertEqual(r.json(), {"text": "Hal note for civic-example.org.uk: Save often: the form times out"})
        self.assertEqual(self.c.request("DELETE", "/api/v2/sites/notes/seed%3Alever-apply").status, 403)
        self.assertEqual(self.c.request("DELETE", f"/api/v2/sites/notes/{quoted}").status, 200)
        self.assertEqual(self.c.request("PUT", f"/api/v2/sites/notes/{quoted}", {"on": True}).status, 404)


class ImportState(Home):
    def test_the_old_sign_ins_and_effort_move_into_the_sites_table_per_employer(self) -> None:
        self.add_job("manual:fen", "https://fenwick.wd3.myworkdayjobs.com/en-GB/careers/job/1")
        self.add_job("manual:moss", "https://mossrow.wd5.myworkdayjobs.com/careers/job/2")
        old = json.dumps(["myworkdayjobs.com", "quayside-example.org"])
        (paths.data_dir() / actions.SIGNED_IN_FILE).write_text(old, encoding="utf-8")
        db.log(self.con, "manual:moss", "effort", "civic-example.org.uk")
        db.log(self.con, "manual:fen", "effort", "civic-example.org.uk")
        self.con.commit()
        counts = actions.import_state(self.con)
        rows = {r["key"]: (r["account"], r["effort"]) for r in self.con.execute("SELECT * FROM sites")}
        self.assertEqual(rows, {"myworkdayjobs.com": ("signed_in", 0),
                                "fenwick.wd3.myworkdayjobs.com": ("signed_in", 0),
                                "mossrow.wd5.myworkdayjobs.com": ("signed_in", 0),
                                "quayside-example.org": ("signed_in", 0), "civic-example.org.uk": ("unknown", 2)})
        self.assertEqual(counts, {"signed_in": 4, "effort": 1})
        self.assertTrue((paths.data_dir() / actions.SIGNED_IN_FILE).exists(), "the old file stays for the engines")
        self.assertEqual(actions.import_state(self.con), counts, "running it again changes nothing")


class SeedFile(unittest.TestCase):
    def test_every_built_in_note_has_a_unique_id_and_passes_the_checks(self) -> None:
        seeds = notes.seed_notes()
        self.assertEqual(len({n.id for n in seeds}), len(seeds))
        for n in seeds:
            notes.validate(n.kind, n.scope_kind, n.cond, n.act, n.text)
            for key in ("pattern", "apply", "closed"):
                if isinstance(n.act.get(key), str):
                    re.compile(n.act[key])

    def test_a_repeated_id_in_the_seed_is_refused(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "seed.toml"
            shutil.copyfile(notes.SEED_PATH, bad)
            with bad.open("a", encoding="utf-8") as f:
                f.write('\n[[note]]\nid = "lever-apply"\nkind = "note"\nscope = "global"\ntext = "Twice"\n')
            with self.assertRaises(notes.NoteError):
                notes.seed_notes(bad)


if __name__ == "__main__":
    unittest.main()
