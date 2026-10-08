"""Evidence from the University course vault, through Hal's index.

Searches the vault's existing vector index (System/.rag, built by Hal's
vault-index.py) for course material that matches a job requirement. Each hit
names the module, so a draft can say where a skill was studied. Needs numpy
and Ollama's embedding model; returns nothing if either is missing.
"""
import importlib.util
import sys
from pathlib import Path

from .config import CONFIG
from .llm import HAL_ROOT

UNI_VAULT = Path(CONFIG.get("course_vault", "~/vaults/University")).expanduser()
MIN_SCORE = 0.65  # nomic-embed-text similarities run high; below this hits are off-topic


def _hal_rag():
    spec = importlib.util.spec_from_file_location("hal_rag", HAL_ROOT / "hal_rag.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault("hal_rag", module)
    spec.loader.exec_module(module)
    return module


_store = None


def _load():
    global _store
    if _store is None:
        rag = _hal_rag()
        _store = (rag, rag.VectorStore(rag.index_dir_for(UNI_VAULT)).load())
    return _store


def evidence(requirements: list[str], per_requirement: int = 2) -> dict[str, list[dict]]:
    """Returns course-note hits per requirement: module, code, heading, excerpt, score."""
    if not requirements or not (UNI_VAULT / "System" / ".rag").exists():
        return {}
    try:
        rag, store = _load()
        vectors = rag.embed_texts(requirements, kind="query", timeout=60)
    except Exception:  # noqa: BLE001 - coursework evidence is optional
        return {}
    out = {}
    for req, vec in zip(requirements, vectors):
        hits = [h for h in store.search(vec, k=8)
                if h["path"].startswith("Areas/Study/") and h["score"] >= MIN_SCORE]
        seen, picked = set(), []
        for h in hits:
            if h["course"] in seen:
                continue
            seen.add(h["course"])
            picked.append({"module": h["course"], "code": h["code"], "heading": h["heading"],
                           "excerpt": h["text"][:300], "score": round(h["score"], 2)})
            if len(picked) == per_requirement:
                break
        if picked:
            out[req] = picked
    return out


def as_text(found: dict[str, list[dict]]) -> str:
    """Formats evidence as plain text for a prompt."""
    lines = []
    for req, hits in found.items():
        for h in hits:
            lines.append(f"- {req} <- {h['module']} ({h['code']}), \"{h['heading']}\": {h['excerpt'][:200]}")
    return "\n".join(lines)
