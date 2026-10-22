"""Gate G7: code reads only registered settings, the old `CONFIG` only where a module has not
been converted yet, and Hal never sets the goal, the scoring or the CV by itself.

`LEGACY_ALLOWED` holds today's `CONFIG` reads as (file, old key) pairs; "*" stands for a key built at run time.
Converting a module removes its entries, until the list is empty. An entry no
code uses any more fails too, so the list only ever shrinks.
"""
import ast
import unittest
from pathlib import Path

import support  # noqa: F401 - sets the temporary home before jobhunt loads

from jobhunt.core import paths, settings

ROOT = Path(paths.code_dir())
JOBHUNT = ROOT / "jobhunt"
SHIM = "jobhunt/config.py"
READS = ("get", "set", "reset")
PERSON_ONLY = settings.PERSON_ONLY
LEGACY_ALLOWED = {
    ("jobhunt/autofill.py", "applicant"),
    ("jobhunt/browser.py", "apply_browser"),
    ("jobhunt/cli/find.py", "alert_score"),
    ("jobhunt/cli/find.py", "web_port"),
    ("jobhunt/coursework.py", "course_vault"),
    ("jobhunt/cv.py", "cv_site"),
    ("jobhunt/digest.py", "digest_to"),
    ("jobhunt/extension.py", "apply_window"),
    ("jobhunt/llm.py", "hal_root"),
    ("jobhunt/notify.py", "ntfy_topic"),
    ("jobhunt/notify.py", "speak_command"),
    ("jobhunt/record.py", "*"),
    ("jobhunt/web/server.py", "web_port"),
}


def _modules():
    for path in sorted(JOBHUNT.rglob("*.py")):
        rel = path.relative_to(ROOT).as_posix()
        yield rel, ast.parse(path.read_text(encoding="utf-8"), rel)


def _name(node: ast.AST) -> str:
    """The last name of `settings`, `core.settings` or `config.CONFIG`, else ""."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    return ""


def _key(node: ast.Call | ast.Subscript) -> str:
    arg = node.slice if isinstance(node, ast.Subscript) else (node.args[0] if node.args else None)
    return arg.value if isinstance(arg, ast.Constant) and isinstance(arg.value, str) else "*"


def _config_reads():
    """(file, old key, line) of every `CONFIG.get(...)` and `CONFIG[...]` outside the shim."""
    for rel, tree in _modules():
        if rel == SHIM:
            continue
        for node in ast.walk(tree):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "get" \
                    and _name(node.func.value) == "CONFIG":
                yield rel, _key(node), node.lineno
            elif isinstance(node, ast.Subscript) and _name(node.value) == "CONFIG" and isinstance(node.ctx, ast.Load):
                yield rel, _key(node), node.lineno


def _settings_calls():
    """(file, call, key, by, line) of every `settings.get/set/reset("...")` and `store.stored("...")`."""
    for rel, tree in _modules():
        for node in ast.walk(tree):
            if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)):
                continue
            owner, attr = _name(node.func.value), node.func.attr
            if not ((owner == "settings" and attr in READS) or (owner == "store" and attr == "stored")):
                continue
            by = next((k.value.value for k in node.keywords
                       if k.arg == "by" and isinstance(k.value, ast.Constant)), "you")
            yield rel, attr, _key(node), by, node.lineno


class Registry(unittest.TestCase):
    """Static checks over every module under jobhunt/."""

    def test_code_reads_only_registered_settings(self):
        registered = set(settings._registry().REGISTRY)
        unknown = [f"{rel}:{line} {attr}({key!r})" for rel, attr, key, _, line in _settings_calls()
                   if key != "*" and key not in registered]
        self.assertEqual(unknown, [])
        reads = {(rel, key) for rel, key, _ in _config_reads()}
        outside = sorted(f"{rel}:{line} CONFIG {key!r}" for rel, key, line in _config_reads()
                         if (rel, key) not in LEGACY_ALLOWED)
        self.assertEqual(outside, [], "convert these to settings.get, or (only for old code) list them")
        self.assertEqual(sorted(LEGACY_ALLOWED - reads), [], "remove entries whose module no longer reads CONFIG")

    def test_hal_never_sets_the_goal_scoring_or_cv(self):
        by_hal = [f"{rel}:{line} {key}" for rel, attr, key, by, line in _settings_calls()
                  if attr in ("set", "reset") and by == "hal" and (key == "*" or key.startswith(PERSON_ONLY))]
        self.assertEqual(by_hal, [])

    def test_every_old_key_the_shim_maps_is_registered_or_kept_elsewhere(self):
        from jobhunt.core.settings import legacy_map, secrets

        registered = set(settings._registry().REGISTRY)
        for entry in legacy_map.LEGACY_KEYS:
            if entry.store == "settings" and entry.setting:
                self.assertIn(entry.setting, registered, entry.old)
            if entry.setting in secrets.NAMES:
                self.assertEqual(settings.setting(entry.setting).kind, "secret", entry.old)

    def test_the_checks_find_what_they_look_for(self):
        tree = ast.parse("settings.get('goal.nope')\nCONFIG.get('web_port')\nsettings.set('goal.x', 1, by='hal')\n")
        calls = [n for n in ast.walk(tree) if isinstance(n, ast.Call)]
        self.assertEqual([_key(c) for c in calls], ["goal.nope", "web_port", "goal.x"])


if __name__ == "__main__":
    unittest.main()
