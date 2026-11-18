"""Which of the person's CVs an application is built from: the best fit for the advert among the CVs marked
"Use this for applications" (`store.starred`), or the one the person picked for it.

The fit of each CV is a blend of two measures:
- skills: how much of what the advert asks for the CV names (`score.keywords`, weighted by how much the advert
  leans on each skill), with the skill lists of every candidate's CV type, so all are measured alike;
- wording: how close the CV's plain text reads to the advert's, by embedding cosine (`ai.embed`), from the AI
  on this computer only. Each CV version is embedded once per model and kept in memory (`_VECTORS`).

Design decisions, each with its reason:

- One CV in use is today's behaviour: it is used with no AI call, no type lookup and no reason to show.
- The person's order is a preference: the first CV (the lead) wins unless another beats it by more than
  `LEAD_MARGIN`, so a near tie never moves an application off the CV the person put first.
- No wording when vectors cannot be had (an online AI, a missing model, an error, a busy queue past
  `EMBED_WAIT_S`): the skills decide alone. Nothing here ever stops a document write: any failure gives the
  lead CV with a reason saying Hal could not compare.
- The reason is built from facts the person can check ("Covers 14 of the 18 skills this advert asks for;
  your Backend CV covers 9"), never from a similarity number alone.
- The choice is kept with the job's CV (`job_cvs.base_choice`, migration 0034, with `base_version_id`), as
  {by, reason, pick, scores}: `by` "you" for a CV the person picked, which a rewrite keeps (on that CV's
  version in use then), and "hal" for Hal's, which a rewrite makes again (`for_writing`).
"""
from __future__ import annotations

import contextlib
import json
import logging
import sqlite3
import threading
from collections.abc import Iterator
from dataclasses import dataclass, field, replace

from jobhunt import ai, db
from jobhunt.cvstudio import model, score, store
from jobhunt.cvstudio.model import CVError

log = logging.getLogger(__name__)

HAL, YOU = "hal", "you"                 # who chose an application's CV (`Choice.by`)
# The two measures count the same: skills are exact but only know the words on the type's list, wording
# catches the sector and the kind of work but cannot say why. Neither alone picked well on made-up adverts.
SKILLS_WEIGHT = 0.5
WORDING_WEIGHT = 0.5
# Embedding cosines of related texts sit high and close together (the record keeps 0.55 as "on the
# subject", `record.MEANING_MIN`); this range is stretched to 0..1 so wording differences count as much as
# skill differences.
WORDING_FLOOR, WORDING_CEIL = 0.5, 0.9
LEAD_MARGIN = 0.05                      # another CV must fit by more than 5 points of 100 to beat the lead
EMBED_CHARS = 6000                      # about 1,500 tokens: inside Ollama's default 2,048-token context
EMBED_WAIT_S = 60                       # a write waits this long at most for a turn before skills decide alone
VECTORS_KEPT = 64                       # versions whose vectors stay in memory; a person keeps a handful of CVs
FALLBACK = "Hal could not compare your CVs this time, so it used the first one in your list."
NO_SKILLS = "This advert names none of the skills Hal compares, so Hal used the first CV in your list."
YOU_CHOSE = "You chose this CV for this application."

_VECTORS: dict[tuple[str, str], list[float]] = {}
_VECTORS_LOCK = threading.Lock()


@dataclass
class Choice:
    """The CV an application is built from: which CV and version, who chose it, why, and every CV's fit."""

    doc_id: str
    version_id: str
    by: str = HAL
    reason: str = ""
    pick: str = ""                      # the CV Hal would pick, marked in the switch dialog
    scores: list[dict] = field(default_factory=list)
    hal_reason: str = ""                # why Hal picks `pick`, kept while the person's own choice stands

    def stored(self) -> str:
        """The JSON kept in `job_cvs.base_choice`."""
        return json.dumps({"by": self.by, "reason": self.reason, "pick": self.pick or self.doc_id,
                           "hal_reason": self.hal_reason if self.by == YOU else self.reason,
                           "scores": self.scores})


@contextlib.contextmanager
def _conn(con: sqlite3.Connection | None) -> Iterator[sqlite3.Connection]:
    if con is not None:
        yield con
        return
    own = db.connect()
    try:
        yield own
    finally:
        own.close()


def your(name: str) -> str:
    """A CV's name as the page says it: "your Backend CV", "your Applied AI CV", "your CV"."""
    name = " ".join(str(name or "").split())
    if name.lower().startswith("my "):
        name = name[3:]
    if not name or name.lower() == "cv":
        return "your CV"
    return f"your {name}" if name.lower().endswith("cv") else f"your {name} CV"


# ------------------------------------------------------------------------------------------- the measures

def wording(similarity: float | None) -> float | None:
    """A cosine stretched from `WORDING_FLOOR`..`WORDING_CEIL` to 0..1; None stays None."""
    if similarity is None:
        return None
    return min(1.0, max(0.0, (similarity - WORDING_FLOOR) / (WORDING_CEIL - WORDING_FLOOR)))


def blend(skills: float | None, similarity: float | None) -> float | None:
    """One CV's fit from 0 to 1: the weighted mean of the measures it has; None when it has neither."""
    parts = [(w, v) for w, v in ((SKILLS_WEIGHT, skills), (WORDING_WEIGHT, wording(similarity))) if v is not None]
    if not parts:
        return None
    return sum(w * v for w, v in parts) / sum(w for w, _ in parts)


def rank(candidates: list[dict]) -> tuple[int, str]:
    """(the index of the CV to use, why in plain words) among `candidates` in the person's order.

    Each candidate is {name, have, of, skills (0..1 or None), similarity (cosine or None)}; `fit` (0..1 or
    None) is set on each. The first is the lead and wins ties within `LEAD_MARGIN`.
    """
    for c in candidates:
        c["fit"] = blend(c.get("skills"), c.get("similarity"))
    if len(candidates) < 2:
        return 0, ""
    if all(c["fit"] is None for c in candidates):
        return 0, NO_SKILLS
    fits = [c["fit"] or 0.0 for c in candidates]
    best = max(range(len(fits)), key=lambda i: (fits[i], -i))
    if fits[best] - fits[0] <= LEAD_MARGIN:
        best = 0
    others = sorted((i for i in range(len(fits)) if i != best), key=lambda i: (-fits[i], i))
    return best, _why(candidates[best], candidates[others[0]], best == 0 and fits[0] < max(fits))


def _why(chosen: dict, runner: dict, kept_lead: bool) -> str:
    """The reason the page shows, from what each CV covers."""
    of, have, theirs = chosen.get("of") or 0, chosen.get("have") or 0, runner.get("have") or 0
    other = your(runner["name"])
    if kept_lead:
        return (f"{your(chosen['name']).capitalize()} and {other} fit this advert about as well, so Hal used the "
                "first one in your list.")
    if of and have != theirs:
        return f"Covers {have} of the {of} skills this advert asks for; {other} covers {theirs}."
    if chosen.get("similarity") is not None and runner.get("similarity") is not None:
        same = f" Both cover {have} of the {of} skills it asks for." if of else ""
        return f"Its wording is closest to this advert's, closer than {other}'s.{same}"
    return f"{your(chosen['name']).capitalize()} and {other} fit this advert about as well, so Hal used the first one."


# ------------------------------------------------------------------------------------------- choosing

def _advert(job: dict) -> str:
    return f"{job.get('title') or ''}\n{job.get('description') or ''}".strip()


def _terms(con: sqlite3.Connection, doc_ids: list[str]) -> tuple[list[str] | None, dict[str, list[str]]]:
    """The skills and aliases of every candidate's CV type together; None for the built-in list."""
    from jobhunt.cvstudio import types

    terms: set[str] = set()
    aliases: dict[str, list[str]] = {}
    for doc_id in doc_ids:
        try:
            kw = types.load(store.type_in_use(con, doc_id), con).keywords
        except LookupError as e:
            log.info("CV %s: no type to take skills from: %s", doc_id, e)
            continue
        terms |= {str(t) for t in kw.get("skills", [])}
        for k, names in (kw.get("aliases") or {}).items():
            aliases.setdefault(k, []).extend(names)
    return (sorted(terms) or None), aliases


def _vectors(advert: str, texts: dict[str, str]) -> tuple[list[float], dict[str, list[float]]] | None:
    """The advert's vector and each version's (by version id), or None when the AI on this computer cannot give
    them; a version's vector is made once per model."""
    try:
        name = ai.embed_model()
        with _VECTORS_LOCK:
            have = {vid: _VECTORS[(vid, name)] for vid in texts if (vid, name) in _VECTORS}
        missing = [vid for vid in texts if vid not in have]
        got = ai.embed([advert[:EMBED_CHARS], *(texts[v][:EMBED_CHARS] for v in missing)], model=name,
                       timeout=EMBED_WAIT_S)
    except ai.LLMUnavailable as e:
        log.info("comparing CVs by skills alone: %s", e)
        return None
    with _VECTORS_LOCK:
        for vid, vec in zip(missing, got[1:], strict=True):
            if len(_VECTORS) >= VECTORS_KEPT:
                _VECTORS.pop(next(iter(_VECTORS)))
            _VECTORS[(vid, name)] = vec
    return got[0], {**have, **dict(zip(missing, got[1:], strict=True))}


def _lead(con: sqlite3.Connection, cvs: list[dict]) -> tuple[str, str]:
    """(the lead CV's id, its version in use), making the first CV when there is none."""
    if cvs:
        doc_id = cvs[0]["id"]
    else:
        store.current(con)
        doc_id = store.lead_id(con)
    return doc_id, store.current(con, doc_id)[0]


def choose(con: sqlite3.Connection, job: dict) -> Choice:
    """Hal's pick of the starred CVs for `job`; the lead CV, with `FALLBACK` as the reason, when comparing fails.

    With one CV in use it makes no AI call and gives no reason.
    """
    cvs = store.starred(con)
    lead, lead_vid = _lead(con, cvs)
    if len(cvs) < 2:
        return Choice(lead, lead_vid, pick=lead)
    try:
        return _compared(con, job, cvs)
    except Exception:  # choosing a CV must never stop the documents: the lead CV is used and the reason says so
        log.exception("could not compare the CVs for job %s", job.get("id"))
        return Choice(lead, lead_vid, reason=FALLBACK, pick=lead)


def _compared(con: sqlite3.Connection, job: dict, cvs: list[dict]) -> Choice:
    advert = _advert(job)
    versions = {d["id"]: store.current(con, d["id"])[0] for d in cvs}
    texts = {versions[d["id"]]: model.plain_text(store.get(versions[d["id"]], con=con), {}) for d in cvs}
    terms, aliases = _terms(con, [d["id"] for d in cvs])
    vectors = _vectors(advert, texts) if advert else None
    rows = []
    for d in cvs:
        vid = versions[d["id"]]
        found = score.keywords(advert, texts[vid], terms=terms, aliases=aliases)
        covered = found["covered"]
        rows.append({"doc_id": d["id"], "version_id": vid, "name": d["name"], "have": len(found["have"]),
                           "of": found["of"], "skills": None if covered is None else covered / 100,
                           "similarity": ai.cosine(vectors[0], vectors[1][vid]) if vectors else None})
    best, why = rank(rows)
    scores = [{"doc_id": c["doc_id"], "name": c["name"], "have": c["have"], "of": c["of"],
               "fit": None if c["fit"] is None else round(100 * c["fit"]),
               "similarity": None if c["similarity"] is None else round(c["similarity"], 3)} for c in rows]
    chosen = rows[best]
    return Choice(chosen["doc_id"], chosen["version_id"], reason=why, pick=chosen["doc_id"], scores=scores)


# ------------------------------------------------------------------------------- kept with the application

def kept(con: sqlite3.Connection, job_id: str) -> Choice | None:
    """The choice kept with the job's CV (`job_cvs`); None before Hal made one. A row from before choices were
    kept reads as Hal's, on the CV its base version belongs to."""
    row = con.execute("""SELECT jc.base_version_id, jc.base_choice, v.doc_id FROM job_cvs jc
                         LEFT JOIN cv_versions v ON v.id = jc.base_version_id WHERE jc.job_id = ?""",
                      (job_id,)).fetchone()
    if row is None:
        return None
    data = json.loads(row["base_choice"] or "{}")
    doc_id = row["doc_id"] or store.lead_id(con) or store.DOC_ID
    return Choice(doc_id, row["base_version_id"], by=data.get("by") or HAL, reason=data.get("reason") or "",
                  pick=data.get("pick") or doc_id, scores=data.get("scores") or [],
                  hal_reason=data.get("hal_reason") or "")


def base_doc(job_id: str, con: sqlite3.Connection | None = None) -> str | None:
    """The CV the job's documents are built from; None while it has no job's CV (an older application, or
    none written yet)."""
    with _conn(con) as c:
        found = kept(c, job_id)
    return found.doc_id if found else None


def for_writing(con: sqlite3.Connection, job: dict, *, again: bool) -> Choice:
    """The CV to build the job's documents on, on its version in use now.

    A CV the person picked is kept, as is Hal's own pick unless `again` (the documents are written afresh,
    `pack.build`), when Hal chooses again. A kept CV that went to the archive is chosen again.
    """
    old = kept(con, job["id"])
    if old is not None:
        try:
            archived = store.doc(con, old.doc_id)["archived"]
        except store.NoSuchCV:
            archived = True
        if not archived and (old.by == YOU or not again):
            return replace(old, version_id=store.current(con, old.doc_id)[0])
    return choose(con, job)


def by_you(con: sqlite3.Connection, job: dict, doc_id: str) -> Choice:
    """The person's pick of CV for the job, on its version in use. Picking Hal's own pick again makes it Hal's
    choice once more, so a later rewrite may choose afresh. CVError for an archived CV, `store.NoSuchCV` for
    none."""
    if store.doc(con, doc_id)["archived"]:
        raise CVError("doc_id: that CV is in the archive. Restore it on My CVs first.")
    old = kept(con, job["id"])
    vid = store.current(con, doc_id)[0]
    if old is None:
        return Choice(doc_id, vid, by=YOU, reason=YOU_CHOSE)
    hal_reason = old.reason if old.by == HAL else old.hal_reason
    if doc_id == old.pick:
        return Choice(doc_id, vid, by=HAL, reason=hal_reason, pick=old.pick, scores=old.scores)
    return Choice(doc_id, vid, by=YOU, reason=YOU_CHOSE, pick=old.pick, scores=old.scores, hal_reason=hal_reason)


def shown(con: sqlite3.Connection, job_id: str) -> dict | None:
    """The CV the application is built from, for the documents step: {doc_id, name, label, by, reason,
    several}; `several` when more than one CV is in use, so the page offers to change it. An application with
    no job's CV yet (written before they were kept) was built from the lead CV, which is named with no reason;
    None when there is no CV at all.
    """
    found = kept(con, job_id)
    if found is None:
        lead = store.lead_id(con)
        if lead is None:
            return None
        found = Choice(lead, "")
    try:
        name = store.doc(con, found.doc_id)["name"] or store.DEFAULT_NAME
    except store.NoSuchCV:
        name = store.DEFAULT_NAME
    return {"doc_id": found.doc_id, "name": name, "label": your(name), "by": found.by, "reason": found.reason,
            "several": len(store.starred(con)) > 1}


def candidates(con: sqlite3.Connection, job_id: str) -> list[dict]:
    """The CVs in use the job's CV can be built from, in the person's order, each {id, name, label, chosen,
    pick, fit, have, of}; `fit`, `have` and `of` are None for a CV Hal has not compared for this job. An
    application written before job CVs were kept was built from the lead CV, so that one is `chosen`."""
    found = kept(con, job_id)
    scores = {s["doc_id"]: s for s in (found.scores if found else [])}
    out = []
    for d in store.starred(con):
        s = scores.get(d["id"], {})
        out.append({"id": d["id"], "name": d["name"], "label": your(d["name"]),
                    "chosen": found.doc_id == d["id"] if found else d["lead"],
                    "pick": bool(found and found.pick == d["id"]),
                    "fit": s.get("fit"), "have": s.get("have"), "of": s.get("of")})
    return out
