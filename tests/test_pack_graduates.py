"""Whole packs from a sectioned record, for a made-up software graduate and a made-up history graduate.

Each graduate's record is built through the sections as the person would build it (`support.graduates.sectioned`:
CVs, a letter, a course with a starred dissertation, lecture material and a starred project), then a whole
pack is written with the recording fake writer of `test_pack`. The tests check that the work given comes from
the starred work and the jobs, that a skill only studied is never told as used, that the letter carries the
person's voice and that a pack stays within its budget of model calls.
"""
from __future__ import annotations

import contextlib
import json
import re
import tempfile
import unittest
from collections.abc import Iterator
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

from support import graduates, people
from support.fake_ai import fake_ai
from support.world import World
from test_pack import FakeWriter, add_job, documents_only, section

from jobhunt import ai, pack, record
from jobhunt.core import settings
from jobhunt.cvstudio import cv
from jobhunt.profile import writing as profile_writing
from jobhunt.sections import coursework, lecture, letters
from jobhunt.writing import plan, strengths

MAX_CALLS = 10             # analyse, brief, fit, three documents, one check, and a redraft of each
VOICE_START = 40           # characters of a letter's excerpt enough to find it in a request
WORK_NAME = re.compile(r"^(?:Project|Job): (.+?)(?: \(|$)", re.M)     # the work a request gives
PEOPLE = ((graduates.AVERY, people.TECHNICAL), (graduates.MARNIE, people.HUMANITIES))
# What the made-up AI reads in each graduate's coursework and lecture material.
READS = {graduates.AVERY: {
    coursework.PURPOSE: {"summary": "Predicts ferry delays from open weather data.",
                         "methods": [{"name": "random forest", "quote": "trained a random forest in Python"}]},
    lecture.PURPOSE: {"topics": [{"name": "Least squares", "quote": "Least squares finds the line"}]}},
    graduates.MARNIE: {
    coursework.PURPOSE: {"summary": "Reads grain prices and riots on the Fen.",
                         "methods": [{"name": "Thematic analysis", "quote": "a thematic analysis of magistrates"}]},
    lecture.PURPOSE: {"topics": [{"name": "Port books as sources", "quote": "Port books record cargoes"}]}}}


@contextlib.contextmanager
def graduate_world(who: graduates.Graduate, person: people.Person,
                   writer: FakeWriter | None = None) -> Iterator[SimpleNamespace]:
    """A made-up world holding `who`'s sectioned record, with the fake writer of `test_pack` (unless `writer`)
    answering every document prompt."""
    writer = writer if writer is not None else FakeWriter()
    with World() as w, people.as_person(person), people.cv_type(person.key), \
            tempfile.TemporaryDirectory(prefix="hal-grad-") as tmp:
        settings.set("you.name", who.name)
        settings.set("you.email", "avery@example.org" if who is graduates.AVERY else person.you["email"])
        with fake_ai({k: json.dumps(v) for k, v in READS[who].items()}):
            graduates.sectioned(w.con, who, Path(tmp))
        with mock.patch.object(ai, "chat", documents_only(writer.chat, ai.chat)), \
                mock.patch.object(ai, "chat_json", documents_only(writer.chat_json, ai.chat_json)), \
                mock.patch.object(cv, "facts_text", lambda: who.cv), \
                mock.patch.object(record, "context", lambda *q, **k: ""):
            yield SimpleNamespace(con=w.con, writer=writer, job=add_job(w.con, person.key))


class WholePack(unittest.TestCase):
    def test_each_graduates_pack_is_written_from_their_starred_work_within_its_calls(self):
        for who, person in PEOPLE:
            with self.subTest(who.name):
                self.check(who, person)

    def check(self, who: graduates.Graduate, person: people.Person) -> None:
        """One graduate's whole pack, checked."""
        with graduate_world(who, person) as g:
            cards = profile_writing.cards(g.con)
            md = pack.build(g.con, g.job["id"], use_web=False).read_text()
            asked = g.writer.asked("letter")[0]["user"]
            studied = profile_writing.studied_skills(g.con)
        starred = {c["name"] for c in cards if c["star"]}
        told = set(WORK_NAME.findall(asked))
        self.assertTrue(told, "the request gives some work")
        self.assertLessEqual(told, starred | {c["name"] for c in cards if c["kind"] == "job"},
                             "only starred projects and jobs are given")
        dissertation = next(c for c in cards if c["dissertation"])
        self.assertTrue(dissertation["star"])
        self.assertEqual(dissertation["source"], "from your dissertation")
        self.assertIn(dissertation["name"], told, "the starred dissertation is given under its title")
        self.assertIn(plan.DISSERTATION_WORDS, asked, "and framed as the dissertation")
        self.assertTrue(studied, "the lecture material gives skills only studied")
        self.assertEqual(strengths.studied_claims(section(md, "Cover letter"), studied), [])
        self.assertIn(letters.excerpt(who.letter)[:VOICE_START], asked, "the letter carries the person's voice")
        self.assertIn("| Source |", section(md, "Fit"))
        self.assertLessEqual(len(g.writer.log), MAX_CALLS)


class Showcase(unittest.TestCase):
    def test_every_document_is_written_from_the_starred_work(self):
        """Regression: a pack whose evidence plan picked nothing was drafted whole and told none of the starred
        work (2026-11-09); the starred cards are now chosen by code for every request."""
        for who, person in PEOPLE:
            with self.subTest(who.name), graduate_world(who, person) as g:
                cards = profile_writing.cards(g.con)
                pack.build(g.con, g.job["id"], use_web=False)
                starred = {c["name"] for c in cards if c["star"]}
                self.assertTrue(starred, "the graduate has a showcase")
                for pid in ("letter", "message", "answers"):
                    told = set(WORK_NAME.findall(g.writer.asked(pid)[0]["user"]))
                    self.assertTrue(starred & told, pid)


if __name__ == "__main__":
    unittest.main()
