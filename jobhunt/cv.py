"""Tailored CV variants built from the portfolio site's cv.json.

Hal's base CV is the site's cv.json with the applicant's own changes laid over
it (data/cv-overrides.json, edited in the web interface's CV builder), so the
website keeps its wording while applications use theirs. A variant reorders
projects and skill groups by relevance to the job and may swap the title for a
track title; the applicant can then edit any application's variant by hand.
The PDF is rendered by the site's own tools/build-cv-pdf.py, so layout matches
the canonical CV.
"""
import copy
import importlib.util
import json
import re
import sys
from pathlib import Path

from .config import CONFIG
from .core import paths

SITE = Path(CONFIG.get("cv_site", "~/sites/cv-site")).expanduser()
CV_JSON = SITE / "src" / "data" / "cv.json"

TRACKS = {
    "ml": ("Graduate - machine learning and applied AI",
           r"machine learning|\bml\b|\bai\b|deep learning|pytorch|llm|computer vision|data scien"),
    "security": ("Graduate - security engineering",
                 r"security|cyber|crypto|threat|vulnerab|penetration|soc\b"),
    "backend": ("Graduate - backend and distributed systems",
                r"backend|back-end|distributed|platform|infrastructure|api|microservice|devops|sre"),
}




def _data() -> Path:
    # The old layout kept these files loose in data/; a moved data folder keeps them in data/cv/.
    return paths.data_dir() if paths.is_legacy_layout() else paths.cv_dir()


# Resolved when used until the CV store replaces them: DATA, and in it OVERRIDES (changes to the
# website's CV), BUILT (the CV built from the applicant's material by cvbuild.py, with what to check),
# BUILT_OVERRIDES (changes to the built CV) and ORIGIN ("built" when Hal's CV starts from the built one).
_LIVE = {"DATA": _data, "OVERRIDES": lambda: _live("DATA") / "cv-overrides.json",
         "BUILT": lambda: _live("DATA") / "cv-built.json",
         "BUILT_OVERRIDES": lambda: _live("DATA") / "cv-built-overrides.json",
         "ORIGIN": lambda: _live("DATA") / "cv-origin"}


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


def site() -> dict:
    """The CV as the portfolio site has it."""
    return json.loads(CV_JSON.read_text())


def origin_name() -> str:
    """Which CV Hal's starts from: "site", the website's, or "built", the one built from the applicant's material."""
    origin, built_cv = _live("ORIGIN"), _live("BUILT")
    built = origin.exists() and origin.read_text().strip() == "built" and built_cv.exists()
    return "built" if built else "site"


def set_origin(name: str) -> None:
    _live("ORIGIN").parent.mkdir(parents=True, exist_ok=True)
    _live("ORIGIN").write_text(("built" if name == "built" else "site") + "\n")


def origin() -> dict:
    """The CV Hal's starts from, before the applicant's changes."""
    return json.loads(_live("BUILT").read_text())["cv"] if origin_name() == "built" else site()


def _overrides_path() -> Path:
    return _live("BUILT_OVERRIDES") if origin_name() == "built" else _live("OVERRIDES")


def overrides() -> list[dict]:
    """The applicant's changes to the CV Hal's starts from, for Hal's applications only."""
    path = _overrides_path()
    return json.loads(path.read_text()) if path.exists() else []


def _find(data: dict, section: str, key):
    items = data.get(section) or []
    if section in PAIRS:
        return next((x for x in items if x[0] == key), None)
    return next((x for x in items if x.get(NAMED_BY[section]) == key), None)


def _apply(data: dict, changes: list[dict], keep_hidden: bool = False) -> dict:
    data = copy.deepcopy(data)
    for c in changes:
        if "section" not in c:
            data[c["field"]] = c["value"]
            continue
        item = _find(data, c["section"], c["key"])
        if item is not None:
            item[c["field"]] = c["value"]
    for section in NAMED_BY:
        for item in data.get(section) or []:
            item["_key"] = item[NAMED_BY[section]]
        if not keep_hidden:
            data[section] = [x for x in data.get(section) or [] if not x.get("hidden")]
    return data if keep_hidden else printable(data)


def printable(data: dict) -> dict:
    """The CV as the PDF builder takes it: entries left out are dropped, and the editor's bookkeeping removed."""
    data = copy.deepcopy(data)
    for section in NAMED_BY:
        data[section] = [{k: v for k, v in x.items() if k not in ("_key", "hidden")}
                         for x in data.get(section) or [] if not x.get("hidden")]
    for section in PAIRS:
        data[section] = [x for x in data.get(section) or [] if x[1]]
    return data


def load() -> dict:
    """Hal's base CV: the one it starts from, with the applicant's changes."""
    return _apply(origin(), overrides())


def for_editing() -> dict:
    """The base CV for the CV builder: entries left out are kept, flagged, and every entry carries its original name."""
    return _apply(origin(), overrides(), keep_hidden=True)


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


def save_base(edited: dict) -> list[dict]:
    """Stores where an edited base CV differs from the one it starts from, and returns those changes."""
    original = origin()
    changes = [{"field": f, "value": edited[f]} for f in TEXT if f in edited and edited[f] != original.get(f)]
    for section in PAIRS:
        names = [x[0] for x in edited.get(section) or []]
        if names != [x[0] for x in original.get(section) or []]:
            # Groups added, renamed or reordered: the section is kept whole, as the applicant wrote it.
            changes.append({"field": section, "value": edited.get(section) or []})
            edited = {**edited, section: []}
    for section in (*NAMED_BY, *PAIRS):
        for item in edited.get(section) or []:
            key = item[0] if section in PAIRS else item.get("_key", item.get(NAMED_BY[section]))
            was = _find(original, section, key)
            if was is None:
                continue
            if section in PAIRS:
                if item[1] != was[1]:
                    changes.append({"section": section, "key": key, "field": 1, "value": item[1]})
                continue
            for field, value in item.items():
                if field in ("_key", "modules") or (field == "hidden" and not value):
                    continue
                if value != was.get(field):
                    changes.append({"section": section, "key": key, "field": field, "value": value})
    path = _overrides_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(changes, indent=1) + "\n")
    return changes


def _words(text: str) -> set[str]:
    return {w for w in re.findall(r"[a-z][a-z0-9+#.]{1,}", text.lower()) if len(w) > 2}


def track_for(job_text: str) -> str | None:
    """Returns the track whose keywords appear most often in the job text."""
    counts = {name: len(re.findall(pattern, job_text, re.I)) for name, (_, pattern) in TRACKS.items()}
    best = max(counts, key=counts.get)
    return best if counts[best] >= 2 else None


def tailor(job_text: str) -> tuple[dict, str | None]:
    """Returns a cv.json variant ordered for this job, and the track chosen."""
    data = copy.deepcopy(load())
    wanted = _words(job_text)

    def relevance(text: str) -> int:
        return len(_words(text) & wanted)

    data["projects"].sort(key=lambda p: -relevance(f"{p['name']} {p['stack']} {p['body']}"))
    data["skills"].sort(key=lambda group: -relevance(" ".join([group[0], *group[1]])))
    track = track_for(job_text)
    if track:
        data["title"] = TRACKS[track][0]
    return data, track


def _pdf_builder():
    """Loads the site's PDF builder as a module, with its tools directory importable."""
    tools = SITE / "tools"
    if str(tools) not in sys.path:
        sys.path.insert(0, str(tools))
    spec = importlib.util.spec_from_file_location("build_cv_pdf", tools / "build-cv-pdf.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def render(variant: dict, out_dir: Path) -> Path:
    """Writes the variant's JSON and its PDF, in Hal's layout for applications, into `out_dir`."""
    from . import cvpdf
    out_dir.mkdir(parents=True, exist_ok=True)
    (out_dir / "cv.json").write_text(json.dumps(printable(variant), indent=2) + "\n")
    return cvpdf.render(variant, out_dir)


def facts_text() -> str:
    """Returns the CV as plain text, the only facts a draft may use."""
    return facts_text_of(load())


def facts_text_of(d: dict) -> str:
    """Returns a CV as plain text."""
    parts = [d["title"], d["summary"]]
    for e in d["education"]:
        parts += [e.get("qualification", ""), e.get("grade", ""), e.get("meta", ""), e.get("detail", "")]
        for year in e.get("modules") or []:
            marks = ", ".join(f"{name} {mark}" for name, mark in year.get("rows", []))
            parts.append(f"{year.get('year')} modules (overall {year.get('mark')}): {marks}")
    parts += [f"{g[0]}: {', '.join(g[1])}" for g in d["skills"]]
    parts += [f"Personal project, {p['name']} ({p['stack']}): {p['body']}" for p in d["projects"]]
    parts.append(d.get("moreProjects", ""))
    parts += [f"{x['role']}, {x['org']} ({x['dates']}): {x['body']}" for x in d["experience"]]
    parts += [f"{a[0]}: {a[1]}" for a in d.get("additional", [])]
    parts.append("Recently graduated; available to start immediately; UK and Irish citizen, "
                 "no sponsorship needed.")
    text = "\n".join(p for p in parts if p)
    return re.sub(r"\*\*", "", text)
