"""'Something wrong here?': the person's report becomes a note they see before it is saved.

1. `start` keeps the report (`site_reports`) and starts `sites.propose_note`.
2. The action sends the words, the page survey and the note kinds to the `site_note` prompt. A valid answer
   becomes a draft, shown with the scopes the person may pick; nothing is used until they press Save. When
   the AI is busy, missing, refuses or answers something that does not pass `notes.validate`, the words are
   saved at once as a `note` for this site, so nothing the person said is lost.
3. `save` makes the note at the chosen scope; `next` is one of the three ways out: try again, finish it
   yourself, or skip the job for now.

The survey holds button names and question labels only, never the answers, and leaves out every question
`autofill.private_question` matches, so neither a private question nor its label reaches the AI.
"""
from __future__ import annotations

import json
import logging
import sqlite3

from jobhunt.core import activity, clock
from jobhunt.sites import notes as N

log = logging.getLogger(__name__)

WHAT = ("Hal closed the form", "This answer is wrong", "A list was not filled in", "The file upload did not work",
        "It needs an account", "Hal pressed the wrong button", "Something else")
MAX_WORDS = 2000          # characters of the person's own words kept with a report
MAX_SURVEY = 80           # labels and button names sent: enough to place the problem, small enough for any model
CHOICES = ("retry", "myself", "skip")
PROMPT = "site_note"
NOTE_KINDS_HELP = {
    "open_url": "open the form directly: {append: \"/apply\"} or {rewrite: {from, to}}",
    "wait_redirect": "wait for a holding page to pass you on: {host, until: \"host_changes\", max_s}",
    "press_first": "press a button before looking for the form: {control: the button's name}",
    "form_frame": "the form is inside a box on the page: {host_contains: [part of the box's address]}",
    "cookie_button": "close the cookie banner with one button: {text} or never press some: {never: [..]}",
    "needs_account": "the site needs an account: {sign_in_url}",
    "buttons": "the site's own words for its buttons: {next: [..], success: [..], closed: [..]}",
    "field_transform": "reshape an answer for one question: {transform: city_only | digits_only | uk_phone | "
                       "date:FORMAT | max_len:N}, with cond {label}",
    "field_answer": "always answer one question this way on this site: {value}, with cond {label}",
    "field_skip": "leave one question empty on this site: {}, with cond {label}",
    "widget": "how lists behave: {no_escape, type_slowly, scroll_list, wait_suggestions_ms}",
    "upload_via": "how to upload a file: {control: the button's name, mode: \"select\" | \"drop\", wait_s}",
    "timing": "the site is slow: {settle_s, page_timeout_s, fill_timeout_s}",
    "note": "free words shown at each visit: {}",
}


def survey(form: dict | None) -> dict:
    """Button names and question labels from a form Hal read, with no answer and no private question."""
    from jobhunt import autofill

    form = form or {}
    labels = []
    for f in form.get("fields") or []:
        label = str(f.get("label") or "").strip()
        if not label or f.get("type") == "password" or autofill.private_question(f):
            continue
        labels.append(label)
    buttons = [str(c.get("name") if isinstance(c, dict) else c).strip()
               for c in (form.get("controls") or form.get("buttons") or [])]
    return {"address": N.site_key(form.get("url") or ""), "questions": list(dict.fromkeys(labels))[:MAX_SURVEY],
            "buttons": [b for b in dict.fromkeys(buttons) if b][:MAX_SURVEY]}


def _form(con: sqlite3.Connection, job_id: str | None) -> dict:
    if not job_id:
        return {}
    row = con.execute("SELECT form FROM applications WHERE job_id = ?", (job_id,)).fetchone()
    try:
        return json.loads(row["form"]) if row and row["form"] else {}
    except ValueError:
        return {}


def _site_for(con: sqlite3.Connection, job_id: str | None, site: str) -> tuple[str, str | None]:
    """The site key and application system a report is about: the form's own address when Hal reached it."""
    url = _form(con, job_id).get("url") or ""
    if not url and job_id:
        row = con.execute("SELECT url FROM jobs WHERE id = ?", (job_id,)).fetchone()
        url = row["url"] if row else ""
    if url:
        return N.site_key(url), N.ats_of(url)
    site = (site or "").strip().lower().removeprefix("https://").removeprefix("http://").strip("/")
    return site, N.ats_of("https://" + site) if site else None


def start(con: sqlite3.Connection, *, job_id: str | None, site: str, what: str, words: str,
          session_id: str | None = None, remote: bool = False) -> dict:
    """Keeps a report and starts turning it into a note; returns {report_id, action_id}."""
    from jobhunt.core import runner

    what = (what or "").strip() or "Something else"
    if what not in WHAT:
        raise ValueError(f"Choose what happened: one of {', '.join(WHAT)}.")
    words = (words or "").strip()[:MAX_WORDS]
    if not words and what == "Something else":
        raise ValueError("Tell Hal what happened in your own words.")
    if job_id and con.execute("SELECT 1 FROM jobs WHERE id = ?", (job_id,)).fetchone() is None:
        raise LookupError("That job is not in Hal's list.")
    key, ats = _site_for(con, job_id, site)
    if not key:
        raise ValueError("Say which site went wrong.")
    cur = con.execute("INSERT INTO site_reports (at, job_id, site, ats, what, words, state) "
                      "VALUES (?, ?, ?, ?, ?, ?, 'new')",
                      (clock.iso(), job_id, key, ats, what, words))
    con.execute("INSERT INTO site_events (at, job_id, site, what, detail) VALUES (?, ?, ?, 'report', ?)",
                (clock.iso(), job_id, key, what))
    con.commit()
    rid = cur.lastrowid
    runner.load_kinds()
    action_id = runner.start("sites.propose_note", params={"report_id": rid, "words": words or what},
                             subject=f"site-report:{rid}",
                             job_id=job_id, session_id=session_id, remote=remote,
                             why=f"You told Hal: {words or what}")
    return {"report_id": rid, "action_id": action_id}


def _report(con: sqlite3.Connection, rid: int) -> sqlite3.Row:
    row = con.execute("SELECT * FROM site_reports WHERE id = ?", (rid,)).fetchone()
    if row is None:
        raise LookupError("That report is not in Hal's list.")
    return row


def scopes(site: str, ats: str | None) -> list[dict]:
    """The places a note from a report may apply, safest first."""
    out = [{"id": "site", "label": "Just this site", "help": "The safe choice.", "where": site}]
    if ats:
        name = N.ATS_NAMES.get(ats, ats)
        out.append({"id": "ats", "label": f"All {name} sites", "where": f"every site that uses {name}",
                    "help": f"{name} is the application system this employer uses. Many employers use it, so a note "
                            f"for all {name} sites helps on each of them."})
    out.append({"id": "global", "label": "Everywhere", "where": "every site",
                "help": "Hal uses the note on every job site."})
    return out


def _ask(row: sqlite3.Row, form: dict) -> dict:
    """The AI's draft for a report, checked; raises ValueError when there is no usable one."""
    from jobhunt import ai
    from jobhunt.ai import prompts

    user = json.dumps({"what_happened": row["what"], "their_words": row["words"] or "",
                       "site": row["site"], "application_system": N.ATS_NAMES.get(row["ats"] or "", ""),
                       "page": survey(form), "kinds": NOTE_KINDS_HELP}, indent=1)
    reply = ai.chat_json(prompts.text(PROMPT), user, role="checker", purpose=PROMPT, priority=0, job_id=row["job_id"])
    kind, cond, act = reply.get("kind"), reply.get("cond") or {}, reply.get("act") or {}
    text = str(reply.get("text") or "").strip()
    if not isinstance(kind, str) or kind not in NOTE_KINDS_HELP:
        raise ValueError(f"the AI chose no kind Hal knows ({kind!r})")
    N.validate(kind, "site", cond, act, text)
    if kind in ("field_answer", "field_skip") and N._private_label(cond):
        raise ValueError("the AI suggested an answer to a private question")
    return {"kind": kind, "cond": cond, "act": act, "text": text}


def propose_note(ctx, report_id: int) -> dict:
    """The action body: a draft for the person to check, or the words saved straight away as a note."""
    from jobhunt.ai import LLMUnavailable, PrivateLeak

    con = ctx.con
    row = _report(con, report_id)
    ctx.step("Reading what you told Hal", total=2)
    form = _form(con, row["job_id"])
    try:
        draft = _ask(row, form)
    except (LLMUnavailable, PrivateLeak, ValueError, N.NoteError) as e:
        why = str(e) or e.__class__.__name__
        log.info("site report %s kept as words: %s", report_id, why)
        ctx.step("Saving your words as a note for this site", total=2)
        note_id = N.add("site", row["site"], "note", {}, {}, _plain(row), said=row["words"], origin="you", con=con)
        con.execute("UPDATE site_reports SET state = 'saved', note_id = ? WHERE id = ?", (note_id, report_id))
        con.commit()
        activity.record("learning", "sites.note_saved", f"Saved your words as a note for {row['site']}",
                        f"You told Hal: {row['words'] or row['what']}", job_id=row["job_id"],
                        detail=f"Hal could not turn them into a note it acts on: {why}", con=con)
        con.commit()
        return {"report_id": report_id, "saved_as_note": True, "note_id": note_id, "text": _plain(row),
                "site": row["site"]}
    con.execute("UPDATE site_reports SET state = 'drafted', draft = ? WHERE id = ?", (json.dumps(draft), report_id))
    con.commit()
    ctx.step("Here is what Hal will remember", total=2)
    return {"report_id": report_id, "site": row["site"],
            "draft": {"text": draft["text"], "kind": draft["kind"], "scopes": scopes(row["site"], row["ats"])}}


def _plain(row: sqlite3.Row) -> str:
    """The person's words as a note's text, cut to a note's length."""
    words = " ".join((row["words"] or row["what"]).split())
    return words if len(words) <= N.MAX_TEXT else words[: N.MAX_TEXT - 3].rstrip() + "..."


def save(con: sqlite3.Connection, report_id: int, scope: str = "site", text: str = "") -> dict:
    """Saves the drafted note where the person chose; returns {note_id}."""
    row = _report(con, report_id)
    if row["state"] == "saved":
        if text.strip() and row["note_id"]:
            N.edit(row["note_id"], text, con=con)
        return {"note_id": row["note_id"]}
    if row["state"] != "drafted" or not row["draft"]:
        raise ValueError("Hal is still reading your report.")
    draft = json.loads(row["draft"])
    where = {"site": ("tenant" if N.site_key("https://" + row["site"]) != N.registrable("https://" + row["site"])
                      else "site", row["site"]), "global": ("global", "")}
    if row["ats"]:
        where["ats"] = ("ats", row["ats"])
    if scope not in where:
        labels = ", ".join(s["label"] for s in scopes(row["site"], row["ats"]))
        raise ValueError(f"Choose where to use the note: {labels}")
    scope_kind, scope_key = where[scope]
    note_id = N.add(scope_kind, scope_key, draft["kind"], draft["cond"], draft["act"], text.strip() or draft["text"],
                    said=row["words"], origin="you", con=con)
    con.execute("UPDATE site_reports SET state = 'saved', note_id = ? WHERE id = ?", (note_id, report_id))
    activity.record("learning", "sites.note_saved", f"Saved a note for {N.scope_words(_note(note_id, con))}",
                    f"You told Hal: {row['words'] or row['what']}", job_id=row["job_id"], con=con)
    con.commit()
    return {"note_id": note_id}


def _note(note_id: str, con: sqlite3.Connection) -> N.Note:
    return next(s.note for s in N.stored(con) if s.note.id == note_id)


def next_step(con: sqlite3.Connection, report_id: int, choice: str, *, session_id: str | None = None,
              remote: bool = False) -> dict:
    """One of the three ways out after a report."""
    from jobhunt.apply import actions as apply_actions
    from jobhunt.core import runner
    from jobhunt.docs import folders

    if choice not in CHOICES:
        raise ValueError("Choose Try again, I'll finish it myself, or Skip this job for now.")
    row = _report(con, report_id)
    job_id = row["job_id"]
    if not job_id:
        raise ValueError("This report is not about one application, so there is nothing to try again.")
    con.execute("UPDATE site_reports SET outcome = ? WHERE id = ?", (choice, report_id))
    con.commit()
    if choice == "retry":
        action_id = apply_actions.start("apply.fill", job_id, params={"fresh": True}, session_id=session_id,
                                        remote=remote, why="You saved a note and asked Hal to try again")
        return {"action_id": action_id}
    if choice == "myself":
        runner.stop_action(job_id)
        con.execute("INSERT OR IGNORE INTO applications (job_id, stage, updated) VALUES (?, 'documents', ?)",
                    (job_id, clock.iso()))
        con.execute("UPDATE applications SET by_hand = 1, updated = ? WHERE job_id = ?", (clock.iso(), job_id))
        activity.record("applying", "sites.by_hand", "You are finishing this application yourself",
                        "You chose \"I'll finish it myself\" after telling Hal what went wrong", job_id=job_id,
                        con=con)
        con.commit()
        number = folders.number_for(con, job_id, create=False)
        return {"route": f"docs-{number}" if number else "docs"}
    stopped = runner.stop_action(job_id)
    status = con.execute("SELECT status FROM jobs WHERE id = ?", (job_id,)).fetchone()
    if stopped or (status and status["status"] == "applying"):
        runner.POOL.submit(apply_actions._close_quietly, job_id)
    activity.record("applying", "sites.skip", "Skipped this job for now. It stays in Applications.",
                    "You chose \"Skip this job for now\" after telling Hal what went wrong", job_id=job_id,
                    detail="Hal stopped" if stopped else None, con=con)
    con.commit()
    return {"route": f"app-{job_id}"}
