"""Adding a CV and "Check what Hal read": the rules, the AI's reading, the report, corrections,
the original file kept byte for byte, and the files Hal refuses with a reason.

The CVs are the made-up people of `tests/support/make_cv_fixtures.py` (a humanities graduate, a technical
graduate and a career changer), as PDF, Word and pasted text. The AI is the fake one; the CV store is the
real one.
"""
import hashlib
import http.client
import json
import os
import stat
import threading
import unittest
import uuid
from unittest import mock

from support import make_cv_fixtures as maker
from support import people
from support.actions import warm_document_reader
from support.api import TIMEOUT_S, HalServer
from support.fake_ai import fake_ai
from support.make_cv_fixtures import BROWSER_EXPECTED, BROWSER_NAME, EXPECTED, FOLDER, NAMES, PEOPLE, main, pdf_bytes

from jobhunt import db
from jobhunt.ai.base import LLMUnavailable
from jobhunt.core import paths, runner, settings
from jobhunt.cvstudio import inline, mdread, model, parse, parse_actions, pdflines, pdfread, store
from jobhunt.cvstudio.lines import Line
from jobhunt.cvstudio.model import KINDS, Bullet, CVDoc, Entry, Section, to_json

WAIT_S = 20
NO_AI = {"cv_parse": LLMUnavailable("The made-up AI is switched off.")}
INVENTED_EMPLOYER = "Grand Imaginary Museum"


def fixture(key: str, ext: str) -> tuple[str, bytes]:
    """The made-up CV file of `key` ("humanities", "technical" or "changer") with extension `ext`."""
    name = f"{NAMES[key]}.{ext}"
    return name, (FOLDER / name).read_bytes()


def upload(hal, client, name: str, data: bytes, query: str = "") -> tuple[int, dict]:
    """POSTs one file as the page's drop zone does; `query` such as "?cv=<id>" picks the CV it goes to."""
    boundary = uuid.uuid4().hex
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"{name}\"\r\n"
            f"Content-Type: application/octet-stream\r\n\r\n".encode() + data + f"\r\n--{boundary}--\r\n".encode())
    con = http.client.HTTPConnection("127.0.0.1", hal.port, timeout=TIMEOUT_S)
    try:
        con.request("POST", "/api/v2/cv/upload" + query, body=body, headers={
            "Host": f"127.0.0.1:{hal.port}", "X-Hal": "1", "Content-Type": f"multipart/form-data; boundary={boundary}",
            "Cookie": "; ".join(f"{k}={v}" for k, v in client.jar.items())})
        r = con.getresponse()
        return r.status, json.loads(r.read())
    finally:
        con.close()


def ai_reply(doc, *, org_of: dict[str, str] | None = None, unsure: tuple[str, ...] = ()) -> str:
    """What a careful AI would answer for `doc`, with some employers swapped and some headings unsure."""
    org_of = org_of or {}
    sections = []
    for s in doc.sections:
        entries = []
        for e in s.entries:
            entries.append({"heading": e.heading, "org": org_of.get(e.heading, e.org), "location": e.location,
                            "start": e.start, "end": e.end, "grade": e.grade, "meta": e.meta, "text": e.text,
                            "bullets": [b.text for b in e.bullets], "items": e.items,
                            "modules": [{"name": m.name, "mark": m.mark, "year": m.year} for m in e.modules],
                            "confidence": {"heading": 0.3 if e.heading in unsure else 0.95}})
        sections.append({"kind": s.kind, "title": s.title, "text": s.text, "entries": entries})
    return json.dumps({"headline": doc.headline, "contact": dict(doc.contact_snapshot), "sections": sections})


def held_ai(entered: threading.Event, release: threading.Event):
    """Holds the AI's reading of a CV until `release` is set, setting `entered` when it starts: a busy AI."""
    ai_read = parse.read_with_ai

    def held(lines, rules=None):
        entered.set()
        release.wait(WAIT_S)
        return ai_read(lines, rules)

    return mock.patch.object(parse, "read_with_ai", held)


def rules_doc(key: str, ext: str = "pdf"):
    """The rules' reading of a made-up CV."""
    name, data = fixture(key, ext)
    return parse.read_rules(parse.lines_of(data, parse.check(data, name)[0])).doc


class Fixtures(unittest.TestCase):
    def test_the_made_up_cv_files_are_what_the_maker_makes(self):
        self.assertEqual(main(["--check"]), 0)

    def test_a_word_file_keeps_each_paragraphs_place_and_a_hash_of_its_text(self):
        _, data = fixture("humanities", "docx")
        lines = parse.docx_lines(data)
        self.assertEqual(lines[0].style, "title")
        self.assertEqual(len({ln.index for ln in lines}), len(lines))
        self.assertTrue(all(ln.hash == hashlib.sha1(ln.text.encode()).hexdigest()[:12] for ln in lines))
        self.assertTrue(any(ln.bullet for ln in lines))


class Rules(unittest.TestCase):
    def test_hal_finds_every_section_of_the_made_up_cvs(self):
        for key, want in EXPECTED.items():
            for ext in ("pdf", "docx", "txt"):
                with self.subTest(person=key, file=ext):
                    doc = rules_doc(key, ext)
                    self.assertEqual([s.kind for s in doc.sections], want["kinds"])
                    for kind, n in want["entries"].items():
                        self.assertEqual(sum(len(s.entries) for s in doc.sections if s.kind == kind), n, kind)
                    skills = sum(len(e.items) for s in doc.sections if s.kind == "skills" for e in s.entries)
                    self.assertEqual(skills, want["skills"])
                    for field in ("name", "email", "phone", "city"):
                        self.assertEqual(doc.contact_snapshot.get(field), want[field], field)

    def test_the_same_file_read_twice_gives_the_same_ids(self):
        self.assertEqual(to_json(rules_doc("technical")), to_json(rules_doc("technical")))
        pdf_ids = [e.id for s in rules_doc("changer", "pdf").sections for e in s.entries]
        docx_ids = [e.id for s in rules_doc("changer", "docx").sections for e in s.entries]
        self.assertEqual(pdf_ids, docx_ids)

    def test_a_two_digit_year_is_always_marked_check_this(self):
        _, data = fixture("humanities", "pdf")
        reading = parse.read_rules(parse.lines_of(data, "pdf"))
        unsure = [r for r in parse.rows(reading) if not r["sure"]]
        self.assertEqual([r["label"] for r in unsure], ["Student Ambassador dates"])
        self.assertEqual(unsure[0]["value"], "Sept 24 to Jun 25")
        self.assertEqual(unsure[0]["why"], parse.TWO_DIGIT_WHY)

    def test_the_degree_and_dissertation_are_read_for_the_setup_guide(self):
        values = parse.prefill(rules_doc("humanities"))
        self.assertEqual(values["you.education.degree"], "BA (Hons)")
        self.assertEqual(values["you.education.subject"], "History and Imaginary Studies")
        self.assertEqual(values["you.education.university"], "University of Nowhere Fen")
        self.assertEqual(values["you.education.grade"], "2:1")
        self.assertEqual(values["you.education.start_year"], "2023")
        self.assertTrue(values["you.education.dissertation"].startswith("Bridges and ballots"))

    def test_every_cv_types_section_titles_are_read_as_headings(self):
        # "every CV type's section titles": "Selected work" and "Strengths" were read as plain lines
        from jobhunt.cvstudio import types

        for type_id in types.builtin_ids():
            for kind, part in types.load(type_id).sections.items():
                if kind in KINDS and part.get("title"):
                    with self.subTest(type=type_id, title=part["title"]):
                        self.assertEqual(parse.heading_kind(Line(text=part["title"]), None), kind)

    def test_marks_in_a_technical_cv_become_modules(self):
        edu = next(s for s in rules_doc("technical", "docx").sections if s.kind == "education")
        self.assertEqual([(m.name, m.mark) for m in edu.entries[0].modules],
                         [("Algorithms", "78"), ("Databases", "72"), ("Human Factors", "69")])

    def test_a_scanned_pdf_is_explained(self):
        data = (FOLDER / "Scanned-CV.pdf").read_bytes()
        with self.assertRaises(parse.CVReadError) as caught:
            parse.check(data, "Scanned-CV.pdf")
        self.assertEqual(str(caught.exception), "This PDF is a picture of a CV, so Hal cannot read it. Upload the "
                                                "Word file, or paste the text.")

    def test_a_cv_over_six_pages_or_ten_megabytes_is_refused_with_the_reason(self):
        with self.assertRaises(parse.CVReadError) as caught:
            parse.check(pdf_bytes(PEOPLE["humanities"], pages=7), "Long.pdf")
        self.assertIn("This CV has 7 pages", str(caught.exception))
        with self.assertRaises(parse.CVReadError) as caught:
            parse.check(b"%PDF" + b"0" * (parse.MAX_BYTES + 1), "Big.pdf")
        self.assertIn("bigger than 10 MB", str(caught.exception))
        with self.assertRaises(parse.CVReadError) as caught:
            parse.check(b"\xd0\xcf\x11\xe0old", "Old.doc")
        self.assertIn(".docx", str(caught.exception))

    def test_a_correction_changes_only_the_field_it_names(self):
        doc = rules_doc("humanities")
        sec = next(s for s in doc.sections if s.kind == "experience")
        path = f"s:experience/e:{sec.entries[0].id}/dates"
        fixed = parse.correct(doc, path, "Sep 2024 to Jun 2025")
        entry = next(s for s in fixed.sections if s.kind == "experience").entries[0]
        self.assertEqual((entry.start, entry.end), ("Sep 2024", "Jun 2025"))
        self.assertEqual(sec.entries[0].start, "Sept 24")
        with self.assertRaises(LookupError):
            parse.correct(doc, "s:experience/e:ex-00000000/dates", "2024")

    def test_the_ais_reading_puts_grades_lone_dates_and_references_where_the_rules_do(self):
        # Claude, reading the made-up PDFs on 2026-10-23, kept ", 2:1" on the degree, gave a certificate's lone
        # year as its end date and called REFERENCES custom; the same files as Word were read the rules' way.
        for key in ("humanities", "changer"):
            with self.subTest(person=key):
                rules = rules_doc(key)
                reply = json.loads(ai_reply(rules))
                for sec in reply["sections"]:
                    if sec["kind"] == "education":
                        e = sec["entries"][0]
                        e["heading"] = f"{e['heading']}, {e['grade']}"
                    if sec["kind"] == "certifications":
                        for e in sec["entries"]:
                            e["start"], e["end"] = "", e["start"]
                    if sec["kind"] == "additional":
                        sec["kind"] = "custom"
                read = parse.from_ai(reply).doc
                self.assertEqual([s.kind for s in read.sections], EXPECTED[key]["kinds"])
                self.assertEqual([e.id for s in read.sections for e in s.entries],
                                 [e.id for s in rules.sections for e in s.entries])
                education = next(s for s in read.sections if s.kind == "education").entries[0]
                self.assertFalse(education.heading.endswith(education.grade))
                for e in (e for s in read.sections if s.kind == "certifications" for e in s.entries):
                    self.assertTrue(e.start)
                    self.assertEqual(e.end, "")


def browser_doc(ext: str):
    """The rules' reading of the made-up CV printed by a browser ("pdf") or of its Markdown ("md")."""
    name = f"{BROWSER_NAME}.{ext}"
    data = (FOLDER / name).read_bytes()
    return parse.read_rules(parse.lines_of(data, parse.check(data, name)[0])).doc


class BrowserPrinted(unittest.TestCase):
    """A CV printed to PDF by Chrome (subset fonts, no spaces, list markers drawn as shapes, wrapped lines)
    and the Markdown it came from. Before 2026-11-18 the PDF read no bold and no bullets, and every wrapped
    tail ("default.", "tables in RAM") became an entry of its own."""

    def test_a_cv_printed_by_a_browser_reads_its_entries_bullets_and_wrapped_lines(self):
        want = BROWSER_EXPECTED
        for ext in ("pdf", "md"):
            with self.subTest(file=ext):
                doc = browser_doc(ext)
                self.assertEqual([s.kind for s in doc.sections], want["kinds"])
                entries = [e for s in doc.sections if s.kind in parse.ENTRY_KINDS for e in s.entries]
                self.assertEqual([e.heading for e in entries], want["headings"])
                self.assertEqual([len(e.bullets) for e in entries], want["bullets"])
                self.assertEqual([1 if e.text else 0 for e in entries], want["texts"])
                self.assertEqual([(e.start, e.end) for e in entries], want["dates"])
                skills = sum(len(e.items) for s in doc.sections if s.kind == "skills" for e in s.entries)
                self.assertEqual(skills, want["skills"])
                for field in ("name", "email", "phone", "city"):
                    self.assertEqual(doc.contact_snapshot.get(field), want[field], field)
                tide = entries[2]
                self.assertTrue(tide.bullets[0].text.endswith("storm surges by default."))
                self.assertIn("A* search", tide.bullets[2].text)
                self.assertTrue(inline.plain(tide.text).startswith("Also: a seat finder"))

    def test_the_printed_pdf_and_its_markdown_read_the_same(self):
        self.assertEqual(to_json(browser_doc("pdf")), to_json(browser_doc("md")))

    def test_a_printed_link_over_a_lines_words_is_kept_as_a_link(self):
        data = (FOLDER / f"{BROWSER_NAME}.pdf").read_bytes()
        self.assertIn("https://example.org/bus-board", [k.url for k in pdfread.links(data)])
        bus = next(ln for ln in parse.lines_of(data, "pdf") if ln.text.startswith("Bus Board"))
        self.assertTrue(bus.marked.startswith("[**Bus Board**](https://example.org/bus-board) (TypeScript): live"))

    def test_only_web_and_email_links_are_read_from_a_pdf(self):
        def char(ch: str, x: float, index: int) -> pdfread.Char:
            return pdfread.Char(ch, 1, x, 700.0, x + 4.5, 709.0, 10.0, x, 700.0, index=index)

        drawn = [char(ch, 200.0 + 6 * i, i) for i, ch in enumerate("see notes or mail")]
        boxes = [pdfread.Link(1, 223.0, 698.0, 252.0, 712.0, "javascript:alert(1)"),
                 pdfread.Link(1, 271.0, 698.0, 302.0, 712.0, "mailto:a@example.org")]
        with mock.patch.object(pdfread, "chars", return_value=drawn), \
                mock.patch.object(pdfread, "links", return_value=boxes):
            lines = pdflines.pdf_lines(b"")
        self.assertEqual([(ln.text, ln.marked) for ln in lines],
                         [("see notes or mail", "see notes or [mail](mailto:a@example.org)")])

    def test_bold_entry_headings_and_bold_lead_ins_are_seen_in_the_pdf(self):
        lines = parse.lines_of((FOLDER / f"{BROWSER_NAME}.pdf").read_bytes(), "pdf")
        by_text = {ln.text: ln for ln in lines}
        self.assertTrue(by_text["Junior Developer Intern"].bold)
        self.assertFalse(by_text["Brackwater Analytics | Jun 2025 - Sep 2025"].bold)
        testing = next(ln for ln in lines if ln.text.startswith("Testing:"))
        self.assertEqual((testing.bullet, testing.bold, testing.lead), (True, False, "Testing:"))
        self.assertEqual(sum(ln.bullet for ln in lines), 8)

    def test_a_space_drawn_just_right_of_a_digit_does_not_split_the_number(self):
        # Chrome's "from 27 minutes": PDFium put the space it worked out 0.01 pt right of the "2"'s pen, and
        # sorting by position read "from 2 7 minutes" (2026-11-18).
        def char(ch: str, x: float, index: int, width: float = 4.5) -> pdfread.Char:
            return pdfread.Char(ch, 1, x, 700.0, x + (0 if ch == " " else width), 709.0, 13.2, x, 700.0,
                                index=index, scale=0.75)

        drawn = [char("f", 200.0, 0), char("r", 204.0, 1), char("o", 207.0, 2), char("m", 211.4, 3),
                 char(" ", 222.40, 4), char("2", 222.39, 5), char("7", 227.89, 6), char(" ", 235.23, 7),
                 char("m", 236.14, 8)]
        with mock.patch.object(pdfread, "chars", return_value=drawn):
            self.assertEqual([ln.text for ln in pdflines.pdf_lines(b"")], ["from 27 m"])

    def test_a_correction_keeps_the_marks_whose_words_are_still_there(self):
        doc = CVDoc(sections=[Section("experience", "experience", "Experience", entries=[
            Entry("ex-1", heading="Guide", bullets=[Bullet("b1", "Led **20** tours for *visiting* families")])])])
        fixed = parse.correct(doc, "s:experience/e:ex-1/b:b1", "Led 20 tours for families")
        self.assertEqual(fixed.sections[0].entries[0].bullets[0].text, "Led **20** tours for families")

    def test_marks_typed_in_a_correction_are_kept_as_typed(self):
        # regression: a typed "**x**" was stored as "\\*\\*x\\*\\*" and the editor showed the backslashes
        doc = CVDoc(sections=[Section("experience", "experience", "Experience", entries=[
            Entry("ex-1", heading="Guide", bullets=[Bullet("b1", "Led tours for *visiting* families")])])])
        for typed in ("Led **20** tours for families", "Led tours, rated 5* by A* students"):
            with self.subTest(typed=typed):
                fixed = parse.correct(doc, "s:experience/e:ex-1/b:b1", typed)
                self.assertEqual(fixed.sections[0].entries[0].bullets[0].text, typed)

    def test_markdown_markers_are_stripped_and_headings_mapped(self):
        lines = mdread.md_lines("---\ntitle: cv\n---\n# Ada Example\n\n## Work history\n\n### Tester\n\n"
                               "Made-up Ltd | 2024 - 2025\n\n* **Speed:** cut a run from *27* to `6` minutes,\n"
                               "  see [the report](https://example.org/r) or <https://example.org/s>\n"
                               "1. A\\* search over snake_case_names\n\n| Tool | Use |\n|---|---|\n| Git | code |\n")
        self.assertEqual([(ln.text, ln.style, ln.bullet) for ln in lines], [
            ("Ada Example", "title", False), ("Work history", "heading 2", False), ("Tester", "entry", False),
            ("Made-up Ltd | 2024 - 2025", "", False),
            ("Speed: cut a run from 27 to 6 minutes, see the report or https://example.org/s", "", True),
            ("A* search over snake_case_names", "", True), ("Tool\tUse", "", False), ("Git\tcode", "", False)])
        self.assertEqual(lines[4].lead, "Speed:")
        self.assertEqual([parse.heading_kind(ln, None) for ln in lines[:3]], [None, "experience", None])
        self.assertEqual(lines[4].marked, "**Speed:** cut a run from *27* to 6 minutes, see "
                                          "[the report](https://example.org/r) or https://example.org/s")
        self.assertEqual([ln.marked for ln in lines[:4] + lines[5:]], [""] * 7)

    def test_markdown_underscores_become_stars_and_only_web_links_are_kept(self):
        lines = mdread.md_lines("- __Led__ the _Saturday_ tours of snake_case_names\n"
                                "- see [my notes](ftp://example.org/notes) and [mail me](mailto:a@example.org)\n"
                                "- **The whole line is bold**\n- un*frigging*believable\n")
        self.assertEqual([ln.marked for ln in lines], [
            "**Led** the *Saturday* tours of snake_case_names", "see my notes and [mail me](mailto:a@example.org)",
            "", ""])
        self.assertEqual(lines[3].text, "un*frigging*believable")     # a star inside a word stays a star

    def test_a_markdown_and_a_printed_cv_keep_bold_inside_a_line(self):
        for ext in ("md", "pdf"):
            with self.subTest(file=ext):
                doc = browser_doc(ext)
                tide = next(e for s in doc.sections if s.kind == "projects" for e in s.entries)
                self.assertTrue(tide.bullets[1].text.startswith("**Testing:** wrote 120 tests"))
                self.assertTrue(tide.text.startswith("**Also:** a seat finder"))
                self.assertIn("A* search", tide.bullets[2].text)
                rows = {r["path"]: r["value"] for r in parse.rows(parse.Reading(doc, {}, "rules"))}
                self.assertNotIn("**", " ".join(rows.values()))

    def test_a_word_file_keeps_its_bold_italic_and_links(self):
        from test_cv_render import FORMATTED, formatted_cv, profile_of, type_for

        from jobhunt.cvstudio import export, render

        person = people.HUMANITIES
        data = export.to_docx(render.view(formatted_cv(), type_for(person.key), profile_of(person)))
        lines = parse.lines_of(data, "docx")
        line = next(ln for ln in lines if "visitor guides" in ln.text)
        self.assertEqual(line.text, inline.plain(FORMATTED))
        self.assertEqual(line.marked, FORMATTED)
        doc = parse.read_rules(lines).doc
        self.assertIn(FORMATTED, [b.text for s in doc.sections for e in s.entries for b in e.bullets])

    def test_markdown_files_are_accepted_and_an_empty_one_is_refused(self):
        self.assertEqual(parse.kind_of("CV.md", b"# Ada"), "md")
        self.assertEqual(parse.kind_of("CV.Markdown", b"# Ada"), "md")
        self.assertEqual(parse.check(b"# Ada\n", "CV.md"), ("md", None))
        with self.assertRaises(parse.CVReadError):
            parse.check(b"  \n", "CV.md")
        with self.assertRaises(parse.CVReadError) as caught:
            parse.kind_of("CV.odt", b"odt")
        self.assertIn("Markdown (.md)", str(caught.exception))


class OneProfile(unittest.TestCase):
    """A CV has one summary section. Before 2026-11-18 a row of facts under the contact line, then a "Profile"
    heading, read as two summaries, so the CV showed "PROFILE" twice, the first holding only that row."""

    def test_a_row_of_facts_above_a_profile_heading_joins_the_headline_not_a_second_profile(self):
        docs = {}
        for ext in ("pdf", "md"):
            with self.subTest(file=ext):
                name = f"{maker.FACTS_NAME}.{ext}"
                data = (FOLDER / name).read_bytes()
                docs[ext] = parse.read_rules(parse.lines_of(data, parse.check(data, name)[0])).doc
                self.assertEqual([s.kind for s in docs[ext].sections], maker.FACTS_EXPECTED["kinds"])
                self.assertEqual(docs[ext].headline, maker.FACTS_EXPECTED["headline"])
                self.assertTrue(docs[ext].sections[0].text.startswith(maker.FACTS_EXPECTED["profile"]))
        self.assertEqual(to_json(docs["pdf"]), to_json(docs["md"]))

    def test_the_same_row_of_facts_in_a_word_file_or_pasted_text_reads_one_profile(self):
        rows = [maker.Line("name", "Rowan Fieldmere"), maker.Line("contact", maker.FACTS[1][1]),
                maker.Line("contact", maker.FACTS[2][1]), maker.Line("heading", "PROFILE"),
                maker.Line("para", maker.FACTS[4][1]), maker.Line("heading", "EDUCATION"),
                maker.Line("entry", "BA (Hons) History, First", "2023 - 2026")]
        made = {"docx": maker.docx_bytes(rows), "txt": maker.ascii_text_of(rows).encode(), "pdf": pdf_bytes(rows)}
        for ext, data in made.items():
            with self.subTest(file=ext):
                doc = parse.read_rules(parse.lines_of(data, ext)).doc
                self.assertEqual([s.kind for s in doc.sections], ["summary", "education"])
                self.assertEqual(doc.headline, maker.FACTS_EXPECTED["headline"])

    def test_a_paragraph_above_a_profile_heading_is_merged_into_it_in_reading_order(self):
        doc = parse.read_rules(parse.text_lines(
            "Ada Example\nada@example.org | 07700 900466\nI sort records and answer questions about them for "
            "visitors to a made-up archive in the fens.\n\nProfile\nCareful with sources and calm with people.\n\n"
            "Education\nBA History    2023 - 2026\n")).doc
        summaries = [s for s in doc.sections if s.kind == "summary"]
        self.assertEqual(len(summaries), 1)
        self.assertEqual(summaries[0].title, "Profile")
        self.assertTrue(summaries[0].text.startswith("I sort records"))
        self.assertTrue(summaries[0].text.endswith("calm with people."))

    def test_a_paragraph_above_the_first_heading_with_no_profile_heading_is_still_the_profile(self):
        doc = parse.read_rules(parse.text_lines(
            "Ada Example\nada@example.org | 07700 900466\nI sort records and answer questions about them for "
            "visitors to a made-up archive in the fens.\n\nEducation\nBA History    2023 - 2026\n")).doc
        self.assertEqual([s.kind for s in doc.sections], ["summary", "education"])
        self.assertTrue(doc.sections[0].text.startswith("I sort records"))

    def test_a_sentence_under_the_contact_line_is_the_profile_never_the_headline(self):
        # 2026-11-19: a one-sentence profile under the contact line was drawn as the headline, above the contact
        for profile in ("Calm archivist who answers questions.", "I sort records and answer questions for "
                        "visitors\nto a made-up archive in the fens"):
            with self.subTest(profile=profile):
                doc = parse.read_rules(parse.text_lines(
                    f"Ada Example\nada@example.org | 07700 900466\n{profile}\n\nEducation\n"
                    "BA History    2023 - 2026\n")).doc
                self.assertEqual(doc.headline, "")
                self.assertEqual([s.kind for s in doc.sections], ["summary", "education"])
                self.assertEqual(doc.sections[0].text, profile.replace("\n", " "))

    def test_a_few_words_under_the_name_are_still_the_headline(self):
        doc = parse.read_rules(parse.text_lines(
            "Ada Example\nArchivist and guide for small museums\nada@example.org | 07700 900466\n\nEducation\n"
            "BA History    2023 - 2026\n")).doc
        self.assertEqual(doc.headline, "Archivist and guide for small museums")
        self.assertEqual([s.kind for s in doc.sections], ["education"])

    def test_a_paragraph_the_ai_gives_as_the_headline_becomes_the_profile(self):
        # 2026-11-19: Claude gave a CV's profile paragraph as its headline; the CV had no headline at all
        paragraph = ("Archivist with three years of work on made-up collections, careful with sources and calm "
                     "with visitors.")
        reply = {"headline": paragraph, "contact": {"name": "Ada Example"}, "sections": [
            {"kind": "education", "title": "Education", "entries": [{"heading": "BA History"}]}]}
        doc = parse.from_ai(reply).doc
        self.assertEqual(doc.headline, "")
        first = doc.sections[0]
        self.assertEqual((first.kind, first.title, first.text), ("summary", "Profile", paragraph))
        reply["sections"].insert(0, {"kind": "summary", "title": "Profile", "text": "Fond of old maps."})
        doc = parse.from_ai(reply).doc
        self.assertEqual([s.kind for s in doc.sections], ["summary", "education"])
        self.assertEqual(doc.sections[0].text, f"{paragraph} Fond of old maps.")
        reply["sections"][0]["text"] = paragraph
        self.assertEqual(parse.from_ai(reply).doc.sections[0].text, paragraph, "a paragraph is never there twice")
        reply["headline"] = "Archivist and guide"
        self.assertEqual(parse.from_ai(reply).doc.headline, "Archivist and guide")

    def test_the_ais_two_summaries_are_merged_into_one_as_the_rules_merge_them(self):
        reply = {"headline": "", "contact": {"name": "Ada Example"}, "sections": [
            {"kind": "summary", "title": "", "text": "I sort records."},
            {"kind": "custom", "title": "Personal statement", "text": "Careful with sources."},
            {"kind": "interests", "title": "Interests", "text": "Old maps."}]}
        doc = parse.from_ai(reply).doc
        self.assertEqual([(s.kind, s.title) for s in doc.sections],
                         [("summary", "Personal statement"), ("interests", "Interests")])
        self.assertEqual(doc.sections[0].text, "I sort records. Careful with sources.")


class KeepRulesSections(unittest.TestCase):
    """The AI's reading never loses what the rules found. On 2026-11-19 Claude returned a CV's Skills section
    with no lines, and Hal kept that reading, so all five skills lines were gone from Hal's copy."""

    def merged(self, key: str, change) -> tuple[parse.Reading, parse.Reading]:
        """The rules' and the AI's reading of a made-up CV, the AI's reply first changed by `change`."""
        name, data = fixture(key, "pdf")
        lines = parse.lines_of(data, parse.check(data, name)[0])
        rules = parse.read_rules(lines)
        reply = json.loads(ai_reply(rules.doc))
        change(reply["sections"])
        ai = parse.from_ai(reply)
        parse.mark_values(ai, lines)
        parse.keep_rules_sections(ai, rules)
        return rules, ai

    def test_an_ai_that_agrees_with_the_rules_keeps_its_own_reading(self):
        for key in ("humanities", "technical", "changer"):
            with self.subTest(person=key):
                rules, ai = self.merged(key, lambda sections: None)
                self.assertEqual(ai.from_rules, [])
                self.assertNotIn(parse.LEFT_OUT_WHY, ai.marks.values())
                self.assertEqual(to_json(ai.doc), to_json(rules.doc))

    def test_a_section_the_ai_left_empty_is_the_rules_section_and_says_so(self):
        def empty_skills(sections):
            next(s for s in sections if s["kind"] == "skills")["entries"] = []
        rules, ai = self.merged("humanities", empty_skills)
        skills = next(s for s in ai.doc.sections if s.kind == "skills")
        self.assertEqual(skills.entries, next(s for s in rules.doc.sections if s.kind == "skills").entries)
        self.assertEqual(ai.from_rules, [skills.title])
        rows = [r for r in parse.rows(ai) if r["path"].startswith(f"s:{skills.id}/")]
        self.assertTrue(rows)
        self.assertTrue(all(not r["sure"] and r["why"] == parse.LEFT_OUT_WHY for r in rows))
        self.assertIn("14 skills", parse.found_words(ai.doc))
        self.assertEqual(model.validate(ai.doc), [])

    def test_a_section_the_ai_left_out_goes_back_in_its_place(self):
        def no_skills(sections):
            sections[:] = [s for s in sections if s["kind"] != "skills"]
        rules, ai = self.merged("humanities", no_skills)
        self.assertEqual([s.kind for s in ai.doc.sections], [s.kind for s in rules.doc.sections])
        self.assertEqual(model.validate(ai.doc), [])

    def test_a_section_the_ai_kept_in_part_is_the_rules_section_whole(self):
        # the rule for a partial loss: the whole section is the rules', never a line silently dropped
        def drop_a_bullet(sections):
            job = next(s for s in sections if s["kind"] == "experience")["entries"][0]
            job["bullets"] = job["bullets"][:-1]
        rules, ai = self.merged("humanities", drop_a_bullet)
        ours = next(s for s in ai.doc.sections if s.kind == "experience")
        self.assertEqual(ours.entries, next(s for s in rules.doc.sections if s.kind == "experience").entries)
        self.assertEqual(ai.from_rules, [ours.title])
        others = [r for r in parse.rows(ai) if not r["path"].startswith(f"s:{ours.id}/")]
        self.assertNotIn(parse.LEFT_OUT_WHY, [r["why"] for r in others])

    def test_a_line_the_ai_moved_to_another_section_is_not_counted_lost(self):
        def move_a_bullet(sections):
            jobs = next(s for s in sections if s["kind"] == "experience")["entries"]
            moved = jobs[0]["bullets"].pop()
            sections.append({"kind": "custom", "title": "Also", "text": moved})
        _, ai = self.merged("humanities", move_a_bullet)
        self.assertEqual(ai.from_rules, [])


class Upload(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        warm_document_reader()

    def setUp(self) -> None:
        self.hal = self.enterContext(HalServer())
        self.client = self.hal.signed_in()

    def read(self, name: str, data: bytes, replies: dict | None = None, query: str = "") -> dict:
        """Uploads a file, waits for `cv.parse` and the record's read of the same file, and returns the
        finished `cv.parse`. The record's read outlived the test on a loaded machine (2026-11-18)."""
        with fake_ai(NO_AI if replies is None else replies):
            status, body = upload(self.hal, self.client, name, data, query)
            self.assertEqual(status, 200, body)
            done = runner.wait(body["action_id"], WAIT_S)
            self.assertTrue(runner.idle(WAIT_S), "the record's read of the uploaded file has ended")
        self.assertEqual(done["state"], "done", done["error"])
        return done

    def versions(self) -> list[dict]:
        con = db.connect()
        try:
            return [dict(r) for r in con.execute("SELECT * FROM cv_versions ORDER BY rowid")]
        finally:
            con.close()

    def report(self) -> dict:
        r = self.client.get("/api/v2/cv/parse-report")
        self.assertEqual(r.status, 200, r.text)
        return r.json()

    def test_an_uploaded_pdf_and_word_file_keep_their_sha256_through_everything(self):
        for ext in ("pdf", "docx"):
            with self.subTest(file=ext):
                name, data = fixture("changer", ext)
                sha = hashlib.sha256(data).hexdigest()
                self.read(name, data)
                con = db.connect()
                try:
                    row = con.execute("SELECT * FROM cv_files WHERE sha256 = ?", (sha,)).fetchone()
                finally:
                    con.close()
                stored = paths.data_dir() / row["rel"]
                self.assertEqual(stored.name, f"{sha}.{ext}")
                self.assertFalse(os.stat(stored).st_mode & (stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))
                first = self.versions()[-1]
                self.assertEqual(first["file_id"], row["id"])
                rows = self.report()["rows"]
                fix = next(r for r in rows if r["path"] == "contact/city")
                self.assertEqual(self.client.request("PUT", "/api/v2/cv/parse-report/" + _q(fix["path"]),
                                                     {"value": "Saltmarsh"}).status, 200)
                original = self.client.get("/api/v2/cv/original")
                self.assertEqual(original.status, 200)
                self.assertEqual(hashlib.sha256(original.body).hexdigest(), sha)
                self.assertIn(f'filename="{name}"', original.headers["Content-Disposition"])
                self.assertEqual(hashlib.sha256(store.file_bytes(row["id"])).hexdigest(), sha)

    def test_an_uploaded_file_can_be_sent_as_your_original(self):
        # an upload kept the file but never made it the baseline, so "send my original" refused
        name, data = fixture("technical", "pdf")
        self.read(name, data)
        first = self.versions()[-1]
        self.assertEqual(store.doc()["baseline_file_id"], first["file_id"])
        r = self.client.request("PUT", "/api/v2/cv/send", {"send": "original"})
        self.assertEqual(r.status, 200, r.text)

    def test_an_uploaded_markdown_cv_is_read_and_shown_as_paragraphs(self):
        name = f"{BROWSER_NAME}.md"
        done = self.read(name, (FOLDER / name).read_bytes())
        self.assertEqual(done["result"]["found"], "contact details, 1 degree, 1 other qualification, 2 jobs, "
                                                  "2 projects, 8 skills")
        report = self.report()
        self.assertEqual(report["original"]["kind"], "md")
        self.assertEqual(report["original"]["paragraphs"][0], {"text": "Avery Quillon", "kind": "title"})

    def test_reading_works_without_an_ai(self):
        name, data = fixture("humanities", "pdf")
        done = self.read(name, data, NO_AI)
        self.assertEqual(done["result"]["how"], "rules")
        self.assertEqual(done["result"]["found"],
                         "contact details, 1 degree, 2 jobs, 1 volunteering role, 14 skills")
        self.assertEqual([u["label"] for u in done["result"]["unsure"]], ["Student Ambassador dates"])
        versions = self.versions()
        self.assertEqual([(v["origin"], v["by"], v["what"]) for v in versions],
                         [("uploaded", "baseline", f"Your file: {name} (never changed)")])
        self.assertTrue(any("Kept the reading from Hal's own rules" in s["text"] for s in done["steps"]))

    def test_values_not_in_the_file_are_marked_check_this(self):
        name, data = fixture("humanities", "docx")
        reply = ai_reply(rules_doc("humanities", "docx"), org_of={"Bookshop Assistant": INVENTED_EMPLOYER},
                         unsure=("Archive Volunteer",))
        with fake_ai({"cv_parse": reply}) as backend:
            _, body = upload(self.hal, self.client, name, data)
            done = runner.wait(body["action_id"], WAIT_S)
        self.assertEqual(done["state"], "done", done["error"])
        self.assertEqual(len(backend.requests("cv_parse")), 1)
        self.assertEqual(backend.requests("cv_parse")[0].priority, 0)
        self.assertEqual([(v["what"], v["n"]) for v in self.versions()], [(f"Your file: {name} (never changed)", 1)])
        self.assertEqual(done["result"]["n"], 1, "a file uploaded once is version 1, never a version 2 over it")
        rows = {r["value"]: r for r in self.report()["rows"]}
        self.assertFalse(rows[INVENTED_EMPLOYER]["sure"])
        self.assertEqual(rows[INVENTED_EMPLOYER]["why"], parse.NOT_FOUND_WHY)
        self.assertFalse(rows["Archive Volunteer"]["sure"])
        self.assertEqual(rows["Archive Volunteer"]["why"], parse.UNSURE_WHY)
        self.assertFalse(rows["Sept 24 to Jun 25"]["sure"])
        self.assertTrue(rows["Marnie Quillfeather"]["sure"])
        self.assertTrue(rows["University of Nowhere Fen"]["sure"])

    def test_a_section_the_ai_lost_is_kept_and_the_report_says_the_ai_did_not_read_it(self):
        name, data = fixture("humanities", "pdf")
        reply = json.loads(ai_reply(rules_doc("humanities")))
        next(s for s in reply["sections"] if s["kind"] == "skills")["entries"] = []
        done = self.read(name, data, {"cv_parse": json.dumps(reply)})
        self.assertEqual(done["result"]["how"], "ai")
        self.assertIn("14 skills", done["result"]["found"])
        self.assertTrue(any(s["text"] == parse_actions.LEFT_OUT_STEP.format(sections="Skills")
                            for s in done["steps"]))
        left = [r for r in self.report()["rows"] if r["why"] == parse.LEFT_OUT_WHY]
        self.assertEqual({r["label"].split(":")[0] for r in left}, {"Skills"})

    def test_an_ai_that_agrees_with_the_rules_makes_no_second_version(self):
        name, data = fixture("technical", "pdf")
        done = self.read(name, data, {"cv_parse": ai_reply(rules_doc("technical"))})
        self.assertEqual(done["result"]["how"], "ai")
        self.assertEqual(len(self.versions()), 1)

    def test_a_correction_makes_a_new_version(self):
        name, data = fixture("humanities", "pdf")
        self.read(name, data)
        before = self.versions()
        row = next(r for r in self.report()["rows"] if r["label"] == "Student Ambassador dates")
        r = self.client.request("PUT", "/api/v2/cv/parse-report/" + _q(row["path"]), {"value": "Sep 2024 to Jun 2025"})
        self.assertEqual(r.status, 200, r.text)
        after = self.versions()
        self.assertEqual(len(after), len(before) + 1)
        self.assertEqual((after[-1]["origin"], after[-1]["by"], after[-1]["what"]),
                         ("corrected", "you", "You corrected 1 thing Hal misread"))
        self.assertEqual(after[-1]["parent_id"], before[-1]["id"])
        self.assertEqual(before[-1]["data"], after[-2]["data"], "the earlier version never changes")
        _, doc = store.current()
        entry = next(s for s in doc.sections if s.kind == "experience").entries[0]
        self.assertEqual((entry.start, entry.end), ("Sep 2024", "Jun 2025"))
        fixed = next(x for x in self.report()["rows"] if x["path"] == row["path"])
        self.assertEqual((fixed["sure"], fixed["corrected"], fixed["value"]), (True, True, "Sep 2024 to Jun 2025"))
        name_row = next(x for x in self.report()["rows"] if x["path"] == "contact/name")
        self.client.request("PUT", "/api/v2/cv/parse-report/" + _q(name_row["path"]),
                            {"value": "Marnie Q. Quillfeather"})
        self.assertEqual(self.versions()[-1]["what"], "You corrected 2 things Hal misread")
        self.assertEqual(settings.get("you.name"), "Marnie Q. Quillfeather")

    def test_an_uploaded_cv_is_in_your_record_once(self):
        """A person who gave Hal their CV in setup found Your record saying "No CVs yet" (2026-11-12)."""
        name, data = fixture("humanities", "docx")
        done = self.read(name, data)
        status, again = upload(self.hal, self.client, name, data)
        self.assertEqual(status, 200, again)
        self.assertEqual((again["action_id"], again["duplicate"]["id"]), (None, done["result"]["cv"]))
        con = db.connect()
        try:
            cvs = con.execute("SELECT section, doc_kind FROM record_sources").fetchall()
        finally:
            con.close()
        self.assertEqual([tuple(r) for r in cvs], [("cv", "cv")])

    def test_each_new_file_is_a_new_cv_at_version_one_and_a_new_version_goes_where_you_say(self):
        """A new CV uploaded on top of an old one showed as "version 9" of it (2026-11-18)."""
        first = self.read(*fixture("humanities", "pdf"))["result"]
        second = self.read(*fixture("technical", "pdf"))["result"]
        self.assertNotEqual(first["cv"], second["cv"])
        self.assertEqual((first["n"], second["n"]), (1, 1))
        self.assertEqual(store.lead_id(), second["cv"])
        again = self.read(*fixture("technical", "docx"), query=f"?cv={first['cv']}")["result"]
        self.assertEqual((again["cv"], again["n"]), (first["cv"], 2))
        report = self.client.get(f"/api/v2/cv/parse-report?cv={first['cv']}").json()
        self.assertEqual((report["n"], report["file"]["name"]), (2, fixture("technical", "docx")[0]))
        name, data = fixture("humanities", "pdf")
        status, dup = upload(self.hal, self.client, name, data)
        self.assertEqual((status, dup["action_id"], dup["duplicate"]["id"]), (200, None, first["cv"]))
        self.assertEqual(self.client.get(f"/api/v2/cv/original?cv={first['cv']}").body,
                         fixture("technical", "docx")[1])

    def test_a_cvs_own_file_is_read_again_into_its_next_version(self):
        # 2026-11-19: a CV read before a reading fix could not get the fixed reading: the same file uploaded
        # again was answered with the CV it made, and stored versions are never rewritten
        name, data = fixture("humanities", "pdf")
        first = self.read(name, data)["result"]
        status, dup = upload(self.hal, self.client, name, data, f"?cv={first['cv']}")
        self.assertEqual((status, dup["action_id"], dup["duplicate"]["id"]), (200, None, first["cv"]))
        reply = ai_reply(rules_doc("humanities"), unsure=("Archive Volunteer",))
        with fake_ai({"cv_parse": reply}):
            r = self.client.post(f"/api/v2/cv/read-again?cv={first['cv']}", {})
            self.assertEqual(r.status, 200, r.text)
            done = runner.wait(r.json()["action_id"], WAIT_S)
        self.assertEqual(done["state"], "done", done["error"])
        self.assertEqual((done["result"]["cv"], done["result"]["n"], done["result"]["how"]), (first["cv"], 2, "ai"))
        newest = self.versions()[-1]
        self.assertEqual((newest["origin"], newest["by"], newest["what"], newest["n"]),
                         ("uploaded", "baseline", parse_actions.READ_AGAIN_WHAT.format(name=name), 2))
        self.assertEqual(newest["file_id"], self.versions()[0]["file_id"])
        report = self.report()
        self.assertEqual(report["n"], 2)
        self.assertIn(parse.UNSURE_WHY, [row["why"] for row in report["rows"]])

    def test_a_cv_with_no_file_cannot_be_read_again(self):
        store.current()
        self.assertEqual(self.client.post("/api/v2/cv/read-again", {}).status, 404)

    def test_a_file_whose_cv_was_deleted_uploads_again_as_a_new_cv(self):
        """A stale CV could not be deleted, and the same file uploaded again answered with that archived CV, so
        the file could never be added again (owner's report, 2026-11-19)."""
        name, data = fixture("humanities", "pdf")
        first = self.read(name, data)["result"]["cv"]
        self.read(*fixture("technical", "pdf"))
        self.client.request("PUT", f"/api/v2/cv/archive?cv={first}", {"on": True})
        status, dup = upload(self.hal, self.client, name, data)
        self.assertEqual((status, dup["duplicate"]["id"], dup["duplicate"]["archived"] is not None),
                         (200, first, True))
        record_files = sorted(p for p in (paths.data_dir() / "record-sources").rglob("*") if p.is_file())
        self.assertTrue(record_files, "the upload added the file to Your record too")
        gone = self.client.request("DELETE", f"/api/v2/cv?cv={first}")
        self.assertEqual(gone.status, 200, gone.text)
        again = self.read(name, data)["result"]
        self.assertNotEqual(again["cv"], first)
        self.assertEqual((store.doc(doc_id=again["cv"])["name"], again["n"]), (NAMES["humanities"], 1))
        self.assertEqual(sorted(p for p in (paths.data_dir() / "record-sources").rglob("*") if p.is_file()),
                         record_files, "Your record's copy of the file stays")

    def test_a_second_upload_while_one_is_read_never_stops_the_first(self):
        """Every upload shared one subject and replaced the reading before it, so that CV was never made
        (2026-11-18); the same file uploaded again while it is read follows that reading."""
        entered, release = threading.Event(), threading.Event()
        first, second = fixture("humanities", "pdf"), fixture("technical", "pdf")
        with fake_ai(NO_AI), held_ai(entered, release):
            status, a = upload(self.hal, self.client, *first)
            self.assertEqual(status, 200, a)
            self.assertTrue(entered.wait(WAIT_S), "the first file is being read")
            status, b = upload(self.hal, self.client, *second)
            self.assertEqual(status, 200, b)
            status, again = upload(self.hal, self.client, *first)
            self.assertEqual((status, again["action_id"], again["duplicate"]), (200, a["action_id"], None))
            release.set()
            done = [runner.wait(x["action_id"], WAIT_S) for x in (a, b)]
        self.assertEqual([d["state"] for d in done], ["done", "done"], [d["error"] for d in done])
        self.assertEqual(len({d["result"]["cv"] for d in done}), 2, "each file made its own CV")

    def listed(self) -> list[dict]:
        """My CVs' list of the CVs in use."""
        r = self.client.get("/api/v2/cv/cvs")
        self.assertEqual(r.status, 200, r.text)
        return r.json()["cvs"]

    def test_an_upload_is_listed_while_the_ai_reads_it_and_its_reading_takes_that_version(self):
        """An uploaded CV was not on My CVs until the AI had read it (2026-11-19)."""
        name, data = fixture("humanities", "docx")
        reply = ai_reply(rules_doc("humanities", "docx"), org_of={"Bookshop Assistant": INVENTED_EMPLOYER})
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        with fake_ai({"cv_parse": reply}), held_ai(entered, release):
            status, body = upload(self.hal, self.client, name, data)
            self.assertEqual(status, 200, body)
            self.assertTrue(entered.wait(WAIT_S), "the AI is reading the file")
            self.assertEqual([(c["id"], c["name"], c["n"], c["reading"]) for c in self.listed()],
                             [(body["cv"], NAMES["humanities"], 1, True)])
            first = self.versions()
            self.assertEqual([(v["n"], v["origin"]) for v in first], [(1, "uploaded")])
            self.assertNotIn(INVENTED_EMPLOYER, first[0]["data"])
            self.assertEqual(self.report()["version_id"], first[0]["id"], "Check what Hal read works meanwhile")
            release.set()
            done = runner.wait(body["action_id"], WAIT_S)
            self.assertTrue(runner.idle(WAIT_S), "the record's read of the uploaded file has ended")
        self.assertEqual(done["state"], "done", done["error"])
        after = self.versions()
        self.assertEqual([(v["id"], v["n"]) for v in after], [(first[0]["id"], 1)], "one upload is one version")
        self.assertIn(INVENTED_EMPLOYER, after[0]["data"])
        self.assertEqual((done["result"]["version_id"], done["result"]["cv"]), (first[0]["id"], body["cv"]))
        self.assertEqual([c["reading"] for c in self.listed()], [False])
        self.assertIn(INVENTED_EMPLOYER, {r["value"] for r in self.report()["rows"]}, "the report follows the AI")

    def test_a_new_version_is_listed_at_once_and_a_version_made_meanwhile_stands(self):
        cv = self.read(*fixture("humanities", "pdf"))["result"]["cv"]
        name, data = fixture("technical", "pdf")
        doc = rules_doc("technical")
        job = next(s for s in doc.sections if s.kind == "experience").entries[0].heading
        entered, release = threading.Event(), threading.Event()
        self.addCleanup(release.set)
        with fake_ai({"cv_parse": ai_reply(doc, org_of={job: INVENTED_EMPLOYER})}), held_ai(entered, release):
            status, body = upload(self.hal, self.client, name, data, f"?cv={cv}")
            self.assertEqual((status, body["cv"]), (200, cv), body)
            self.assertTrue(entered.wait(WAIT_S), "the AI is reading the file")
            self.assertEqual([(c["n"], c["reading"]) for c in self.listed() if c["id"] == cv], [(2, True)])
            upload_v = store.doc(doc_id=cv)["current_version_id"]
            mine = store.new_version(store.get(upload_v), origin="manual", what="You changed your CV", by="you",
                                     doc_id=cv)
            release.set()
            done = runner.wait(body["action_id"], WAIT_S)
            self.assertTrue(runner.idle(WAIT_S), "the record's read of the uploaded file has ended")
        self.assertEqual(done["state"], "done", done["error"])
        self.assertEqual((done["result"]["version_id"], done["result"]["n"]), (upload_v, 2))
        self.assertIn(INVENTED_EMPLOYER, to_json(store.get(upload_v)))
        self.assertEqual(store.doc(doc_id=cv)["current_version_id"], mine, "the version you made stays in use")
        self.assertNotIn(INVENTED_EMPLOYER, to_json(store.get(mine)))

    def test_a_reading_that_fails_leaves_no_mark_and_keeps_the_rules_reading(self):
        def broken(lines, rules=None):
            raise RuntimeError("the made-up AI fell over")

        name, data = fixture("technical", "pdf")
        with mock.patch.object(parse, "read_with_ai", broken):
            status, body = upload(self.hal, self.client, name, data)
            self.assertEqual(status, 200, body)
            done = runner.wait(body["action_id"], WAIT_S)
            self.assertTrue(runner.idle(WAIT_S), "the record's read of the uploaded file has ended")
        self.assertEqual(done["state"], "error")
        self.assertEqual([(c["id"], c["n"], c["reading"]) for c in self.listed()], [(body["cv"], 1, False)])
        self.assertEqual([v["origin"] for v in self.versions()], ["uploaded"])

    def test_a_scanned_pdf_is_explained_when_it_is_uploaded(self):
        status, body = upload(self.hal, self.client, "Scanned-CV.pdf", (FOLDER / "Scanned-CV.pdf").read_bytes())
        self.assertEqual(status, 400)
        self.assertEqual(body["error"]["message"], parse.PICTURE_WORDS)
        self.assertEqual(self.versions(), [])

    def test_pasted_text_is_read_like_a_file(self):
        text = (FOLDER / f"{NAMES['changer']}.txt").read_text(encoding="ascii")
        with fake_ai(NO_AI):
            r = self.client.post("/api/v2/cv/upload", {"text": text})
            self.assertEqual(r.status, 200, r.text)
            done = runner.wait(r.json()["action_id"], WAIT_S)
        self.assertEqual(done["state"], "done", done["error"])
        self.assertEqual(self.versions()[0]["what"], "Your file: Pasted CV.txt (never changed)")
        report = self.report()
        self.assertEqual(report["original"]["kind"], "txt")
        self.assertEqual(report["original"]["paragraphs"][0], {"text": "Priya Ashdown-Vell", "kind": "title"})
        self.assertEqual(self.client.post("/api/v2/cv/upload", {"text": "  "}).status, 400)

    def test_the_cv_fills_only_the_details_you_have_not_typed(self):
        settings.set("you.name", "Marnie Typed-Name", by="you")
        name, data = fixture("humanities", "pdf")
        done = self.read(name, data)
        self.assertEqual(settings.get("you.name"), "Marnie Typed-Name")
        self.assertEqual(settings.get("you.email"), "marnie.quillfeather@example.org")
        self.assertEqual(settings.get("you.education.university"), "University of Nowhere Fen")
        self.assertNotIn("you.name", done["result"]["filled"])
        from_cv = self.report()["from_cv"]
        self.assertNotIn("you.name", from_cv)
        self.assertEqual(from_cv["you.city"], "Harrowgate Vale")

    def test_the_check_shows_pdf_pages_as_pictures_and_word_files_as_paragraphs(self):
        name, data = fixture("technical", "pdf")
        self.read(name, data)
        report = self.report()
        self.assertEqual(report["original"]["page_urls"], ["/api/v2/cv/original/page/1"])
        page = self.client.get("/api/v2/cv/original/page/1")
        self.assertEqual((page.status, page.body[:8]), (200, b"\x89PNG\r\n\x1a\n"))
        self.assertEqual(self.client.get("/api/v2/cv/original/page/2").status, 404)
        name, data = fixture("technical", "docx")
        self.read(name, data)
        report = self.report()
        self.assertEqual(report["original"]["page_urls"], [])
        kinds = {p["kind"] for p in report["original"]["paragraphs"]}
        self.assertEqual(kinds, {"title", "heading", "bullet", "text"})
        self.assertEqual(report["found"], "contact details, 1 degree, 1 job, 2 projects, 1 certificate, 8 skills")

    def test_nothing_is_read_before_a_cv_is_added(self):
        empty = self.client.get("/api/v2/cv/parse-report")
        self.assertEqual((empty.status, empty.json()["file"], empty.json()["rows"]), (200, None, []))
        self.assertEqual(self.client.get("/api/v2/cv/original").status, 404)


class EveryKindPromise(unittest.TestCase):
    def test_reading_a_cv_keeps_the_promises_of_gate_g9(self):
        from support.world import World
        from test_action_kinds import problems

        runner.load_kinds()
        with World() as w:
            self.assertEqual(problems(runner.KINDS["cv.parse"], w), [])


def _q(path: str) -> str:
    from urllib.parse import quote

    return quote(path, safe="")


if __name__ == "__main__":
    unittest.main()
