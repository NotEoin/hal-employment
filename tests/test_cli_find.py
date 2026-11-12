"""`jobhunt add` keeps a job whose advert page cannot be read, and says so in one plain line."""
import argparse
import io
import unittest
import urllib.error
from contextlib import closing, redirect_stdout
from unittest import mock

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt import db, research
from jobhunt.cli import find


class AddByHand(unittest.TestCase):
    def test_a_job_whose_page_cannot_be_read_is_added_without_its_advert(self):
        # `jobhunt add` with no network (or a dead link) died in a traceback and added nothing (2026-11-12)
        args = argparse.Namespace(url="https://jobs.example.org/archivist", company="Tidewater Museum",
                                  title="Assistant Archivist", location="Leeds", salary="")
        out = io.StringIO()
        with mock.patch.object(research, "page_text",
                               side_effect=urllib.error.URLError("No address associated with hostname")), \
                redirect_stdout(out):
            find.cmd_add(args)
        job_id = out.getvalue().split()[-1]
        with closing(db.connect()) as con:
            row = con.execute("SELECT title, description FROM jobs WHERE id = ?", (job_id,)).fetchone()
        self.assertEqual(tuple(row), ("Assistant Archivist", ""))
        self.assertIn("could not read the advert", out.getvalue())


if __name__ == "__main__":
    unittest.main()
