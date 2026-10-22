"""Builds a CV from the applicant's own material: notes, transcripts, dossiers, old CVs.

Sources are kept as plain text in data/cv-sources/. The local model reads each
in turn and lists the facts it states; a second pass writes the CV from those
facts alone, in the shape the website's PDF builder takes, following the
options chosen. Module marks are copied from the facts as they stand, never
rewritten. Numbers no source states, and claims the facts do not support, are
listed for the applicant to check before the CV is used.
"""
import json
import re
import subprocess
import zipfile
from io import BytesIO
from pathlib import Path

from . import cv, db, documents, gpu, llm, record
from .core import paths
from .pack import VERIFY, slug, unsupported_numbers


def _sources() -> Path:
    # The old layout kept them in data/cv-sources; a moved data folder keeps them in data/cv/sources.
    return paths.data_dir() / "cv-sources" if paths.is_legacy_layout() else paths.cv_dir() / "sources"


# Resolved when used: SOURCES, the applicant's material, and PREVIEW, the built CV's PDF, which the
# web interface may show because the cache folder is servable.
_LIVE = {"SOURCES": _sources, "PREVIEW": lambda: paths.cache_dir() / "built-cv"}


def __getattr__(name):  # old path constants, read live (PEP 562)
    if name in _LIVE:
        return _LIVE[name]()
    raise AttributeError(name)


def _live(name: str):
    """An old path constant: the value a test assigned to the module, else the live path."""
    return globals()[name] if name in globals() else _LIVE[name]()


CHUNK = 12000     # characters of a source read at once
MAX_SOURCE = 400_000

OPTIONS = {
    "website": True,    # read the website's CV as one more source
    "record": True,     # read every note in the employment record (record.py) as well
    "results": True,    # open entries with what was achieved, measured where the sources measure it
    "two_pages": True,  # keep within the two pages the layout is made for
    "interests": True,  # fill the Additional section with community work and interests
    "projects": 4,      # projects on the CV; the rest go in the further-projects line
    "modules_min": 79,  # module marks shown on the PDF from this mark up; 0 shows all
    "focus": "general",  # general | backend | ml | security
}
FOCUS = {"general": "software engineering in general", "backend": "backend and distributed systems",
         "ml": "machine learning and applied AI", "security": "security engineering"}

EXTRACT = """You read one piece of a job applicant's own material (notes, a transcript, a dossier,
an old CV, a project write-up) and list every fact it states that could go on a CV.
Reply with JSON only:
{"education": [{"qualification", "institution", "dates", "grade", "points": [...],
   "modules": [{"year": e.g. "Final year", "mark": overall mark or null, "rows": [[module name, mark]]}]}],
 "experience": [{"role", "org", "dates", "points": [...]}],
 "projects": [{"name", "stack", "points": [...]}],
 "skills": [...], "achievements": [...], "community": [...], "interests": [...]}
Copy names, dates, numbers and marks exactly. Leave out anything the text does not state;
use "" or [] where it is silent. Marks are numbers out of 100."""

COMPOSE = """You write a UK graduate CV for Firstname Lastname from the facts given, and nothing else.
Reply with JSON only:
{"title": one line naming the kind of graduate and the work sought,
 "summary": 3 or 4 sentences,
 "education": [{"qualification", "grade" or "", "meta": "Institution | dates | anything else short",
   "detail": one paragraph}],
 "experience": [{"role", "org", "dates", "body": one paragraph}],
 "projects": [{"name": "Name - what it is in a few words", "stack": the main tools, comma separated,
   "body": one paragraph}],
 "skills": [[group name, [items]]], "moreProjects": one sentence naming further projects,
 "additional": [[label, text]], "closing": "References available on request."}
Rules: use only the facts; never invent an employer, date, grade, number or technology.
British English. Plain, specific sentences; no cliches such as "passionate" or "results-driven".
Open each education, project and experience paragraph with a short bold lead phrase in
**double asterisks** followed by a colon, as in "**Dissertation, Title:** ...". Order
entries most recent first and projects strongest first."""


def sources() -> list[dict]:
    """The material the CV is built from, newest first."""
    if not _live("SOURCES").exists():
        return []
    files = sorted(_live("SOURCES").glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.stem, "chars": len(p.read_text(errors="replace")),
             "preview": p.read_text(errors="replace")[:200]} for p in files]


def _text_of(name: str, data: bytes) -> str:
    """Plain text from an uploaded file: text as it is, PDFs through pdftotext, Word documents by their XML."""
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        out = subprocess.run(["pdftotext", "-layout", "-", "-"], input=data, capture_output=True, timeout=60)
        if out.returncode:
            raise ValueError(f"could not read {name}: {out.stderr.decode(errors='replace')[:200]}")
        return out.stdout.decode(errors="replace")
    if suffix == ".docx":
        with zipfile.ZipFile(BytesIO(data)) as z:
            xml = z.read("word/document.xml").decode(errors="replace")
        xml = re.sub(r"</w:p>", "\n", xml)
        return re.sub(r"<[^>]+>", "", xml)
    return data.decode("utf-8", errors="replace")


def add(name: str, text: str = "", data: bytes | None = None) -> str:
    """Keeps a piece of material, pasted or uploaded, and returns the name it is kept under."""
    if data is not None:
        text = _text_of(name, data)
    text = text.strip()
    if not text:
        raise ValueError("there is no text to keep")
    if len(text) > MAX_SOURCE:
        raise ValueError(f"that is {len(text):,} characters; split it into pieces under {MAX_SOURCE:,}")
    stem = slug(Path(name).stem if data is not None else name) or "notes"
    _live("SOURCES").mkdir(parents=True, exist_ok=True)
    path, n = _live("SOURCES") / f"{stem}.txt", 2
    while path.exists():
        path, n = _live("SOURCES") / f"{stem}-{n}.txt", n + 1
    path.write_text(text + "\n")
    return path.stem


def remove(name: str) -> None:
    path = (_live("SOURCES") / f"{name}.txt").resolve()
    if path.parent == _live("SOURCES").resolve() and path.exists():
        path.unlink()


def _material(options: dict) -> list[tuple[str, str]]:
    sources = _live("SOURCES")
    found = [(p.stem, p.read_text(errors="replace")) for p in sorted(sources.glob("*.txt"))] if sources.exists() else []
    if options.get("website"):
        found.append(("website CV", json.dumps(cv.printable(cv.site()), indent=1)))
    if options.get("record") and record.VAULT.exists():
        try:
            record.convert()
            notes = [n for n in record._walk((".md",)) if n.name != "Start Here.md"]
        except record.RecordUnavailable:
            notes = []
        found += [(f"record: {n.relative_to(record.VAULT)}", n.read_text(encoding="utf-8", errors="replace"))
                  for n in notes]
    return found


def _facts(material: list[tuple[str, str]], report) -> dict:
    """Reads each source, a piece at a time, into one list of facts."""
    facts: dict = {k: [] for k in ("education", "experience", "projects", "skills", "achievements",
                                   "community", "interests")}
    pieces = [(name, text[i:i + CHUNK]) for name, text in material for i in range(0, len(text), CHUNK)]
    for n, (name, piece) in enumerate(pieces, 1):
        report(f"reading your material ({n} of {len(pieces)}: {name})")
        got = llm.chat_json(EXTRACT, f"Source: {name}\n\n{piece}", role="main", timeout=600, ctx=16384)
        for key in facts:
            value = got.get(key) or []
            facts[key].extend(value if isinstance(value, list) else [value])
    return facts


def _modules(facts: dict) -> list[dict]:
    """The fullest transcript the material holds, with only rows that are a module and a mark."""
    best: list[dict] = []
    for entry in facts["education"]:
        groups = []
        for g in (entry.get("modules") if isinstance(entry, dict) else None) or []:
            if not isinstance(g, dict):
                continue
            rows = [[str(r[0]), r[1]] for r in g.get("rows") or []
                    if isinstance(r, list) and len(r) == 2 and isinstance(r[1], (int, float))
                    and not isinstance(r[1], bool) and 0 <= r[1] <= 100]
            mark = g.get("mark")
            if rows and str(g.get("year") or "").strip():
                groups.append({"year": str(g["year"]), "rows": rows,
                               **({"mark": mark} if isinstance(mark, (int, float)) and 0 <= mark <= 100 else {})})
        if sum(len(g["rows"]) for g in groups) > sum(len(g["rows"]) for g in best):
            best = groups
    return best


def _shape(draft: dict, facts: dict, options: dict) -> dict:
    """Puts the model's draft into the exact shape the PDF builder checks, filling what it must have."""
    site = cv.site()
    out = {"title": str(draft.get("title") or site["title"]), "contact": site["contact"],
           "summary": str(draft.get("summary") or site["summary"]),
           "education": [], "skills": [], "projects": [], "experience": [], "additional": [],
           "moreProjects": str(draft.get("moreProjects") or "").strip() or site["moreProjects"],
           "closing": str(draft.get("closing") or "").strip() or "References available on request."}
    for e in draft.get("education") or []:
        if isinstance(e, dict) and e.get("qualification") and e.get("detail"):
            out["education"].append({k: str(e[k]) for k in ("qualification", "grade", "meta", "detail") if e.get(k)})
    for e in out["education"]:
        e.setdefault("meta", e["qualification"])
    if not out["education"]:
        raise RuntimeError("the material gave no education to put on the CV")
    # The transcript is copied from the material, or failing that from the website, never written by the model.
    modules = _modules(facts)
    site_degree = next((e for e in site["education"] if e.get("modules")), None)
    out["education"][0]["modules"] = modules or (site_degree or {}).get("modules", [])
    if not out["education"][0]["modules"]:
        raise RuntimeError("no module marks were found; add your transcript as a source")
    out["education"][0]["modulesNote"] = "Marks are out of 100. 70 and above is a First."
    if options.get("modules_min"):
        out["education"][0]["pdfModulesMin"] = max(0, min(100, int(options["modules_min"])))
    for g in draft.get("skills") or []:
        if isinstance(g, list) and len(g) == 2 and isinstance(g[1], list):
            items = [str(x) for x in g[1] if str(x).strip()]
            if items:
                out["skills"].append([str(g[0]), items])
    for p in (draft.get("projects") or [])[:max(1, int(options.get("projects") or 4))]:
        if isinstance(p, dict) and p.get("name") and p.get("body"):
            out["projects"].append({"slug": slug(p["name"]) or "project", "name": str(p["name"]),
                                    "stack": str(p.get("stack") or "").strip() or "-", "body": str(p["body"])})
    for x in draft.get("experience") or []:
        if isinstance(x, dict) and all(x.get(k) for k in ("role", "org", "dates", "body")):
            out["experience"].append({k: str(x[k]) for k in ("role", "org", "dates", "body")})
    if options.get("interests"):
        out["additional"] = [[str(a[0]), str(a[1])] for a in draft.get("additional") or []
                             if isinstance(a, list) and len(a) == 2 and a[1]]
    return _plain(out)


def _plain(value):
    """Every string in the CV in the plain ASCII the PDF fonts draw, without long dashes."""
    if isinstance(value, str):
        return documents._ascii(llm.undash(value))
    if isinstance(value, list):
        return [_plain(v) for v in value]
    if isinstance(value, dict):
        return {k: _plain(v) for k, v in value.items()}
    return value


def _checks(built: dict, material: list[tuple[str, str]], facts: dict) -> list[str]:
    """What to check before using the CV: numbers no source states, and claims the facts do not back."""
    text = cv.facts_text_of(built)
    every = "\n".join(t for _, t in material)
    flags = [f"a number no source states: {n}" for n in unsupported_numbers(text, every)]
    try:
        reply = llm.chat_json(VERIFY, f"Facts:\n{json.dumps(facts)[:30000]}\n\nDrafts:\n{text}",
                              role="main", timeout=600, ctx=16384)
        flags += [f"not backed by your material: {c}" for c in reply.get("unsupported", [])]
    except llm.LLMUnavailable:
        flags.append("the claims could not be checked: the model stopped")
    return flags


def build(options: dict, report=lambda message: None) -> dict:
    """Builds a CV from the material and keeps it as the draft; returns it with what to check."""
    options = {**OPTIONS, **options}
    reason = gpu.busy()
    if reason:
        raise RuntimeError(reason)
    material = _material(options)
    if not material:
        raise RuntimeError("add some material first: paste notes or upload a transcript or an old CV")
    facts = _facts(material, report)
    report("writing the CV")
    style = [f"Point the title and summary at {FOCUS.get(options['focus'], FOCUS['general'])}, "
             "and put the projects that show it first."]
    if options.get("results"):
        style.append("Open each paragraph with what was achieved, with the measure where the facts give one.")
    if options.get("two_pages"):
        style.append("Keep within two A4 pages: summary under 70 words, each paragraph under 90 words.")
    style.append(f"Write {int(options.get('projects') or 4)} projects; name any others in moreProjects.")
    if not options.get("interests"):
        style.append("Leave additional empty.")
    draft = llm.chat_json(COMPOSE, "\n".join(style) + f"\n\nFacts:\n{json.dumps(facts, ensure_ascii=False)[:60000]}",
                          role="writer", timeout=900, ctx=32768)
    built = _shape(draft, facts, options)
    report("checking the CV against your material")
    flags = _checks(built, material, facts)
    cv.BUILT.write_text(json.dumps({"cv": built, "flags": flags, "options": options, "built": db.now()}, indent=1) + "\n")
    cv.render(built, _live("PREVIEW"))
    return {"cv": built, "flags": flags}
