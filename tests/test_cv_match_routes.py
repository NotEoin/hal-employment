"""The routes of "Match my file's look" against a real server: matching a CV's own PDF into a look chosen for
it, the honest page counts, one "Make it fit" step at a time, renaming and deleting. A stand-in printer counts
pages (`test_cv_pagelimit.stand_in`), and the files are made up (`support/layout_pdf.py`, `tests/fixtures/cv`)."""
from __future__ import annotations

import unittest

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support import layout_pdf
from support.api import HalServer
from support.make_cv_fixtures import FOLDER, NAMES, scanned_pdf_bytes
from test_cv_pagelimit import LONG_PAGES, humanities_cv_in_use, stand_in

from jobhunt.cvstudio import match_routes, measures, mylooks, store

FILE = "Made-up-CV.pdf"


def keep(data: bytes, name: str) -> str:
    """`data` kept as the lead CV's own file; its id."""
    file_id = store.add_file(data, name, role="baseline")
    store.set_baseline_file(file_id)
    return file_id


class Matching(unittest.TestCase):
    def setUp(self) -> None:
        self.hal = self.enterContext(HalServer())
        self.client = self.hal.signed_in()
        self.printer = self.enterContext(stand_in())
        self.doc_id = humanities_cv_in_use()

    def url(self, path: str) -> str:
        return f"/api/v2{path}?cv={self.doc_id}"

    def match(self) -> dict:
        reply = self.client.post(self.url("/cv/look/match"))
        self.assertEqual(reply.status, 200, reply.text)
        return reply.json()

    def test_a_cv_with_its_own_pdf_is_matched_into_a_look_chosen_for_it(self) -> None:
        keep(layout_pdf.one_line_cv()[0], FILE)
        before = self.client.get(self.url("/cv/look/match")).json()
        self.assertEqual((before["can"], before["look"], before["file"]["pages"]), (True, None, 1))
        got = self.match()
        look = got["look"]
        self.assertEqual(look["name"], "Like Made-up-CV.pdf")
        self.assertEqual(store.doc(doc_id=self.doc_id)["look_id"], look["id"])
        self.assertEqual(mylooks.get(look["id"]).measures.entry_form, "line")
        self.assertEqual(look["words"], f"Your file is 1 page. Hal's copy in this look is {LONG_PAGES} pages. "
                                        + match_routes.LONGER)
        self.assertEqual((look["file_pages"], look["hal_pages"], look["can_fit"]), (1, LONG_PAGES, True))
        self.assertIn("one page", look["notes"][0])
        self.assertEqual(len(got["file"]["page_urls"]), 1)

    def test_matching_the_same_file_again_measures_it_into_the_same_look(self) -> None:
        keep(layout_pdf.one_line_cv()[0], FILE)
        first = self.match()["look"]["id"]
        self.assertEqual(self.match()["look"]["id"], first)
        self.assertEqual(len(mylooks.all_saved()), 1)

    def test_make_it_fit_takes_one_step_each_press_and_stops_at_its_limit(self) -> None:
        keep(layout_pdf.one_line_cv()[0], FILE)
        look = self.match()["look"]
        size = mylooks.get(look["id"]).measures.size
        calls = self.printer.calls
        for step in range(1, measures.TIGHTEN_STEPS + 1):
            reply = self.client.post(self.url(f"/cv/looks/{look['id']}/fit"))
            self.assertEqual(reply.status, 200, reply.text)
            self.assertEqual(mylooks.get(look["id"]).steps, step)
        self.assertLess(mylooks.get(look["id"]).measures.size, size)
        self.assertGreater(self.printer.calls, calls, "a tighter look prints again for its count")
        after = reply.json()["look"]
        self.assertFalse(after["can_fit"])
        self.assertTrue(after["words"].endswith(match_routes.NO_ROOM))
        refused = self.client.post(self.url(f"/cv/looks/{look['id']}/fit"))
        self.assertEqual((refused.status, refused.json()["error"]["message"]), (409, match_routes.NO_ROOM))

    def test_a_word_file_is_not_matched_and_the_card_says_why(self) -> None:
        keep((FOLDER / f"{NAMES['changer']}.docx").read_bytes(), "Made-up-CV.docx")
        got = self.client.get(self.url("/cv/look/match")).json()
        self.assertFalse(got["can"])
        self.assertIn("Made-up-CV.docx is a Word file", got["why"])
        self.assertEqual(self.client.post(self.url("/cv/look/match")).status, 400)

    def test_a_cv_with_no_file_says_so(self) -> None:
        got = self.client.get(self.url("/cv/look/match")).json()
        self.assertEqual((got["can"], got["why"]), (False, match_routes.NO_FILE))

    def test_a_scan_cannot_be_matched(self) -> None:
        keep(scanned_pdf_bytes(), "Scan.pdf")
        reply = self.client.post(self.url("/cv/look/match"))
        self.assertEqual(reply.status, 400)
        self.assertIn("no text", reply.json()["error"]["message"])
        self.assertEqual(mylooks.all_saved(), [])

    def test_a_look_is_renamed_and_deleted_only_once_no_cv_uses_it(self) -> None:
        keep(layout_pdf.one_line_cv()[0], FILE)
        look = self.match()["look"]
        renamed = self.client.request("PUT", f"/api/v2/cv/looks/{look['id']}", {"name": "My compact one"})
        self.assertEqual(renamed.json(), {"id": look["id"], "name": "My compact one"})
        empty = self.client.request("PUT", f"/api/v2/cv/looks/{look['id']}", {"name": " "})
        self.assertEqual((empty.status, empty.json()["error"]["field"]), (400, "name"))
        refused = self.client.request("DELETE", f"/api/v2/cv/looks/{look['id']}")
        self.assertEqual(refused.status, 409)
        self.assertIn("uses the look My compact one", refused.json()["error"]["message"])
        self.client.request("PUT", self.url("/cv/look"), {"id": "classic"})
        self.assertEqual(self.client.request("DELETE", f"/api/v2/cv/looks/{look['id']}").status, 200)
        self.assertEqual(self.client.request("DELETE", "/api/v2/cv/looks/my-nothing").status, 404)


if __name__ == "__main__":
    unittest.main()
