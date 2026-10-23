"""Writes the owner's search preferences and form facts, read from the old code, for the one-time import.

In Hal's first version the owner's preferences were constants in the code: the pay floor, the places and the
skill weights in `score.py`, the aggregator searches, the email address in `gmail.py`, and the form facts
in `autofill.py`'s `profile()`. The code is now generic, so this script reads it once, when moving to the
new version, from a fixed git ref (ef53088 by default), and writes `<config>/import/owner-prefs.toml`,
which `python -m jobhunt import` reads.

    python scripts/dev/export_owner_prefs.py [--repo PATH] [--ref REF] [--out FILE]

Design notes:
- The old modules are parsed with `ast`, never imported: importing them would read the private answers
  (`profile()` merges `private()`), the website repo and the old config at import time.
- Only literal values are taken. `profile()` entries built at run time (name, email, phone, links) come
  from the CV contact module and the website's `cv.json` instead, as the old code built them.
- The private answers are never read: this script never opens `private.toml`, never calls `private()`,
  and leaves out the `HEALTH` keys and `carer` from every table it writes.
- Place names are found from `place()`'s own `if re.search(CONSTANT, ...): return "Name", points` lines, so
  nothing here names the owner's places.
- Prints the output path and counts only, never a value.
"""
from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DEFAULT_REF = "ef53088"
GIT_TIMEOUT_S = 30         # `git show` of one small file; a slower answer means git is stuck
PRIVATE_KEYS = ("carer", "disability", "disability_type", "guaranteed_interview", "adjustments")
CV_JSON = ("src", "data", "cv.json")
CONTACT_MODULE = ("tools", "cv_contact.py")
LINK_FORMS = {"linkedin": "https://www.{}", "github": "https://{}", "website": "https://{}"}
LINK_SOURCE = {"linkedin": "linkedin", "github": "github", "website": "site"}


class ExportError(RuntimeError):
    """The old code is not in the shape this script reads; the message names what is missing."""


def read_source(repo: Path, ref: str | None, rel: str) -> str | None:
    """A file of the old code: from git at `ref`, or the working tree when `ref` is empty. None when absent."""
    if not ref:
        path = repo / rel
        return path.read_text(encoding="utf-8") if path.is_file() else None
    done = subprocess.run(["git", "-C", str(repo), "show", f"{ref}:{rel}"], capture_output=True, text=True,
                          timeout=GIT_TIMEOUT_S, check=False)
    return done.stdout if done.returncode == 0 else None


def _module(repo: Path, ref: str | None, rel: str) -> ast.Module:
    text = read_source(repo, ref, rel)
    if text is None:
        raise ExportError(f"{rel} is not in the old code at {ref or 'the working tree'}")
    return ast.parse(text, filename=rel)


def constants(tree: ast.Module) -> dict[str, object]:
    """Module-level `NAME = <literal>` assignments (strings, numbers, lists, dicts), by name."""
    out: dict[str, object] = {}
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = ast.literal_eval(node.value)
            except ValueError:
                continue
    return out


def _function(tree: ast.Module, name: str) -> ast.FunctionDef:
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name == name:
            return node
    raise ExportError(f"the old code has no {name}()")


def _config_default(tree: ast.Module, key: str) -> str | None:
    """The default in a `CONFIG.get("<key>", "<default>")` call anywhere in the module."""
    for node in ast.walk(tree):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get"
                and len(node.args) == 2 and isinstance(node.args[0], ast.Constant) and node.args[0].value == key
                and isinstance(node.args[1], ast.Constant)):
            return str(node.args[1].value)
    return None


def _searched_constant(test: ast.expr) -> str | None:
    """`X` in a test of the form `re.search(X, ...)` (possibly under `not`)."""
    for node in ast.walk(test):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "search"
                and node.args and isinstance(node.args[0], ast.Name)):
            return node.args[0].id
    return None


def places(tree: ast.Module) -> list[tuple[str, str, int]]:
    """(constant, place name, points) from `place()`, in the order it checks them (first is preferred)."""
    found = []
    for node in _function(tree, "place").body:
        if not isinstance(node, ast.If):
            continue
        name = _searched_constant(node.test)
        ret = node.body[0] if node.body else None
        if name and isinstance(ret, ast.Return) and isinstance(ret.value, ast.Tuple):
            place, points = (ast.literal_eval(e) for e in ret.value.elts)
            found.append((name, str(place), int(points)))
    found.sort(key=lambda p: -p[2])
    return found


def training_places(tree: ast.Module) -> list[str]:
    """Constants of the `if train_and_place and not re.search(X, ...)` rule."""
    for node in ast.walk(_function(tree, "score")):
        if isinstance(node, ast.If) and "train_and_place" in ast.unparse(node.test):
            name = _searched_constant(node.test)
            if name:
                return [name]
    return []


def _first_alternative(match: re.Match) -> str:
    return match.group(1).split("|")[0]


def _top_level(pattern: str) -> list[str]:
    """The alternatives of a pattern, splitting on `|` outside parentheses only."""
    parts, depth, current = [], 0, ""
    for ch in pattern:
        depth += {"(": 1, ")": -1}.get(ch, 0)
        if ch == "|" and depth == 0:
            parts.append(current)
            current = ""
        else:
            current += ch
    return [*parts, current]


def words_of(pattern: str) -> list[str]:
    """The plain names a `\\bname\\b|\\bother\\b` pattern matches, in order: `kings? cross` -> `kings cross`."""
    out = []
    for part in _top_level(pattern):
        text = part.replace("\\b", "")
        text = re.sub(r"\(([^()]*)\)", _first_alternative, text)
        text = text.replace("?", "").replace("\\", "").strip()
        if text and text not in out:
            out.append(text)
    return out


def skill_lines(skills: dict[str, int]) -> list[str]:
    """`scoring.skills` lines: `python = 3` for a plain word, `re:<pattern> = 3` for anything else."""
    lines = []
    for pattern, points in skills.items():
        plain = re.fullmatch(r"\\b([a-z0-9 +#.-]+)\\b", pattern)
        word = plain.group(1).replace("\\", "") if plain else None
        lines.append(f"{word} = {points}" if word and "+" not in word else f"re:{pattern} = {points}")
    return lines


def profile_literals(tree: ast.Module) -> dict[str, str]:
    """The literal string entries of `profile()`'s dict, without the private keys."""
    health = constants(tree).get("HEALTH") or ()
    for node in ast.walk(_function(tree, "profile")):
        if isinstance(node, ast.Dict) and node.keys:
            out = {}
            for k, v in zip(node.keys, node.values, strict=True):
                if isinstance(k, ast.Constant) and isinstance(v, ast.Constant) and isinstance(v.value, str):
                    if k.value not in PRIVATE_KEYS and k.value not in health:
                        out[str(k.value)] = v.value
            return out
    raise ExportError("profile() has no dict of answers")


def _config(repo: Path, ref: str | None) -> dict:
    """The old config.toml: the working tree's copy, else the one at `ref`."""
    text = read_source(repo, None, "config.toml") or read_source(repo, ref, "config.toml") or ""
    return tomllib.loads(text)


def links(site: Path) -> dict[str, str]:
    """linkedin, github and website as the old `profile()` built them from cv.json's `contact`."""
    file = site.joinpath(*CV_JSON)
    if not file.is_file():
        return {}
    contact = json.loads(file.read_text(encoding="utf-8")).get("contact") or {}
    return {key: LINK_FORMS[key].format(contact[src]) for key, src in LINK_SOURCE.items() if contact.get(src)}


def collect(repo: Path, ref: str | None) -> dict[str, dict]:
    """Every table of owner-prefs.toml, read from the old code at `ref`."""
    score = _module(repo, ref, "jobhunt/score.py")
    score_consts = constants(score)
    found = places(score)
    by_constant = {c: name for c, name, _ in found}
    aliases = []
    for constant, name, _ in found:
        words = [w for w in words_of(str(score_consts.get(constant, ""))) if w.lower() != name.lower()]
        if words:
            aliases.append(f"{name}: {', '.join(words)}")
    goal: dict[str, object] = {"places": [name for _, name, _ in found], "place_aliases": aliases,
                               "training_ok_places": [by_constant[c] for c in training_places(score)
                                                      if c in by_constant]}
    if "SALARY_FLOOR" in score_consts:
        goal["min_pay"] = int(score_consts["SALARY_FLOOR"])
    scoring = {"skills": skill_lines(dict(score_consts.get("SKILLS") or {}))}
    agg = constants(_module(repo, ref, "jobhunt/sources/aggregators.py"))
    sources = {"queries": list(agg.get("QUERIES") or []), "places": list(agg.get("PLACES") or [])}
    email = {}
    address = constants(_module(repo, ref, "jobhunt/gmail.py")).get("ADDRESS")
    if address:
        email["address"] = str(address)

    config = _config(repo, ref)
    site = config.get("cv_site") or _config_default(_module(repo, ref, "jobhunt/cv.py"), "cv_site")
    hal_root = config.get("hal_root") or _config_default(_module(repo, ref, "jobhunt/llm.py"), "hal_root")
    meta = {"ref": ref or "working tree"}
    if site:
        meta["cv_site"] = str(site)
    if hal_root:
        meta["hal_root"] = str(hal_root)

    you = profile_literals(_module(repo, ref, "jobhunt/autofill.py"))
    if site:
        you.update(links(Path(str(site)).expanduser()))
    you.update({k: str(v) for k, v in (config.get("applicant") or {}).items() if k not in PRIVATE_KEYS})
    return {"import": meta, "goal": goal, "scoring": scoring, "email": email, "sources": sources, "you": you}


def _toml_value(value: object) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(v) for v in value) + "]"
    return json.dumps(str(value), ensure_ascii=True)   # a JSON string is a valid TOML basic string


def to_toml(tables: dict[str, dict]) -> str:
    """The tables as TOML, with a header saying what the file is."""
    lines = ["# The owner's preferences, read from the old code by scripts/dev/export_owner_prefs.py.",
             "# Read once by `python -m jobhunt import`. Never holds a private answer.", ""]
    for name, table in tables.items():
        lines.append(f"[{name}]")
        lines += [f"{key} = {_toml_value(value)}" for key, value in table.items()]
        lines.append("")
    return "\n".join(lines)


def default_out() -> Path:
    """`<config>/import/owner-prefs.toml`, outside the repo."""
    from jobhunt.core import paths

    return paths.config_dir() / "import" / "owner-prefs.toml"


def main(argv: list[str] | None = None) -> int:
    """Entry point: writes the file and prints its path and counts."""
    parser = argparse.ArgumentParser(description="Write the owner's preferences from the old code for the import.")
    parser.add_argument("--repo", type=Path, default=ROOT, help="the checkout that holds the old code")
    parser.add_argument("--ref", default=DEFAULT_REF, help="git ref of the old code; empty reads the working tree")
    parser.add_argument("--out", type=Path, help="where to write (default: <config>/import/owner-prefs.toml)")
    args = parser.parse_args(argv)
    try:
        tables = collect(args.repo, args.ref)
    except (ExportError, subprocess.TimeoutExpired, OSError, tomllib.TOMLDecodeError, SyntaxError) as e:
        print(f"Could not read the old preferences: {e}", file=sys.stderr)
        return 1
    out = args.out or default_out()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(to_toml(tables), encoding="utf-8")
    out.chmod(0o600)
    counts = ", ".join(f"{name} {len(table)}" for name, table in tables.items())
    print(f"Wrote {out} ({counts}).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
