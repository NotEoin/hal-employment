"""Migration 0006: find."""

from jobhunt.core.db.migrate import add_column, run_sql

TITLE = "Added job kinds, pay, eligibility and reasons"

# (table, column, declaration); every new column is nullable or has a default.
COLUMNS = (
    ("jobs", "kind", "TEXT"),                                 # graduate_job | graduate_scheme | internship |
    # placement | insight | apprenticeship | experienced | other
    ("jobs", "kind_quote", "TEXT"),                           # advert words that set the kind
    ("jobs", "hours", "TEXT"),                                # full | part | NULL
    ("jobs", "contract", "TEXT"),                             # permanent | fixed | temp | NULL
    ("jobs", "paid", "INTEGER"),                              # 1 paid, 0 unpaid, NULL not stated
    ("jobs", "pay_min", "INTEGER"),                           # pounds a year (hourly and weekly converted, 9.2)
    ("jobs", "pay_max", "INTEGER"),
    ("jobs", "pay_per", "TEXT"),                              # year | month | week | day | hour, as advertised
    ("jobs", "students_only", "INTEGER"),
    ("jobs", "eligibility", "TEXT"),                          # JSON {requires, years: [..], quote, eligible, why}
    ("jobs", "hidden_rule", "TEXT"),                          # rule id that hid it; NULL when shown
    ("jobs", "screen_state", "TEXT"),                         # unscreened | eligible | out | error
    # (replaces the "screened eligible/out" text in score_notes)
    ("jobs", "screen_why", "TEXT"),
    ("jobs", "closes_at", "TEXT"),
    ("jobs", "saved_at", "TEXT"),
    ("jobs", "open_state", "TEXT"),                           # open | closed | unknown
    ("jobs", "open_checked_at", "TEXT"),
    ("jobs", "scored_at", "TEXT"),
    ("jobs", "goal_version", "INTEGER"),
    ("jobs", "preset", "TEXT"),
    ("jobs", "source_id", "TEXT"),                            # sources.id that found it
    ("jobs", "ats", "TEXT"),                                  # greenhouse | lever | workable | workday | ...
    ("jobs", "site_key", "TEXT"),                             # sites.key of the application form's site
)

SCHEMA = """
CREATE INDEX jobs_shown ON jobs(active, hidden_rule, score);
CREATE INDEX jobs_kind ON jobs(kind, active);
CREATE TABLE job_reasons (
  job_id TEXT NOT NULL REFERENCES jobs(id), rule TEXT NOT NULL,
  stage TEXT NOT NULL,                 -- filter | eligibility | points | screen | closed
  points INTEGER NOT NULL DEFAULT 0, hide INTEGER NOT NULL DEFAULT 0,
  label TEXT NOT NULL,                 -- "In Leeds, your first choice of place"
  note TEXT,                           -- today's short note for score_notes ("entry-level title")
  quote TEXT,                          -- advert words, verbatim
  data TEXT,                           -- JSON extras (distance, pay figure)
  PRIMARY KEY (job_id, rule));
CREATE INDEX job_reasons_hidden ON job_reasons(hide, rule);
CREATE TABLE job_requirements (
  job_id TEXT NOT NULL REFERENCES jobs(id), n INTEGER NOT NULL, text TEXT NOT NULL,
  essential INTEGER,
  state TEXT NOT NULL,                 -- met | part | miss | off ("This does not matter to me")
  quote TEXT, mine TEXT, mine_sources TEXT, tip TEXT,   -- mine_sources: JSON [{kind, ref, quote}]
  cv_version_id TEXT, updated TEXT NOT NULL, PRIMARY KEY (job_id, n));
CREATE TABLE job_feedback (
  id INTEGER PRIMARY KEY, job_id TEXT NOT NULL REFERENCES jobs(id), at TEXT NOT NULL,
  verdict TEXT NOT NULL,               -- no | save | unsave | put_back | apply | discard
  reason TEXT,                         -- too_far | pay_low | not_my_work | too_senior | not_employer |
                                       -- closed | too_much_effort | other
  words TEXT);
CREATE INDEX job_feedback_reason ON job_feedback(reason, at);
CREATE TABLE goal_versions (
  id INTEGER PRIMARY KEY, at TEXT NOT NULL,
  by TEXT NOT NULL,                    -- you | setup | quiz | chat | import | learned:<item id>
  summary TEXT NOT NULL,               -- "Lowest pay changed to 24,000 pounds"
  data TEXT NOT NULL);                 -- every goal.* and scoring.* value as JSON
"""


def up(con):
    """Added job kinds, pay, eligibility and reasons."""
    for table, column, declaration in COLUMNS:
        add_column(con, table, column, declaration)
    run_sql(con, SCHEMA)
