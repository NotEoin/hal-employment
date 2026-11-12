"""The reason for this employer: one specific fact about the employer paired with one item of the person's.

`make(job, advert, brief, plan)` asks the AI once (prompt `motive_pair`) for a fact of the advert or the
company brief, an item of the evidence plan (`writing.plan`) and a one-line reason that ties them; `checked`
keeps the pairing only when it holds, and `block(pairing)` is what the letter request says: the pairing to
write the reason from, or that there is none and the letter gives no reason beyond the role.

Design decisions, each with its reason:

- The pairing is chosen before the letter, so its reason for the employer is one thing of the person's tied
  to one thing of the employer's: letters by a local model gave generic motivation that echoed the advert
  in every graded run (2026-11-07), even after the echo check rewrote them.
- A fact is kept only when the advert or the brief holds it word for word (spaces and case aside,
  `plan.quoted`), and it is specific: at least `MIN_SPECIFIC` of its own words are not words any employer
  uses of itself (`GENERIC`, the little words, the job title and the employer's name). "a culture of
  innovation and excellence" fits every employer, so it is no reason.
- The item must be one of the plan's, so the person's side is a fact they checked; with no pairing that
  holds, the letter says less rather than invent one (`writing.frame`). Only items tied to a project or job
  card are offered while the plan has any (`offered`), so the reason names a piece of work: reasons such as
  "because I have built features for a team project" named none (2026-11-08).
- The fact is about the employer, never a duty of the job (`duty`): openings led in with the advert's
  requirements ("Your requirement to write and improve SQL queries against Postgres ...", 2026-11-08), and a
  duty says what the job holder will do, not what the employer is. A fact that reads as a duty or a requirement
  ("write and improve ...", "experience of ...", "you will ...") is no pairing. The letter's opening says the
  fact alone and names no project (`writing.frame`); the item is for the "why here" answers, which may tie
  the reason to that one piece of work.
- The reason is the writer's own observation of the employer, never the advert given back (2026-11-08): all
  15 graded "why here" answers and several openings restated the advert ("Your focus on ownership and
  working with stakeholders ... is a clear priority for me"), paraphrased an employee's testimonial, or gave
  no reason. So a fact the brief holds is taken from the brief before the advert, and the request gives the
  brief first, as the brief says what the company makes, for whom, and its product or mission. A fact in the
  first person or from a quoted line (`testimonial`: an employee's words, not the employer's) or one that
  lists the advert's requirements (`requirements`: a requirement word, or `LIST_ITEMS` items from the
  advert) is no pairing, nor is one opening "Your ...", which speaks to the job holder. A sentence giving
  the reason (the opening's, a "why here" answer's first) fails when it restates the advert (`restated`):
  it shares a run of `RUN_WORDS` words with it, or more than `SENTENCE_SHARE` of its own words come from one
  advert sentence; it is drafted again once.
- Only work the ranking fits to the job may carry the reason (`make`'s `fitting`, `writing.rank.fits`):
  graders saw a device-tracking sniffer offered to an ML employer, and a coursework expense tracker given
  as the fit (2026-11-08).
- A "why here" answer leads with the employer (`addresses`, 2026-11-08): four graded answers said only
  "because I built <project>". Its first sentence must say "you" or "your", name the employer, or name its
  product with a specific word of the pairing's fact (`STEM` letters of it, as "sensors" and "sensor" are one).
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from jobhunt import llm
from jobhunt.ai import prompts
from jobhunt.writing import check, drafts
from jobhunt.writing.plan import Item, quoted

log = logging.getLogger(__name__)

PROMPT = "motive_pair"
MIN_SPECIFIC = 2           # own words of an employer's fact beyond the generic: "flood sensors", "river data"
# The first words of a line of what the job holder will do or must have: a duty or a requirement, not a fact
# about the employer (see the module notes).
DUTY = re.compile(r"^\W*(?:you(?:r|'ll| will| would| should| must| have| need)?|we(?:'re| are) (?:looking|seeking)|the "
                  r"(?:successful )?(?:candidate|applicant|role holder)|candidates?|applicants?|must|should|"
                  r"experience|knowledge|ability|proficiency|familiarity|understanding|strong|excellent|"
                  r"essential|desirable|responsible for|requirements?|write|build|develop|design|maintain|"
                  r"support|help|work|create|deliver|manage|assist|analy[sz]e|test|implement|improve|collaborate|"
                  r"contribute|answer|use|own|run|lead)\b", re.I)
# Words any employer uses of itself or of what it offers, so they make no reason to choose this one.
GENERIC = frozenset("""innovation innovative innovate excellence excellent values value culture growth grow growing
passion passionate diverse diversity inclusion inclusive equality people customer customers client clients quality
committed commitment leading leader leaders world class impact mission vision success successful opportunity
opportunities team teams collaborative collaboration dynamic exciting fast paced environment company organisation
organization business industry sector best great support supportive supporting development develop learning learn
career careers progression training benefits benefit community friendly fun reward rewarding work working workplace
employer staff colleagues future purpose driven forward thinking cutting edge award winning global
national local new modern brand role roles join offer offers offering full""".split())
YOU = re.compile(r"\b(?:you|your|yours)\b", re.I)
# One person speaking: an employee's testimonial, never the employer's own fact ("I joined in 2023 and ...").
FIRST_PERSON = re.compile(r"\b(?:I|I'm|I've|I'd|me|my|mine)\b")
QUOTE_MARKS = "\"\u201c\u201d"
# Words of a requirement of the job holder: a fact holding one lists what the advert asks, not what the employer is.
REQUIRES = re.compile(r"\b(?:experience (?:of|in|with)|skills?|requirements?|knowledge of|ability to|proficien\w*|"
                      r"qualifications?|ownership|stakeholders?|degree in|familiarity)\b", re.I)
LIST_ITEMS = 3             # items a fact of the advert lists, by commas, "and" or "or", to read as a list of asks
RUN_WORDS = 5              # words in a row a reason shares with the advert to restate it (2026-11-08)
SENTENCE_SHARE = 0.6       # share of a reason's own words one advert sentence may hold before it restates it
RESTATED_MIN = 3           # own words a reason needs before its share is weighed: "I want this role" is no echo
RESTATED = ('restates the advert ("{said}"); say in your own words what you see in the '
            "employer's product or mission")

STEM = 5                   # letters two words share to be one word ("sensors" and "sensor", "triage" and "triaging")
NONE_SAID = ("Motivation: no specific reason to choose this employer was found in the applicant's facts. Name the "
             "role and give no reason beyond it; never invent one.")


@dataclass(frozen=True)
class Pairing:
    """One fact about the employer, the plan item it ties to, and why."""

    fact: str                  # the employer's own words, from the advert or the brief
    item: Item
    reason: str


def specific(fact: str, names: tuple[str, ...] = ()) -> bool:
    """True when an employer's fact has at least `MIN_SPECIFIC` words of its own that no employer says of itself."""
    named = {w for n in names for w in check.words_of(n)}
    own = {w for w in check.words_of(fact) if w not in GENERIC and w not in drafts.LITTLE_WORDS and w not in named}
    return len(own) >= MIN_SPECIFIC


def duty(fact: str) -> bool:
    """True when an employer's fact reads as a duty of the job or a requirement (`DUTY`), not a fact about the
    employer."""
    return bool(DUTY.match(fact))


def testimonial(fact: str, source: str = "") -> bool:
    """True when a fact is one person's words: in the first person, or from a line of `source` in quote marks."""
    if FIRST_PERSON.search(fact):
        return True
    line = next((ln for ln in source.splitlines() if fact.lower() in ln.lower()), "")
    return any(q in line for q in QUOTE_MARKS)


def requirements(fact: str, from_advert: bool = True) -> bool:
    """True when a fact lists what the job asks: a requirement word (`REQUIRES`), or, from the advert, at least
    `LIST_ITEMS` items."""
    items = [p for p in re.split(r",|;|\band\b|\bor\b", fact) if p.strip()]
    return bool(REQUIRES.search(fact)) or (from_advert and len(items) >= LIST_ITEMS)


def _plain(text: str, names: tuple[str, ...]) -> list[str]:
    """The words of a text in order, lower case, the job title's and the employer's words left out."""
    named = {w for n in names for w in check.words_of(n)}
    return [w for w in check.words_of(text) if w not in named]


def restated(sentence: str, advert: str, names: tuple[str, ...] = ()) -> str:
    """`RESTATED` when a sentence giving the reason for the employer restates the advert (see the module notes),
    else "": a run of `RUN_WORDS` words in common, or more than `SENTENCE_SHARE` of its own words (at least
    `RESTATED_MIN`) in one advert sentence. The title's and the employer's words never count."""
    mine = _plain(sentence, names)
    theirs = _plain(advert, names)
    runs = {tuple(theirs[i:i + RUN_WORDS]) for i in range(len(theirs) - RUN_WORDS + 1)}
    run = next((mine[i:i + RUN_WORDS] for i in range(len(mine) - RUN_WORDS + 1)
                if tuple(mine[i:i + RUN_WORDS]) in runs), None)
    if run:
        return RESTATED.format(said=" ".join(run))
    own = {w for w in mine if w not in drafts.LITTLE_WORDS}
    if len(own) < RESTATED_MIN:
        return ""
    for part in re.split(r"(?<=[.!?;:])\s+|\n+", advert):
        shared = own & set(_plain(part, names))
        if len(shared) > SENTENCE_SHARE * len(own):
            return RESTATED.format(said=" ".join(w for w in mine if w in shared))
    return ""


def _stems(text: str, leave: set[str] = frozenset()) -> set[str]:
    return {w[:STEM] for w in check.words_of(text) if w not in drafts.LITTLE_WORDS and w not in GENERIC
            and w not in leave}


def addresses(sentence: str, employer: str, fact: str = "") -> bool:
    """True when a sentence speaks to or of the employer: "you" or "your", the employer's name, or a specific word
    of the employer's `fact` (its product, its mission), not only of the person's own work."""
    if YOU.search(sentence or "") or (employer.strip() and drafts.names_tool(sentence, employer.strip())):
        return True
    leave = set(check.words_of(employer))
    return bool(_stems(fact, leave) & _stems(sentence, leave))


def checked(reply: dict, advert: str, brief: str, plan: list[Item], names: tuple[str, ...] = ()) -> Pairing | None:
    """The pairing of a reply when it holds (see the module notes), else None."""
    if not isinstance(reply, dict):
        return None
    said = str(reply.get("employer_fact") or "")
    fact = quoted(said, brief) or quoted(said, advert)
    source = brief if quoted(said, brief) else advert
    item = next((i for i in plan if i.id == str(reply.get("item") or "").strip().upper()), None)
    reason = " ".join(str(reply.get("reason") or "").split())
    if not (fact and item and reason and specific(fact, names)) or duty(fact) or testimonial(fact, source) \
            or requirements(fact, source is advert):
        if said:
            log.info("motivation: dropped a pairing that does not hold: %r", reply)
        return None
    return Pairing(fact, item, reason)


def offered(plan: list[Item], fitting: set[str] | None = None) -> list[Item]:
    """The items a pairing may rest on: those tied to a project or job card, of the cards in `fitting` (the
    ranking's top half) when it is given and any are, or every item when none is tied to a card."""
    tied = [i for i in plan if i.card and not i.skills]
    return [i for i in tied if fitting is None or i.card in fitting] or tied or plan


def make(names: tuple[str, ...], advert: str, brief: str, plan: list[Item],
         fitting: set[str] | None = None) -> Pairing | None:
    """The pairing for one letter: one AI call, then `checked`; None when the plan is empty or nothing holds.
    `names` are the job title and the employer's name; `fitting` the names of the work the ranking fits to the
    job (`writing.rank.fits`), None with no ranking."""
    if not plan:
        return None
    title, company = (*names, "", "")[:2]
    items = offered(plan, fitting)
    request = (f"Job: {title} at {company}\n\nCompany brief (look here first):\n{brief or 'none'}\n\n"
               f"Advert:\n{advert}\n\nEvidence plan:\n" + "\n".join(i.words() for i in items))
    reply = llm.chat_json(prompts.text(PROMPT), request, role="main", purpose=PROMPT)
    return checked(reply, advert, brief, items, names)


def block(pairing: Pairing | None) -> str:
    """What the letter request says about the reason for this employer."""
    if pairing is None:
        return NONE_SAID
    return (f"Motivation pairing (write why this employer from this alone): the employer's \"{pairing.fact}\" "
            f"and the applicant's \"{pairing.item.text}\", because {pairing.reason}")
