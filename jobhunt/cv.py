"""The person's main CV as Hal keeps it: where it starts, the person's edits to it, and its variants per job.

Hal keeps its own copy of the CV in the CV store (`cvstudio/store.py`): numbered versions that
never change. This module keeps the old cv.json-shaped functions working over that store for the callers
not converted yet (Classic, pack, cvpdf, cvscore): `load()` and `for_editing()` read the
version in use, and `save_base()` writes a new version. The store's first version is imported once from
the importer's copy of the website CV and the old overrides (`cvstudio/import_v1.py`); Hal never reads or
writes a website folder after that. `site()` and `starter()` still say where a CV would start from, for the
CV builder (cvbuild.py). A variant for one job reorders projects and skill groups by relevance to the
advert. The PDF is drawn by cvpdf.py.
"""
import copy
import json
import re
from pathlib import Path

from .core import paths

# Title lines per kind of role, chosen by the advert's words. None are built in, so the CV's own title
# stands.
TRACKS: dict[str, tuple[str, str]] = {}
SITE_COPY = ("import", "cv", "site-cv.json")   # the importer's one copy of the website CV
LINKS = (("linkedin", "you.links.linkedin"), ("site", "you.links.website"), ("github", "you.links.github"))


def _data() -> Path:
    # The old layout kept these files loose in data/; a moved data folder keeps them in data/cv/.
    return paths.data_dir() if paths.is_legacy_layout() else paths.cv_dir()


# Resolved when used: DATA, the CV folder; and in it BUILT, the CV built from the person's material
# by cvbuild.py, with what to check.
_LIVE = {"DATA": _data, "BUILT": lambda: _live("DATA") / "cv-built.json"}


def __getattr__(name):  # old path constants, read live (PEP 562)
    if name in _LIVE:
        return _LIVE[name]()
    raise AttributeError(name)


def _live(name: str):
    """An old path constant: the value a test assigned to the module, else the live path."""
    return globals()[name] if name in globals() else _LIVE[name]()


# Entries are matched across versions by what names them, not by position, so the site can add or reorder them.
NAMED_BY = {"education": "qualification", "projects": "name", "experience": "role"}
PAIRS = ("skills", "additional")  # entries written as [name, value]; only the value is edited
# Whole fields the applicant may set: text, and for Hal's layout the number of projects drawn in full.
TEXT = ("title", "summary", "moreProjects", "closing", "pdfProjects")
CLASSIC_EDIT = "You edited your CV in the classic screens"
BUILT_CHOSEN = "You chose the CV Hal built from your material"
SITE_CHOSEN = "You went back to the CV you started from"


def site() -> dict:
    """The CV Hal's main CV started from: the importer's copy of the website CV, else the starter CV."""
    copied = paths.data_dir().joinpath(*SITE_COPY)
    if copied.exists():
        return json.loads(copied.read_text(encoding="utf-8"))
    return starter()


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


def _store():
    from .cvstudio import store

    return store


def _current_v1(keep_hidden: bool) -> dict:
    from .cvstudio import model

    data = model.to_v1(_store().current()[1], profile())
    for section in NAMED_BY:
        for item in data.get(section) or []:
            item["_key"] = item.get(NAMED_BY[section]) or ""
    return data if keep_hidden else printable(data)


def origin_name() -> str:
    """Which CV Hal's starts from: "built", the one built from the applicant's material, else "site"."""
    _, doc = _store().current()
    return "built" if doc.extras.get("from") == "built" else "site"


def set_origin(name: str) -> None:
    """Starts Hal's CV again from the built CV, or from the first version; either way as a new version."""
    from .cvstudio import model

    store = _store()
    if name == "built" and _live("BUILT").exists():
        doc = model.from_v1(json.loads(_live("BUILT").read_text())["cv"])
        doc.extras["from"] = "built"
        store.new_version(doc, origin="generated", what=BUILT_CHOSEN, by="you")
        return
    first = next((v for v in reversed(store.versions()) if not v["binned"]), None)
    if first is not None and origin_name() == "built":
        store.new_version(store.get(first["id"]), origin="manual", what=SITE_CHOSEN, by="you")


def origin() -> dict:
    """The CV Hal's starts from, before the applicant's changes: the first version, in cv.json shape."""
    from .cvstudio import model

    store = _store()
    store.current()
    first = store.versions()[-1]
    data = model.to_v1(store.get(first["id"]), profile())
    for section in NAMED_BY:
        for item in data.get(section) or []:
            item["_key"] = item.get(NAMED_BY[section]) or ""
    return data


def overrides() -> list[dict]:
    """How Hal's CV differs from the one it started from, one item per change."""
    store = _store()
    vid, _ = store.current()
    return store.compare(store.versions()[-1]["id"], vid)


def printable(data: dict) -> dict:
    """The CV as the PDF builder takes it: entries left out are dropped, and the editor's bookkeeping removed."""
    from .cvstudio import import_v1

    return import_v1.printable(data)


def _apply(data: dict, changes: list[dict], keep_hidden: bool = False) -> dict:
    """The old way of laying saved changes over a CV, kept for the import and its callers."""
    from .cvstudio import import_v1

    out = import_v1.apply_overrides(data, changes)
    return out if keep_hidden else printable(out)


def load() -> dict:
    """Hal's base CV in cv.json shape: the version in use, entries left out dropped."""
    return _current_v1(keep_hidden=False)


def for_editing() -> dict:
    """The base CV for the classic CV screens: entries left out kept and flagged; each entry carries its
    id (`_id`) and its name (`_key`)."""
    return _current_v1(keep_hidden=True)


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
    an order change), says what kind of change it is ("text", "order", "added", "left out"),
    and carries a short label and the base's wording.
    """
    out = []
    for f in ("title", "summary", "moreProjects", "closing"):
        if (variant.get(f) or "") != (base.get(f) or ""):
            out.append({"path": f, "kind": "text", "label": {"moreProjects": "Further projects"}.get(f, f.capitalize()),
                        "before": base.get(f) or ""})
    for section in (*NAMED_BY, *PAIRS):
        mine = variant.get(section) or []
        theirs = _shown(base.get(section))
        before = [_name(section, x) for x in theirs]
        shown = [_name(section, x) for x in _shown(mine)]
        common = [n for n in shown if n in before]
        if common != [n for n in before if n in shown]:
            moved = [f"{n} (was {before.index(n) + 1}, now {shown.index(n) + 1})" for n in common
                     if before.index(n) != shown.index(n)]
            out.append({"path": section, "kind": "order", "label": f"Order of {section}",
                        "before": ", ".join(moved[:6])})
        for name in before:
            if name not in shown:
                out.append({"path": section, "kind": "left out", "label": f"{name} ({section})", "before": ""})
        for i, item in enumerate(mine):
            name = _name(section, item)
            if isinstance(item, dict) and item.get("hidden"):
                continue
            was = _find(base, section, name) if section in PAIRS else next(
                (x for x in theirs if _name(section, x) == name), None)
            if was is None:
                out.append({"path": f"{section}.{i}", "kind": "added", "label": f"{name} ({section})", "before": ""})
                continue
            if section in PAIRS:
                if item[1] != was[1]:
                    out.append({"path": f"{section}.{i}.1", "kind": "text", "label": name,
                                "before": ", ".join(was[1]) if isinstance(was[1], list) else was[1]})
                continue
            for field, value in item.items():
                if field in ("_key", "hidden", "modules") or value == was.get(field):
                    continue
                out.append({"path": f"{section}.{i}.{field}", "kind": "text", "label": f"{name}: {field}",
                            "before": was.get(field) or ""})
    return out


def _keep_what_cv_json_cannot_hold(doc, new) -> None:
    """Puts back what the cv.json shape flattens: sections of other kinds, hidden sections, locks and order.

    Volunteering-like entries travel through the classic screens as experience and return to their own
    section by id; other sections (and hidden ones) cannot be edited there, so they are kept as they were.
    """
    from .cvstudio import model

    by_id = {s.id: s for s in new.sections}
    kept = []
    for s in doc.sections:
        if s.kind in model.V1_TITLES and s.visible:
            continue
        if s.kind in model.V1_AS_EXPERIENCE and s.visible and "experience" in by_id:
            mine = {e.id for e in s.entries}
            exp = by_id["experience"]
            back = copy.deepcopy(s)
            back.entries = [e for e in exp.entries if e.id in mine]
            exp.entries = [e for e in exp.entries if e.id not in mine]
            kept.append(back)
            continue
        if s.visible and "additional" in by_id and s.kind not in model.V1_TITLES:
            labels = {s.title, *(e.heading for e in s.entries)}
            add = by_id["additional"]
            add.entries = [e for e in add.entries if e.heading not in labels]
        kept.append(copy.deepcopy(s))
    kept_ids = {s.id for s in kept}
    merged = {**{s.id: s for s in new.sections if s.id not in kept_ids}, **{s.id: s for s in kept}}
    before = {s.id: s for s in doc.sections}
    for s in merged.values():
        if s.id in before:
            s.locked = before[s.id].locked
    order = [i for i in before if i in merged] + [i for i in merged if i not in before]
    new.sections = [merged[i] for i in order]


def save_base(edited: dict) -> list[dict]:
    """Saves the classic screens' edit of the base CV as a new version; returns what changed (none: no version).

    Entries are matched by the `_id` the screens got from `for_editing`, so a renamed entry keeps its id.
    """
    from .cvstudio import model

    store = _store()
    vid, doc = store.current()
    merged = {**model.to_v1(doc, profile()), **{k: v for k, v in edited.items() if k != "contact"}}
    for section in NAMED_BY:
        for item in merged.get(section) or []:
            item.pop("_key", None)
    new = model.from_v1(merged)
    new.extras = {**doc.extras, **{k: v for k, v in new.extras.items() if k != "v1_order"}}
    new.contact_snapshot = doc.contact_snapshot
    _keep_what_cv_json_cannot_hold(doc, new)
    found = model.diff(doc, new)
    if found:
        store.new_version(new, origin="edited", what=CLASSIC_EDIT, by="you", parent=vid)
    return found


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


def status_line() -> str:
    """When the course ends or ended, and the right to work, as the settings say them; "" when unsaid."""
    from .core import settings

    parts = []
    if _you("goal.study_end"):
        parts.append(f"Course ends or ended: {_you('goal.study_end')}.")
    labels = dict(settings.setting("you.right_to_work").choices or ())
    work = _you("you.work_status") or labels.get(_you("you.right_to_work"), "")
    if work:
        parts.append(f"Right to work: {work}.")
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
