"""What one action's AI calls cost: calls, tokens and seconds by prompt, and `jobhunt docs-time`."""
import argparse
import contextlib
import io
import tempfile
import unittest
from pathlib import Path

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt import db
from jobhunt.ai import usage
from jobhunt.cli import docs as docs_cli
from jobhunt.core import paths

ACTION = "made-up-action"
JOB = "demo:1"


def _call(con, purpose: str, ms: int, tokens: tuple, *, ok: int = 1, effort: str | None = "low") -> None:
    con.execute("""INSERT INTO ai_usage (at, provider, priority, purpose, ok, ms, action_id, job_id, effort,
                                         tokens_in, tokens_out, tokens_thinking)
                   VALUES ('2026-11-19T12:00:00Z', 'hal', 2, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (purpose, ok, ms, ACTION, JOB, effort, *tokens))


class Usage(unittest.TestCase):
    def setUp(self):
        tmp = self.enterContext(tempfile.TemporaryDirectory(prefix="hal-usage-"))
        self.enterContext(paths.set_override(Path(tmp)))
        paths.ensure()
        self.con = db.connect()
        self.addCleanup(self.con.close)
        db.upsert(self.con, {"id": JOB, "source": "demo", "company": "Riverside Records", "title": "Junior Archivist",
                             "location": "Leeds", "url": "https://jobs.example.org/1", "description": "",
                             "salary": "", "posted_at": None, "board_size": None})
        self.con.execute("""INSERT INTO actions (id, kind, state, job_id, subject, created, started, ended, priority)
                            VALUES (?, 'docs.prepare', 'done', ?, ?, '2026-11-19T12:00:00Z', '2026-11-19T12:00:00Z',
                                    '2026-11-19T12:05:00Z', 1)""", (ACTION, JOB, JOB))
        _call(self.con, "analyse", 20_000, (1000, 600, 400))
        _call(self.con, "message", 15_000, (1400, 500, 380))
        _call(self.con, "message", 14_000, (1500, 450, None))
        _call(self.con, "brief", 3_000, (None, None, None), ok=0, effort="none")
        self.con.commit()

    def test_calls_are_added_up_by_prompt_in_the_order_first_asked(self):
        found = usage.by_prompt(self.con, ACTION)
        self.assertEqual([(c.purpose, c.calls, c.failed, c.seconds, c.tokens_in, c.tokens_out, c.thinking, c.effort)
                          for c in found],
                         [("analyse", 1, 0, 20.0, 1000, 600, 400, "low"),
                          ("message", 2, 0, 29.0, 2900, 950, 380, "low"),
                          ("brief", 1, 1, 3.0, None, None, None, "none")])
        self.assertAlmostEqual(found[0].model_seconds, 1000 / usage.READ_TOKENS_S + 600 / usage.WRITE_TOKENS_S)
        self.assertEqual(found[2].model_seconds, 0, "no tokens, no estimate")

    def test_the_last_finished_document_write_of_a_job_is_found(self):
        self.assertEqual(usage.last_documents(self.con, JOB)["id"], ACTION)
        self.assertIsNone(usage.last_documents(self.con, "demo:2"))

    def test_docs_time_prints_each_prompt_and_the_totals(self):
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            docs_cli.cmd_docs_time(argparse.Namespace(id=None, action=ACTION))
        lines = out.getvalue().splitlines()
        self.assertTrue(lines[0].startswith(f"action {ACTION} (done)"))
        self.assertEqual(lines[3].split(), ["message", "low", "2", "29", "2,900", "950", "380"])
        self.assertTrue(lines[-1].startswith("4 calls (1 failed), 0.9 minutes of calls, about 0.6 minutes"))


if __name__ == "__main__":
    unittest.main()
