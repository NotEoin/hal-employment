"""Migration 0007: saved answers with a scope.

Every row of today's global `answers` table is copied into `answers2` with scope `review` and source
`import`, so none is reused until the person chooses where it applies (or the data task `answers.rescope`
classifies it once the settings exist). Rows whose label asks a private question (disability, adjustments,
guaranteed interview schemes, caring for someone) are not copied: they stay in the old table, which no AI
path reads, and only their count is kept, in `meta`.

The label patterns are a frozen copy of L autofill.py:33-49, so a later change to the live classifier
(`autofill.private_question`) never changes what this migration did. They match more widely than the live
classifier where in doubt: a private answer left uncopied costs one retype, a copied one could leak.
"""

import re

from jobhunt.core.db.migrate import run_sql

TITLE = "Added saved answers with where to use them"

SCHEMA = """
CREATE TABLE answers2 (
  id INTEGER PRIMARY KEY, question TEXT NOT NULL, label TEXT NOT NULL,
  answer TEXT NOT NULL,                -- JSON string, list or boolean
  scope TEXT NOT NULL,                 -- everywhere | site | job | never | review
  scope_key TEXT NOT NULL DEFAULT '',  -- site key or job id
  kind TEXT NOT NULL DEFAULT 'fixed',  -- fixed | derived (salary, how_heard: worked out each time)
  uses INTEGER NOT NULL DEFAULT 0, last_used TEXT,
  source TEXT NOT NULL DEFAULT 'you',  -- you | import | learned | note
  updated TEXT NOT NULL,
  UNIQUE (question, scope, scope_key));
"""

DISABILITY = re.compile(r"disab|long[- ]term (physical|health|condition)|neurodiver|health condition|impairment", re.I)
SCHEME = re.compile(r"disability confident|guaranteed interview|interview guarantee|guaranteed (an )?interview", re.I)
ADJUST = re.compile(r"reasonable (adjustment|accommodation)|adjustments?\b|access (needs|requirements)|"
                    r"additional (support|needs|requirements)|extra time|support (you )?(need|require)", re.I)
CARER = re.compile(r"\bcarers?\b|caring|look(s|ing)? after|care for (someone|anyone|another|a |an |family|relatives?|"
                   r"children|dependants?)|unpaid care|provide care", re.I)
# About the employer's networks or news, not the applicant (L autofill.py:46).
NOT_ASKED = re.compile(r"hear (more )?about|information about|network|newsletter", re.I)
PRIVATE_SKIPPED = "answers2:private_not_copied"


def private_label(label: str) -> bool:
    """True when a saved answer's label asks one of the private questions."""
    return bool(SCHEME.search(label) or ADJUST.search(label) or CARER.search(label)
                or (DISABILITY.search(label) and not NOT_ASKED.search(label)))


def up(con):
    """Creates `answers2` and copies every non-private saved answer into it, waiting for a choice."""
    run_sql(con, SCHEMA)
    skipped = 0
    for question, label, answer, updated in con.execute(
            "SELECT question, label, answer, updated FROM answers ORDER BY question").fetchall():
        if private_label(label or "") or private_label(question or ""):
            skipped += 1
            continue
        con.execute("INSERT INTO answers2 (question, label, answer, scope, scope_key, kind, source, updated) "
                    "VALUES (?, ?, ?, 'review', '', 'fixed', 'import', ?)", (question, label, answer, updated))
    con.execute("INSERT OR REPLACE INTO meta (k, v) VALUES (?, ?)", (PRIVATE_SKIPPED, str(skipped)))
