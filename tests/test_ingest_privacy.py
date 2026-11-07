"""Private files kept out of the record: each rule's hits and near misses, the ingest, and "Add it anyway".

Every number, name and key here is made up. Check digits are worked out to pass or fail on purpose, and the
key-shaped strings are built from pieces so no literal in this file looks like a live key.
"""
import json
import unittest
from pathlib import Path
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.actions import finished
from support.api import HalServer
from support.fake_ai import fake_ai
from test_ingest import CV_TEXT, REPLY, home, put, rows, run

from jobhunt import record
from jobhunt.core import paths, settings
from jobhunt.ingest import actions as ingest_actions
from jobhunt.ingest import items, privacy, understand

RANDOM = "q7Rk2Lm9Xz4Tb8Nw3Vc6Hy1Jd5Fg0SpE"         # made up, 32 characters with letters and digits
KEY_BODY = "\n".join(["MIIEow" + RANDOM + RANDOM[::-1]] * 5)


def reason(text: str, rel: str = "Files/document.txt", said: str = "") -> str:
    """The rule's verdict on `text`."""
    return privacy.reason(rel, text, said)


class Rules(unittest.TestCase):
    def test_a_national_insurance_number_by_its_label_is_private(self):
        self.assertEqual(reason("National Insurance number: AB 12 34 56 C"), privacy.ID_NUMBER)
        self.assertEqual(reason("NI No: AB123456C"), privacy.ID_NUMBER)

    def test_a_ni_shaped_code_without_its_label_or_with_a_never_issued_prefix_passes(self):
        self.assertEqual(reason("Order reference AB123456C, dispatched Tuesday."), "")
        self.assertEqual(reason("National Insurance number: GB 12 34 56 C"), "")

    def test_a_us_social_security_number_by_its_label_is_private(self):
        self.assertEqual(reason("SSN: 123-45-6789"), privacy.ID_NUMBER)
        self.assertEqual(reason("Call 123-45-6789 about the order."), "")

    def test_a_passports_machine_readable_lines_are_private(self):
        mrz = "P<GBRDOE<<ALEX<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<\n1234567897GBR9001012M3001014<<<<<<<<<<<<<<04"
        self.assertEqual(reason("PASSPORT\n" + mrz), privacy.ID_DOCUMENT)

    def test_merge_markers_and_talk_of_passports_pass(self):
        self.assertEqual(reason("<<<<<<< HEAD\nx = 1\n=======\nx = 2\n>>>>>>> branch"), "")
        self.assertEqual(reason("The machine readable zone of a passport starts with P<."), "")

    def test_a_sort_code_with_an_account_number_is_bank_details(self):
        self.assertEqual(reason("Sort code: 12-34-56\nAccount number: 12345678"), privacy.BANK)

    def test_a_form_asking_for_bank_details_without_numbers_passes(self):
        self.assertEqual(reason("Enter your sort code and account number so we can pay you."), "")
        self.assertEqual(reason("Sort code: 12-34-56"), "")

    def test_an_iban_counts_only_when_its_check_digits_hold(self):
        self.assertEqual(reason("Pay to IBAN GB82 WEST 1234 5698 7654 32 SORT ME"), privacy.BANK)
        self.assertEqual(reason("Pay to IBAN GB82 WEST 1234 5698 7654 33"), "")

    def test_a_card_number_that_passes_the_luhn_check_is_bank_details(self):
        self.assertEqual(reason("Card: 4929 1234 5678 9015"), privacy.BANK)
        self.assertEqual(reason("Visa ending 4929123456789015, expires 09/28"), privacy.BANK)

    def test_card_shaped_numbers_that_fail_the_check_or_are_test_numbers_pass(self):
        self.assertEqual(reason("Card: 4929 1234 5678 9016"), "")
        self.assertEqual(reason("Use the test card 4242 4242 4242 4242 in the sandbox."), "")
        self.assertEqual(reason("Order 4929123456789015 was shipped."), "", "no card words and not grouped")

    def test_a_decimal_or_a_rounded_id_beside_card_words_is_not_a_card(self):
        # a results table's "0.4929123456789015" and a tweet ID next to "card" kept data files out (2026-11-06)
        self.assertEqual(reason("Card size,0.4929123456789015,0.05"), "")
        self.assertEqual(reason("Used my visa card again,4550000000000006,2014-12-31"), "")
        self.assertEqual(reason("Visa card 4929123456789015, expires 09/28"), privacy.BANK)

    def test_an_nhs_number_with_a_valid_check_digit_beside_nhs_is_medical(self):
        self.assertEqual(reason("NHS number: 943 476 5919"), privacy.MEDICAL)
        self.assertEqual(reason("NHS number: 943 476 5918"), "")
        self.assertEqual(reason("Reference 943 476 5919 for your order."), "")

    def test_a_fit_note_is_medical(self):
        note = ("Statement of fitness for work\nPatient name: Alex Doe\nI assessed your case and advise you are "
                "not fit for work because of back pain. GP surgery, made-up town.")
        self.assertEqual(reason(note), privacy.MEDICAL)

    def test_one_medical_word_on_a_cv_or_a_nursing_essay_passes(self):
        cv = CV_TEXT + "\nHealthcare assistant, Made-up NHS Trust hospital, 2022. Helped in the clinic."
        self.assertEqual(reason(cv), "")
        essay = ("Reflection essay, word count 1500. The patient was diagnosed with asthma; the GP referral, "
                 "medication and treatment plan followed NHS guidance.")
        self.assertEqual(reason(essay), "")

    def test_a_date_of_birth_with_a_passport_or_licence_number_is_private(self):
        self.assertEqual(reason("Name: Alex Doe\nDate of birth: 01/02/2000\nPassport number: 20481234"),
                         privacy.BIRTH_AND_ID)
        self.assertEqual(reason("Date of birth 9th October 2000\nLicence number: DOE99901020AB9CD"),
                         privacy.BIRTH_AND_ID)

    def test_a_transcripts_date_of_birth_is_taken_out_and_the_transcript_kept(self):
        # a fresh read held two of a person's transcripts for a birth date in their details table (2026-11-06)
        text = ("Student Details\n  Name             Alex Doe\n  Date of Birth    9th October 2000\n"
                "  University Student Number    20481234\nModule HIS101: 68")
        self.assertEqual(reason(text), "")
        clean = privacy.redact(text)
        self.assertNotIn("October 2000", clean)
        self.assertIn("Date of Birth    [removed]", clean)
        self.assertIn("20481234", clean, "the student number names the person, so it stays")
        for written in ("Date of birth: 01/02/2000", "DOB 2000-02-01", "Born on 1 Feb 2000",
                        "Date of birth: October 9, 2000"):
            self.assertNotIn("2000", privacy.redact(written), written)

    def test_a_transcript_with_a_student_number_but_no_birth_date_passes(self):
        self.assertEqual(reason("Transcript\nStudent number: 20481234\nModule HIS101: 68"), "")
        self.assertEqual(reason("Date of birth: 1 May 2000\nDriving licence: full, clean"), "")

    def test_a_payslip_is_a_payslip(self):
        slip = ("Payslip, March\nTax code 1257L\nGross pay \u00a32,000.00\nNet pay \u00a31,640.00\n"
                "Tax period 12\nNI number AB 12 34 56 C")
        self.assertEqual(reason(slip), privacy.PAY)
        self.assertEqual(reason("P60 End of year certificate\nPAYE reference 123/A456\nPay 21,000.00 tax 2,100.00"),
                         privacy.PAY)

    def test_a_lecture_on_payroll_and_a_cv_naming_payroll_work_pass(self):
        lecture = ("Lecture 4: payroll. Learning outcomes: read a payslip, a tax code and net pay. For example, "
                   "gross pay of \u00a32,000.00 gives net pay of \u00a31,640.00.")
        self.assertEqual(reason(lecture), "")
        self.assertEqual(reason(CV_TEXT + "\nRan PAYE payroll and P60s for a small charity."), "")

    def test_a_private_key_and_live_looking_tokens_are_secrets(self):
        self.assertEqual(reason(f"-----BEGIN RSA PRIVATE KEY-----\n{KEY_BODY}\n-----END RSA PRIVATE KEY-----"),
                         privacy.SECRETS)
        for token in ("gh" + "p_" + RANDOM + "abcd", "github" + "_pat_" + RANDOM, "sk" + "-ant-" + RANDOM,
                      "AK" + "IA" + RANDOM[:16].upper(), "h" + "f_" + RANDOM):
            self.assertEqual(reason(f"export TOKEN_IN_NOTES {token}"), privacy.SECRETS, token[:4])

    def test_a_real_password_value_in_settings_or_code_is_a_secret(self):
        self.assertEqual(reason("DB_HOST=localhost\nDB_PASSWORD=Xk9mQ2vLp7w\n"), privacy.SECRETS)
        self.assertEqual(reason("PASSWORD=Xk9mQ2vLp7w"), privacy.SECRETS)
        self.assertEqual(reason('db = connect(user="app", password="Xk9#mQ2vLp")'), privacy.SECRETS)

    def test_empty_keys_placeholders_and_code_that_names_a_variable_pass(self):
        self.assertEqual(reason("API_KEY=\nSECRET_KEY=\nDB_PASSWORD=${DB_PASSWORD}\n"), "")
        self.assertEqual(reason('password = request.form["password"]\ntoken = get_token()\n'), "")
        self.assertEqual(reason('api_key = "your-api-key-here"\nkey = "sk-xxxxxxxxxxxxxxxxxxxxxxxx"'), "")
        self.assertEqual(reason("AK" + "IAIOSFODNN7EXAMPLE is the documented example key."), "")

    def test_a_cryptography_lecture_that_talks_about_keys_and_passwords_passes(self):
        lecture = ("Lecture 7: public key cryptography. Learning outcomes: explain why a private key stays secret. "
                   "A key file starts -----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA...\n"
                   "-----END RSA PRIVATE KEY-----\nFor example, never write password = \"letmein123\" in code; "
                   "students should hash passwords with a salt.")
        self.assertEqual(reason(lecture, rel="Security/Week 7 slides.pdf"), "")

    def test_a_cv_with_its_owners_email_and_phone_passes(self):
        cv = CV_TEXT + "\nEmail: avery@example.org  Phone: 07700 900123\nLinkedIn: example.org/in/avery"
        self.assertEqual(reason(cv, rel="CV.docx"), "")

    def test_the_ais_own_verdict_counts_when_it_names_a_kind_the_rules_keep_out(self):
        self.assertEqual(reason("Just a letter.", said="a bank statement"), "a bank statement")
        self.assertEqual(reason("Just a letter.", said="medical " * 30), privacy.SAID_BY_AI, "too long to show")
        self.assertEqual(reason("Just a letter.", said="true"), "", "names no kind")

    def test_contact_details_the_ai_calls_sensitive_never_keep_a_cv_out(self):
        # a fresh read held four of a person's CVs for "phone number, email address" (2026-11-06)
        for said in ("phone number, email address", "phone number, email address, location, citizenship",
                     "Date of Birth, Student HESA Number, University Student Number"):
            self.assertEqual(reason(CV_TEXT, rel="CV.docx", said=said), "", said)
        self.assertEqual(reason("Just a letter.", said="no"), "")
        self.assertEqual(reason("Just a letter."), "")


BANK_TEXT = "Statement for Alex Doe\nSort code: 12-34-56\nAccount number: 12345678\nBalance 120.00\n"


class Ingest(unittest.TestCase):
    def test_a_private_file_gets_no_note_no_facts_and_never_reaches_the_ai(self):
        found = []
        with home() as (con, vault, files), fake_ai({"ingest_doc": json.dumps(REPLY)}) as backend, \
                mock.patch("jobhunt.profile.store.add_found", lambda c, k, f, e: found.append(k)):
            put(files, "Money/CV of bank details.md", BANK_TEXT)
            run(con, [files])
            row = rows(con)["Files/Money/CV of bank details.md"]
            self.assertEqual((row["state"], row["private"], row["note"]), ("private", privacy.BANK, None))
            self.assertEqual(backend.requests("ingest_doc"), [])
            self.assertEqual(found, [])
            self.assertFalse(list(vault.rglob("*bank*")), "no note and no copy of the file")
            shown = items.private(con)
            self.assertEqual(shown, [{"id": row["id"], "name": "Files/Money/CV of bank details.md",
                                      "reason": privacy.BANK}])
            self.assertNotIn("12345678", json.dumps(shown))

    def test_a_copy_of_a_private_file_in_the_same_read_is_held_with_it(self):
        with home() as (con, _vault, files), fake_ai({}):
            put(files, "a/statement.txt", BANK_TEXT)
            put(files, "b/statement copy.txt", BANK_TEXT)
            run(con, [files])
            got = rows(con)
            self.assertEqual({r["state"] for r in got.values()}, {"private", "duplicate"})
            self.assertFalse(items.pending(con, "b1"))
            self.assertEqual(len(items.private(con)), 1)

    def test_with_the_check_off_the_file_is_read_as_before(self):
        with home() as (con, _vault, files), fake_ai({}):
            settings.set("record.privacy_check", False)
            put(files, "statement.txt", BANK_TEXT)
            run(con, [files])
            self.assertEqual(rows(con)["Files/statement.txt"]["state"], "done")

    def test_a_file_the_ai_calls_sensitive_is_kept_out_after_its_answer(self):
        def described(rel, text, rule):
            return {**understand.light(rel, text, rule), "by": "model", "sensitive": "medical information"}

        with home() as (con, vault, files), fake_ai({}), mock.patch.object(understand, "ask", described):
            put(files, "CV.md", CV_TEXT)
            run(con, [files])
            row = rows(con)["Files/CV.md"]
            self.assertEqual((row["state"], row["private"], row["note"]), ("private", "medical information", None))
            self.assertFalse(list(vault.rglob("CV*")))

    def test_a_transcript_is_read_without_its_birth_date_and_never_copied_whole(self):
        with home() as (con, vault, files), fake_ai({"ingest_doc": json.dumps(REPLY)}) as backend:
            settings.set("record.copy_originals", True)
            put(files, "Transcript.txt", "Transcript\nName: Alex Doe\nDate of birth: 01/02/2000\n"
                                         "Student number: 20481234\nModule HIS101: 68\n")
            run(con, [files])
            row = rows(con)["Files/Transcript.txt"]
            self.assertEqual(row["state"], "done")
            self.assertNotIn("01/02/2000", (vault / row["note"]).read_text())
            self.assertNotIn("01/02/2000", json.dumps(backend.requests("ingest_doc")))
            self.assertFalse([p for p in vault.rglob("Transcript*") if p.suffix == ".txt"], "no copy of the file")

    def test_a_file_let_in_is_read_and_not_held_again_when_read_later(self):
        with home() as (con, vault, files), fake_ai({}):
            put(files, "statement.txt", BANK_TEXT)
            run(con, [files])
            item_id = rows(con)["Files/statement.txt"]["id"]
            self.assertEqual(privacy.requeue(con, "b2", [item_id]), 1)
            run(con, [], batch="b2")
            again = rows(con, "b2")["Files/statement.txt"]
            self.assertEqual((again["state"], again["allowed"]), ("done", 1))
            self.assertTrue((vault / again["note"]).is_file())
            self.assertEqual(items.private(con), [])
            run(con, [files], batch="b3")
            self.assertEqual(rows(con, "b3")["Files/statement.txt"]["allowed"], 1)
            self.assertNotEqual(rows(con, "b3")["Files/statement.txt"]["state"], "private")
            put(files, "statement.txt", BANK_TEXT + "Balance 99.00\n")
            run(con, [files], batch="b4")
            self.assertEqual(rows(con, "b4")["Files/statement.txt"]["state"], "private", "changed bytes: checked again")


class Routes(unittest.TestCase):
    def setUp(self):
        self.hal = self.enterContext(HalServer())
        self.folder = Path(paths.record_default())
        self.enterContext(mock.patch.object(record, "VAULT", self.folder, create=True))
        self.addCleanup(record._CACHE.clear)
        self.enterContext(fake_ai({}))
        self.files = self.hal.home / "Old laptop"

    def test_add_it_anyway_reads_the_file_into_the_record(self):
        put(self.files, "CV.md", CV_TEXT)
        put(self.files, "Bank/statement.txt", BANK_TEXT)
        c = self.hal.signed_in()
        self.assertEqual(finished(c.post("/api/v2/record/ingest", {"folders": [str(self.files)]})
                                  .json()["action_id"])["state"], "done")
        shown = c.get("/api/v2/record/ingest").json()
        self.assertEqual([(p["name"], p["reason"]) for p in shown["private"]],
                         [("Old laptop/Bank/statement.txt", privacy.BANK)])
        self.assertEqual(shown["states"].get("private"), 1)
        self.assertEqual(c.post("/api/v2/record/ingest/private/allow", {"id": "x"}).status, 400)
        self.assertEqual(c.post("/api/v2/record/ingest/private/allow", {"id": 99999}).status, 404)
        out = c.post("/api/v2/record/ingest/private/allow", {"id": shown["private"][0]["id"]})
        self.assertEqual(out.status, 200, out.text)
        self.assertFalse(out.json()["add_again"])
        self.assertEqual(finished(out.json()["action_id"])["state"], "done")
        after = c.get("/api/v2/record/ingest").json()
        self.assertEqual((after["private"], after["states"]), ([], {"done": 1}))

    def test_a_file_hal_no_longer_has_is_let_in_when_added_again(self):
        c = self.hal.signed_in()
        with mock.patch.object(ingest_actions, "queue", return_value="a1") as queue:
            gone = self.hal.home / "unpacked" / "statement.txt"
            from jobhunt import db

            con = db.connect()
            try:
                item_id = items.add(con, "b0", path=str(gone), rel="uni.zip/statement.txt", sha256="ab" * 32)
                privacy.hold(con, item_id, privacy.BANK)
                con.commit()
            finally:
                con.close()
            out = c.post("/api/v2/record/ingest/private/allow", {"id": item_id}).json()
        self.assertEqual(out, {"action_id": None, "add_again": True})
        queue.assert_not_called()
        con = db.connect()
        try:
            again = items.add(con, "b9", path="/elsewhere/statement.txt", rel="uni.zip/statement.txt",
                              sha256="ab" * 32)
            self.assertEqual(con.execute("SELECT allowed FROM ingest_items WHERE id = ?", (again,)).fetchone()[0], 1)
        finally:
            con.close()


if __name__ == "__main__":
    unittest.main()
