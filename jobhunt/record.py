"""The employment record: a vault of everything about the applicant, searched whenever Hal writes.

The CV is a polished summary; the record holds the rest: certificates, transcripts,
references, awards, the full story of each job and project. It is its own Obsidian
vault (config.toml's `record_vault`, by default ~/vaults/Employment-Record),
kept apart from the main Hal vault so that health, personal and planning notes can
never be quoted to an employer. Hal Employment reads this vault and nothing else.

Documents dropped in (PDF, Word, PowerPoint, Excel, web pages, saved emails) are
turned into Markdown notes beside them by Microsoft's MarkItDown, keeping their
headings and tables, so they can be searched in sensible sections. Chunking and
the vector store are Hal's own (hal_rag.py), and the index sits where Hal keeps
every vault's (System/.rag), so Hal's tools can search the record too
(`vault-index.py --vault`, ask.py, the MCP server). Embedding is done here rather
than by hal_rag so it can run on the CPU (num_gpu 0): a lookup never competes for
the graphics card. The vectors match Hal's: same model, same prefixes.

Needs numpy (for hal_rag) and `markitdown[pdf,docx,pptx,xlsx,outlook]` in the venv,
and `pdftotext` (poppler) on the PATH.
"""
import importlib.util
import json
import os
import re
import subprocess
import sys
import urllib.request
from datetime import date
from pathlib import Path

from .config import CONFIG
from .llm import HAL_ROOT

VAULT = Path(CONFIG.get("record_vault", "~/vaults/Employment-Record")).expanduser()
OLLAMA = (CONFIG.get("ollama_url") or "http://localhost:11434").rstrip("/")
MODEL = CONFIG.get("record_embed_model", "nomic-embed-text")
# What MarkItDown reads; Markdown notes themselves are indexed as they are.
DOCUMENTS = (".pdf", ".docx", ".pptx", ".xlsx", ".html", ".htm", ".epub", ".msg", ".txt")
MIN_SCORE = 0.55  # nomic-embed-text similarities run high; below this a passage is off the subject


def _hal_rag():
    """Hal's retrieval library, loaded from Hal's own folder (config.toml's `hal_root`)."""
    if "hal_rag" in sys.modules:
        return sys.modules["hal_rag"]
    spec = importlib.util.spec_from_file_location("hal_rag", HAL_ROOT / "hal_rag.py")
    if spec is None or not (HAL_ROOT / "hal_rag.py").exists():
        raise RecordUnavailable(f"Hal's retrieval library is not at {HAL_ROOT / 'hal_rag.py'}")
    module = importlib.util.module_from_spec(spec)
    sys.modules["hal_rag"] = module
    spec.loader.exec_module(module)
    return module


class RecordUnavailable(Exception):
    pass


# Turning documents into notes ---------------------------------------------------------------

_MARKITDOWN = None


def _text_of(path: Path) -> str:
    """A document as Markdown, keeping its headings, lists and tables; pdftotext for a PDF without headings.

    MarkItDown is called only through convert_local on files inside the vault, with plugins off and no
    cloud service configured, so a document is never fetched from or sent anywhere.
    """
    global _MARKITDOWN
    text = ""
    try:
        if _MARKITDOWN is None:
            from markitdown import MarkItDown
            _MARKITDOWN = MarkItDown(enable_plugins=False)
        text = _MARKITDOWN.convert_local(str(path)).markdown or ""
    except Exception:  # noqa: BLE001 - any converter failure falls back below, or leaves the file unread
        text = ""
    # On a PDF without headings MarkItDown parts a form's labels from their values, and its guessed tables
    # run a transcript's columns together ("10 76" for credits and mark); pdftotext's layout mode keeps
    # each row on one line with its columns apart. Pipe tables are therefore no sign of structure here.
    if path.suffix.lower() == ".pdf" and (len(text.strip()) < 50 or not re.search(r"^#{1,6} ", text, re.M)):
        out = subprocess.run(["pdftotext", "-layout", str(path), "-"], capture_output=True, timeout=60)
        text = out.stdout.decode(errors="replace") if out.returncode == 0 and out.stdout.strip() else text
    if not text.strip() and path.suffix.lower() in (".txt", ".md"):
        text = path.read_text(errors="replace")
    return text


def convert() -> list[Path]:
    """Writes a note beside each document that has none, or whose document changed; returns the notes written."""
    written = []
    for doc in _walk(DOCUMENTS):
        note = doc.with_name(f"{doc.stem} ({doc.suffix[1:].upper()}).md")
        if note.exists() and note.stat().st_mtime >= doc.stat().st_mtime:
            continue
        # Runs of spaces inside a line are kept: in pdftotext's layout they are what keeps columns apart.
        text = re.sub(r"[ \t]+\n", "\n", _text_of(doc).replace("\f", "\n"))
        text = re.sub(r"\n{3,}", "\n\n", text).strip()
        if not text:
            continue
        note.write_text(f"---\ntype: document\nsource: \"[[{doc.name}]]\"\nconverted: {date.today().isoformat()}\n---\n"
                        f"# {doc.stem}\n\nText of [[{doc.name}]], read by Hal Employment.\n\n{text}\n", encoding="utf-8")
        written.append(note)
    return written


def _walk(suffixes: tuple) -> list[Path]:
    rag = _hal_rag()
    found = []
    for folder, dirs, files in os.walk(VAULT):
        dirs[:] = [d for d in dirs if d not in rag.SKIP_DIRS and not d.startswith(".")]
        found += [Path(folder) / f for f in files if f.lower().endswith(suffixes)]
    return sorted(found)


def _embed(texts: list[str], kind: str) -> list[list[float]]:
    """Embeds texts with nomic-embed-text on the CPU (num_gpu 0), with the task prefixes Hal's index uses."""
    prefix = "search_query: " if kind == "query" else "search_document: "
    out = []
    for i in range(0, len(texts), 32):
        body = json.dumps({"model": MODEL, "input": [prefix + t for t in texts[i:i + 32]],
                           "options": {"num_gpu": 0}, "keep_alive": "10m"}).encode()
        req = urllib.request.Request(f"{OLLAMA}/api/embed", data=body, headers={"Content-Type": "application/json"})
        try:
            with urllib.request.urlopen(req, timeout=300) as r:
                out += json.loads(r.read())["embeddings"]
        except (OSError, KeyError, ValueError) as e:
            raise RecordUnavailable(f"the embedding model did not answer (is Ollama running?): {e}") from e
    return out


def _store():
    rag = _hal_rag()
    return rag.VectorStore(rag.index_dir_for(VAULT)).load()


def sync() -> dict:
    """Converts new documents and re-indexes changed notes; returns what changed."""
    if not VAULT.exists():
        raise RecordUnavailable(f"no employment record at {VAULT}")
    rag = _hal_rag()
    converted = convert()
    store = _store()
    notes = dict(rag.iter_vault_notes(VAULT))
    hashes = {rel: rag.file_hash(p) for rel, p in notes.items()}
    changed = [rel for rel, h in hashes.items() if store.manifest.get(rel) != h]
    gone = set(store.manifest) - set(notes)
    if changed or gone:
        store.drop_paths(set(changed) | gone)
        # No overlap: hal_rag carries a passage's tail into the next section under the new heading, which
        # would file one year's module marks under the next year's.
        chunks = [c for rel in changed
                  for c in rag.chunk_note(rel, notes[rel].read_text(encoding="utf-8", errors="replace"), overlap=0)]
        if chunks:
            store.add(chunks, _embed([f"{c.title} - {c.heading}\n{c.text}" for c in chunks], "document"))
        store.manifest.update({rel: hashes[rel] for rel in changed})
        store.save()
        _CACHE.clear()
    return {"notes": len(notes), "passages": len(store.chunks), "changed": len(changed), "removed": len(gone),
            "converted": [str(p.relative_to(VAULT)) for p in converted]}


_CACHE: dict = {}


def search(query: str, k: int = 5, min_score: float = MIN_SCORE) -> list[dict]:
    """The passages of the record that best answer a query, best first, each with its note and heading."""
    return search_many([query], k, min_score)[0]


def search_many(queries: list[str], k: int = 5, min_score: float = MIN_SCORE) -> list[list[dict]]:
    """One search per query, embedding them together."""
    if not queries:
        return []
    if "store" not in _CACHE:
        _CACHE["store"] = _store()
    store = _CACHE["store"]
    if not store.chunks:
        return [[] for _ in queries]
    keep = ("path", "title", "heading", "text", "score")
    return [[{f: h[f] for f in keep} | {"score": round(h["score"], 3)} for h in store.search(vec, k=k)
             if h["score"] >= min_score] for vec in _embed(queries, "query")]


def context(queries: list[str], per_query: int = 3, limit: int = 6000) -> str:
    """Passages of the record relevant to the queries, as text for a prompt; "" when the record cannot be read."""
    try:
        found = search_many([q for q in queries if q.strip()][:20], per_query)
    except RecordUnavailable:
        return ""
    seen, lines = set(), []
    for hits in found:
        for h in hits:
            key = (h["path"], h["heading"], h["text"][:80])
            if key in seen:
                continue
            seen.add(key)
            lines.append(f"[{h['path']} > {h['heading']}]\n{h['text']}")
    text = "\n\n".join(lines)
    return text[:limit]


ASK = """You answer questions about a job applicant from passages of their own employment record.
Use only the passages. After each fact, cite the note it came from in square brackets, as given,
for example [Certificates/CITI.md]. If the passages do not answer the question, say so plainly
and suggest what the applicant could add to their record. Be brief."""


def ask(question: str) -> dict:
    """Answers a question from the record with the local model, citing notes; the passages alone if it cannot run."""
    from . import gpu, llm
    hits = search(question, k=6, min_score=0.45)
    if not hits:
        return {"answer": "Your record has nothing on that yet.", "sources": [], "by": "search"}
    reason = gpu.busy()
    sources = list(dict.fromkeys(h["path"] for h in hits))
    if reason:
        return {"answer": "", "sources": sources, "passages": hits, "by": "search", "note": reason}
    passages_text = "\n\n".join(f"[{h['path']}] ({h['heading']})\n{h['text']}" for h in hits)
    try:
        answer = llm.chat(ASK, f"Question: {question}\n\nPassages:\n{passages_text}", role="main", timeout=300)
    except llm.LLMUnavailable as e:
        return {"answer": "", "sources": sources, "passages": hits, "by": "search", "note": str(e)}
    return {"answer": llm.undash(answer.strip()), "sources": sources, "passages": hits, "by": "model"}


def status() -> dict:
    """How the record stands: where it is, how much it holds, and when it was last indexed."""
    try:
        store = _store() if VAULT.exists() else None
    except RecordUnavailable:
        store = None
    manifest = _hal_rag().index_dir_for(VAULT) / "manifest.json" if store is not None else None
    return {"vault": str(VAULT), "exists": VAULT.exists(), "notes": len(store.manifest) if store else 0,
            "passages": len(store.chunks) if store else 0,
            "indexed": date.fromtimestamp(manifest.stat().st_mtime).isoformat() if manifest and manifest.exists() else None}
