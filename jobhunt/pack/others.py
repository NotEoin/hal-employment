"""The drafts beside the letter: the message to the person hiring and the form answers, each written in one
call from the job's one request and checked with the letter (`checks.settle`)."""
from __future__ import annotations

from jobhunt import ai
from jobhunt.pack.checks import CUT_FLAG, CUT_WHAT, DANGLING_FLAG, ECHO_FLAG, Draft, check_drafts
from jobhunt.pack.drafting import Given, _draft, given, spelt
from jobhunt.pack.hiring import hiring_contacts
from jobhunt.pack.letter import check_all, employer_words
from jobhunt.pack.reading import SOMEONE, Brief, _job_text, previous_applications, prompt
from jobhunt.profile import writing as profile_writing
from jobhunt.writing import drafts, eligibility, flow, opening, plain, relocation, repeats, stage, titles

REPEAT_FLAG = "says what an earlier answer already says"   # an answer still repeating after its redraft
NO_ANSWER = 'Hal drafted no answer to "{question}": your facts gave nothing it could check. Answer it yourself'


def _answers_text(answers: list[dict]) -> str:
    return "\n".join(f"- {a.get('question', '')}: {a.get('answer', '')}" for a in answers)


def echoed_answers(answers: list[dict], sources: tuple[str, ...], names: tuple[str, ...]) -> list[str]:
    """The sentences of the "why do you want to work here" answers that restate the advert or brief."""
    return [s for a in answers if drafts.why_question(str(a.get("question") or ""))
            for s in drafts.echoed(str(a.get("answer") or ""), *sources, names)]


def repeated_answers(answers: list[dict]) -> list[tuple[int, str, int]]:
    """Each sentence of a later answer that repeats an earlier answer (`repeats.repeated`)."""
    return repeats.repeated([str(a.get("answer") or "") for a in answers])


def repeat_note(answers: list[dict], found: list[tuple[int, str, int]]) -> str:
    """The part of the answers' redraft request about later answers repeating earlier ones."""
    listed = "\n".join(f"- In the answer to \"{answers[i].get('question', '')}\": {s} (already said in the answer "
                       f"to \"{answers[j].get('question', '')}\")" for i, s, j in found)
    return (f"These sentences repeat what an earlier answer already says:\n{listed}\nRewrite those later answers "
            "so each one uses a different piece of evidence from the applicant's facts (another project, job, "
            "module or result).")


def message_opening(brief: Brief) -> opening.Opening:
    """The message's greeting and first sentence (`writing.opening`): the person's name and stage, the role as a
    person would say it, and the advert's first hiring contact (`hiring_contacts`)."""
    contacts = hiring_contacts(brief.analysis, _job_text(brief.job))
    name = str(brief.who.get("name") or "")
    return opening.Opening.of("" if name == SOMEONE else name, stage.current(), titles.of_job(brief.job),
                              contacts[0]["name"] if contacts else "")


def _names(brief: Brief) -> tuple[str, str]:
    return titles.of_job(brief.job), brief.job["company"]


def drafted_message(brief: Brief, facts: Given, *, request: str = "", now: str = "",
                    trimmed: set[str] | None = None) -> Draft:
    """The body of the message to the person hiring, written in one call after its built opening
    (`message_opening`), as a `Draft` for `settle`; a greeting or an introduction the writer adds is taken out
    (`writing.opening.body_of`). `now` is the message as it stands when the person asked for a change."""
    who, first = brief.who, message_opening(brief)
    trimmed = set() if trimmed is None else trimmed
    extra = first.note + (f"\n\nThe message as it stands:\n{now}" if now else "")
    asked = facts.request("message", extra=extra, asked=request)

    def body(text: str) -> str:
        draft = _draft(who, "message", text, trimmed)
        said = opening.body_of(draft)
        if draft in trimmed:
            trimmed.add(said)
        return said

    d = Draft("message", body(asked), lambda note: "")
    d.rewrite = lambda note: body(f"{asked}\n\nYour first draft:\n{d.value}\n\n{note}")
    return d


def drafted_answers(brief: Brief, facts: Given, questions: list[str], *, request: str = "",
                    now: list[dict] | None = None) -> Draft:
    """The answers to `questions`, written together in one call from the job's request, as a `Draft` for
    `settle`; `now` holds the answers as they stand when the person asked for a change. Each answer keeps its
    question, and one the reply leaves out is "" (listed later as `NO_ANSWER`)."""
    who = brief.who
    listing = "\n".join(f"- {q}" + (f"\n  As it stands: {a}" if (a := _now_of(now, q)) else "") for q in questions)
    asked = facts.request("answers", extra=f"Questions:\n{listing}", asked=request)

    def ask(text: str) -> list[dict]:
        found = ai.chat_json(prompt("answers", who), text, role="writer", timeout=ai.LONG_TIMEOUT_S,
                             purpose="answers").get("answers", [])
        replies = [(str(a.get("question") or ""), spelt(ai.undash(str(a.get("answer") or "")), who))
                   for a in found if isinstance(a, dict)]
        return [{"question": q, "answer": a} for q, a in zip(questions, _matched(questions, replies), strict=True)]

    sources, names = employer_words(brief), _names(brief)

    def extra(answers: list[dict]) -> list[str]:
        found = repeated_answers(answers)
        return [repeat_note(answers, found)] if found else []

    d = Draft("answers", ask(asked), lambda note: [], rule_kind="answer", as_text=_answers_text,
              echoed=lambda answers: echoed_answers(answers, sources, names), extra=extra)
    def again(note: str) -> list[dict]:
        said = ask(f"{asked}\n\nYour first answers:\n{_answers_text(d.value)}\n\n{note}")
        # a redraft with no answer at all keeps the first answers, never a set of blanks
        return said if any(a["answer"].strip() for a in said) else []

    d.rewrite = again
    return d


def _matched(questions: list[str], replies: list[tuple[str, str]]) -> list[str]:
    """Each question's answer among the reply's (question, answer) pairs: the one under its own words, else the
    reply's unmatched answers in turn, as a model may reword a question; "" when none is left."""
    by_question = {q: a for q, a in replies}
    left = iter([a for q, a in replies if q not in questions])
    return [by_question.get(q) or next(left, "") for q in questions]


def _now_of(now: list[dict] | None, question: str) -> str:
    return next((str(a.get("answer") or "") for a in now or () if a.get("question") == question), "")


def finished(brief: Brief, facts: Given, message: Draft, answers: Draft | None, *,
             trimmed: set[str] = frozenset()) -> dict:
    """The settled message and answers tidied, with their "Check before sending" lines: {message, answers,
    flags}. The message starts with its built opening."""
    you, cards = eligibility.person(), profile_writing.cards()
    sources = employer_words(brief)
    body = plain.tidy(relocation.without_own_move(message.value, you), cards)
    said = answers.value if answers is not None else []
    said = [{**a, "answer": plain.tidy(relocation.without_own_move(str(a.get("answer") or ""), you), cards)}
            for a in said]
    written = "\n\n".join([body, *(a["answer"] for a in said)])
    flags = check_drafts(brief, written, sources=(*sources, facts.words()), kind="answer",
                         claims=message.claims + (answers.claims if answers is not None else []))
    flags += [f"{ECHO_FLAG}: {s}" for s in echoed_answers(said, sources, _names(brief))]
    flags += [f"{REPEAT_FLAG}: {s}" for _, s, _ in repeated_answers(said)]
    flags += [f"{DANGLING_FLAG}: {s}" for s in flow.dangling(body)]
    flags += [NO_ANSWER.format(question=a["question"]) for a in said if not a["answer"].strip()]
    if message.value in trimmed:
        flags.append(CUT_FLAG.format(what=CUT_WHAT["message"]))
    return {"message": message_opening(brief).put(body), "answers": said, "flags": flags}


def write_others(con, job, who: dict, analysis: dict, about: str, *, facts: str, kept: str, fit: dict,
                 questions: list[str] = (), request: str = "", message_now: str = "",
                 answers_now: list[dict] | None = None) -> dict:
    """The message to the person hiring and the answers to `questions`, each written in one call from the job's
    one request and checked together in one call (`settle`): {message, answers, flags}. No questions, no answers
    call. For a change the person asks for (`docs.change`): `request` is their words, `message_now` and
    `answers_now` the drafts as they stand. A pack writes them beside its letter instead (`pack.build`), so the
    three share one check."""
    brief = Brief(job, who, analysis, about, facts, kept)
    given_facts = given(brief, fit, previous_applications(con, job))
    trimmed: set[str] = set()
    message = drafted_message(brief, given_facts, request=request, now=message_now, trimmed=trimmed)
    answers = drafted_answers(brief, given_facts, list(questions), request=request, now=answers_now) \
        if questions else None
    check_all(brief, given_facts, [message, *([answers] if answers else [])])
    return finished(brief, given_facts, message, answers, trimmed=trimmed)
