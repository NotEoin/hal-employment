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
  holds, the letter says less rather than invent one (`writing.frame`).
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

from jobhunt import llm
from jobhunt.ai import prompts
from jobhunt.writing import check, drafts
from jobhunt.writing.plan import Item, quoted

log = logging.getLogger(__name__)

PROMPT = "motive_pair"
MIN_SPECIFIC = 2           # own words of an employer's fact beyond the generic: "flood sensors", "river data"
# Words any employer uses of itself or of what it offers, so they make no reason to choose this one.
GENERIC = frozenset("""innovation innovative innovate excellence excellent values value culture growth grow growing
passion passionate diverse diversity inclusion inclusive equality people customer customers client clients quality
committed commitment leading leader leaders world class impact mission vision success successful opportunity
opportunities team teams collaborative collaboration dynamic exciting fast paced environment company organisation
organization business industry sector best great support supportive supporting development develop learning learn
career careers progression training benefits benefit community friendly fun reward rewarding work working workplace
employer staff colleagues future purpose driven forward thinking cutting edge award winning global
national local new modern brand role roles join offer offers offering full""".split())
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


def checked(reply: dict, advert: str, brief: str, plan: list[Item], names: tuple[str, ...] = ()) -> Pairing | None:
    """The pairing of a reply when it holds (see the module notes), else None."""
    if not isinstance(reply, dict):
        return None
    said = str(reply.get("employer_fact") or "")
    fact = quoted(said, advert) or quoted(said, brief)
    item = next((i for i in plan if i.id == str(reply.get("item") or "").strip().upper()), None)
    reason = " ".join(str(reply.get("reason") or "").split())
    if not (fact and item and reason and specific(fact, names)):
        if said:
            log.info("motivation: dropped a pairing that does not hold: %r", reply)
        return None
    return Pairing(fact, item, reason)


def make(names: tuple[str, ...], advert: str, brief: str, plan: list[Item]) -> Pairing | None:
    """The pairing for one letter: one AI call, then `checked`; None when the plan is empty or nothing holds.
    `names` are the job title and the employer's name."""
    if not plan:
        return None
    title, company = (*names, "", "")[:2]
    request = (f"Job: {title} at {company}\n\nAdvert:\n{advert}\n\nCompany brief:\n{brief or 'none'}\n\n"
               "Evidence plan:\n" + "\n".join(i.words() for i in plan))
    return checked(llm.chat_json(prompts.text(PROMPT), request, role="main"), advert, brief, plan, names)


def block(pairing: Pairing | None) -> str:
    """What the letter request says about the reason for this employer."""
    if pairing is None:
        return NONE_SAID
    return (f"Motivation pairing (write why this employer from this alone): the employer's \"{pairing.fact}\" "
            f"and the applicant's \"{pairing.item.text}\", because {pairing.reason}")
