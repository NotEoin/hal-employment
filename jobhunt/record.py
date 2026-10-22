"""Your record: a folder of everything about the person, searched whenever Hal writes.

The CV is a polished summary; the record holds the rest: certificates, transcripts, references, awards, the
full story of each job, placement and piece of volunteering. It is a plain folder (setting `record.folder`;
setup makes "Employment Record" in Documents), so it can also be opened as an Obsidian vault. Hal reads this
folder and nothing else of the person's files.

Documents dropped in (PDF, Word, PowerPoint, Excel, web pages, saved emails) are turned into Markdown notes
beside them, keeping their headings and tables, so they can be searched in sensible sections: by
Microsoft's MarkItDown when it is installed, else by pypdfium2 for PDFs (`cvstudio/pdfread.py`) and by
reading a Word file's XML. Photos and scans are read by OCR when tesseract is installed and `record.ocr`
is on.

Search is by words (BM25) over passages split at each heading, in pure Python, so it needs no AI, no
graphics card and no internet; the index is one JSON file in the data folder. A passage's score is the share
of the query's weighted words it contains, from 0 to 1, so a threshold means the same for a short query as
for a long one. With `record.search` set to "meaning", Hal also ranks passages by meaning with the AI on this
computer when it answers, and falls back to words when it does not.

Files in the folders `record.private_folders` names (default "Private") are never sent to any AI: they are
left out of `context`, `ask`, `notes_for_ai` and the vectors, and only the person's own search shows them.
"""
import hashlib
import json
import logging
import math
import os
import re
import shutil
import subprocess
import tempfile
import xml.etree.ElementTree as ET
import zipfile
from collections import Counter
from datetime import date
from pathlib import Path

from .config import CONFIG
from .core import paths

log = logging.getLogger(__name__)
NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)   # Windows: no console flashes up for tesseract
OCR_TIMEOUT_S = 120
OCR_SCALE = 300 / 72      # 300 dpi, tesseract's documented best resolution for printed text
EMBED_MODEL_DEFAULT = "nomic-embed-text"
EMBED_BATCH = 32
MEANING_MIN = 0.55        # nomic-embed-text similarities run high; below this a passage is off the subject


def _setting(key: str, legacy_key: str | None = None, default=None):
    # The stored setting wins; config.toml answers in the old layout until the importer has copied it.
    from .core import settings
    from .core.settings import store

    if legacy_key is None:
        return settings.get(key)
    return store.stored(key) or CONFIG.get(legacy_key) or default


# Resolved when used: VAULT (the record folder, setting record.folder) and INDEX (the search index).
_LIVE = {"VAULT": lambda: Path(_setting("record.folder", "record_vault") or paths.record_default()).expanduser(),
         "INDEX": lambda: paths.data_dir() / "record-index" / "index.json"}


def __getattr__(name):  # old constants, read live (PEP 562)
    if name in _LIVE:
        return _LIVE[name]()
    raise AttributeError(name)


def _live(name: str):
    """An old constant: the value a test assigned to the module, else the live value."""
    return globals()[name] if name in globals() else _LIVE[name]()


# What is converted to notes; Markdown notes themselves are indexed as they are.
DOCUMENTS = (".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".epub", ".msg", ".txt")
# Photos and scans of certificates, read by OCR.
IMAGES = (".png", ".jpg", ".jpeg", ".webp", ".tif", ".tiff")
MIN_SCORE = 0.5  # below half the query's weight a passage is about something else
SKIP_DIRS = {".obsidian", ".trash"}
# Navigation notes, not content: the "Start Here" note setup writes is one.
SKIP_TYPE = {"topic", "dashboard", "index"}
MAX_CHARS = 1200  # a passage's size: a paragraph or two, enough to quote from
# BM25's usual constants (Robertson and Zaragoza, 2009).
K1, B = 1.5, 0.75
STOP = set("""a about above after again all also am an and any are as at be been being below between both but by
can could did do does doing down during each few for from further had has have having he her here hers him his how
i if in into is it its itself just me more most my no nor not now of off on once only or other our ours out over own
please same she should so some such than that the their theirs them then there these they this those through to too
under until up very was we were what when where which while who whom why will with would you your yours yourself
give provide describe tell us""".split())


class RecordUnavailable(Exception):
    pass


# Turning documents into notes ---------------------------------------------------------------

_MARKITDOWN = None


def _run(args: list[str], **kw) -> subprocess.CompletedProcess:
    return subprocess.run(args, capture_output=True, creationflags=NO_WINDOW, **kw)


def _tesseract() -> str | None:
    """Where tesseract is: on PATH, or where its Windows installer puts it; None without it."""
    found = shutil.which("tesseract")
    if found:
        return found
    for base, sub in ((os.environ.get("ProgramFiles"), "Tesseract-OCR"),
                      (os.environ.get("LOCALAPPDATA"), "Programs/Tesseract-OCR")):
        if base and (Path(base) / sub / "tesseract.exe").exists():
            return str(Path(base) / sub / "tesseract.exe")
    return None


def _ocr(path: Path) -> str:
    """The words in an image, or in each page of a PDF with no text layer, by tesseract; "" without it."""
    exe = _tesseract()
    if not exe or not _setting("record.ocr"):
        return ""
    if path.suffix.lower() != ".pdf":
        try:
            out = _run([exe, str(path), "-"], timeout=OCR_TIMEOUT_S)
        except (OSError, subprocess.TimeoutExpired):
            log.warning("tesseract could not read %s", path.name)
            return ""
        return out.stdout.decode("utf-8", errors="replace") if out.returncode == 0 else ""
    from .cvstudio import pdfread

    with tempfile.TemporaryDirectory() as tmp:
        texts = []
        for n in range(1, pdfread.pages(path) + 1):
            page = Path(tmp) / f"page-{n}.png"
            page.write_bytes(pdfread.page_png(path, n, OCR_SCALE))
            texts.append(_ocr(page))
        return "\n\n".join(texts)


def _docx(path: Path) -> str:
    """A Word document's paragraphs, with its headings as Markdown headings, read from its XML."""
    ns = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
    with zipfile.ZipFile(path) as z:
        root = ET.fromstring(z.read("word/document.xml"))
    lines = []
    for para in root.iter(f"{ns}p"):
        text = "".join(t.text or "" for t in para.iter(f"{ns}t")).strip()
        if not text:
            continue
        style = para.find(f"{ns}pPr/{ns}pStyle")
        level = re.search(r"Heading(\d)", style.get(f"{ns}val", "")) if style is not None else None
        lines.append(f"{'#' * int(level.group(1))} {text}" if level else text)
    return "\n\n".join(lines)


def _text_of(path: Path) -> str:
    """A document as Markdown, keeping its headings, lists and tables; pdftotext for a PDF without headings.

    MarkItDown is called only through convert_local on files inside the record, with plugins off and no
    cloud service configured, so a document is never fetched from or sent anywhere.
    """
    global _MARKITDOWN
    suffix = path.suffix.lower()
    if suffix in IMAGES:
        return _ocr(path)
    text = ""
    try:
        if _MARKITDOWN is None:
            from markitdown import MarkItDown
            _MARKITDOWN = MarkItDown(enable_plugins=False)
        text = _MARKITDOWN.convert_local(str(path)).markdown or ""
    except Exception:  # a converter missing or failing falls back below, or leaves the file unread
        text = ""
    if not text.strip() and suffix == ".docx":
        try:
            text = _docx(path)
        except (OSError, KeyError, zipfile.BadZipFile, ET.ParseError):
            text = ""
    # On a PDF without headings MarkItDown parts a form's labels from their values, and its guessed tables
    # run a transcript's columns together ("10 76" for credits and mark); reading the text by position keeps
    # each row on one line with its columns apart. Pipe tables are therefore no sign of structure here.
    if suffix == ".pdf" and (len(text.strip()) < 50 or not re.search(r"^#{1,6} ", text, re.M)):
        from .cvstudio import pdfread

        laid = pdfread.text(path, layout=True)
        text = laid if laid.strip() else text
        if len(text.strip()) < 20:  # a scan: the pages are pictures of text
            text = _ocr(path)
    if not text.strip() and suffix in (".txt", ".html", ".htm"):
        text = path.read_text(encoding="utf-8", errors="replace")
        if suffix != ".txt":
            text = re.sub(r"<h([1-6])[^>]*>(.*?)</h\1>",
                          lambda m: "\n\n" + "#" * int(m.group(1)) + " " + m.group(2) + "\n\n", text, flags=re.I | re.S)
            text = re.sub(r"<[^>]+>", " ", text)
    return text


def convert() -> list[Path]:
    """Writes a note beside each document that has none, or whose document changed; returns the notes written."""
    written = []
    for doc in _walk(DOCUMENTS + IMAGES):
        note = doc.with_name(f"{doc.stem} ({doc.suffix[1:].upper()}).md")
        if note.exists() and note.stat().st_mtime >= doc.stat().st_mtime:
            continue
        # Runs of spaces inside a line are kept: in pdftotext's layout they are what keeps columns apart.
        text = re.sub(r"[ \t]+\n", "\n", _text_of(doc).replace("\r\n", "\n").replace("\f", "\n"))
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if not text:
            continue
        note.write_text(f"---\ntype: document\nsource: \"[[{doc.name}]]\"\nconverted: {date.today().isoformat()}\n---\n"
                        f"# {doc.stem}\n\nText of [[{doc.name}]], read by Hal Employment.\n\n{text}\n", encoding="utf-8")
        written.append(note)
    return written


def _walk(suffixes: tuple) -> list[Path]:
    found = []
    for folder, dirs, files in os.walk(_live("VAULT")):
        dirs[:] = [d for d in dirs if d not in SKIP_DIRS and not d.startswith(".")]
        found += [Path(folder) / f for f in files if f.lower().endswith(suffixes)]
    return sorted(found)


# Passages -----------------------------------------------------------------------------------

def _frontmatter(text: str) -> tuple[dict, str]:
    fm, body = {}, text
    if text.startswith("---"):
        end = text.find("\n---", 3)
        if end != -1:
            for line in text[3:end].splitlines():
                m = re.match(r"^([A-Za-z0-9_]+):\s*(.*)$", line)
                if m:
                    fm[m.group(1)] = m.group(2).strip()
            body = text[end + 4:]
    return fm, body


def should_index(text: str) -> bool:
    """False for navigation notes (index, topic, dashboard) and empty stubs."""
    fm, _ = _frontmatter(text)
    return fm.get("type", "").strip().lower() not in SKIP_TYPE and fm.get("status", "").strip().lower() != "stub"


def chunks(rel: str, text: str) -> list[dict]:
    """A note's passages, each under its nearest heading and no longer than MAX_CHARS.

    No passage carries text from the one before: a carried tail would file one year's
    module marks under the next year's heading.
    """
    fm, body = _frontmatter(text)
    body = "\n".join(line for line in body.splitlines() if not line.strip().startswith("<!--"))
    found = re.search(r"^#\s+(.*)$", body, re.M)
    title = fm.get("title") or (found.group(1).strip() if found else Path(rel).stem)
    out, heading, buf = [], title, []

    def flush():
        passage = "\n".join(buf).strip()
        if len(passage) >= 40:  # a fragment shorter than this is a stray label, not a fact
            out.append({"path": rel, "title": title, "heading": heading, "text": passage})
        buf.clear()

    for para in re.split(r"\n\s*\n", body):
        para = para.strip()
        if not para:
            continue
        h = re.match(r"^#{1,6}\s+(.*)$", para)
        if h:
            flush()
            heading = h.group(1).strip()
            continue
        if buf and sum(len(p) + 1 for p in buf) + len(para) > MAX_CHARS:
            flush()
        buf.append(para)
    flush()
    return out


def _stem(word: str) -> str:
    """A word cut to a short stem, so "archives", "archival" and "archive" match, as do "communicating" and
    "communication"; seven letters keep most words apart and most of their forms together."""
    if len(word) > 4:
        for end in ("ations", "ation", "ments", "ment", "ings", "ing", "ies", "ers", "ed", "es", "er", "al", "ly", "s"):
            if word.endswith(end) and len(word) - len(end) >= 3:
                word = word[:-len(end)] + ("y" if end == "ies" else "")
                break
    if len(word) > 4 and word.endswith("e"):
        word = word[:-1]
    return word[:7]


def words(text: str) -> list[str]:
    """The searchable words of a text: lower case, stop words and possessives dropped, stemmed."""
    text = re.sub(r"['\u2019]s\b", "", text.lower())
    return [_stem(w) for w in re.findall(r"[a-z0-9]+", text) if w not in STOP and len(w) > 1]


# The index ----------------------------------------------------------------------------------

def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _load() -> dict:
    try:
        return json.loads(_live("INDEX").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"manifest": {}, "chunks": []}


def private_folders() -> set[str]:
    """The folder names whose files are never sent to any AI, in lower case."""
    return {str(name).strip().lower() for name in _setting("record.private_folders") or [] if str(name).strip()}


def is_private(rel: str) -> bool:
    """True when a file of the record (a path inside it, with "/") sits in a private folder at any depth."""
    hidden = private_folders()
    return any(part.lower() in hidden for part in rel.split("/")[:-1])


def sync() -> dict:
    """Converts new documents and re-indexes changed notes; returns what changed."""
    if not _live("VAULT").exists():
        raise RecordUnavailable(f"no employment record at {_live('VAULT')}")
    converted = convert()
    index = _load()
    notes = {}
    for path in _walk((".md",)):
        if should_index(path.read_text(encoding="utf-8", errors="replace")):
            notes[path.relative_to(_live("VAULT")).as_posix()] = path
    hashes = {rel: _hash(p) for rel, p in notes.items()}
    changed = [rel for rel, h in hashes.items() if index["manifest"].get(rel) != h]
    gone = set(index["manifest"]) - set(notes)
    if changed or gone:
        drop = set(changed) | gone
        index["chunks"] = [c for c in index["chunks"] if c["path"] not in drop]
        for rel in changed:
            index["chunks"] += chunks(rel, notes[rel].read_text(encoding="utf-8", errors="replace"))
        index["manifest"] = {rel: h for rel, h in index["manifest"].items() if rel not in gone} | \
            {rel: hashes[rel] for rel in changed}
    vectors_changed = _embed_new(index)
    if changed or gone or vectors_changed:
        index["read"] = _now()
        _save(index)
    return {"notes": len(notes), "passages": len(index["chunks"]), "changed": len(changed), "removed": len(gone),
            "converted": [p.relative_to(_live("VAULT")).as_posix() for p in converted]}


def _now() -> str:
    from .core import clock

    return clock.iso()


def _save(index: dict) -> None:
    _live("INDEX").parent.mkdir(parents=True, exist_ok=True)
    tmp = _live("INDEX").with_suffix(".tmp")
    tmp.write_text(json.dumps(index), encoding="utf-8")
    tmp.replace(_live("INDEX"))
    _CACHE.clear()


# Search by meaning ------------------------------------------------------------------------

def _by_meaning() -> bool:
    return _setting("record.search") == "meaning"


def _local_ai():
    """The AI backend when it runs on this computer; None for one that sends text elsewhere."""
    from . import ai

    be = ai.backend()
    return None if getattr(be, "online", True) else be


def _embed_model() -> str:
    return str(_setting("ai.model.embed") or EMBED_MODEL_DEFAULT)


def _passage_key(c: dict) -> str:
    return hashlib.sha256(f"{c['title']} - {c['heading']}\n{c['text']}".encode()).hexdigest()


def _embed(texts: list[str]) -> list[list[float]]:
    """Vectors for `texts` from the AI on this computer; raises `RecordUnavailable` when it cannot answer."""
    from . import ai

    be = _local_ai()
    if be is None:
        raise RecordUnavailable("search by meaning needs the AI on this computer")
    out: list[list[float]] = []
    try:
        for i in range(0, len(texts), EMBED_BATCH):
            out += be.embed(texts[i:i + EMBED_BATCH], _embed_model())
    except ai.LLMUnavailable as e:
        raise RecordUnavailable(f"the AI on this computer did not answer: {e}") from e
    return out


def _embed_new(index: dict) -> bool:
    """Adds vectors for passages that have none, when searching by meaning; private passages never get one."""
    if not _by_meaning():
        return False
    vectors = index.setdefault("vectors", {})
    if index.get("vector_model") != _embed_model():
        vectors.clear()
        index["vector_model"] = _embed_model()
    wanted = {_passage_key(c): c for c in index["chunks"] if not is_private(c["path"])}
    missing = [k for k in wanted if k not in vectors]
    stale = set(vectors) - set(wanted)
    for k in stale:
        del vectors[k]
    if not missing:
        return bool(stale)
    try:
        found = _embed([f"{wanted[k]['title']} - {wanted[k]['heading']}\n{wanted[k]['text']}" for k in missing])
    except RecordUnavailable as e:
        log.info("record vectors not made: %s", e)
        return bool(stale)
    vectors.update(zip(missing, found, strict=True))
    return True


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=False))
    size = math.sqrt(sum(x * x for x in a)) * math.sqrt(sum(y * y for y in b))
    return dot / size if size else 0.0


def _meaning_search(queries: list[str], k: int) -> list[list[dict]] | None:
    """Passages ranked by meaning, or None when there are no vectors or the AI does not answer."""
    index = _searcher()["raw"]
    vectors = index.get("vectors") or {}
    if not vectors or index.get("vector_model") != _embed_model():
        return None
    try:
        asked = _embed(queries)
    except RecordUnavailable as e:
        log.info("searching by words: %s", e)
        return None
    keyed = [(c, vectors.get(_passage_key(c))) for c in index["chunks"]]
    out = []
    for q in asked:
        scored = sorted(((_cosine(q, v), c) for c, v in keyed if v is not None), key=lambda x: -x[0])
        out.append([{**{f: c[f] for f in ("path", "title", "heading", "text")}, "score": round(s, 3), "by": "meaning"}
                    for s, c in scored[:k] if s >= MEANING_MIN])
    return out


# Search by words --------------------------------------------------------------------------

_CACHE: dict = {}


def _searcher() -> dict:
    """The index with each passage's word counts and the collection's statistics, built once per change."""
    if "index" not in _CACHE:
        raw = _load()
        found = raw["chunks"]
        bags = [Counter(words(f"{c['title']} {c['heading']}\n{c['text']}")) for c in found]
        df = Counter(w for bag in bags for w in bag)
        n = len(found)
        lengths = [sum(b.values()) for b in bags]
        _CACHE["index"] = {"raw": raw, "chunks": found, "bags": bags, "lengths": lengths,
                           "avg": sum(lengths) / n if n else 0.0,
                           "idf": {w: math.log(1 + (n - d + 0.5) / (d + 0.5)) for w, d in df.items()}, "n": n}
    return _CACHE["index"]


def search(query: str, k: int = 5, min_score: float = MIN_SCORE, include_private: bool = False) -> list[dict]:
    """The passages of the record that best answer a query, best first, each with its note and heading.

    Passages in private folders are left out unless `include_private`, which only the person's own search
    (the command line) sets.
    """
    return search_many([query], k, min_score, include_private)[0]


def search_many(queries: list[str], k: int = 5, min_score: float = MIN_SCORE,
                include_private: bool = False) -> list[list[dict]]:
    """One search per query: by meaning when chosen and the AI answers, else by words (BM25)."""
    if _by_meaning():
        found = _meaning_search(queries, k)
        if found is not None:
            return found
    s = _searcher()
    results = []
    for query in queries:
        terms = list(dict.fromkeys(words(query)))
        # A word no passage holds still counts in the query's weight, as the rarest kind of word.
        weight = {t: s["idf"].get(t, math.log(1 + (s["n"] + 0.5) / 0.5)) for t in terms}
        total = sum(weight.values())
        hits = []
        for i, bag in enumerate(s["bags"]):
            if not include_private and is_private(s["chunks"][i]["path"]):
                continue
            held = [t for t in terms if t in bag]
            if not held or not total:
                continue
            norm = K1 * (1 - B + B * s["lengths"][i] / (s["avg"] or 1))
            bm25 = sum(weight[t] * bag[t] * (K1 + 1) / (bag[t] + norm) for t in held)
            share = sum(weight[t] for t in held) / total
            if share >= min_score:
                hits.append((share, bm25, i))
        hits.sort(reverse=True)
        results.append([{**{f: s["chunks"][i][f] for f in ("path", "title", "heading", "text")},
                         "score": round(share, 3), "by": "words"} for share, _, i in hits[:k]])
    return results


def context(queries: list[str], per_query: int = 3, limit: int = 6000) -> str:
    """Passages of the record relevant to the queries, as text for a prompt; "" when the record cannot be read.

    Never holds a passage from a private folder.
    """
    try:
        found = search_many([q for q in queries if q.strip()][:20], per_query)
    except (OSError, ValueError, KeyError, TypeError):
        return ""
    seen, lines = set(), []
    for hits in found:
        for h in hits:
            key = (h["path"], h["heading"], h["text"][:80])
            if key in seen or is_private(h["path"]):
                continue
            seen.add(key)
            lines.append(f"[{h['path']} > {h['heading']}]\n{h['text']}")
    return "\n\n".join(lines)[:limit]


def notes_for_ai() -> list[tuple[str, str]]:
    """Every note of the record Hal may give the AI, as (path inside the record, text): no private folder,
    no navigation note. Documents are converted first."""
    if not _live("VAULT").exists():
        return []
    convert()
    out = []
    for path in _walk((".md",)):
        rel = path.relative_to(_live("VAULT")).as_posix()
        text = path.read_text(encoding="utf-8", errors="replace")
        if not is_private(rel) and should_index(text):
            out.append((rel, text))
    return out


ASK_TIMEOUT_S = 300
ASK_MIN = 0.34            # a question is longer than a search; a third of its weight finds the passage


def ask(question: str) -> dict:
    """Answers a question from the record with the AI, citing notes; the passages alone if it cannot run.

    Only passages outside private folders are sent.
    """
    from . import ai
    from .ai import prompts

    hits = search(question, k=6, min_score=ASK_MIN)
    if not hits:
        return {"answer": "Your record has nothing on that yet.", "sources": [], "by": "search"}
    sources = list(dict.fromkeys(h["path"] for h in hits))
    reason = ai.ready(priority=0, purpose="record_ask")
    if reason:
        return {"answer": "", "sources": sources, "passages": hits, "by": "search", "note": reason}
    passages_text = "\n\n".join(f"[{h['path']}] ({h['heading']})\n{h['text']}" for h in hits)
    try:
        answer = ai.chat(prompts.text("record_ask"), f"Question: {question}\n\nPassages:\n{passages_text}",
                         role="checker", timeout=ASK_TIMEOUT_S, purpose="record_ask", priority=0)
    except ai.LLMUnavailable as e:
        return {"answer": "", "sources": sources, "passages": hits, "by": "search", "note": str(e)}
    from .llm import undash

    return {"answer": undash(answer.strip()), "sources": sources, "passages": hits, "by": "model"}


def status() -> dict:
    """How the record stands: where it is, how much it holds, and when it was last indexed."""
    vault = _live("VAULT")
    index = _load() if vault.exists() else {"manifest": {}, "chunks": []}
    return {"vault": str(vault), "exists": vault.exists(), "notes": len(index["manifest"]),
            "passages": len(index["chunks"]), "read": index.get("read"),
            "indexed": date.fromtimestamp(_live("INDEX").stat().st_mtime).isoformat()
            if vault.exists() and _live("INDEX").exists() else None}


NOTE_SUFFIX = re.compile(r" \((PDF|DOCX|PPTX|XLSX|HTML|HTM|EPUB|MSG|TXT|PNG|JPG|JPEG|WEBP|TIF|TIFF)\)\.md$")


def files() -> list[dict]:
    """The person's own files in the record (not the notes Hal wrote beside them), by path inside it."""
    vault = _live("VAULT")
    if not vault.exists():
        return []
    out = []
    for folder, dirs, names in os.walk(vault):
        dirs[:] = sorted(d for d in dirs if d not in SKIP_DIRS and not d.startswith("."))
        for name in sorted(names):
            if name.startswith(".") or NOTE_SUFFIX.search(name):
                continue
            rel = (Path(folder) / name).relative_to(vault).as_posix()
            out.append({"path": rel, "name": name, "group": rel.split("/")[0] if "/" in rel else "",
                        "private": is_private(rel)})
    return out


def file_path(rel: str) -> Path:
    """The file at `rel` inside the record; raises `LookupError` for anything outside it or missing."""
    vault = _live("VAULT").resolve()
    path = (vault / rel).resolve()
    if vault not in path.parents or not path.is_file():
        raise LookupError("That file is not in your record.")
    return path
