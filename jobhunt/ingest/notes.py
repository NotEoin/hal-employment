"""Writing the ingest's notes into the record: source notes, entity notes, Index notes and links between them.

Layout, inside the record (an Obsidian vault):

- `Sources/<Category>/<Title>.md`, one per document or unit, with list frontmatter (`frontmatter.py`):
  type source, category, doc_type, title, tags, period, organisations, skills, about_owner, source, sha256,
  ingested (UTC, with Z) and `hal: ingested`, or `hal: needs-review` when a quote was not found or it is
  unclear whose the document is. The body is the summary, the key facts with their quotes, links, the
  original, then the converted text.
- Entity notes in `Organisations/`, `Places/`, `People/`, `Projects/`, `Courses/` and `Skills/`, each
  `type: entity` with a "Mentioned in" list. The record's search skips them (`record.SKIP_TYPE`).
- `Sources/<Category>/Index.md` and `Sources/Index.md`: plain links, no Dataview, `type: index`.
- Originals are copied to `record.ORIGINALS` (`Attachments/Sources/<Category>/`) when
  `record.copy_originals` is on and the file is at most `COPY_MAX_BYTES`; larger ones are named by path.

File names are ASCII (`slug`), so a note's name works in every file system and in a wikilink. The link
pass puts a wikilink on the first mention of each known entity or source title in a new note: longest titles
first, never inside a heading, a code block or an existing link, and never to the note itself.
"""
from __future__ import annotations

import re
import shutil
import unicodedata
from pathlib import Path

from jobhunt import record
from jobhunt.core import clock
from jobhunt.ingest import frontmatter

SOURCES = "Sources"
INDEX = "Index"
COPY_MAX_BYTES = 100 * 1024 ** 2   # 100 MiB: a scanned portfolio fits; a lecture recording is linked by path
TITLE_CHARS = 80                   # file names stay well under Windows' 260-character path limit
LINK_MIN_CHARS = 4                 # "Art" or "UCL" would link half the words of a note
LINKS_MAX = 60                     # per note: past this a note is more blue than black
ENTITY_FOLDERS = {"organisations": "Organisations", "places": "Places", "people": "People",
                  "projects": "Projects", "courses": "Courses", "skills": "Skills"}
ENTITY_WORD = {"organisations": "organisation", "places": "place", "people": "person", "projects": "project",
               "courses": "course", "skills": "skill"}
MENTIONED = "## Mentioned in"
UNSAFE = re.compile(r'[\\/:*?"<>|#^\[\]]+')
FACT_FIELDS = {"job": ("title", "employer", "start", "end"), "education": ("qualification", "subject", "institution",
               "grade"), "skill": ("name", "level"), "project": ("name", "role", "period"),
               "achievement": ("title", "date"), "certificate": ("name", "issuer", "date"),
               "basic": ("name", "email", "phone", "location"), "link": ("label", "url")}
# Parts of a note the link pass never changes: code, existing links, headings and the frontmatter.
PROTECTED = re.compile(r"```.*?```|`[^`\n]*`|\[\[[^\]]*\]\]|\[[^\]]*\]\([^)]*\)|^#[^\n]*$|https?://\S+", re.S | re.M)


def slug(title: str) -> str:
    """`title` as an ASCII file name: accents dropped, characters a file system or link refuses removed."""
    ascii_text = unicodedata.normalize("NFKD", str(title)).encode("ascii", "ignore").decode("ascii")
    clean = " ".join(UNSAFE.sub(" ", ascii_text).split()).strip(" .")
    clean = clean[:TITLE_CHARS].rstrip(" .")
    if not clean:
        return "Untitled"
    return f"{clean} (document)" if clean.lower() == INDEX.lower() else clean


def _rel(vault: Path, path: Path) -> str:
    return path.relative_to(vault).as_posix()


def link(rel: str, shown: str | None = None) -> str:
    """A wikilink to the note at `rel` (inside the record), shown as `shown` or its name."""
    target = rel[:-3] if rel.endswith(".md") else rel
    name = shown or Path(target).name
    return f"[[{target}|{name}]]" if name != target else f"[[{target}]]"


def free_note(folder: Path, title: str, sha256: str) -> Path:
    """The path for a note titled `title`: the same file when it holds this document, else a free name."""
    base, n = slug(title), 2
    path = folder / f"{base}.md"
    while path.exists():
        fm, _ = frontmatter.parse(path.read_text(encoding="utf-8", errors="replace"))
        if sha256 and fm.get("sha256") == sha256:
            return path
        path, n = folder / f"{base} ({n}).md", n + 1
    return path


def keep_original(vault: Path, original: Path, category: str, *, move: bool = False) -> str | None:
    """Copies (or moves) an original into the record's originals folder; its path inside the record, or None.

    None when copies are off or the file is past `COPY_MAX_BYTES`. A file already there with the same name
    and size is taken as the same copy.
    """
    from jobhunt.core import settings

    if not settings.get("record.copy_originals") or original.stat().st_size > COPY_MAX_BYTES:
        return None
    folder = vault / record.ORIGINALS / slug(category)
    folder.mkdir(parents=True, exist_ok=True)
    stem, suffix = slug(original.stem), original.suffix.lower()
    target, n = folder / f"{stem}{suffix}", 2
    while target.exists() and target.stat().st_size != original.stat().st_size:
        target, n = folder / f"{stem} ({n}){suffix}", n + 1
    if not target.exists():
        if move:
            shutil.move(str(original), target)
        else:
            shutil.copy2(original, target)
    elif move:
        original.unlink()
    return _rel(vault, target)


def _fact_line(f: dict) -> str:
    fields = f.get("fields") or {}
    shown = [str(fields.get(k)).strip() for k in FACT_FIELDS.get(f["kind"], ()) if str(fields.get(k) or "").strip()]
    line = f"{f['kind'].capitalize()}: {', '.join(shown) or f.get('field') or 'a fact'}"
    quote = f' "{f["quote"]}"' if f.get("quote") else ""
    if f.get("verified"):
        return f"- {line}.{quote}"
    return f"- **Check this:** {line}.{quote} (these words were not found in the document)"


def needs_review(desc: dict) -> bool:
    """True when a person should look at the note: a quote not found, or a rich document of unclear owner."""
    return any(not f["verified"] for f in desc["facts"]) or (desc.get("by") == "model"
                                                             and desc["about_owner"] == "unsure")


def entity_rel(kind: str, name: str) -> str:
    """The path inside the record of the entity note for `name`."""
    return f"{ENTITY_FOLDERS[kind]}/{slug(name)}.md"


def write_source(vault: Path, desc: dict, *, text: str, source: str, sha256: str, original: str | None,
                 extra_links: list[str] = ()) -> str:
    """Writes the note for one document or unit; returns its path inside the record."""
    folder = vault / SOURCES / slug(desc["category"])
    folder.mkdir(parents=True, exist_ok=True)
    path = free_note(folder, desc["title"], sha256)
    ents = desc["entities"]
    fields = {"type": "source", "category": desc["category"], "doc_type": desc["doc_type"], "title": desc["title"],
              "tags": desc["tags"], "period": desc["period"], "organisations": ents["organisations"],
              "skills": ents["skills"], "about_owner": desc["about_owner"], "source": source, "sha256": sha256,
              "ingested": clock.iso(), "hal": "needs-review" if needs_review(desc) else "ingested"}
    lines = [f"# {desc['title']}", ""]
    if desc["summary"]:
        lines += [desc["summary"], ""]
    if desc.get("private"):
        lines += ["This file holds one of your private answers, so Hal did not send it to the AI.", ""]
    if desc["facts"]:
        lines += ["## Key facts", "", *(_fact_line(f) for f in desc["facts"]), ""]
    linked = [f"- {ENTITY_FOLDERS[k]}: " + ", ".join(link(entity_rel(k, n), n) for n in ents[k])
              for k in ENTITY_FOLDERS if ents.get(k)]
    if linked or extra_links:
        lines += ["## Links", "", *linked, *extra_links, ""]
    lines += ["## Original", "", f"![[{original}]]" if original and original.lower().endswith(
        (".png", ".jpg", ".jpeg", ".webp", ".gif")) else (f"[[{original}]]" if original else f"`{source}`"), ""]
    if text:
        lines += ["## Text", "", text, ""]
    path.write_text(frontmatter.dump(fields) + "\n".join(lines), encoding="utf-8")
    return _rel(vault, path)


def add_mentions(vault: Path, desc: dict, note_rel: str) -> list[str]:
    """Adds `note_rel` to the "Mentioned in" list of each entity the document names; returns their paths."""
    out = []
    for kind, names in desc["entities"].items():
        for name in names:
            if len(slug(name)) < 2 or slug(name) == "Untitled":
                continue
            rel = entity_rel(kind, name)
            path = vault / rel
            line = f"- {link(note_rel, desc['title'])}"
            if path.exists():
                text = path.read_text(encoding="utf-8", errors="replace")
                if line in text:
                    out.append(rel)
                    continue
                text = text.rstrip("\n") + ("\n" if MENTIONED in text else f"\n\n{MENTIONED}\n\n") + line + "\n"
            else:
                path.parent.mkdir(parents=True, exist_ok=True)
                text = (frontmatter.dump({"type": "entity", "entity": ENTITY_WORD[kind], "title": name,
                                          "tags": [ENTITY_WORD[kind]]})
                        + f"# {name}\n\n{MENTIONED}\n\n{line}\n")
            path.write_text(text, encoding="utf-8")
            out.append(rel)
    return out


def write_indexes(vault: Path) -> list[str]:
    """Rewrites `Sources/Index.md` and each category's `Index.md` from the notes there; returns their paths."""
    root = vault / SOURCES
    if not root.is_dir():
        return []
    written, top = [], []
    for folder in sorted(p for p in root.iterdir() if p.is_dir()):
        notes = sorted(p for p in folder.glob("*.md") if p.stem != INDEX)
        if not notes:
            continue
        rows = []
        for p in notes:
            fm, _ = frontmatter.parse(p.read_text(encoding="utf-8", errors="replace"))
            rows.append(f"- {link(_rel(vault, p), str(fm.get('title') or p.stem))}")
        index = folder / f"{INDEX}.md"
        index.write_text(frontmatter.dump({"type": "index", "title": folder.name, "tags": ["index"]})
                         + f"# {folder.name}\n\n" + "\n".join(rows) + "\n", encoding="utf-8")
        written.append(_rel(vault, index))
        top.append(f"- {link(_rel(vault, index), folder.name)} ({len(notes)})")
    index = root / f"{INDEX}.md"
    index.write_text(frontmatter.dump({"type": "index", "title": "Sources", "tags": ["index"]})
                     + "# Sources\n\n" + "\n".join(top) + "\n", encoding="utf-8")
    return [*written, _rel(vault, index)]


def known_titles(vault: Path) -> dict[str, str]:
    """Every entity and source note's title (lower case) and its path inside the record."""
    out = {}
    folders = [vault / f for f in ENTITY_FOLDERS.values()] + [vault / SOURCES]
    for folder in folders:
        if not folder.is_dir():
            continue
        for p in folder.rglob("*.md"):
            if p.stem == INDEX:
                continue
            fm, _ = frontmatter.parse(p.read_text(encoding="utf-8", errors="replace"))
            title = str(fm.get("title") or p.stem).strip()
            if len(title) >= LINK_MIN_CHARS:
                out.setdefault(title.lower(), _rel(vault, p))
    return out


def link_note(text: str, titles: dict[str, str], own: str) -> str:
    """`text` with a wikilink on the first mention of each title in `titles`, outside protected parts."""
    fm_end = 0
    if text.startswith(frontmatter.FENCE):
        end = text.find("\n" + frontmatter.FENCE, len(frontmatter.FENCE))
        fm_end = end + len(frontmatter.FENCE) + 1 if end != -1 else 0
    head, body = text[:fm_end], text[fm_end:]
    wanted = {t: rel for t, rel in titles.items() if rel != own}
    if not wanted:
        return text
    pattern = re.compile(r"(?<![\w\[])(" + "|".join(re.escape(t) for t in sorted(wanted, key=len, reverse=True))
                         + r")(?![\w\]])", re.I)
    done: set[str] = set()

    def swap(segment: str) -> str:
        def one(m: re.Match) -> str:
            key = m.group(1).lower()
            if key in done or len(done) >= LINKS_MAX:
                return m.group(0)
            done.add(key)
            return link(wanted[key], m.group(1))
        return pattern.sub(one, segment)

    out, last = [], 0
    for m in PROTECTED.finditer(body):
        out += [swap(body[last:m.start()]), m.group(0)]
        last = m.end()
    out.append(swap(body[last:]))
    return head + "".join(out)


def link_pass(vault: Path, note_rels: list[str]) -> int:
    """Links the first mention of each known title in the notes at `note_rels`; returns how many changed."""
    titles = known_titles(vault)
    changed = 0
    for rel in note_rels:
        path = vault / rel
        if not path.is_file():
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        new = link_note(text, titles, rel)
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed += 1
    return changed
