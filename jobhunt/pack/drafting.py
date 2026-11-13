"""What the letter, the message and the answers are drafted from: the shared request, a whole draft asked
for again when it is cut off, the paragraph scope and the one story ranking per pack."""
import logging
import re

from jobhunt import ai, record
from jobhunt.pack.checks import applicant_names
from jobhunt.pack.fitmap import known_tools
from jobhunt.pack.reading import Brief, _job_text, _record_part, _requirements, field_of, previous_applications, prompt
from jobhunt.profile import writing as profile_writing
from jobhunt.writing import drafts, fields, paragraphs, plain, rank, spelling, stage
from jobhunt.writing import plan as planning

log = logging.getLogger(__name__)


CUT_NOTE = "Your last reply stopped mid-sentence. Write the whole draft again, and end it with a full sentence."
ROOM_CTX = 16384          # tokens for the second ask after a cut-off reply, as for the CV score and critique
# The letter and the message are drafted cooler than other prose, as each paragraph is (`writing.paragraphs`).
DRAFT_TEMPERATURE = paragraphs.DRAFT_TEMPERATURE
NOTE_PARA = re.compile(r"\(.*\)\Z|(notes?|word count)\b", re.S | re.I)   # a closing paragraph about the draft


def _context(brief: Brief, fit: dict, history: str) -> str:
    """The request text every drafted document shares: the job, the evidence and the person's dates."""
    job, analysis = brief.job, brief.analysis
    return (f"Job: {job['title']} at {job['company']}\nRole summary: {analysis.get('summary', '')}\n"
            + (f"Earlier applications to this employer: {history}. Do not mention them unless asked.\n"
               if history else "") +
            f"{stage.today_lines()}\nMention availability only if the advert's start date is "
            f"flexible; for a fixed intake, refer to that intake instead.\n"
            f"Company: {brief.about}\nEvidence map: {fit.get('matches', [])}\n\nCV facts:\n{brief.facts}"
            + _record_part(f"Employment record ({record.OWN_DOCUMENTS}; use it where the CV is silent)", brief.kept))


def shared_context(con, job, who: dict, read: dict, about: str, facts: str) -> str:
    """The request text the letter, message and answers share (`_context`) for a `read_job` reading."""
    return _context(Brief(job, who, read["analysis"], about, facts, read["kept"]), read["fit"],
                    previous_applications(con, job))


def spelt(text: str, who: dict) -> str:
    """An AI draft in the person's spelling (`who["spelling_code"]`; UK English when it is unset)."""
    return spelling.respell(text, who.get("spelling_code"))


def _draft(who: dict, prompt_id: str, user: str, trimmed: set[str]) -> str:
    """A text draft (letter or message) at `DRAFT_TEMPERATURE`, asked for again once with more room when the
    reply stops mid-sentence (`drafts.cut_off`); when that one stops too it is kept to its last full sentence
    and added to `trimmed`, so "Check before sending" can say so if it is the draft that is kept."""
    def ask(text: str, ctx: int | None = None) -> str:
        said = ai.chat(prompt(prompt_id, who), text, role="writer", ctx=ctx, temperature=DRAFT_TEMPERATURE,
                       timeout=ai.LONG_TIMEOUT_S, purpose=prompt_id).strip()
        return spelt(drop_notes(ai.undash(said)), who)

    draft = ask(user)
    if not drafts.cut_off(draft):
        return draft
    again = ask(f"{user}\n\n{CUT_NOTE}", ROOM_CTX)
    if not drafts.cut_off(again):
        return again
    kept = drafts.last_full_sentence(again)
    trimmed.add(kept)
    return kept


def drop_notes(text: str) -> str:
    """A draft without the model's closing notes about it, and without Markdown bold."""
    # Claude ended a message with "(About 75 words. The advert names no individual, ...)" and began one with
    # "**Subject:** ..." (2026-10-23); neither belongs in what the person sends
    paras = [p for p in re.split(r"\n\s*\n", text.strip()) if p.strip()]
    while len(paras) > 1 and NOTE_PARA.match(paras[-1].strip()):
        paras.pop()
    return re.sub(r"\*\*(.+?)\*\*", r"\1", "\n\n".join(paras))


def paragraph_scope(brief: Brief, *, found: list[planning.Story], cards: list[dict], request: str = "",
                    kind: str = "letter") -> paragraphs.Scope:
    """What each paragraph of one job's `kind` of draft is drafted with and checked against beside its own story
    (`writing.paragraphs`): the job and its advert, today, the field's conventions, the employer's words, every
    story and card, the person's names, modules and settings wording, and for a letter, while "Match my own
    writing" is on, short excerpts of the person's own letters for their voice (`sections.letters`)."""
    job, who, about = brief.job, brief.who, brief.about
    now, advert = stage.current(), _job_text(job)
    company = (f"What the employer does (from the company brief; never copy its words):\n"
               f"{about.strip()[:paragraphs.BRIEF_CHARS]}\n\n") if (about or "").strip() else ""
    context = (f"The job: {job['title']} at {job['company']}\nThe advert (for what the job is; never copy its "
               f"words):\n{advert[:paragraphs.ADVERT_CHARS]}\n\n{company}{stage.today_lines(now)}\n\n"
               + fields.block(field_of(job, who, brief.analysis), kind))
    return paragraphs.Scope(tuple(found), tuple(cards), tuple(known_tools(cards)),
                            f"{advert}\n{about}\n{stage.today_lines(now)}", advert, about,
                            (job["title"], job["company"]), now, tuple(profile_writing.module_names()),
                            (brief.facts, brief.kept),
                            tuple(applicant_names(who)), tuple(plain.settings_labels()), who, context.strip(), request,
                            tuple(profile_writing.studied_skills()), _voice(kind), tuple(profile_writing.degrees()))


def _voice(kind: str) -> tuple[str, ...]:
    """Excerpts of the person's own letters for a letter's requests (`sections.letters.voice_excerpts`); none for
    another kind of draft, or while "Match my own writing" (`cvdocs.voice`) is off."""
    from jobhunt.core import settings
    from jobhunt.sections import letters

    if kind != "letter" or not settings.get("cvdocs.voice"):
        return ()
    return tuple(letters.voice_excerpts())


def story_ranking(brief: Brief, found: list[planning.Story], cards: list[dict]) -> list[rank.Ranked]:
    """The one AI ranking of the person's work for one job (`writing.rank`), logged with its reasons; [] when it
    gave nothing usable, so the job's words choose the work."""
    job, analysis = brief.job, brief.analysis
    ranked = rank.make(rank.Job(job["title"], job["company"], str(analysis.get("summary") or ""),
                                tuple(_requirements(analysis)), brief.about or ""), cards, found)
    if ranked:
        log.info("story ranking for job %s: %s", job["id"], rank.said(ranked))
    else:
        log.info("story ranking for job %s: nothing usable, so the job's words chose the work", job["id"])
    return ranked


def fitting_names(ranking: list[rank.Ranked]) -> set[str] | None:
    """The names of the work the ranking fits to the job (`rank.fits`), for `motive.make`; None with no ranking."""
    return {r.name for r in ranking if rank.fits(r.name, ranking)} if ranking else None
