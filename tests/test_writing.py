"""The writing checker: each rule a pattern can see is found and quoted, and plain writing passes.

The drafts are made up for the tests. The checker reads nothing but its arguments, so these tests need no
home, no AI and no database.
"""
import json
import unittest
from pathlib import Path

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt.writing import check, fields, rules

PLAIN_LETTER = """Dear Ada Penhallow,

The Collections Assistant post at Riverside Heritage Trust caught my eye because the advert asks for object
research, which is what my dissertation was. I traced the trade records of an invented port town through
three parish archives and wrote it up in twelve thousand words.

As a volunteer guide at a made-up heritage centre, I answered visitors' questions on every shift and wrote
short notes for the next guide. The written exercise you describe is work of that kind.

Could we talk about the role?

Yours sincerely,
Marnie"""


def rule_ids(hits) -> list[str]:
    return sorted({h.rule for h in hits})


FLAGS = json.loads((Path(__file__).parent / "fixtures" / "check_flags.json").read_text(encoding="utf-8"))


class FalseAlarms(unittest.TestCase):
    """Check before sending listed 22 false alarms against 11 real problems in five packs (2026-11-07); each
    kind of false alarm and real problem is in `fixtures/check_flags.json`, and every one must be judged right.
    """

    def judged(self, kind: str, flags) -> None:
        wrong = [c["text"] for c in FLAGS[kind] if flags(c["text"]) != c["flagged"]]
        real = sum(c["flagged"] for c in FLAGS[kind])
        self.assertEqual(wrong, [], f"{kind}: {len(wrong)} judged wrong of {len(FLAGS[kind])} ({real} real)")

    def test_an_ing_tail_is_flagged_only_when_it_claims_meaning(self):
        self.judged("ing_tail", lambda text: "L5" in rule_ids(check.check(text, "letter")))

    def test_a_letter_sentence_is_flagged_only_when_it_copies_a_whole_cv_line(self):
        self.judged("cv_copy", lambda text: "C5" in rule_ids(check.check(text, "letter", cv_text=FLAGS["cv"])))

    def test_a_claim_the_cv_states_is_never_flagged(self):
        from jobhunt.writing import drafts

        self.judged("cv_claim", lambda text: not drafts.stated_in(text, FLAGS["cv"]))


class Codes(unittest.TestCase):
    """"number not in the CV: 2031/23" was listed in every pack of one person: a module code and its year
    (2026-11-08)."""

    def test_a_module_code_and_its_year_are_never_numbers_to_check(self):
        for said in ("I took CSC2031/23 in my second year.", "CSC2031 covered databases.", "COMP-1001 taught me C."):
            self.assertEqual(check.unsupported_numbers(said, "Software Engineering 70"), [], said)
        self.assertEqual(check.unsupported_numbers("CSC2031 had 40 labs.", "Software Engineering 70"), ["40"])
        self.assertEqual(check.unsupported_numbers("I scored 2031 points.", "Software Engineering 70"), ["2031"],
                         "a number on its own is still checked")

    def test_a_paragraphs_numbers_leave_codes_out_too(self):
        from jobhunt.writing import paragraphs

        self.assertEqual(paragraphs.numbers("CSC2031/23 gave me 12 labs."), {"12"})


class Checker(unittest.TestCase):
    def test_a_plain_specific_letter_passes(self):
        hits = check.check(PLAIN_LETTER, "letter", sources=("",), spelling="en-GB", target_words=120)
        self.assertEqual([h for h in hits if h.severity in rules.FLAGGED], [])

    def test_machine_words_and_stock_cliches_fail_on_one_match(self):
        hits = check.check("I am a passionate team player who leveraged synergy.", "answer")
        self.assertEqual([h.quote for h in hits if h.rule == "L1"], ["passionate", "team player", "leveraged",
                                                                     "synergy"])
        self.assertTrue(all(h.severity == "high" for h in hits if h.rule == "L1"))

    def test_the_old_cliche_list_is_still_flagged(self):
        # pack.CLICHES flagged these before the rule set replaced it; each must still be listed
        for phrase in ("excited", "thrilled", "passion", "thrive", "fast-paced", "perfectly align", "dynamic",
                       "eager to", "synergy", "leverage"):
            with self.subTest(phrase=phrase):
                self.assertIn("L1", rule_ids(check.check(f"A made-up line with {phrase} in it.", "letter")))
        self.assertIn("C2", rule_ids(check.check("I am writing to apply for the post.", "letter")))
        self.assertIn("C6", rule_ids(check.check("I look forward to hearing from you.", "letter")))
        self.assertIn("L11", rule_ids(check.check("Two years \u2014 at a made-up trust.", "letter")))
        self.assertIn("L11", rule_ids(check.check("2023 \u2013 2024", "cv")))

    def test_fashionable_words_are_flagged_only_two_to_a_paragraph(self):
        self.assertNotIn("L2", rule_ids(check.check("A robust filing system for the archive.", "letter")))
        hits = check.check("A robust filing system that fostered seamless handovers.", "letter")
        self.assertIn("L2", rule_ids(hits))

    def test_a_staged_contrast_and_an_ing_tail_are_found(self):
        hits = check.check("It was not just a placement but a calling. I sorted the letters, showcasing my care.",
                           "letter")
        self.assertIn("L8", rule_ids(hits))
        self.assertEqual([h.quote for h in hits if h.rule == "L5"], ["showcasing my care"])

    def test_a_contrast_turned_round_is_found_but_a_plain_not_is_not(self):
        # "This knowledge is practical, not just theoretical." passed the check in a real letter (2026-11-05)
        self.assertIn("L8", rule_ids(check.check("The archive work was practical, not just theoretical.", "letter")))
        self.assertNotIn("L8", rule_ids(check.check("I did not just read the files; I listed them.", "letter")))

    def test_three_stacked_qualities_are_found_but_a_list_of_subjects_is_not(self):
        self.assertIn("L9", rule_ids(check.check("I am creative, organised and driven.", "letter")))
        self.assertNotIn("L9", rule_ids(check.check("I studied history, politics and French.", "letter")))

    def test_filler_openers_hedges_and_chatbot_words_are_found(self):
        hits = check.check("Certainly! In today's world I believe I could help. Furthermore, I hope this helps. "
                           "Send it to [Company].", "answer")
        self.assertTrue({"L13", "L16", "L17"} <= set(rule_ids(hits)))

    def test_a_number_the_facts_do_not_hold_is_found_and_one_they_hold_passes(self):
        hits = check.check("Guided 40 tours in 2025.", "cv", sources=("Guided 40 tours a year from 2024.",))
        self.assertEqual([h.quote for h in hits if h.rule == "T1"], ["2025."])
        self.assertEqual(hits[0].flag, "number not in the CV or advert: 2025.")
        self.assertIn("T4", rule_ids(check.check("Greatly improved the rota.", "cv", sources=("rota",))))
        self.assertNotIn("T4", rule_ids(check.check("Greatly improved the rota.", "cv",
                                                    sources=("greatly improved the rota",))))

    def test_cv_lines_need_a_verb_first_no_first_person_and_one_idea(self):
        long_line = "Organised " + " ".join(["the made-up shelves"] * 12)
        hits = check.check(f"- Responsible for the till\n- I wrote the newsletter\n- {long_line}", "cv")
        self.assertEqual(rule_ids(hits), ["B1", "B3", "B4"])
        same = "\n".join(f"- Wrote note {n}" for n in range(4))
        self.assertIn("B7", rule_ids(check.check(same, "cv")))

    def test_first_person_is_fine_in_a_letter(self):
        self.assertNotIn("B4", rule_ids(check.check("I wrote the newsletter for our society.", "letter")))

    def test_a_letter_that_repeats_a_cv_line_is_found(self):
        cv_text = "Catalogued four hundred letters from a made-up parish archive for the online index"
        letter = "In my last role I catalogued four hundred letters from a made-up parish archive for the online index."
        self.assertIn("C5", rule_ids(check.check(letter, "letter", cv_text=cv_text)))
        self.assertNotIn("C5", rule_ids(check.check(PLAIN_LETTER, "letter", cv_text=cv_text)))

    def test_the_greeting_and_sign_off_must_pair_as_in_the_uk(self):
        named_faithfully = "Dear Ada Penhallow,\n\nA made-up body.\n\nYours faithfully,\nMarnie"
        team_sincerely = "Dear Hiring Team,\n\nA made-up body.\n\nYours sincerely,\nMarnie"
        madam_faithfully = "Dear Sir or Madam,\n\nA made-up body.\n\nYours faithfully,\nMarnie"
        self.assertIn("C8", rule_ids(check.check(named_faithfully, "letter")))
        self.assertIn("C8", rule_ids(check.check(team_sincerely, "letter")))
        self.assertNotIn("C8", rule_ids(check.check(madam_faithfully, "letter")))
        self.assertNotIn("C8", rule_ids(check.check(PLAIN_LETTER, "letter")))
        kind_regards = "Dear Hiring Team,\n\nA made-up body.\n\nKind regards,\nMarnie"
        self.assertNotIn("C8", rule_ids(check.check(kind_regards, "letter")))

    def test_the_spelling_set_in_settings_is_kept(self):
        self.assertIn("L10", rule_ids(check.check("Organized the catalog.", "cv", spelling="en-GB")))
        self.assertNotIn("L10", rule_ids(check.check("Organised the catalogue.", "cv", spelling="en-GB")))
        self.assertIn("L10", rule_ids(check.check("Organised the catalogue.", "cv", spelling="en-US")))

    def test_an_unset_spelling_is_checked_as_uk_english(self):
        # an empty spelling used to turn the check off altogether
        for unset in ("", None, "fr"):
            self.assertIn("L10", rule_ids(check.check("Organized the catalog.", "cv", spelling=unset)))

    def test_a_spelling_hit_carries_the_form_to_write_instead(self):
        hits = [h for h in check.check("Organized the color catalog.", "cv", spelling="en-GB") if h.rule == "L10"]
        self.assertEqual([(h.quote, h.fix) for h in hits],
                         [("Organized", "Organised"), ("color", "colour"), ("catalog", "catalogue")])
        self.assertIn('(write "colour")', hits[1].flag)
        self.assertIn("UK English", hits[1].message)

    def test_length_limits_hold_for_letters_and_answers(self):
        long_letter = " ".join(["word"] * 400)
        hits = check.check(long_letter, "letter", target_words=250)
        self.assertEqual([(h.rule, h.severity) for h in hits if h.rule == "C1"], [("C1", "medium")])
        self.assertIn("C1", rule_ids(check.check(" ".join(["word"] * 120), "answer", max_words=100)))
        self.assertNotIn("C1", rule_ids(check.check(" ".join(["word"] * 90), "answer", max_words=100)))

    def test_a_word_the_person_uses_themselves_is_theirs_to_keep(self):
        self.assertNotIn("L1", rule_ids(check.check("A dynamic archive.", "letter", allowed=("dynamic",))))

    def test_every_rule_names_its_kinds_and_a_known_severity(self):
        for rule in rules.RULES:
            with self.subTest(rule=rule.id):
                self.assertTrue(rule.rule and rule.name)
                self.assertIn(rule.severity, rules.SEVERITIES)
                self.assertTrue(set(rule.applies) <= set(rules.KINDS))
        with self.assertRaises(ValueError):
            check.check("A line.", "email")

    def test_a_revision_note_quotes_each_problem_with_its_rule(self):
        hits = check.high(check.check("I am passionate about it.", "letter"))
        note = check.revision_note(hits)
        self.assertIn('"passionate"', note)
        self.assertIn("Revise the draft once", note)


class Fields(unittest.TestCase):
    def test_the_advert_decides_the_field_and_the_title_counts_most(self):
        found = fields.resolve("Archive Assistant", "Catalogue the collections of a made-up library.")
        self.assertEqual(found.field, "humanities")
        self.assertEqual(fields.resolve("Graduate Developer", "Python and SQL.").field, "tech")
        self.assertEqual(fields.resolve("Staff Nurse", "Band 5 on a made-up NHS ward.").field, "healthcare")

    def test_with_nothing_to_go_on_the_field_is_general_never_a_guess(self):
        self.assertEqual(fields.resolve("Coordinator", "Keep projects on time.").field, "general")
        self.assertEqual(fields.conventions(fields.Resolved(), "letter"), list(rules.GENERAL.letter))

    def test_the_advert_decides_the_field_and_the_persons_own_is_only_background(self):
        # a computing graduate's letter for a complaints role was written in the tech field's voice when the
        # advert showed no clear field (2026-11-05)
        person = {"cv_type_id": "humanities_grad"}
        plain = fields.resolve("Coordinator", "Keep projects on time.", person)
        self.assertEqual((plain.field, plain.background), ("general", "humanities"))
        self.assertEqual(fields.resolve("", "", person).field, "humanities", "with no job, the person's field")
        complaints = fields.resolve("Complaints Officer", "Investigate complaints under FCA rules for our lending "
                                    "products in financial services.", {"cv_type_id": "technical_grad"})
        self.assertEqual(complaints.field, "finance")
        civil = fields.resolve("Executive Officer", "Civil Service behaviours.", person)
        self.assertEqual((civil.field, civil.background), ("public_sector", "humanities"))
        self.assertTrue(set(rules.BY_FIELD["humanities"].background) <= set(fields.conventions(civil, "answer")))

    def test_every_field_has_conventions_for_each_kind_of_document(self):
        for field in (rules.GENERAL, *rules.FIELDS):
            for kind in rules.KINDS:
                with self.subTest(field=field.id, kind=kind):
                    self.assertTrue(fields.conventions(fields.Resolved(field.id), kind))


if __name__ == "__main__":
    unittest.main()
