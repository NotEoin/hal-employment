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


OVERRIDES = Path(__file__).resolve().parent.parent / "data" / "cv-overrides.json"
# Entries are matched across versions by what names them, not by position, so the site can add or reorder them.
NAMED_BY = {"education": "qualification", "projects": "name", "experience": "role"}
PAIRS = ("skills", "additional")  # entries written as [name, value]; only the value is edited
TEXT = ("title", "summary", "moreProjects", "closing")


def site() -> dict:
    """The CV as the portfolio site has it."""
    return json.loads(CV_JSON.read_text())


def overrides() -> list[dict]:
    """The applicant's changes to the site's CV, for Hal's applications only."""
    return json.loads(OVERRIDES.read_text()) if OVERRIDES.exists() else []


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
    return data


def load() -> dict:
    """Hal's base CV: the site's, with the applicant's changes."""
    return _apply(site(), overrides())


def for_editing() -> dict:
    """The base CV for the CV builder: entries left out are kept, flagged, and every entry carries its site name."""
    return _apply(site(), overrides(), keep_hidden=True)


def save_base(edited: dict) -> list[dict]:
    """Stores where an edited base CV differs from the site's, and returns those changes."""
    original = site()
    changes = [{"field": f, "value": edited[f]} for f in TEXT if f in edited and edited[f] != original.get(f)]
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
    OVERRIDES.parent.mkdir(parents=True, exist_ok=True)
    OVERRIDES.write_text(json.dumps(changes, indent=1) + "\n")
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
    """Writes the variant's JSON and PDF into `out_dir` and returns the PDF path."""
    out_dir.mkdir(parents=True, exist_ok=True)
    json_path = out_dir / "cv.json"
    pdf_path = out_dir / "Firstname-Lastname-CV.pdf"
    json_path.write_text(json.dumps(printable(variant), indent=2) + "\n")
    builder = _pdf_builder()
    builder.CONTENT, builder.OUT = json_path, pdf_path
    try:
        builder.build()
    except ValueError:
        # The builder prints OUT relative to the site root, which fails for a path
        # outside it; the PDF is already written by then.
        if not pdf_path.exists():
            raise
    return pdf_path


def facts_text() -> str:
    """Returns the CV as plain text, the only facts a draft may use."""
    d = load()
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
