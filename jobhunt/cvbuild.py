"""Builds a CV from the applicant's own material: notes, transcripts, dossiers, old CVs.

Sources are kept as plain text in the CV folder's `sources/`. The AI reads each in turn and lists the
facts it states (prompt `cv_extract`); a second pass writes the CV from those facts alone (`cv_compose`),
in the shape cvpdf.py draws, following the options chosen. Module marks are copied from the facts as they
stand, never rewritten, and are optional: a CV without a transcript still builds. Numbers no source
states, and claims the facts do not support, are listed for the applicant to check before the CV is used.
Notes in the record's private folders are never read here, so they never reach the AI.
"""
import json
import re
import zipfile
from io import BytesIO
from pathlib import Path

from . import ai, cv, db, record
from .core import paths
from .cvpdf import _drawable
from .cvstudio import pdfread
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
READ_TIMEOUT_S, WRITE_TIMEOUT_S = 600, 900
READ_CTX, WRITE_CTX = 16384, 32768   # one piece of material, then every fact at once
FIRST_NOTE = "Marks are out of 100. 70 and above is a First."   # UK degree classes

OPTIONS = {
    "website": True,    # read the website's CV as one more source
    "record": True,     # read every note in the employment record (record.py) as well
    "results": True,    # open entries with what was achieved, measured where the sources measure it
    "two_pages": True,  # keep within the two pages the layout is made for
    "interests": True,  # fill the Additional section with community work and interests
    "projects": 4,      # projects on the CV; the rest go in the further-projects line
    "modules_min": 79,  # module marks shown on the PDF from this mark up; 0 shows all
    "focus": "",        # the kind of work to point the CV at, in the applicant's words; "" for none
}

def sources() -> list[dict]:
    """The material the CV is built from, newest first."""
    if not _live("SOURCES").exists():
        return []
    files = sorted(_live("SOURCES").glob("*.txt"), key=lambda p: p.stat().st_mtime, reverse=True)
    return [{"name": p.stem, "chars": len(p.read_text(errors="replace")),
             "preview": p.read_text(errors="replace")[:200]} for p in files]


def _text_of(name: str, data: bytes) -> str:
    """Plain text from an uploaded file: text as it is, PDFs by their text layer, Word documents by their XML."""
    suffix = Path(name).suffix.lower()
    if suffix == ".pdf":
        text = pdfread.text(data, layout=True)
        if not text.strip():
            raise ValueError(f"could not read {name}: it has no text Hal can read")
        return text
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
        found.append(("starting CV", json.dumps(cv.printable(cv.site()), indent=1)))
    if options.get("record"):
        found += [(f"record: {rel}", text) for rel, text in record.notes_for_ai()]
    return found


def _facts(material: list[tuple[str, str]], report) -> dict:
    """Reads each source, a piece at a time, into one list of facts."""
    facts: dict = {k: [] for k in ("education", "experience", "projects", "skills", "achievements",
                                   "community", "interests")}
    pieces = [(name, text[i:i + CHUNK]) for name, text in material for i in range(0, len(text), CHUNK)]
    for n, (name, piece) in enumerate(pieces, 1):
        report(f"reading your material ({n} of {len(pieces)}: {name})")
        got = ai.chat_json(_prompt("cv_extract"), f"Source: {name}\n\n{piece}", role="checker",
                           timeout=READ_TIMEOUT_S, ctx=READ_CTX, purpose="cv_extract")
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
    out = {"title": str(draft.get("title") or site.get("title") or ""), "contact": site.get("contact") or cv.contact(),
           "summary": str(draft.get("summary") or site.get("summary") or ""),
           "education": [], "skills": [], "projects": [], "experience": [], "additional": [],
           "moreProjects": str(draft.get("moreProjects") or "").strip() or site.get("moreProjects", ""),
           "closing": str(draft.get("closing") or "").strip() or "References available on request."}
    for e in draft.get("education") or []:
        if isinstance(e, dict) and e.get("qualification") and e.get("detail"):
            out["education"].append({k: str(e[k]) for k in ("qualification", "grade", "meta", "detail") if e.get(k)})
    for e in out["education"]:
        e.setdefault("meta", e["qualification"])
    if not out["education"]:
        raise RuntimeError("the material gave no education to put on the CV")
    # The transcript is copied from the material, or failing that from the starting CV, never written by the
    # AI. Many people have none to give, so a CV without module marks is built all the same.
    modules = _modules(facts)
    site_degree = next((e for e in site.get("education") or [] if e.get("modules")), None)
    modules = modules or (site_degree or {}).get("modules", [])
    if modules:
        out["education"][0]["modules"] = modules
        out["education"][0]["modulesNote"] = FIRST_NOTE
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
    """Every string in the CV in what the PDF fonts draw (accented letters kept), without long dashes."""
    return _drawable(value)


def _prompt(prompt_id: str) -> str:
    from .ai import prompts

    return prompts.text(prompt_id)


def _checks(built: dict, material: list[tuple[str, str]], facts: dict) -> list[str]:
    """What to check before using the CV: numbers no source states, and claims the facts do not back."""
    text = cv.facts_text_of(built)
    every = "\n".join(t for _, t in material)
    flags = [f"a number no source states: {n}" for n in unsupported_numbers(text, every)]
    try:
        reply = ai.chat_json(VERIFY, f"Facts:\n{json.dumps(facts)[:30000]}\n\nDrafts:\n{text}",
                             role="checker", timeout=READ_TIMEOUT_S, ctx=READ_CTX, purpose="verify")
        flags += [f"not backed by your material: {c}" for c in reply.get("unsupported", [])]
    except ai.LLMUnavailable:
        flags.append("the claims could not be checked: the AI stopped")
    return flags


def build(options: dict, report=lambda message: None) -> dict:
    """Builds a CV from the material and keeps it as the draft; returns it with what to check."""
    options = {**OPTIONS, **options}
    reason = ai.ready(priority=0, purpose="cv_compose")
    if reason:
        raise RuntimeError(reason)
    material = _material(options)
    if not material:
        raise RuntimeError("add some material first: paste notes or upload a transcript or an old CV")
    facts = _facts(material, report)
    report("writing the CV")
    focus = str(options.get("focus") or "").strip()
    # Classic's menu sends "general" for no particular kind of work.
    style = [f"Point the title and summary at {focus}, and put the entries that show it first."
             ] if focus and focus != "general" else []
    if options.get("results"):
        style.append("Open each paragraph with what was achieved, with the measure where the facts give one.")
    if options.get("two_pages"):
        style.append("Keep within two A4 pages: summary under 70 words, each paragraph under 90 words.")
    style.append(f"Write {int(options.get('projects') or 4)} projects; name any others in moreProjects.")
    if not options.get("interests"):
        style.append("Leave additional empty.")
    draft = ai.chat_json(_prompt("cv_compose"), "\n".join(style)
                         + f"\n\nFacts:\n{json.dumps(facts, ensure_ascii=False)[:60000]}",
                         role="writer", timeout=WRITE_TIMEOUT_S, ctx=WRITE_CTX, purpose="cv_compose")
    built = _shape(draft, facts, options)
    report("checking the CV against your material")
    flags = _checks(built, material, facts)
    cv.BUILT.parent.mkdir(parents=True, exist_ok=True)
    cv.BUILT.write_text(json.dumps({"cv": built, "flags": flags, "options": options, "built": db.now()}, indent=1) + "\n")
    cv.render(built, _live("PREVIEW"))
    return {"cv": built, "flags": flags}
