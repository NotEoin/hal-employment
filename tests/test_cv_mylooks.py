"""Personal looks kept in the person's own data: chosen like a built-in, listed after them, printed again when
their measures change (and not when only their name does), never deleted while a CV uses them, and never
written to the code folder."""
from __future__ import annotations

import unittest

import support  # noqa: F401 - sets the temporary home before jobhunt loads
from support.api import HalServer
from support.world import World
from test_cv_pagelimit import humanities_cv_in_use, stand_in

from jobhunt import db
from jobhunt.cvstudio import measures, mylooks, pagelimit, render, store

SMALL = measures.validate({"size": 9.2, "line_height": 1.25, "entry_form": "line"})


def files_under(folder) -> list[str]:
    """Every file below `folder`, by its path inside it."""
    return sorted(str(p.relative_to(folder)) for p in folder.rglob("*") if p.is_file())


class Kept(unittest.TestCase):
    def test_a_personal_look_is_chosen_and_drawn_like_a_built_in(self) -> None:
        with World():
            saved = mylooks.create("Like my-cv.pdf", SMALL)
            self.assertEqual(saved.id, "my-like-my-cv-pdf")
            found = render.look(saved.id)
            self.assertTrue(found.personal)
            self.assertEqual((found.name, found.entry_form), ("Like my-cv.pdf", "line"))
            self.assertEqual([x.id for x in render.all_looks()], ["classic", "modern", "compact", "two", saved.id])
            doc_id = humanities_cv_in_use()
            store.set_look(saved.id, doc_id=doc_id)
            page = render.render_cv_html(store.current(doc_id=doc_id)[1], None, store.doc(doc_id=doc_id)["look_id"],
                                         {"name": "Marnie Quillfeather"})
            self.assertIn("font-size: 9.2pt", page)
            self.assertIn('class="cv-line"', page)

    def test_an_id_never_repeats_a_built_in_or_another_look(self) -> None:
        with World():
            first, second = mylooks.create("Classic", SMALL), mylooks.create("Classic", SMALL)
            self.assertEqual((first.id, second.id), ("my-classic", "my-classic-2"))
            self.assertEqual(render.look("classic").personal, False)

    def test_nothing_personal_is_written_to_the_code_folder(self) -> None:
        before = files_under(render.LOOKS_DIR)
        with World():
            saved = mylooks.create("Like my-cv.pdf", SMALL)
            mylooks.rename(saved.id, "Mine")
            mylooks.set_measures(saved.id, measures.tighter(SMALL, 0), 1)
        self.assertEqual(files_under(render.LOOKS_DIR), before)

    def test_an_unknown_look_is_still_refused(self) -> None:
        with World(), self.assertRaises(render.UnknownLook):
            render.look("my-nothing")

    def test_a_saved_look_that_no_longer_reads_is_drawn_with_the_default_measures(self) -> None:
        with World():
            saved = mylooks.create("Mine", SMALL)
            con = db.connect()
            try:
                con.execute("UPDATE cv_looks SET measures = ? WHERE id = ?", ('{"size": 99}', saved.id))
                con.commit()
            finally:
                con.close()
            with self.assertLogs("jobhunt.cvstudio.mylooks", "WARNING"):
                self.assertEqual(mylooks.get(saved.id).measures, measures.Measures())


class Changes(unittest.TestCase):
    def test_a_tightened_look_prints_again_and_a_renamed_one_does_not(self) -> None:
        with World(), stand_in() as p:
            doc_id = humanities_cv_in_use()
            saved = mylooks.create("Mine", SMALL)
            store.set_look(saved.id, doc_id=doc_id)
            vid = store.current(doc_id=doc_id)[0]
            pagelimit.measure(vid)
            self.assertEqual(p.calls, 1)
            mylooks.rename(saved.id, "Still mine")
            self.assertTrue(pagelimit.known(vid)[1], "a new name prints the same page")
            mylooks.set_measures(saved.id, measures.tighter(SMALL, 0), 1)
            self.assertFalse(pagelimit.known(vid)[1], "new measures print a new page")
            pagelimit.measure(vid)
            self.assertEqual(p.calls, 2)

    def test_a_look_in_use_cannot_be_deleted_and_the_cv_is_named(self) -> None:
        with World():
            doc_id = humanities_cv_in_use()
            store.rename(doc_id, "Museum CV")
            saved = mylooks.create("Mine", SMALL)
            store.set_look(saved.id, doc_id=doc_id)
            with self.assertRaises(mylooks.InUse) as caught:
                mylooks.delete(saved.id)
            self.assertEqual(str(caught.exception), 'Your CV "Museum CV" uses the look Mine. Choose another look for '
                                                    "it first, then delete this one.")
            store.set_look("classic", doc_id=doc_id)
            mylooks.delete(saved.id)
            self.assertIsNone(mylooks.get(saved.id))

    def test_a_look_needs_a_name(self) -> None:
        with World():
            saved = mylooks.create("Mine", SMALL)
            with self.assertRaises(store.CVError):
                mylooks.rename(saved.id, "   ")


class Route(unittest.TestCase):
    def test_the_gallery_lists_your_own_looks_after_the_built_ins_and_one_can_be_chosen(self) -> None:
        with HalServer() as hal:
            client = hal.signed_in()
            saved = mylooks.create("Like my-cv.pdf", SMALL)
            looks = client.get("/api/v2/cv/looks").json()
            self.assertEqual([(x["id"], x["personal"]) for x in looks][-2:], [("two", False), (saved.id, True)])
            chosen = client.request("PUT", "/api/v2/cv/look", {"id": saved.id})
            self.assertEqual(chosen.json()["look"], {"id": saved.id, "name": "Like my-cv.pdf"})
            page = client.get("/api/v2/cv/versions/current/preview.html").text
            self.assertIn("font-size: 9.2pt", page)


if __name__ == "__main__":
    unittest.main()
