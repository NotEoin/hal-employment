"""The pack's Markdown file: its sections, the company check added later, the copy for the Hal add-on and
the letter notes read back."""
import re
import textwrap
from datetime import date
from pathlib import Path

from ..company import reputation
from ..core import events
from .hiring import message_to, salutation
from .letter import NOTE_KEYS, NOTES_HEADING
from .reading import Brief, slug

PREPARED = "docs.prepared"    # the event the Hal add-on's Applications mirror listens for
# The pack's last section. It names no command: the Documents page has a button for each.
CHECKLIST = ("Read every draft and correct anything that is not true", "Attach the CV made for this job",
             "Press Submit on the employer's site yourself, then press \"I sent it myself\" if Hal has not seen it",
             "Send the message to the person hiring the same day")
NOTHING_TO_CHECK = "Nothing to check."   # the "Check before sending" section when it lists nothing
CHECK_HEADING = "## Check before sending"


def mirror_note(job, pack_md: Path) -> Path | None:
    """Copies the pack into the Hal add-on's Applications folder under the old note name; None when it is off."""
    from ..addons.hal import mirror

    return mirror.copy(pack_md, f"{slug(job['company'])} - {slug(job['title'])}.md")


def add_company_check(pack_md: Path, company: str, job=None) -> None:
    """Adds or refreshes the company check in an existing pack, and says the pack changed; `job` is the job row
    whose advert ties a lookup's hit to the employer (`reputation.Advert`)."""
    flags, section = reputation.check(company, reputation.advert_of(job))
    text = re.sub(r"## Company check\n.*?(?=\n## )", "", pack_md.read_text(), flags=re.S)
    if flags:
        bullets = "".join(f"- {f}\n" for f in flags)
        text = text.replace(f"{CHECK_HEADING}\n\n{NOTHING_TO_CHECK}\n", f"{CHECK_HEADING}\n\n", 1)
        if f"{CHECK_HEADING}\n\n" in text:
            text = text.replace(f"{CHECK_HEADING}\n\n", f"{CHECK_HEADING}\n\n" + bullets, 1)
        else:
            text = text.replace("\n## Role\n", f"\n{CHECK_HEADING}\n\n{bullets}\n## Role\n", 1)
    text = text.replace("\n## Role\n", "\n" + section + "\n## Role\n", 1)
    pack_md.write_text(text)
    title = text.splitlines()[0].removeprefix("# ")
    company_name, _, role = title.partition(": ")
    events.publish(PREPARED, company=company_name, title=role, pack=str(pack_md))


def read_letter_notes(job) -> dict:
    """The letter's Tone, Length, Evidence and Checked lines as the pack recorded them; {} for older packs."""
    from .. import documents

    notes = documents.read_pack(job).get(NOTES_HEADING, "")
    return {NOTE_KEYS[k]: v.strip() for k, v in re.findall(r"^- (\w+): (.*)$", notes, re.M) if k in NOTE_KEYS}


def _markdown(brief: Brief, urls, fit, letter, message, answers, pdf, track, flagged, notes) -> str:
    """The pack's Markdown: the job, what to check, the role, the company, the fit map and every draft."""
    job, analysis, about, who = brief.job, brief.analysis, brief.about, brief.who
    lines = [f"# {job['company']}: {job['title']}", "",
             f"- Apply: {job['url']}",
             f"- Location: {job['location'] or 'not stated'} | Salary: {job['salary'] or 'not stated'}",
             f"- Tailored CV: `{pdf}`" + (f" (track: {track})" if track else ""),
             f"- Drafted: {date.today().isoformat()} | Job id: `{job['id']}`", ""]
    lines += [CHECK_HEADING, ""] + ([f"- {f}" for f in flagged] or [NOTHING_TO_CHECK]) + [""]
    lines += ["## Role", "", analysis.get("summary", ""), "", "## Company", "", about.strip(), ""]
    lines += [f"- Source: {u}" for u in urls] + ([""] if urls else [])
    lines += ["## Fit", "", "| Requirement | Evidence | Source | Strength |", "| --- | --- | --- | --- |"]
    lines += [f"| {m.get('requirement', '')} | {m.get('evidence', '')} | {m.get('source', '')} | "
              f"{m.get('strength', '')} |" for m in fit.get("matches", [])]
    gaps = fit.get("gaps", [])
    lines += ["", "Gaps to address or accept: " + ("; ".join(gaps) if gaps else "none found"), ""]
    lines += ["## Cover letter", "", salutation(analysis), "", letter, "", f"{who['signoff']},", who["name"], ""]
    lines += [f"## {NOTES_HEADING}", ""] + [f"- {k}: {notes[v]}" for k, v in NOTE_KEYS.items()] + [""]
    lines += ["## Message to the hiring manager", "", "To: " + message_to(job, analysis, who), "", message, ""]
    lines += ["## Form answers", ""]
    for a in answers:
        lines += [f"**{a.get('question', '')}**", "", textwrap.fill(a.get("answer", ""), 100), ""]
    lines += ["## Before submitting", "", *(f"- [ ] {item}" for item in CHECKLIST), ""]
    return "\n".join(lines)
