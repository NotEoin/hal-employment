"""Tailored CV variants built from the portfolio site's cv.json.

A variant reorders projects and skill groups by relevance to the job and may
swap the title for a track title. Wording comes only from cv.json, so a
variant can't claim anything the canonical CV doesn't. The PDF is rendered
by the site's own tools/build-cv-pdf.py, so layout matches the canonical CV.
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


def load() -> dict:
    return json.loads(CV_JSON.read_text())


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
    json_path.write_text(json.dumps(variant, indent=2) + "\n")
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
