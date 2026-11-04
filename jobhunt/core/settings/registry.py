"""Every setting Hal has, declared once.

The Settings page, setup, search, validation, history, Reset and the "code reads only registered keys" test
are all drawn from `REGISTRY`. The catalogue lives in `core/settings/catalog/<group>.py`; an area that needs a
key of its own registers it in `jobhunt/<area>/settings.py` with an explicit `group=`.

Design notes:
- `register()` checks each setting as it is added (kind, group, default against choices and range, the
  `visible_if` expression), so a bad catalogue entry fails at import rather than on someone's screen.
- `visible_if` is parsed by a tiny grammar (`key == 'v'`, `key != 'v'`, `key`, joined by `and`) and never
  passed to `eval()`, because catalogue files from add-ons are data, not code we want to run on a string.
- A setting whose default depends on the machine or the person gives `default_fn`; `default` then holds the
  value used when `default_fn` cannot decide.
"""

from __future__ import annotations

import importlib
import pkgutil
import re
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal, get_args

Kind = Literal[
    "text", "email", "phone", "url", "int", "bool", "choice", "seg", "multi", "tags", "ordered_tags",
    "textlist", "money", "range", "times", "hours", "date", "path",    # stored values
    "secret", "private",                                               # stored elsewhere
    "link", "button", "info", "list", "locked", "prompts", "danger",   # display rows, no stored value
]
KINDS: tuple[str, ...] = get_args(Kind)
STORED_KINDS = frozenset(KINDS[:KINDS.index("secret")])
ELSEWHERE_KINDS = frozenset(("secret", "private"))
DISPLAY_KINDS = frozenset(KINDS) - STORED_KINDS - ELSEWHERE_KINDS
CHOICE_KINDS = frozenset(("choice", "seg", "multi"))
PLATFORMS = ("linux", "windows")
REMOTE = ("ok", "label", "local_only")

# (id, name on screen, icon), in page order.
GROUPS: list[tuple[str, str, str]] = [
    ("you", "You", "user"),
    ("goal", "What you're looking for", "target"),
    ("prefs", "Preferences", "sliders"),
    ("sites", "Job sites", "globe"),
    ("scoring", "How Hal picks jobs", "sliders"),
    ("cvdocs", "CV and documents", "file"),
    ("applying", "Applying", "check"),
    ("email", "Email", "mail"),
    ("notify", "Notifications", "bell"),
    ("ai", "Hal's brain (AI)", "cpu"),
    ("schedule", "Schedule", "clock"),
    ("privacy", "Privacy and data", "shield"),
    ("updates", "Updates", "download"),
    ("remote", "Use Hal from another computer", "laptop"),
    ("health", "Is Hal working?", "heart"),
    ("advanced", "Advanced", "code"),
    ("addon", "Hal add-on", "plug"),
]
# Shown only in developer mode; the add-on group also needs the Hal tools on this computer.
DEVELOPER_GROUPS = frozenset(("addon",))
# Keys Hal keeps for itself; never on the Settings page.
INTERNAL_GROUP = "internal"

KEY = re.compile(r"^[a-z][a-z0-9_]*(\.[a-z0-9_]+)+$")
_CLAUSE = re.compile(r"^(?P<key>[a-z][a-z0-9_.]*)\s*(?:(?P<op>==|!=)\s*'(?P<value>[^']*)')?$")


@dataclass(frozen=True)
class Setting:
    """One setting: how it is stored, shown, parsed and checked."""

    key: str                                   # "goal.min_pay"
    kind: Kind
    default: Any                               # a value; see default_fn for machine- or person-dependent ones
    label: str                                 # the words on screen
    help: str = ""
    group: str = ""                            # Settings page group id; defaults to the key's first part
    choices: tuple[tuple[Any, str], ...] = ()  # (stored value, words on screen); empty for a "choice" means
                                               # the list is worked out at run time (AI models, 7.2)
    min: float | None = None
    max: float | None = None
    unit: str = ""                             # "miles", "points", "checks"
    restart: bool = False                      # takes effect after a restart; the page offers Restart
    rescore: bool = False                      # changes scores; the page offers "Re-check N jobs"
    goal: bool = False                         # part of the goal; a change writes a goal version
    advanced: bool = False                     # under "Show more" in its group
    dev_only: bool = False                     # only when advanced.developer_mode is on
    machine: bool = False                      # about this computer (port, start at login); not exported
    remote: Literal["ok", "label", "local_only"] = "ok"   # label: shows "On <host>"; local_only: disabled
                                                           # in a session opened from another computer
    platforms: tuple[str, ...] = ()            # ("windows",), ("linux",) or () for all
    visible_if: str = ""                       # "ai.provider == 'claude_cli'"; parsed, never eval'd
    order: int = 0
    default_fn: Callable[[Callable[[str], Any]], Any] | None = None   # get -> default
    parse: Callable[[Any], Any] | None = None  # what the user typed -> value ("22,000" -> 22000)
    validate: Callable[[Any], str | None] | None = None   # plain-words problem, or None
    show: Callable[[Any], str] | None = None   # value -> words ("Hal reads this as 22,000 pounds a year")
    secret_name: str = ""                      # kind "secret": the store name
    to: str = ""                               # kind "link": app route, e.g. "jobs-where"
    action: str = ""                           # kind "button": API path or action kind

    @property
    def group_id(self) -> str:
        """The group this setting shows in: `group`, else the key's first part."""
        return self.group or self.key.split(".", 1)[0]

    @property
    def stored(self) -> bool:
        """True when the value lives in the `settings` table."""
        return self.kind in STORED_KINDS


class SettingError(ValueError):
    """A value the user gave cannot be used; str() is the words shown under the field."""

    def __init__(self, key: str, message: str) -> None:
        super().__init__(message)
        self.key = key
        self.message = message


REGISTRY: dict[str, Setting] = {}
CATALOG_PACKAGE = "jobhunt.core.settings.catalog"
MIGRATIONS_PACKAGE = "jobhunt.core.db.migrations"


def _group_ids() -> set[str]:
    return {g[0] for g in GROUPS} | {INTERNAL_GROUP}


def parse_condition(expr: str) -> list[tuple[str, str | None, str | None]]:
    """Splits a `visible_if` expression into (key, op, value) clauses; raises ValueError on anything else."""
    if not expr.strip():
        return []
    clauses = []
    for part in re.split(r"\s+and\s+", expr.strip()):
        m = _CLAUSE.match(part.strip())
        if not m:
            raise ValueError(f"cannot read visible_if clause {part!r} (use key == 'v', key != 'v' or key)")
        clauses.append((m["key"], m["op"], m["value"]))
    return clauses


def _check(s: Setting) -> None:
    """Raises ValueError when a catalogue entry is inconsistent."""
    where = f"setting {s.key!r}"
    if not KEY.match(s.key):
        raise ValueError(f"{where}: keys are lower-case dotted names such as 'goal.min_pay'")
    if s.kind not in KINDS:
        raise ValueError(f"{where}: unknown kind {s.kind!r}")
    if not s.label.strip():
        raise ValueError(f"{where}: needs a label")
    if s.group_id not in _group_ids():
        raise ValueError(f"{where}: unknown group {s.group_id!r}")
    if s.remote not in REMOTE:
        raise ValueError(f"{where}: remote must be one of {REMOTE}")
    if any(p not in PLATFORMS for p in s.platforms):
        raise ValueError(f"{where}: platforms must be among {PLATFORMS}")
    if s.kind == "secret" and not s.secret_name:
        raise ValueError(f"{where}: a secret needs secret_name")
    if s.kind == "link" and not s.to:
        raise ValueError(f"{where}: a link needs `to`")
    if s.kind in CHOICE_KINDS and s.kind != "choice" and not s.choices:
        raise ValueError(f"{where}: a {s.kind} needs choices")
    parse_condition(s.visible_if)
    if not s.stored:
        return
    values = [c[0] for c in s.choices]
    if s.choices and s.default_fn is None:
        given = s.default if s.kind == "multi" else [s.default]
        missing = [v for v in given if v not in values]
        if missing:
            raise ValueError(f"{where}: default {missing!r} is not one of its choices")
    if isinstance(s.default, int | float) and not isinstance(s.default, bool):
        if (s.min is not None and s.default < s.min) or (s.max is not None and s.default > s.max):
            raise ValueError(f"{where}: default {s.default} is outside {s.min} to {s.max}")


def register(*items: Setting) -> None:
    """Adds settings; a duplicate key or an inconsistent entry raises at import."""
    for s in items:
        if s.key in REGISTRY:
            raise ValueError(f"setting {s.key!r} is registered twice")
        _check(s)
    for s in items:
        REGISTRY[s.key] = s


def _area_settings_modules() -> list[str]:
    """Dotted names of every `settings.py` or `*_settings.py` under jobhunt, outside the catalogue.

    Found by reading the folders rather than `pkgutil.walk_packages`, which would import every package.
    """
    import jobhunt

    root = Path(jobhunt.__file__).resolve().parent
    names = []
    for path in root.rglob("*.py"):
        if not (path.stem == "settings" or path.stem.endswith("_settings")):
            continue
        name = ".".join(("jobhunt", *path.relative_to(root).with_suffix("").parts))
        # cli/settings.py is the `settings` command and migrations/0003_settings.py a schema step, not keys.
        if name.startswith(("jobhunt.cli.", CATALOG_PACKAGE + ".", MIGRATIONS_PACKAGE + ".")):
            continue
        names.append(name)
    return sorted(names)


def load_all() -> None:
    """Imports core/settings/catalog/*.py, then every module named settings.py under jobhunt/<area>/."""
    catalog = importlib.import_module(CATALOG_PACKAGE)
    for info in sorted(pkgutil.iter_modules(catalog.__path__), key=lambda i: i.name):
        importlib.import_module(f"{CATALOG_PACKAGE}.{info.name}")
    for name in _area_settings_modules():
        importlib.import_module(name)


def default_of(setting: Setting, get: Callable[[str], Any]) -> Any:
    """The default in effect: `default_fn(get)` when given and it decides, else `default`."""
    if setting.default_fn is None:
        return setting.default
    value = setting.default_fn(get)
    return setting.default if value is None else value


def visible(setting: Setting, get: Callable[[str], Any], *, developer: bool, platform: str) -> bool:
    """Evaluates visible_if: `key == 'v'`, `key != 'v'`, `key` (truthy), joined by `and`. No eval()."""
    if setting.group_id == INTERNAL_GROUP:
        return False
    if (setting.dev_only or setting.group_id in DEVELOPER_GROUPS) and not developer:
        return False
    if setting.platforms and platform not in setting.platforms:
        return False
    for key, op, value in parse_condition(setting.visible_if):
        current = get(key)
        text = "" if current is None else str(current).lower() if isinstance(current, bool) else str(current)
        if op is None and not current:
            return False
        if op == "==" and text != value:
            return False
        if op == "!=" and text == value:
            return False
    return True


def in_group(group: str) -> list[Setting]:
    """The settings of one group, in page order (`order`, then registration order)."""
    items = [s for s in REGISTRY.values() if s.group_id == group]
    return sorted(items, key=lambda s: s.order)
