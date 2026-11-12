"""Duplicate experiences: cards that are one project or course under different names are merged after a read,
with a way back.

Each test runs in its own temporary home with synthetic cards; the AI is the fake one, answering the
`profile_same` prompt with the groups each test gives.
"""
import json
import os
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets a temporary home before jobhunt is imported
from support.api import HalServer
from support.fake_ai import fake_ai

from jobhunt import db
from jobhunt.core import paths, settings
from jobhunt.core.settings import store as settings_store
from jobhunt.ingest import items
from jobhunt.profile import duplicates, store, tiers

NOW = "2026-11-08T09:00:00Z"
CV = "Sources/CVs and letters/Avery Quillon CV.md"
README = "Sources/Code/gauge_bot.md"
REPORT = "Sources/Coursework/Tide gauge report.md"
DRAFT = "Sources/Coursework/Harbour lights draft.md"
DOSSIER = "Sources/Coursework/Project dossier.md"
BEAM_README = "Sources/Code/beam_bot.md"


def own(source: str, doc_type: str, quote: str = "") -> dict:
    """Evidence from one of the person's own documents."""
    return {"source_rel": source, "quote": quote, "field": "", "doc_type": doc_type, "role": "own"}


def grouping(*groups: list[str]):
    """A made-up AI reply that groups the cards with these names, by the ids it is sent."""
    def reply(req) -> str:
        sent = [json.loads(line) for line in req.user.splitlines() if line.startswith("{")]
        ids = {c["name"]: c["id"] for c in sent}
        return json.dumps({"groups": [[ids[n] for n in g if n in ids] for g in groups]})
    return reply


class Duplicates(unittest.TestCase):
    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(paths.set_override(self.home))
        self.enterContext(mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_FAKE_NOW": NOW}))
        settings_store.drop_cache()
        self.addCleanup(settings_store.drop_cache)
        self.con = db.connect()
        self.addCleanup(self.con.close)

    def project(self, name: str, source: str, doc_type: str, **fields) -> int:
        return store.add_found(self.con, "project", {"name": name, **fields}, own(source, doc_type, name))

    def twins(self) -> tuple[int, int]:
        """One project on the CV and in its repository's README under another name: (CV card, README card)."""
        cv = self.project("Tide gauge logger", CV, "cv", tools=["Python"])
        readme = self.project("gauge_bot", README, "repository", tools=["SQLite"], summary="Logs the bay's gauges.")
        return cv, readme

    def test_cards_the_ai_calls_one_project_are_merged_into_the_cv_card_with_all_their_evidence(self):
        cv, readme = self.twins()
        with fake_ai({"profile_same": grouping(["gauge_bot", "Tide gauge logger"])}):
            counts = duplicates.refresh(self.con)
        kept, merged = store.item(self.con, cv), store.item(self.con, readme)
        self.assertEqual((counts["groups"], counts["merged"]), (1, 1))
        self.assertEqual(kept["fields"]["name"], "Tide gauge logger", "the name on the CV is kept")
        self.assertEqual(sorted(kept["fields"]["tools"]), ["Python", "SQLite"])
        self.assertEqual(sorted(e["source_rel"] for e in kept["evidence"]), sorted([CV, README]))
        self.assertEqual((merged["state"], merged["merged_into"], merged["merged_why"]),
                         ("hidden", cv, "Merged into Tide gauge logger"))
        self.assertEqual(duplicates.listing(self.con)[0]["item"]["id"], cv)

    def test_a_shared_file_merges_only_when_the_ai_agrees_and_a_shared_cv_never_does(self):
        a = self.project("Harbour lights", DRAFT, "dissertation")
        b = self.project("Beacon survey", DRAFT, "dissertation")
        c = self.project("Wick sensor", CV, "cv")
        d = self.project("Beam log", CV, "cv")
        alone = duplicates.refresh(self.con, judge=False)
        self.assertEqual(alone["merged"], 0, "one file can hold two pieces of work")
        with fake_ai({"profile_same": grouping(["Harbour lights", "Beacon survey"])}):
            counts = duplicates.refresh(self.con)
        self.assertEqual(counts["groups"], 1)
        self.assertEqual(store.item(self.con, b)["merged_into"], a)
        self.assertIsNone(store.item(self.con, d)["merged_into"], "a CV lists every project: sharing it says nothing")
        self.assertIsNone(store.item(self.con, c)["merged_into"])
        linked = {"kind": "project", "fields": {"link": "https://example.org/code/wick"}, "evidence": []}
        self.assertTrue(duplicates.certain(linked, {**linked, "fields": {"link": "example.org/code/wick/"}}))

    def test_a_card_joins_a_group_only_when_it_agrees_with_the_card_kept(self):
        cv, readme = self.twins()
        other = self.project("Moon dial", REPORT, "report")
        with fake_ai({"profile_same": grouping(["Tide gauge logger", "gauge_bot"], ["gauge_bot", "Moon dial"])}):
            counts = duplicates.refresh(self.con)
        self.assertEqual((counts["merged"], store.item(self.con, readme)["merged_into"]), (1, cv))
        self.assertIsNone(store.item(self.con, other)["merged_into"],
                          "a chain of pairs put an unrelated card in the CV's card (2026-11-08)")

    def test_cards_of_two_repositories_are_never_merged_whatever_the_ai_says(self):
        for n, note in enumerate((README, BEAM_README)):
            unit = items.add(self.con, "b0", path=f"/made/up/repo{n}", rel=f"repo{n}", kind="repo", state="done")
            items.update(self.con, unit, note=note)
        self.project("gauge_bot", README, "repository")
        self.project("beam_bot", BEAM_README, "repository")
        with fake_ai({"profile_same": grouping(["gauge_bot", "beam_bot"])}):
            counts = duplicates.refresh(self.con)
        self.assertEqual(counts["merged"], 0, "a tool pack merged into a scheduler's card (2026-11-08)")

    def repo(self, path: str, note: str) -> None:
        """A made-up repository the record read, whose note is `note`."""
        unit = items.add(self.con, "b0", path=path, rel=path.rsplit("/", 1)[-1], kind="repo", state="done")
        items.update(self.con, unit, note=note)

    def test_a_repositorys_card_merges_into_one_without_a_repository_only_when_a_distinctive_word_is_shared(self):
        # a personal follow-up repository was merged into the dissertation it followed, as the AI judged them
        # one (2026-11-08)
        self.repo("/made/up/tidewatch-extended", README)
        self.repo("/made/up/wifi_sniffer", BEAM_README)
        follow_up = self.project("tidewatch-extended", README, "repository", summary="Where I took it next.")
        thesis = self.project("Coastal Flood Forecasting", DRAFT, "dissertation")
        sniffer = self.project("wifi_sniffer", BEAM_README, "repository")
        cv = self.project("Probe Request Sniffer", CV, "cv")
        with fake_ai({"profile_same": grouping(["Coastal Flood Forecasting", "tidewatch-extended"],
                                               ["Probe Request Sniffer", "wifi_sniffer"])}):
            counts = duplicates.refresh(self.con)
        self.assertIsNone(store.item(self.con, follow_up)["merged_into"])
        self.assertIsNone(store.item(self.con, thesis)["merged_into"])
        self.assertEqual((counts["merged"], store.item(self.con, sniffer)["merged_into"]), (1, cv),
                         "a shared word and the AI's word tie a repository to the CV's name for it")
        repo, named = ({"id": n, "title": t, "fields": {"name": t}} for n, t in ((1, "probe_sniffer"),
                                                                              (2, "Probe Request Sniffer")))
        self.assertFalse(duplicates.unrelated(repo, named, {1: {"/made/up/probe_sniffer"}}))

    def test_a_card_merged_away_is_never_a_merge_target(self):
        cv, readme = self.twins()
        other = self.project("Moon dial", REPORT, "report")
        store.merge_into(self.con, readme, cv, duplicates.MERGED + "Tide gauge logger")
        with self.assertRaises(ValueError):
            store.merge_into(self.con, other, readme, duplicates.MERGED + "gauge_bot")
        self.assertIsNone(store.item(self.con, other)["merged_into"])

    def test_the_persons_own_website_or_a_sites_front_page_never_makes_two_cards_one(self):
        # a flagship project's "Write-ups at <the person's site>" merged it into the portfolio site's card
        # (2026-11-08)
        settings.set("you.links.website", "https://pages.example.org/~avery")
        wick = self.project("Wick sensor", REPORT, "report", link="pages.example.org/~avery/")
        lights = self.project("Harbour lights survey", DRAFT, "dissertation", link="https://pages.example.org/~avery")
        flagship = self.project("Tidecaster", README, "repository", link="https://example.org")
        site = self.project("Portfolio", DOSSIER, "report", link="https://example.org/")
        self.assertEqual(len({wick, lights, flagship, site}), 4, "a new fact never joins a card by such a link")
        self.assertEqual(duplicates.refresh(self.con, judge=False)["merged"], 0, "nor is it a certain merge")
        store.merge_into(self.con, flagship, site, duplicates.MERGED + "Portfolio")
        self.assertEqual(duplicates.retake(self.con), 1, "a tidy takes apart a merge it made")
        page = self.project("Beam counter", README, "repository", link="https://pages.example.org/~avery/beam")
        self.assertEqual(self.project("Lantern beam log", REPORT, "report", link="pages.example.org/~avery/beam/"),
                         page, "a page of the site names one project")

    def test_one_website_under_four_names_is_shown_to_the_ai_with_its_hosts_and_repositories(self):
        # one portfolio site stayed four cards: "Portfolio", "<name>-portfolio", "<name>.me" and an older
        # "<User>.github.io" repository (2026-11-08)
        self.repo("/made/up/quillonworks-portfolio", README)
        self.repo("/made/up/AQuillon.github.io", BEAM_README)
        portfolio = self.project("Portfolio", REPORT, "report", link="https://quillonworks.me/")
        repo = self.project("quillonworks-portfolio", README, "repository")
        address = self.project("quillonworks.me", DRAFT, "report")
        older = self.project("AQuillon.github.io", BEAM_README, "repository")
        self.assertEqual(duplicates.refresh(self.con, judge=False)["merged"], 0, "a site is never a certain merge")
        sent = []

        def reply(req) -> str:
            sent.extend(json.loads(line) for line in req.user.splitlines() if line.startswith("{"))
            return grouping(["Portfolio", "quillonworks-portfolio", "quillonworks.me", "AQuillon.github.io"])(req)
        with fake_ai({"profile_same": reply}):
            duplicates.refresh(self.con)
        cards = {c["name"]: c for c in sent}
        self.assertEqual(cards["Portfolio"]["host"], "quillonworks.me")
        self.assertEqual(cards["quillonworks-portfolio"]["repositories"], ["quillonworks-portfolio"])
        self.assertEqual(cards["AQuillon.github.io"]["repositories"], ["AQuillon.github.io"])
        into = {i: store.item(self.con, i)["merged_into"] for i in (portfolio, repo, address)}
        self.assertEqual(len({into[i] or i for i in into}), 1, "the site, its address and its repository are one")
        self.assertIsNone(store.item(self.con, older)["merged_into"], "two repositories stay two projects")

    def test_a_repository_named_after_a_sites_address_joins_the_card_linking_that_site(self):
        self.repo("/made/up/AQuillon.github.io", BEAM_README)
        site = self.project("Personal site", REPORT, "report", link="https://aquillon.github.io")
        repo = self.project("AQuillon.github.io", BEAM_README, "repository")
        self.assertEqual(duplicates.refresh(self.con, judge=False)["merged"], 0)
        with fake_ai({"profile_same": grouping(["Personal site", "AQuillon.github.io"])}):
            counts = duplicates.refresh(self.con)
        self.assertEqual(counts["merged"], 1, "the site's own name is the word they share")
        self.assertIn(store.item(self.con, repo)["merged_into"] or store.item(self.con, site)["merged_into"],
                      (site, repo))

    def test_cards_sharing_a_file_that_lists_three_projects_are_not_one(self):
        for name in ("Tide clock", "Wick sensor", "Beam log"):
            self.project(name, DOSSIER, "report")
        counts = duplicates.refresh(self.con, judge=False)
        self.assertEqual(counts["merged"], 0, "a dossier of every project lists them, as a CV does")
        self.assertEqual(duplicates.listing_files(self.con), {DOSSIER})

    def test_cards_years_apart_with_no_file_in_common_are_never_merged_whatever_the_ai_says(self):
        self.project("Tide clock", CV, "cv", period="2017")
        self.project("Moon dial", REPORT, "report", period="2024")
        with fake_ai({"profile_same": grouping(["Tide clock", "Moon dial"])}):
            counts = duplicates.refresh(self.con)
        self.assertEqual(counts["merged"], 0)

    def test_a_card_the_person_confirmed_is_never_merged_without_them(self):
        cv, readme = self.twins()
        store.confirm(self.con, cv)
        with fake_ai({"profile_same": grouping(["gauge_bot", "Tide gauge logger"])}):
            counts = duplicates.refresh(self.con)
        self.assertEqual(counts["merged"], 0)
        self.assertEqual(store.item(self.con, readme)["state"], "hal")

    def test_undo_puts_the_cards_back_as_they_were_and_hal_never_merges_them_again(self):
        cv, readme = self.twins()
        with fake_ai({"profile_same": grouping(["gauge_bot", "Tide gauge logger"])}):
            duplicates.refresh(self.con)
            duplicates.undo(self.con, cv)
            again = duplicates.refresh(self.con)
        kept, back = store.item(self.con, cv), store.item(self.con, readme)
        self.assertEqual(again["merged"], 0)
        self.assertEqual(([e["source_rel"] for e in kept["evidence"]], kept["fields"]["tools"]), ([CV], ["Python"]))
        self.assertEqual((back["state"], back["merged_into"], [e["source_rel"] for e in back["evidence"]]),
                         ("hal", None, [README]))
        self.assertEqual(duplicates.listing(self.con), [])
        with self.assertRaises(ValueError):
            duplicates.undo(self.con, cv)

    def test_a_new_fact_naming_a_merged_card_goes_to_the_card_it_was_merged_into(self):
        cv, _readme = self.twins()
        with fake_ai({"profile_same": grouping(["gauge_bot", "Tide gauge logger"])}):
            duplicates.refresh(self.con)
        later = self.project("gauge_bot", REPORT, "report", tools=["Rust"])
        self.assertEqual(later, cv)
        self.assertIn("Rust", store.item(self.con, cv)["fields"]["tools"])

    def test_courses_under_two_names_are_merged_and_a_card_with_no_twin_stays(self):
        a = store.add_found(self.con, "education", {"qualification": "BSc", "subject": "Ocean Science",
                                                    "institution": "Brackwater Institute of Technology",
                                                    "start": "2020", "end": "2023"}, own(CV, "cv"))
        b = store.add_found(self.con, "education", {"qualification": "Bachelor of Science",
                                                    "subject": "Marine and Coastal Studies",
                                                    "institution": "BIT Brackwater",
                                                    "start": "2020", "end": "2023"}, own(REPORT, "report"))
        with fake_ai({"profile_same": lambda req: json.dumps({"groups": [[a, b], [a, 999]]})}):
            counts = duplicates.refresh(self.con)
        self.assertEqual(counts["merged"], 1)
        self.assertEqual(store.item(self.con, b)["merged_into"], a)


class PersonMerges(unittest.TestCase):
    """One project was two cards, one with its repository's notes and one resting only on a list of projects;
    hiding either lost its facts, so letters never told it (2026-11-08). The cards here are made up."""

    def setUp(self):
        self.home = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.enterContext(paths.set_override(self.home))
        self.enterContext(mock.patch.dict(os.environ, {"HAL_EMPLOYMENT_FAKE_NOW": NOW}))
        self.con = db.connect()
        self.addCleanup(self.con.close)
        self.listed = store.add_found(self.con, "project", {"name": "BEAM Navigation"}, own(DOSSIER, "project"))
        self.real = store.add_found(self.con, "project", {"name": "Rover pilot - real-time control",
                                                          "tools": ["Rust"]},
                                    own(BEAM_README, "repository", "Beam steers the rover in real time."))

    def test_the_person_merges_a_checked_card_into_the_one_they_keep_with_all_its_evidence(self):
        store.confirm(self.con, self.real)
        kept = duplicates.same_as(self.con, self.real, self.listed)
        gone = store.item(self.con, self.real)
        self.assertEqual((kept["id"], kept["fields"]["name"]), (self.listed, "BEAM Navigation"))
        self.assertEqual(sorted(e["source_rel"] for e in kept["evidence"]), sorted([DOSSIER, BEAM_README]))
        self.assertIn("Rust", kept["fields"]["tools"])
        self.assertEqual((gone["state"], gone["merged_into"]), ("hidden", self.listed))
        self.assertEqual(gone["merged_why"], "You checked it, then said it is the same as BEAM Navigation")
        moved = self.con.execute("SELECT moved_from FROM profile_evidence WHERE source_rel = ?",
                                 (BEAM_README,)).fetchone()[0]
        self.assertEqual(moved, self.real)

    def test_the_kept_card_keeps_its_name_when_the_merged_cards_file_ranks_higher(self):
        cv = store.add_found(self.con, "project", {"name": "Rover steering"}, own(CV, "cv"))
        kept = duplicates.same_as(self.con, cv, self.listed)
        self.assertEqual(kept["fields"]["name"], "BEAM Navigation")
        later = store.add_found(self.con, "project", {"name": "Rover steering", "tools": ["Lua"]}, own(CV, "cv"))
        self.assertEqual(later, self.listed, "a new fact naming the merged card goes to the card kept")

    def test_unmerge_gives_the_merged_card_back_its_state_its_words_and_its_evidence(self):
        store.edit(self.con, self.real, {"summary": "Steers a rover from its lidar."})
        duplicates.same_as(self.con, self.real, self.listed)
        duplicates.undo(self.con, self.listed)
        back, kept = store.item(self.con, self.real), store.item(self.con, self.listed)
        self.assertEqual((back["state"], back["merged_into"]), ("edited", None))
        self.assertEqual(back["fields"]["summary"], "Steers a rover from its lidar.")
        self.assertEqual([e["source_rel"] for e in back["evidence"]], [BEAM_README])
        self.assertEqual([e["source_rel"] for e in kept["evidence"]], [DOSSIER])

    def test_hals_own_merges_still_never_take_a_card_the_person_checked(self):
        store.confirm(self.con, self.real)
        with self.assertRaises(ValueError):
            store.merge_into(self.con, self.real, self.listed, duplicates.MERGED + "BEAM Navigation")
        self.assertIsNone(store.item(self.con, self.real)["merged_into"])

    def test_the_kept_card_keeps_the_tier_hals_ai_gave_it(self):
        with self.con:
            self.con.execute("UPDATE profile_items SET tier = 'core', tier_by = 'hal', tier_why = 'Made up', "
                             "tier_basis = 'made-up' WHERE id = ?", (self.listed,))
        duplicates.same_as(self.con, self.real, self.listed)
        self.assertEqual(tiers.refresh(self.con, judge=False)["unjudged"], 0, "nothing for the AI to judge again")
        self.assertEqual(store.item(self.con, self.listed)["tier"], "core")

    def test_a_hidden_card_the_person_keeps_is_shown_again(self):
        store.hide(self.con, self.real)
        kept = duplicates.same_as(self.con, self.listed, self.real)
        self.assertEqual(kept["state"], "hal")
        self.assertEqual(store.item(self.con, self.listed)["state"], "hidden")

    def test_a_merge_that_makes_no_sense_is_refused_and_changes_nothing(self):
        skill = store.add_found(self.con, "skill", {"name": "Rust"}, own(CV, "cv"))
        other = store.add_found(self.con, "skill", {"name": "Lua"}, own(CV, "cv"))
        job = store.add_found(self.con, "job", {"title": "Rover tester", "employer": "Brackwater Robotics"},
                              own(CV, "cv"))
        for gone, into in ((self.real, self.real), (self.real, job), (skill, other)):
            with self.assertRaises(ValueError):
                duplicates.same_as(self.con, gone, into)
        duplicates.same_as(self.con, self.real, self.listed)
        for gone, into in ((self.real, self.listed), (self.listed, self.real)):
            with self.assertRaises(ValueError):
                duplicates.same_as(self.con, gone, into)
        with self.assertRaises(LookupError):
            duplicates.same_as(self.con, self.listed, 9999)
        self.assertEqual(store.item(self.con, self.listed)["state"], "hal")

    def test_a_hidden_card_from_a_shown_cards_own_files_is_offered_and_never_merged(self):
        twin = store.add_found(self.con, "project", {"name": "beam_bot"}, own(BEAM_README, "repository"))
        store.hide(self.con, twin)
        (found,) = duplicates.hidden_twins(self.con)
        self.assertEqual((found["item"]["id"], [i["id"] for i in found["hidden"]]), (self.real, [twin]))
        self.assertEqual((store.item(self.con, twin)["state"], store.item(self.con, twin)["merged_into"]),
                         ("hidden", None))
        store.hide(self.con, self.listed)
        self.assertEqual([t["item"]["id"] for t in duplicates.hidden_twins(self.con)], [self.real],
                         "a card resting on a list of projects is not a twin")


class Route(unittest.TestCase):
    def test_the_person_says_two_cards_are_one_and_unmerge_takes_them_apart(self):
        hal = self.enterContext(HalServer())
        c = hal.signed_in()
        with closing(db.connect()) as con:
            listed = store.add_found(con, "project", {"name": "BEAM Navigation"}, own(DOSSIER, "project"))
            real = store.add_found(con, "project", {"name": "Rover pilot"}, own(BEAM_README, "repository"))
            store.confirm(con, real)
        kept = c.post(f"/api/v2/profile/items/{real}/same-as", {"into": listed}).json()
        self.assertEqual((kept["id"], kept["title"]), (listed, "BEAM Navigation"))
        (group,) = c.get("/api/v2/profile/check").json()["merged"]
        self.assertEqual((group["item"]["id"], [i["id"] for i in group["merged"]], group["by"]),
                         (listed, [real], "person"))
        self.assertEqual(c.post(f"/api/v2/profile/items/{real}/same-as", {"into": listed}).status, 400)
        self.assertEqual(c.post(f"/api/v2/profile/items/{listed}/same-as", {"into": "x"}).status, 400)
        self.assertEqual(c.post(f"/api/v2/profile/items/{listed}/same-as", {"into": 9999}).status, 404)
        c.post(f"/api/v2/profile/items/{listed}/unmerge")
        with closing(db.connect()) as con:
            self.assertEqual(store.item(con, real)["state"], "confirmed")


    def test_the_check_page_lists_what_hal_merged_and_undo_restores_it(self):
        hal = self.enterContext(HalServer())
        c = hal.signed_in()
        with closing(db.connect()) as con:
            cv = store.add_found(con, "project", {"name": "Tide gauge logger"}, own(CV, "cv"))
            readme = store.add_found(con, "project", {"name": "gauge_bot"}, own(README, "repository"))
            store.merge_into(con, readme, cv, duplicates.MERGED + "Tide gauge logger")
        page = c.get("/api/v2/profile/check").json()
        (group,) = page["merged"]
        self.assertEqual((group["item"]["id"], [i["id"] for i in group["merged"]]), (cv, [readme]))
        self.assertEqual(c.post(f"/api/v2/profile/items/{cv}/unmerge").json()["id"], cv)
        self.assertEqual(c.get("/api/v2/profile/check").json()["merged"], [])
        self.assertEqual(c.post(f"/api/v2/profile/items/{cv}/unmerge").status, 400)


if __name__ == "__main__":
    unittest.main()
