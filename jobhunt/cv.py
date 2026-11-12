"""The person's main CV in the cv.json shape that the PDF builder, the packs and the scorer read.

Hal keeps the CV in the CV store (`cvstudio/store.py`): numbered versions that never change. `load()`
reads the version in use in the older cv.json shape, so the modules that draw or score a CV need no store
of their own. The store's first version is imported once from the importer's copy of the website CV and
its old overrides (`cvstudio/import_v1.py`), or made from the settings by `starter()`. A variant for one
job reorders projects and skill groups by relevance to the advert. The PDF is drawn by cvpdf.py.
"""
import copy
import json
import re
from pathlib import Path

# Title lines per kind of role, chosen by the advert's words. None are built in, so the CV's own title
# stands; the CV types choose titles instead.
TRACKS: dict[str, tuple[str, str]] = {}
LINKS = (("linkedin", "you.links.linkedin"), ("site", "you.links.website"), ("github", "you.links.github"))

# Entries are matched across versions by what names them, not by position, so the site can add or reorder them.
NAMED_BY = {"education": "qualification", "projects": "name", "experience": "role"}
PAIRS = ("skills", "additional")  # entries written as [name, value]; only the value is edited


def _you(key: str) -> str:
    from .core import settings

    return str(settings.get(key) or "").strip()


def contact() -> dict:
    """The contact block a CV prints under the name: location, and only the profile links the person gave."""
    out = {"location": _you("you.location_line") or _you("you.city")}
    for key, setting in LINKS:
        value = re.sub(r"^https?://(www\.)?", "", _you(setting)).rstrip("/")
        if value:
            out[key] = value
    return out


def starter() -> dict:
    """A short CV made from the settings, for someone who has not added or built a CV yet."""
    degree = _you("you.education.degree")
    meta = " | ".join(x for x in (_you("you.education.university"), _you("goal.study_end")) if x)
    education = [{"qualification": degree, "grade": _you("you.education.grade"), "meta": meta or degree,
                  "detail": ""}] if degree else []
    return {"title": degree or "Graduate", "contact": contact(), "summary": "", "education": education,
            "skills": [], "projects": [], "experience": [], "additional": [], "moreProjects": "", "closing": ""}


def profile() -> dict:
    """The profile parts a CV prints that are not in its body: the contact block from the settings."""
    return {"contact": contact()}


def printable(data: dict) -> dict:
    """The CV as the PDF builder takes it: entries left out are dropped, and the editor's bookkeeping removed."""
    from .cvstudio import import_v1

    return import_v1.printable(data)


def load() -> dict:
    """Hal's base CV in cv.json shape: the version in use, entries left out dropped."""
    from .cvstudio import model, store

    data = model.to_v1(store.current()[1], profile())
    for section in NAMED_BY:
        for item in data.get(section) or []:
            item["_key"] = item.get(NAMED_BY[section]) or ""
    return printable(data)


def _find(data: dict, section: str, key):
    items = data.get(section) or []
    if section in PAIRS:
        return next((x for x in items if x[0] == key), None)
    return next((x for x in items if x.get(NAMED_BY[section]) == key), None)


def _name(section: str, item) -> str:
    """What names an entry across versions of a CV: its original name where the editor kept one."""
    if section in PAIRS:
        return item[0]
    return item.get("_key") or item.get(NAMED_BY[section]) or ""


def _shown(items: list) -> list:
    return [x for x in items or [] if not (isinstance(x, dict) and x.get("hidden"))]


def changes(base: dict, variant: dict) -> list[dict]:
    """Lists where an application's CV differs from the base CV, for skimming before approving it.

    Each change names the field by its path in `variant` ("projects.0.body", or a section for
    an order change), has an `id` unique in the list (the path, except "<section>:order" and
    "<section>:left out:<name>", which share a section's path), says what kind of change it is ("text",
    "order", "added", "left out"),
    and carries a short label, the base's wording (`before`) and the application's (`after`), so the
    person sees what Hal changed rather than only that it changed.
    """
    out = []
    for f in ("title", "summary", "moreProjects", "closing"):
        if (variant.get(f) or "") != (base.get(f) or ""):
            out.append({"path": f, "kind": "text", "label": {"moreProjects": "Further projects"}.get(f, f.capitalize()),
                        "before": _plain(base.get(f)), "after": _plain(variant.get(f))})
    for section in (*NAMED_BY, *PAIRS):
        mine = variant.get(section) or []
        theirs = _shown(base.get(section))
        before = [_name(section, x) for x in theirs]
        shown = [_name(section, x) for x in _shown(mine)]
        common = [n for n in shown if n in before]
        if common != [n for n in before if n in shown]:
            moved = [f"{n} (was {before.index(n) + 1}, now {shown.index(n) + 1})" for n in common
                     if before.index(n) != shown.index(n)]
            out.append({"path": section, "id": f"{section}:order", "kind": "order", "label": f"Order of {section}",
                        "before": ", ".join(moved[:6]), "after": ", ".join(common[:6])})
        for name in before:
            if name not in shown:
                out.append({"path": section, "id": f"{section}:left out:{name}", "kind": "left out",
                            "label": f"{name} ({section})", "before": name,
                            "after": ""})
        for i, item in enumerate(mine):
            name = _name(section, item)
            if isinstance(item, dict) and item.get("hidden"):
                continue
            was = _find(base, section, name) if section in PAIRS else next(
                (x for x in theirs if _name(section, x) == name), None)
            if was is None:
                out.append({"path": f"{section}.{i}", "kind": "added", "label": f"{name} ({section})", "before": "",
                            "after": name})
                continue
            if section in PAIRS:
                if item[1] != was[1]:
                    out.append({"path": f"{section}.{i}.1", "kind": "text", "label": name,
                                "before": _plain(was[1], ", "), "after": _plain(item[1], ", ")})
                continue
            for field, value in item.items():
                if field in ("_key", "hidden", "modules") or value == was.get(field):
                    continue
                out.append({"path": f"{section}.{i}.{field}", "kind": "text", "label": f"{name}: {field}",
                            "before": _plain(was.get(field)), "after": _plain(value)})
    for change in out:
        change.setdefault("id", change["path"])
    return out


def revert(base: dict, variant: dict, undone) -> dict:
    """A copy of `variant` with the changes whose ids are in `undone` put back as `base` has them.

    This is what Undo on the application page sends: the change stays listed (from the variant), and the
    CV printed and uploaded holds the base's wording, entry or order instead.
    """
    undone = set(undone or [])
    out = copy.deepcopy(variant)
    drop: dict[str, set[int]] = {}
    restore: dict[str, list] = {}
    reorder: set[str] = set()
    for c in changes(base, variant):
        if c["id"] not in undone:
            continue
        section, *rest = c["path"].split(".")
        if c["kind"] == "order":
            reorder.add(section)
        elif c["kind"] == "left out":
            was = next((x for x in _shown(base.get(section)) if _name(section, x) == c["before"]), None)
            if was is not None:
                restore.setdefault(section, []).append(copy.deepcopy(was))
        elif c["kind"] == "added":
            drop.setdefault(section, set()).add(int(rest[0]))
        elif not rest:
            out[section] = copy.deepcopy(base.get(section))
        else:
            item = out[section][int(rest[0])]
            name = _name(section, item)
            was = _find(base, section, name) if section in PAIRS else next(
                (x for x in _shown(base.get(section)) if _name(section, x) == name), None)
            if was is None:
                continue
            if section in PAIRS:
                out[section][int(rest[0])] = [item[0], copy.deepcopy(was[1])]
            elif rest[1] in was:
                item[rest[1]] = copy.deepcopy(was[rest[1]])
            else:
                item.pop(rest[1], None)
    for section, gone in drop.items():
        out[section] = [x for i, x in enumerate(out[section]) if i not in gone]
    for section, items in restore.items():
        out[section] = [*(out.get(section) or []), *items]
    for section in reorder:
        order = [_name(section, x) for x in _shown(base.get(section))]
        out[section] = sorted(out.get(section) or [], key=lambda x: order.index(_name(section, x))
                              if _name(section, x) in order else len(order))
    return out


def _plain(value, sep: str = "\n") -> str:
    """A CV field as text to show: a list of lines joined, anything else as written."""
    if isinstance(value, list):
        return sep.join(_plain(v, sep) for v in value)
    return "" if value is None else str(value)


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9+#.]{1,}", text.lower()) if len(w) > 2}


def track_for(job_text: str) -> str | None:
    """Returns the track whose keywords appear most often in the job text."""
    counts = {name: len(re.findall(pattern, job_text, re.I)) for name, (_, pattern) in TRACKS.items()}
    if not counts:
        return None
    best = max(counts, key=counts.get)
    return best if counts[best] >= 2 else None


def tailor(job_text: str) -> tuple[dict, str | None]:
    """Returns a cv.json variant ordered for this job, and the track chosen."""
    data = copy.deepcopy(load())
    wanted = _words(job_text)

    def relevance(text: str) -> int:
        return len(_words(text) & wanted)

    data.setdefault("projects", []).sort(key=lambda p: -relevance(f"{p['name']} {p['stack']} {p['body']}"))
    data.setdefault("skills", []).sort(key=lambda group: -relevance(" ".join([group[0], *group[1]])))
    track = track_for(job_text)
    if track:
        data["title"] = TRACKS[track][0]
    return data, track


def render(variant: dict, out_dir: Path) -> Path:
    """Writes the variant's JSON and its PDF, in Hal's layout for applications, into `out_dir`."""
    from . import cvpdf
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cv.json").write_text(json.dumps(printable(variant), indent=2) + "\n")
    return cvpdf.render(variant, out_dir)


def facts_text() -> str:
    """Returns the CV as plain text, the only facts a draft may use."""
    return facts_text_of(load())


# The right to work in plain words, for drafts and forms alike. A setting's own label, or how a form should word
# it (`you.work_status`), reached the letter as it stood: "as a British or Irish citizen" for a person who is a
# UK and Irish citizen (2026-11-07), so drafts get the person's plain values instead.
WORK_WORDS = {"uk_no_visa": "I have the right to work in the UK", "uk_visa": "I have a UK visa",
              "need_sponsorship": "I need a visa sponsored to work in the UK"}


def status_line() -> str:
    """When the course ends or ended, the right to work in plain words (`WORK_WORDS`) and the nationality the
    person gave, as the settings say them; "" when unsaid."""
    parts = []
    if _you("goal.study_end"):
        parts.append(f"Course ends or ended: {_you('goal.study_end')}.")
    work = WORK_WORDS.get(_you("you.right_to_work"), "")
    if work:
        parts.append(f"Right to work: {work}.")
    if _you("you.nationality"):
        parts.append(f"Nationality: {_you('you.nationality')}.")
    return " ".join(parts)


def facts_text_of(d: dict) -> str:
    """Returns a CV as plain text, with the status line from the settings."""
    parts = [d.get("title", ""), d.get("summary", "")]
    for e in d.get("education") or []:
        parts += [e.get("qualification", ""), e.get("grade", ""), e.get("meta", ""), e.get("detail", "")]
        for year in e.get("modules") or []:
            marks = ", ".join(f"{name} {mark}" for name, mark in year.get("rows", []))
            parts.append(f"{year.get('year')} modules (overall {year.get('mark')}): {marks}")
    parts += [f"{g[0]}: {', '.join(g[1])}" for g in d.get("skills") or []]
    parts += [f"Project, {p['name']} ({p.get('stack', '')}): {p['body']}" for p in d.get("projects") or []]
    parts.append(d.get("moreProjects", ""))
    parts += [f"{x['role']}, {x['org']} ({x['dates']}): {x['body']}" for x in d.get("experience") or []]
    parts += [f"{a[0]}: {a[1]}" for a in d.get("additional") or []]
    parts.append(status_line())
    text = "\n".join(p for p in parts if p)
    return re.sub(r"\*\*", "", text)
