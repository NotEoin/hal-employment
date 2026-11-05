"""Migration 0021: the person's edits of Hal's drafts, kept as pairs to learn their writing from.

Each row is one letter, answer, message or CV line as Hal drafted it and as the person left it. Repeated
edits of the same piece keep the first draft and the latest final, so a pair always shows what Hal wrote
against what the person chose. Nothing here is a private answer: the save paths skip every question
`autofill.private_question` marks before a pair is kept.

The table stands on its own (no foreign key), so this migration does not depend on 0019 or 0020.
"""

from jobhunt.core.db.migrate import run_sql

TITLE = "Added the edits you make to Hal's drafts"

SCHEMA = """
CREATE TABLE writing_edits (
  id INTEGER PRIMARY KEY,
  kind TEXT NOT NULL,                  -- letter | answer | message | cv_line
  ref TEXT NOT NULL,                   -- what was edited: a job id, a question, a CV line's target
  job_id TEXT,
  draft TEXT NOT NULL,                 -- Hal's text, as first edited
  final TEXT NOT NULL,                 -- the person's latest text
  created TEXT NOT NULL, updated TEXT NOT NULL);
CREATE INDEX writing_edits_kind ON writing_edits (kind, updated);
CREATE INDEX writing_edits_ref ON writing_edits (kind, ref);
"""


def up(con):
    """Added the edits you make to Hal's drafts."""
    run_sql(con, SCHEMA)
