"""File and folder names safe on every system and every application site."""
import tempfile
import unittest
from pathlib import Path

from support import stubs

from jobhunt.core import naming, paths, settings
from jobhunt.core.settings import store

stubs.install()


class Names(unittest.TestCase):
    """Names come from the person's settings, folded to plain ASCII."""

    def test_accents_are_dropped_from_a_file_stem(self):
        self.assertEqual(naming.file_stem("Se\u00e1n \u00d3 Bria\u00edn"), "Sean-O-Briain")
        self.assertEqual(naming.file_stem("  "), naming.FALLBACK_STEM)

    def test_a_file_name_follows_its_template(self):
        self.assertEqual(naming.file_name("cv", name="Robin Testwell", template="{name} CV.pdf"),
                         "Robin Testwell CV.pdf")
        self.assertEqual(naming.file_name("letter", name="Zo\u00eb Made-Up", template="{name} letter.pdf"),
                         "Zoe Made-Up letter.pdf")

    def test_a_template_may_only_use_name(self):
        with self.assertRaises(ValueError):
            naming.file_name("cv", name="Robin", template="{surname}.pdf")
        with self.assertRaises(ValueError):
            naming.file_name("photo", name="Robin", template="{name}.png")

    def test_characters_windows_refuses_are_removed(self):
        self.assertEqual(naming.safe_part('A<b>:c"/d\\e|f?g*h. . '), "A b c d e f g h")

    def test_a_folder_name_is_numbered_and_short(self):
        name = naming.folder_name(42, "Fernhill Analytics", "Graduate Software Engineer: Platform / Tools")
        self.assertEqual(name, "0042 Fernhill Analytics - Graduate Software Engineer Platform Tools")
        self.assertLessEqual(len(naming.folder_name(7, "A" * 60, "B" * 60)), naming.MAX_FOLDER)

    def test_without_arguments_the_settings_give_the_name_and_template(self):
        with tempfile.TemporaryDirectory(prefix="hal-naming-") as tmp, paths.set_override(Path(tmp)):
            store.drop_cache()
            self.addCleanup(store.drop_cache)
            settings.set("you.name", "Robin Testwell")
            self.assertEqual(naming.file_name("cv"), "Robin Testwell CV.pdf")


if __name__ == "__main__":
    unittest.main()
