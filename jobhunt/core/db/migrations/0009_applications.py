"""Migration 0009: applications and their files.

Existing applications are numbered 1, 2, 3 ... in the order they were started: their first `applying` status
event, else the application's `updated` time, with the job id breaking ties. Folders and file names use the
number, so the order must be stable and never depend on the order rows happen to be stored in.
"""

from jobhunt.core.db.migrate import add_column, run_sql

TITLE = "Added application numbers and files"

# (table, column, declaration); every new column is nullable or has a default.
COLUMNS = (
    ("applications", "number", "INTEGER"),                    # 1, 2, 3 ... shown as 0412; folder prefix
    ("applications", "folder", "TEXT"),                       # relative to the applications folder
    ("applications", "mode", "TEXT"),                         # helper | window | headless, fixed at start
    ("applications", "helper_id", "TEXT"),                    # which paired helper holds the tab
    ("applications", "session_id", "TEXT"),                   # the session that started it
    ("applications", "approved_at", "TEXT"),                  # documents approved
    ("applications", "handed_over_at", "TEXT"),               # filled; waiting for you to press Submit
    ("applications", "sent_at", "TEXT"),
    ("applications", "sent_how", "TEXT"),                     # site_confirmed | you_said | email
    ("applications", "closed_reason", "TEXT"),                # rejected | withdrawn | advert_closed |
    # too_much_effort | not_interested | problem | skipped_for_now
    ("applications", "closed_detail", "TEXT"),                # in the person's words
    ("applications", "docs_need_redraft", "INTEGER NOT NULL DEFAULT 0"),
    ("applications", "cv_version_id", "TEXT"),
    ("applications", "cv_sent_as", "TEXT"),                   # original | hal_copy
    ("applications", "by_hand", "INTEGER NOT NULL DEFAULT 0"),  # "I'll finish it myself"
    ("applications", "follow_up_at", "TEXT"),
)

SCHEMA = """
CREATE UNIQUE INDEX applications_number ON applications(number);
CREATE TABLE files (                                         -- every file the API may serve (5.3 /files)
  id TEXT PRIMARY KEY,                                       -- random 16 hex
  root TEXT NOT NULL,                  -- applications | legacy_packs | shots | cache | cv | exports | record
  rel TEXT NOT NULL, kind TEXT NOT NULL, job_id TEXT, sha256 TEXT, size INTEGER,
  label TEXT, created TEXT NOT NULL,
  UNIQUE (root, rel));
CREATE TABLE app_documents (
  job_id TEXT NOT NULL REFERENCES jobs(id),
  kind TEXT NOT NULL,                  -- cv_pdf | cv_docx | letter_pdf | letter_txt | answers_md |
                                       -- message_txt | advert_html | prep_md
  file_id TEXT REFERENCES files(id), text TEXT, cv_version_id TEXT,
  approved_at TEXT, updated TEXT NOT NULL, PRIMARY KEY (job_id, kind));
"""


NUMBER_IN_ORDER = """
SELECT a.job_id,
       (SELECT min(e.at) FROM events e
         WHERE e.job_id = a.job_id AND e.kind = 'status' AND (e.detail = 'applying' OR e.detail LIKE 'applying:%'))
         AS started,
       a.updated
  FROM applications a
 WHERE a.number IS NULL
"""


def up(con):
    """Adds the columns and tables, then numbers existing applications in the order they were started."""
    for table, column, declaration in COLUMNS:
        add_column(con, table, column, declaration)
    run_sql(con, SCHEMA)
    taken = con.execute("SELECT coalesce(max(number), 0) FROM applications").fetchone()[0]
    rows = con.execute(NUMBER_IN_ORDER).fetchall()
    # The first 19 characters are the same in old (+00:00) and new (Z) timestamps.
    rows = sorted(rows, key=lambda r: ((r[1] or r[2] or "")[:19], r[0]))
    for number, row in enumerate(rows, start=taken + 1):
        con.execute("UPDATE applications SET number = ? WHERE job_id = ?", (number, row[0]))
