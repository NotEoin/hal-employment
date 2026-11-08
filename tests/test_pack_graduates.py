"""Whole packs from a sectioned record, for a made-up software graduate and a made-up history graduate.

Each graduate's record is built through the sections as the person would build it (`support.graduates.sectioned`:
CVs, a letter, a course with a starred dissertation, lecture material and a starred project), then a whole
pack is written with the recording fake writer of `test_pack`. The tests check that the stories come from the
starred work and the jobs, that a skill only studied is never told as used, that the letter carries the
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
from test_pack import FakeWriter, add_job, prompt_id, section

from jobhunt import cv, llm, pack, record
from jobhunt.core import settings
from jobhunt.profile import writing as profile_writing
from jobhunt.sections import coursework, lecture, letters
from jobhunt.writing import plan, strengths

MAX_CALLS = 30             # the budget of model calls per pack (`test_pack`, 2026-11-08)
VOICE_START = 40           # characters of a letter's excerpt enough to find it in a request
STORY_NAME = re.compile(r"^(?:Project|Job): (.+?)(?: \(|$)", re.M)    # the work a paragraph request tells
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
CARD_SOURCE = re.compile(r"^(S\d+) \(Profile\): (?:Project|Job)\b[^:]*: (.+)$", re.M)
PICK_WORDS = 6             # words of a Profile card line a made-up pick quotes


class GraduateWriter(FakeWriter):
    """The fake writer of `test_pack`, whose evidence plan picks the opening words of each project and job line
    of the Profile block, as a model choosing the person's work would."""

    def reply(self, system: str, user: str) -> object:
        if prompt_id(system) == "plan":
            return {"evidence": [{"source": sid, "quote": " ".join(text.split()[:PICK_WORDS]), "use": "the work"}
                                 for sid, text in CARD_SOURCE.findall(user)]}
        return super().reply(system, user)


@contextlib.contextmanager
def graduate_world(who: graduates.Graduate, person: people.Person) -> Iterator[SimpleNamespace]:
    """A made-up world holding `who`'s sectioned record, with the fake writer answering every document prompt."""
    writer = GraduateWriter()
    with World() as w, people.as_person(person), people.cv_type(person.key), \
            tempfile.TemporaryDirectory(prefix="hal-grad-") as tmp:
        settings.set("you.name", who.name)
        settings.set("you.email", "avery@example.org" if who is graduates.AVERY else person.you["email"])
        with fake_ai({k: json.dumps(v) for k, v in READS[who].items()}):
            graduates.sectioned(w.con, who, Path(tmp))
        with mock.patch.object(llm, "chat", writer.chat), mock.patch.object(llm, "chat_json", writer.chat_json), \
                mock.patch.object(cv, "facts_text", lambda: who.cv), \
                mock.patch.object(record, "context", lambda *q, **k: ""):
            yield SimpleNamespace(con=w.con, writer=writer, job=add_job(w.con, person.key))


class WholePack(unittest.TestCase):
    def test_each_graduates_pack_tells_their_starred_work_within_thirty_calls(self):
        for who, person in PEOPLE:
            with self.subTest(who.name):
                self.check(who, person)

    def check(self, who: graduates.Graduate, person: people.Person) -> None:
        """One graduate's whole pack, checked."""
        with graduate_world(who, person) as g:
            cards = profile_writing.cards(g.con)
            md = pack.build(g.con, g.job["id"], use_web=False).read_text()
            stories = [e["user"] for e in g.writer.asked("story")]
            frames = [e["user"] for e in g.writer.asked("frame")]
            studied = profile_writing.studied_skills(g.con)
        starred = {c["name"] for c in cards if c["star"]}
        told = {m.group(1) for u in stories for m in [STORY_NAME.search(u)] if m}
        self.assertTrue(told, "the pack tells some work")
        self.assertLessEqual(told, starred | {c["name"] for c in cards if c["kind"] == "job"},
                             "only starred projects and jobs are told")
        dissertation = next(c for c in cards if c["dissertation"])
        self.assertTrue(dissertation["star"])
        self.assertEqual(dissertation["source"], "from your dissertation")
        self.assertIn(dissertation["name"], told, "the starred dissertation is told under its title")
        self.assertTrue(any(plan.DISSERTATION_WORDS in u for u in stories), "and framed as the dissertation")
        self.assertTrue(studied, "the lecture material gives skills only studied")
        self.assertEqual(strengths.studied_claims(section(md, "Cover letter"), studied), [])
        self.assertTrue(frames and any(letters.excerpt(who.letter)[:VOICE_START] in u for u in frames),
                        "the letter's frame carries the person's voice")
        self.assertIn("| Source |", section(md, "Fit"))
        self.assertLessEqual(len(g.writer.log), MAX_CALLS)


if __name__ == "__main__":
    unittest.main()
