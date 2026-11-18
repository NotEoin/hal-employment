"""Migration 0033: several CVs, each with its own name, history and version numbers.

- `cv_documents` gains `name` (the person can change it), `starred` ("Use this for applications"),
  `archived` (when it went to the archive; NULL while in use) and `position` (the order of the starred ones).
- `cv_preferences.doc_id` names the CV a line preference belongs to (keep my wording, lock a line), because a
  line's target only means something in one CV. NULL is every CV (tone, length, kinds left out).
- The one history Hal kept so far is split into one CV per upload, in the same transaction as the new columns,
  so no page ever sees half of it. An "uploaded" version starts a CV, and the versions after it, up to the next
  upload, belong to it; versions before the first upload (imported, generated) form one CV. Each CV's versions
  are numbered again from 1 in their old order. Version ids, files and every reference stay as they were.
- The CV holding the version in use keeps the id "main", its file and its settings, and is starred: tailoring,
  the pack writer and exports read the same CV as before. The others are archived, nothing is deleted.
- Per-job (tailored) versions go with the CV their base version went to (from `job_cvs`), else with the CV in
  use when they were made. They take no number of that CV: `n` below zero is their own count, only there for
  the table's UNIQUE (doc_id, n), and is never shown.
- Suggestions follow the version they were worked out from; a line preference follows the suggestion it came
  from, else stays with "main".
"""

from pathlib import PurePath

from jobhunt.core.db.migrate import add_column, iso

TITLE = "Added several CVs, each with its own versions"

# Frozen copies of the live code's values (`store.DOC_ID`, `suggest.LINE_PREFERENCES`), so a later change there
# never changes what this migration did.
MAIN = "main"
FIRST_NAME = "My CV"                    # the CV made before any upload: imported or a starter
LINE_PREFERENCES = ("keep_wording", "lock_line")


def _name(con, file_id: str | None) -> str:
    if not file_id:
        return FIRST_NAME
    row = con.execute("SELECT name FROM cv_files WHERE id = ?", (file_id,)).fetchone()
    return (PurePath(row[0]).stem.strip() if row else "") or FIRST_NAME


def _groups(rows: list[tuple]) -> list[list[tuple]]:
    """The non-job versions in number order, cut before each upload."""
    groups: list[list[tuple]] = []
    for row in rows:
        if row[2] == "uploaded" or not groups:
            groups.append([])
        groups[-1].append(row)
    return groups


def _file_of(group: list[tuple]) -> str | None:
    """The file a group's upload came from; None for the versions before the first upload."""
    return group[0][3] if group[0][2] == "uploaded" else None


def _add_cv(con, doc_id: str, group: list[tuple], main: tuple) -> None:
    """One archived CV for a group of versions, with the main CV's settings and the group's own file."""
    route, send, type_id, look_id, layout = main
    file_id = _file_of(group)
    kept = [r for r in group if not r[4]] or group
    own_route, own_send = (route, send) if file_id else ("improve", "hal_copy")
    con.execute("""INSERT INTO cv_documents (id, route, send, baseline_file_id, current_version_id, type_id,
                   look_id, layout, created, updated, name, starred, archived, position)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0, ?, 0)""",
                (doc_id, own_route, own_send, file_id, kept[-1][0], type_id, look_id, layout, group[0][5],
                 group[-1][5], _name(con, file_id), iso()))


def _split(con) -> None:
    doc = con.execute("SELECT route, send, type_id, look_id, layout, current_version_id "
                      "FROM cv_documents WHERE id = ?", (MAIN,)).fetchone()
    if doc is None:
        return
    rows = con.execute("SELECT id, n, origin, file_id, binned, created FROM cv_versions "
                       "WHERE doc_id = ? AND job_id IS NULL ORDER BY n", (MAIN,)).fetchall()
    groups = _groups(rows)
    if not groups:
        con.execute("UPDATE cv_documents SET name = ?, starred = 1 WHERE id = ?", (FIRST_NAME, MAIN))
        return
    lead = next((i for i, g in enumerate(groups) if any(r[0] == doc[5] for r in g)), len(groups) - 1)
    ids = {i: MAIN if i == lead else f"cv-{g[0][0]}" for i, g in enumerate(groups)}
    where = {r[0]: i for i, g in enumerate(groups) for r in g}
    con.execute("UPDATE cv_documents SET name = ?, starred = 1, position = 0 WHERE id = ?",
                (_name(con, _file_of(groups[lead])), MAIN))
    for i, g in enumerate(groups):
        if i != lead:
            _add_cv(con, ids[i], g, tuple(doc[:5]))
    _move_job_versions(con, rows, where, ids, lead)
    for i, g in enumerate(groups):
        if i != lead:
            for n, r in enumerate(g, 1):
                con.execute("UPDATE cv_versions SET doc_id = ?, n = ? WHERE id = ?", (ids[i], n, r[0]))
    # ascending, so each new number is free: every lower old number has already moved down or away
    for n, r in enumerate(groups[lead], 1):
        con.execute("UPDATE cv_versions SET n = ? WHERE id = ?", (n, r[0]))
    _move_suggestions(con, where, ids)


def _move_job_versions(con, rows: list[tuple], where: dict, ids: dict, lead: int) -> None:
    jobs = con.execute("SELECT id, n FROM cv_versions WHERE doc_id = ? AND job_id IS NOT NULL ORDER BY n",
                       (MAIN,)).fetchall()
    base_of = {v: b for v, b in con.execute("SELECT version_id, base_version_id FROM job_cvs "
                                             "WHERE version_id IS NOT NULL")}
    counts: dict[str, int] = {}
    for vid, n in jobs:
        group = where.get(base_of.get(vid, ""))
        if group is None:
            before = [r[0] for r in rows if r[1] < n]
            group = where[before[-1]] if before else lead
        doc_id = ids[group]
        counts[doc_id] = counts.get(doc_id, 0) + 1
        con.execute("UPDATE cv_versions SET doc_id = ?, n = ? WHERE id = ?", (doc_id, -counts[doc_id], vid))


def _move_suggestions(con, where: dict, ids: dict) -> None:
    doc_of = {vid: ids[g] for vid, g in where.items()}
    for sid, based_on, result in con.execute("SELECT id, based_on, result_version_id FROM cv_suggestions "
                                             "WHERE doc_id = ?", (MAIN,)).fetchall():
        doc_id = doc_of.get(based_on) or doc_of.get(result or "")
        if doc_id and doc_id != MAIN:
            con.execute("UPDATE cv_suggestions SET doc_id = ? WHERE id = ?", (doc_id, sid))
    marks = ",".join("?" * len(LINE_PREFERENCES))
    for pid, from_suggestion in con.execute(f"SELECT id, from_suggestion FROM cv_preferences WHERE kind IN ({marks})",
                                            LINE_PREFERENCES).fetchall():
        row = con.execute("SELECT doc_id FROM cv_suggestions WHERE id = ?", (from_suggestion,)).fetchone()
        con.execute("UPDATE cv_preferences SET doc_id = ? WHERE id = ?", (row[0] if row else MAIN, pid))


def up(con):
    """Adds the CVs' names, stars, archive and order, and splits the one history into one CV per upload."""
    add_column(con, "cv_documents", "name", "TEXT NOT NULL DEFAULT ''")
    add_column(con, "cv_documents", "starred", "INTEGER NOT NULL DEFAULT 0")
    add_column(con, "cv_documents", "archived", "TEXT")
    add_column(con, "cv_documents", "position", "INTEGER NOT NULL DEFAULT 0")
    add_column(con, "cv_preferences", "doc_id", "TEXT")
    _split(con)
