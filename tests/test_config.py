"""The board list: the data folder's imported copy wins over the checkout's file."""
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt import config
from jobhunt.core import paths


class Boards(unittest.TestCase):
    """Each test in a fresh temporary home."""

    def setUp(self):
        tmp = tempfile.TemporaryDirectory(prefix="hal-boards-")
        self.addCleanup(tmp.cleanup)
        override = paths.set_override(Path(tmp.name))
        override.__enter__()
        self.addCleanup(override.__exit__, None, None, None)

    def test_the_board_list_comes_from_the_imported_copy(self):
        # the checkout's boards.toml is deleted; the import left a copy in the data folder
        imported = paths.data_dir() / "import" / "boards.toml"
        imported.parent.mkdir(parents=True, exist_ok=True)
        imported.write_text('[[board]]\nname = "Made-up Board"\nats = "greenhouse"\nslug = "madeup"\n')
        self.assertEqual(config.boards_path(), imported)
        self.assertEqual([b["name"] for b in config.boards()], ["Made-up Board"])


if __name__ == "__main__":
    unittest.main()
