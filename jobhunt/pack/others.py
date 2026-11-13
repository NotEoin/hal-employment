"""The drafts beside the letter: the message to the person hiring and the form answers, one story a
paragraph when the plan tells any, else drafted whole, and none left blank."""

from .. import ai
from ..profile import writing as profile_writing
from ..writing import (
    drafts,
    eligibility,
    fields,
    flow,
    motive,
    opening,
    paragraphs,
    plain,
    rank,
    relocation,
    repeats,
    stage,
)
from ..writing import plan as planning
from .checks import (
    CUT_FLAG,
    CUT_WHAT,
    DANGLING_FLAG,
    ECHO_FLAG,
    _hits,
    check_drafts,
    fix_claims,
    fix_echoes,
    revise_once,
)
from .drafting import _draft, fitting_names, paragraph_scope, spelt, story_ranking
from .hiring import hiring_contacts
from .reading import COMMON_QUESTIONS, SOMEONE, Brief, _job_text, _requirements, evidence_plan, field_of, prompt

REPEAT_FLAG = "says what an earlier answer already says"   # an answer still repeating after its rewrite


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
    """The request to rewrite each later answer that repeats an earlier one, using other evidence."""
    listed = "\n".join(f"- In the answer to \"{answers[i].get('question', '')}\": {s} (already said in the answer "
                       f"to \"{answers[j].get('question', '')}\")" for i, s, j in found)
    return (f"These sentences repeat what an earlier answer already says:\n{listed}\nRewrite only those later "
            "answers so each one uses a different piece of evidence from the applicant's facts (another project, "
            "job, module or result). Keep the other answers as they are.")


def fix_repeats(answers: list[dict], rewrite):
    """The answers rewritten once by `rewrite(note)` when a later one repeats an earlier one; returns (answers,
    whether they were rewritten). One rewrite, never a loop: what still repeats is listed by the caller."""
    found = repeated_answers(answers)
    if not found:
        return answers, False
    again = rewrite(repeat_note(answers, found))
    return (again or answers), bool(again)


def _answers(brief: Brief, request: str, *, sources: tuple[str, ...],
             names: tuple[str, ...] = ()) -> tuple[list[dict], list[str], list[str]]:
    """Answers to the questions `request` lists, revised once together when one breaks a high-severity rule,
    once when a "why here" answer restates the advert (`echoed_answers`) and once more when they make claims
    the facts do not support, after once more when a later answer repeats an earlier one (`fix_repeats`);
    returns (answers, the claims still unsupported, the sentences still restating the advert)."""
    who = brief.who

    def ask(text: str) -> list[dict]:
        found = ai.chat_json(prompt("answers", who), text, role="writer", timeout=ai.LONG_TIMEOUT_S,
                            purpose="answers").get("answers", [])
        return [{**a, "answer": spelt(ai.undash(a.get("answer", "")), who)}
                for a in found if isinstance(a, dict)]

    def again(note: str) -> list[dict]:
        return ask(f"{request}\n\nYour answers:\n{_answers_text(answers)}\n\n{note}")

    answers = ask(request)
    hits = [h for a in answers for h in _hits(brief, a["answer"], "answer", sources)]
    answers = revise_once(answers, hits,
                          lambda note: ask(f"{request}\n\nYour first answers:\n{_answers_text(answers)}\n\n{note}"))[0]
    answers, _ = fix_echoes(answers, lambda found: echoed_answers(found, sources, names), again)
    answers, _ = fix_repeats(answers, again)
    answers, claims = fix_claims(brief, answers, again, employer=sources, as_text=_answers_text)[:2]
    return answers, claims, echoed_answers(answers, sources, names)


def _message(who: dict, context: str, trimmed: set[str]) -> str:
    """The body of the message to the person hiring, cleaned of the model's notes and of a greeting or an
    introduction it wrote, which the opening says (`writing.opening.body_of`)."""
    draft = _draft(who, "message", context, trimmed)
    body = opening.body_of(draft)
    if draft in trimmed:
        trimmed.add(body)
    return body


def message_opening(brief: Brief) -> opening.Opening:
    """The message's greeting and first sentence (`writing.opening`): the person's name and stage, the role, and
    the advert's first hiring contact (`hiring_contacts`)."""
    contacts = hiring_contacts(brief.analysis, _job_text(brief.job))
    name = str(brief.who.get("name") or "")
    return opening.Opening.of("" if name == SOMEONE else name, stage.current(), brief.job["title"],
                              contacts[0]["name"] if contacts else "")


def answers_request(brief: Brief, questions: list[str], context: str) -> str:
    """The request for answers to `questions`: each question, the shared context and the field's conventions."""
    return ("Questions:\n" + "\n".join(f"- {q}" for q in questions)
            + f"\n\n{context}\n\n{fields.block(field_of(brief.job, brief.who, brief.analysis), 'answer')}")


NO_ANSWER = 'Hal drafted no answer to "{question}": your facts gave nothing it could check. Answer it yourself'


def _story_of(item: planning.Item, found: list[planning.Story]) -> planning.Story | None:
    """The story a plan item belongs to: its card's, else the one holding its quote."""
    return next((s for s in found if (item.card and s.name == item.card) or item.text in (*s.facts, *s.planned)),
                None)


STORY_TRIES = 3            # stories one answer or the message tries before the whole-draft path writes it


def _fitting(left: list[planning.Story], ranking: list[rank.Ranked]) -> list[planning.Story]:
    """The stories of `left` the ranking fits to the job (`rank.fits`), or all of `left` with no ranking or when it
    fits none of them: a "why here" or "why fit" answer never offers work the ranking puts in its bottom half,
    as graders saw a device-tracking sniffer offered to an ML employer (2026-11-08)."""
    return [s for s in left if rank.fits(s.name, ranking)] or left


def _why_story(left: list[planning.Story], used: dict[str, int],
               pairing: motive.Pairing | None, ranking: list[rank.Ranked] = ()) -> planning.Story:
    """The story a "why here" answer ties its reason to, among those the ranking fits to the job (`_fitting`):
    the pairing's item's story while the pack has told it no more than any other (`used`), else the one told
    least (`plan.choose`); counted in `used` either way. A project the letter told was told again in the "why
    here" answer while other work was left untold (2026-11-08)."""
    left = _fitting(left, list(ranking))
    own = _story_of(pairing.item, left) if pairing else None
    if own is None or used.get(own.key, 0) > min(used.get(s.key, 0) for s in left):
        return planning.choose(left, 1, used)[0]
    used[own.key] = used.get(own.key, 0) + 1
    return own


def _story_answer(question: str, found: list[planning.Story], used: dict[str, int], scope: paragraphs.Scope,
                  pairing: motive.Pairing | None, ranking: list[rank.Ranked] = ()) -> list[paragraphs.Paragraph]:
    """The paragraphs answering one form question: a "why here" question from the motivation `pairing` and the
    story `_why_story` gives, any other from the stories the pack has told least (`plan.choose`), one or two
    (`plan.examples_wanted`). When every paragraph is dropped the next story is tried, up to `STORY_TRIES`
    stories; all the paragraphs tried are returned, the ones that stand with text."""
    out: list[paragraphs.Paragraph] = []
    left = list(found)
    for _ in range(STORY_TRIES):
        if not left:
            break
        if drafts.why_question(question):
            story = _why_story(left, used, pairing, ranking)
            tied = pairing is not None and story is _story_of(pairing.item, [story])
            said = [paragraphs.write_paragraph(story, paragraphs.why(question, pairing, tied), scope)]
        else:
            pool = _fitting(left, list(ranking)) if drafts.fit_question(question) else left
            chosen = planning.choose(pool, planning.examples_wanted(question), used)
            said = [paragraphs.write_paragraph(s, paragraphs.answer(question, k, len(chosen)), scope)
                    for k, s in enumerate(chosen)]
        out += said
        if any(p.text for p in said):
            break
        left = [s for s in left if s not in {p.story for p in said}]
    return out


def _story_message(found: list[planning.Story], scope: paragraphs.Scope, after: str, used: dict[str, int],
                   ranking: list[rank.Ranked] = ()) -> list[paragraphs.Paragraph]:
    """The message's body after the opening sentence `after`: one story in `paragraphs.message`'s shape, which
    lets its question touch the work's area when the `ranking` fits the work to the job (`rank.fits`), the story
    the pack has told least first (`used`, counted up for the one that stands), the next one tried when it is
    dropped, up to `STORY_TRIES`."""
    out: list[paragraphs.Paragraph] = []
    for story in sorted(found, key=lambda s: used.get(s.key, 0))[:STORY_TRIES]:   # stable: best first at a tie
        shape = paragraphs.message(after, rank.fits(story.name, list(ranking)))
        out.append(paragraphs.write_paragraph(story, shape, scope))
        if out[-1].text:
            used[story.key] = used.get(story.key, 0) + 1
            break
    return out


def _story_others(brief: Brief, scope: paragraphs.Scope, *,
                  plan: list[planning.Item], questions: list[str], pairing: motive.Pairing | None, paired: bool,
                  told: tuple[str, ...], ordered: list[planning.Story], whole_message=None,
                  whole_answers=None, ranking: list[rank.Ranked] = ()) -> dict:
    """The message and the answers told one piece of work a paragraph (`writing.paragraphs`): the message is its
    built opening and one story's few lines and question; each answer one story, or two when its question asks
    for more than one example. Each prefers work the letter (`told`: its paragraphs' stories),
    the message and earlier answers have not told, in the order of `ordered` (`rank.Told.others`: the ranking's,
    else the plan's stories and the other cards the job fits). The message's question may touch its work's
    area only when the `ranking` fits that work to the job (`rank.fits`).

    Nothing is left blank (2026-11-08: 8 of 15 graded packs had an empty answer or a message with nothing after
    its introduction, as a dropped paragraph left nothing): a dropped paragraph's next story is tried
    (`_story_answer`, `_story_message`), and what is still empty is written by the whole-draft path,
    `whole_message()` for the message and `whole_answers(questions)` ({question: answer}) for the answers.

    A paragraph kept although the AI check doubted it is listed for the person to check (`Paragraph.checks`), as
    in a letter: a message kept a crossed timing claim while the pack said "Nothing to check" (2026-11-08)."""
    you, cards, calls = eligibility.person(), list(scope.cards), 0
    found = list(ordered)
    used = dict.fromkeys(told, 1)
    first = message_opening(brief)
    tried = _story_message(found, scope, first.sentence, used, ranking)
    body = next((p for p in tried if p.text), tried[-1] if tried else paragraphs.Paragraph(found[0]))
    calls += sum(p.calls for p in tried)
    flags = [body.listed] if body.dropped else list(body.checks)
    if not body.text and whole_message is not None:
        body.text, flags = whole_message(), []
        calls += 1
    if not paired and any(drafts.why_question(q) for q in questions):
        pairing = motive.make((brief.job["title"], brief.job["company"]), scope.advert, scope.brief, plan,
                              fitting_names(list(ranking)))
        calls += 1
    answers = []
    for question in questions:
        said = _story_answer(question, found, used, scope, pairing, ranking)
        calls += sum(p.calls for p in said)
        text = " ".join(p.text for p in said if p.text)
        flags += [line for p in said if p.text for line in p.checks]
        answers.append({"question": question, "answer": text, "dropped": [p.listed for p in said if p.dropped]})
    blank = [a["question"] for a in answers if not a["answer"]]
    if blank and whole_answers is not None:
        written = whole_answers(blank)
        calls += 1
        for a in answers:
            a["answer"] = a["answer"] or written.get(a["question"], "")
    for a in answers:
        if not a["answer"]:
            flags += [*a["dropped"], NO_ANSWER.format(question=a["question"])]
        del a["dropped"]
        a["answer"] = plain.tidy(relocation.without_own_move(a["answer"], you), cards)
    message = plain.tidy(relocation.without_own_move(body.text, you), cards)
    written = "\n\n".join([message, *(a["answer"] for a in answers)])
    flags = check_drafts(brief, written, sources=(scope.advert, scope.brief, scope.allowed), kind="answer",
                         claims=[]) + flags
    return {"message": first.put(message), "answers": answers, "flags": flags, "calls": calls}


def write_others(who: dict, job, analysis: dict, about: str, *, message_request: str, answers_request: str,
                 facts: str, kept: str, plan: list[planning.Item] | None = None,
                 questions: list[str] = (), request: str = "", pairing: motive.Pairing | None = None,
                 paired: bool = False, told: tuple[str, ...] = (), ranking: list[rank.Ranked] | None = None) -> dict:
    """The message to the person hiring and the form answers, written from their requests and checked as a
    pack checks them: {message, answers, flags}. `answers_request` "" writes no answers.

    When the evidence `plan` or a starred card tells any piece of work (`plan.stories`, `plan.with_showcase`), they are
    told one piece of work a paragraph (`_story_others`): the answers to `questions`, the person's own words (`request`)
    in every request, a "why here" answer from the letter's motivation `pairing` (`paired` when the letter asked for
    one, which may be None; else one is asked for here), the work the letter told (`told`) left to the answers last, and
    the rest in the order of the letter's `ranking` (one AI call made here unless given, `story_ranking`). Otherwise the
    message is written after its opening (`message_opening`: the greeting and who is writing, with no AI), asked for
    again when it stops mid-sentence and rewritten once without claims the facts do not support (project contexts and
    misplaced tools included); a greeting or introduction the writer adds is taken out (`writing.opening.body_of`), and
    the message returned starts with the opening. The answers are revised for the writing rules, for restating the
    advert and for claims (`_answers`). The pack and a change the person asks for (`docs.change`) both write them here,
    so neither skips a check. The `plan` is the pack's, else one picked here for the advert's and the common questions.
    """
    job_text = _job_text(job)
    their_words, trimmed = (job_text, about), set()
    plan = plan if plan is not None else evidence_plan(analysis, facts, kept, tuple(COMMON_QUESTIONS))
    brief = Brief(job, who, analysis, about, facts, kept)
    cards = profile_writing.cards()
    found = planning.with_showcase(planning.stories(plan, cards), cards)
    if found:
        scope = paragraph_scope(brief, found=found, cards=cards, request=request, kind="answer")
        def whole_message() -> str:
            return _message(who, f"{message_request}\n\n{message_opening(brief).note}", set())

        def whole_answers(blank: list[str]) -> dict[str, str]:
            rest = answers_request.split("\n\n", 1)[1] if "\n\n" in answers_request else ""
            asked = "Questions:\n" + "\n".join(f"- {q}" for q in blank) + (f"\n\n{rest}" if rest else "")
            found, _, _ = _answers(brief, asked, sources=their_words, names=(job["title"], job["company"]))
            said = [str(a.get("answer") or "") for a in found]
            by_question = {str(a.get("question") or ""): s for a, s in zip(found, said, strict=True)}
            return {q: by_question.get(q) or (said[k] if len(said) == len(blank) else "") for k, q in enumerate(blank)}

        asked = ranking is None
        ranking = story_ranking(brief, found, cards) if asked else ranking
        ordered = rank.order(found, cards, ranking, "\n".join(_requirements(analysis))).others
        written = _story_others(brief, scope, plan=plan,
                                questions=list(questions) if answers_request else [], pairing=pairing,
                                paired=paired, told=told, ordered=list(ordered), whole_message=whole_message,
                                whole_answers=whole_answers, ranking=ranking)
        return {**written, "calls": written["calls"] + asked}
    first = message_opening(brief)
    message_request += f"\n\n{first.note}"

    def rewrite_message(note: str) -> str:
        return _message(who, f"{message_request}\n\nYour draft:\n{message}\n\n{note}", trimmed)

    message = _message(who, message_request, trimmed)
    message, message_claims, _ = fix_claims(brief, message, rewrite_message, employer=their_words)
    cut = message in trimmed
    answers, answer_claims, answer_echoes = _answers(
        brief, answers_request, sources=their_words,
        names=(job["title"], job["company"])) if answers_request else ([], [], [])
    you = eligibility.person()
    message = plain.tidy(relocation.without_own_move(message, you), cards)
    answers = [{**a, "answer": plain.tidy(relocation.without_own_move(str(a.get("answer") or ""), you), cards)}
               for a in answers]
    written = "\n\n".join([message, *(a.get("answer", "") for a in answers)])
    flags = check_drafts(brief, written, sources=their_words, kind="answer", claims=message_claims + answer_claims)
    flags += [f"{ECHO_FLAG}: {s}" for s in answer_echoes]
    flags += [f"{REPEAT_FLAG}: {s}" for _, s, _ in repeated_answers(answers)]
    flags += [f"{DANGLING_FLAG}: {s}" for s in flow.dangling(message)]
    if cut:
        flags.append(CUT_FLAG.format(what=CUT_WHAT["message"]))
    return {"message": first.put(message), "answers": answers, "flags": flags}
