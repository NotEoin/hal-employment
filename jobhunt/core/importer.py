"""The owner's one-time import: moves today's in-checkout data into Hal Employment's own folders.

`run(Options(...))` copies the old checkout's `data/`, `packs/`, `config.toml`, list files, key files and
the website's CV into the data folder, migrates the copy, turns the old settings into registered settings,
seals it with the `.hal-data` marker and locks the old copy read-only. `undo()` goes back. Each step adds
one plain line to the report (counts and hashes only, never content), which the command line prints.

Design notes:
- Everything is built in a staging home beside the target (`.<data folder>.importing/`, or a temporary
  folder for a dry run) through `paths.set_override`, so settings, the private store and migrations write
  to the copy with their normal code. The target folder appears only at the seal, by one rename. Until
  then the default folder has no marker, so `paths` keeps the legacy layout and a hal-jobs command run in
  the middle of the import reads the old data instead of an empty new database.
- Any failure before the seal leaves the old data untouched; the staging home is removed by the next run.
- The old copy is renamed only after the seal, so there is never a moment with no data folder to find.
- The import holds its own lock (`.<data folder>.import.lock`) rather than `state/migrate.lock`, because
  step 5's `migrate()` takes that lock itself and an flock is not re-entrant within one process.
- `private.toml` is copied byte for byte, compared by SHA-256, and never parsed, printed or logged.
- Secrets are written outside the staging home, straight to the person's store, because the keyring is
  per user, not per folder; a dry run counts them and writes none. They are saved just after the seal:
  the store also writes a history and Activity row through `db.connect()`, which before the seal would
  open (and migrate) the old database in place.
- The computer is reached only through `System` (port, timers, disk, retiring timers), so tests pass
  fakes and never touch systemd or a real server.

Not done here: step 10's copy of `~/.claude/commands/jobs.md` (the Hal add-on renders it, and only when
`addon.jobs_command` is on, which the import leaves off), and step 14's rescore itself (the import marks
`find.rescore_all` pending; the server's data-task runner does the work).
"""
from __future__ import annotations

import ast
import contextlib
import hashlib
import importlib
import json
import logging
import os
import re
import secrets as token_source
import shlex
import shutil
import socket
import sqlite3
import stat
import subprocess
import tempfile
import tomllib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from jobhunt.core import clock, paths
from jobhunt.core.db import migrate
from jobhunt.core.settings import legacy_map

log = logging.getLogger(__name__)

TIMERS = ("jobhunt-discover.timer", "jobhunt-mail.timer", "jobhunt-digest.timer")
SYSTEMCTL_TIMEOUT_S = 10      # `systemctl --user is-active` answers at once; longer means the user bus is stuck
PORT_TIMEOUT_S = 1.0          # a local server accepts a connection immediately or not at all
LOCK_TIMEOUT_S = 1.0          # a second import should be told at once, not wait
DISK_FACTOR = 2               # free space wanted: the copy, plus the migration backup of the database
DEFAULT_PORT = 8737           # `advanced.port`'s default, used only to look for a running Hal
TOPIC_CHARS = "abcdefghijklmnopqrstuvwxyz0123456789"
TOPIC_LENGTH = 24             # `hal-` plus 24 random letters and digits
SKIPPED_PROFILE_FILES = ("Singleton*",)   # Chromium's lock links; a copy of them makes the profile look in use
WHY = "You ran the one-time move of your old Hal's data into its new folder."
INCOMPLETE = "import.incomplete"
REPORT = ("import", "report.json")
LOCKED_SUFFIX = ".imported-"
RESCORE_TASK, RESCORE_AFTER = "find.rescore_all", 6
KINDS = ["graduate_job", "graduate_scheme", "internship", "fixed_term"]   # the owner confirms these
SECRET_FILES = {"gmail-app-password": "email.app_password", "adzuna": "sites.adzuna_key", "reed": "sites.reed_key"}
# hal.conf key -> (setting, the config.toml key that wins over it, as the old llm module did)
HAL_CONF_MODELS = {"HAL_MODEL": ("ai.model.checker", "main_model"),
                   "HAL_WRITER_MODEL": ("ai.model.writer", "writer_model"),
                   "HAL_FAST_MODEL": ("ai.model.quick", "fast_model"),
                   "HAL_OLLAMA_URL": ("ai.ollama_url", "ollama_url")}
OWNER_DEFAULTS: dict[str, Any] = {
    "scoring.preset": "technical_grad", "goal.stage": "new_grad", "goal.kinds": KINDS,
    "ai.provider": "local", "ai.local_only": True, "advanced.developer_mode": True,
    "addon.enabled": True, "addon.respect_off": False,
}
CV_JSON = ("src", "data", "cv.json")
CONTACT_MODULE = ("tools", "cv_contact.py")
TABLE_PREFIXES = ("goal", "scoring", "email")   # owner-prefs.toml tables whose keys are settings as they stand


class ImportStopped(RuntimeError):
    """The import stopped before changing anything that matters; str() is the plain-words reason."""


def _yes(value: Any) -> bool:
    return str(value).strip().lower() in ("yes", "true", "y")


def _start(value: Any) -> str:
    words = str(value).strip().lower()
    if re.search(r"immediate|asap|now|straight away", words):
        return "now"
    if re.search(r"\b(1|one) week", words):
        return "1w"
    if re.search(r"\b(1|one) month", words):
        return "1m"
    raise ImportStopped("The old answer for your start date is not one Hal can keep (key: start). "
                        "Change it in owner-prefs.toml to Immediately, 1 week or 1 month, then run the import again.")


def _diversity(value: Any) -> str:
    return "prefer_not" if str(value).strip().lower() == "prefer not to say" else "leave"


def _country(value: Any) -> None:
    if str(value).strip().lower() not in ("united kingdom", "uk"):
        raise ImportStopped("Hal works only in the United Kingdom for now (key: country).")


# Every fact of the old `profile()` and where it lives now. None: checked, not stored.
YOU_MAP: dict[str, tuple[str | None, Callable[[Any], Any] | None]] = {
    "first_name": ("you.name", None), "last_name": ("you.name", None), "full_name": ("you.name", None),
    "preferred_name": ("you.preferred_name", None), "email": ("you.email", None), "phone": ("you.phone", None),
    "linkedin": ("you.links.linkedin", None), "github": ("you.links.github", None),
    "website": ("you.links.website", None), "city": ("you.city", None), "country": (None, _country),
    "location": ("you.location_line", None), "right_to_work": ("you.right_to_work", None),
    "sponsorship": ("you.right_to_work", None), "work_status": ("you.work_status", None),
    "nationality": ("you.nationality", None), "notice": ("you.notice", None), "start": ("you.start", _start),
    "university": ("you.education.university", None), "degree": ("you.education.degree", None),
    "grade": ("you.education.grade", None), "grad_year": ("goal.study_end", None),
    "office": ("you.office", _yes), "relocate": ("you.relocate", _yes), "diversity": ("you.diversity", _diversity),
    "salary": ("you.salary_answer", None),
}
FROM_CONTACT = ("first_name", "last_name", "full_name", "preferred_name", "email", "phone")


@dataclass
class Options:
    """What the command line asked for."""

    repo: Path | None = None             # the old checkout; default the code folder when it holds data/jobs.db
    dry_run: bool = False
    retire_timers: bool = False
    force: bool = False
    legacy_secrets: Path | None = None   # default ~/.config/jobhunt
    prefs: Path | None = None            # default <config>/import/owner-prefs.toml


def _tcp_answers(port: int) -> bool:
    try:
        with socket.create_connection(("127.0.0.1", port), timeout=PORT_TIMEOUT_S):
            return True
    except OSError:
        return False


def _new_app_answers(port: int) -> bool:
    from jobhunt.web import app

    return app.answering(port)


def _timer_active(name: str) -> bool:
    if not shutil.which("systemctl"):
        return False
    try:
        done = subprocess.run(["systemctl", "--user", "is-active", "--quiet", name], timeout=SYSTEMCTL_TIMEOUT_S,
                              check=False, capture_output=True)
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0


def _platform_call(name: str) -> Callable[[], Any] | None:
    from jobhunt.core import platform

    fn = getattr(platform, name, None)
    return fn if callable(fn) else None


def _free_bytes(path: Path) -> int:
    while not path.exists():
        path = path.parent
    return shutil.disk_usage(path).free


@dataclass
class System:
    """What the import asks of the computer; tests pass fakes."""

    port_answers: Callable[[int], bool] = _tcp_answers
    new_app_answers: Callable[[int], bool] = _new_app_answers
    timer_active: Callable[[str], bool] = _timer_active
    platform_call: Callable[[str], Callable[[], Any] | None] = _platform_call
    free_bytes: Callable[[Path], int] = _free_bytes
    say: Callable[[str], None] = lambda line: None
    after_step: Callable[[str], None] = lambda step: None   # tests look at the world between steps


@dataclass
class Report:
    """The lines and counts of one run; `report.json` holds `steps`, never a value from the old files."""

    steps: list[dict] = field(default_factory=list)
    say: Callable[[str], None] = lambda line: None

    def add(self, step: str, line: str, **counts: Any) -> None:
        """One step's plain line, shown at once and kept for the report and Activity."""
        self.steps.append({"step": step, "line": line, **counts})
        log.info("import %s: %s", step, counts or "done")
        self.say(line)


def _sha(path: Path) -> str:
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _files(folder: Path) -> dict[str, str]:
    """Relative path -> SHA-256 of every regular file under `folder`."""
    return {str(p.relative_to(folder)): _sha(p) for p in sorted(folder.rglob("*"))
            if p.is_file() and not p.is_symlink()}


def _size(folder: Path) -> int:
    if not folder.exists():
        return 0
    return sum(p.stat().st_size for p in folder.rglob("*") if p.is_file() and not p.is_symlink())


def target_dirs() -> tuple[Path, Path]:
    """(data, config) the import writes: the temporary home in tests, else the platform folders."""
    home = paths.home_override()
    if home is not None:
        return home / "data", home / "config"
    return paths._default_data_dir(), paths.config_dir()   # the legacy layout hides the default from data_dir()


def source_repo(opts: Options) -> Path:
    """The old checkout: `--from-repo`, else the code folder when it still holds `data/jobs.db`."""
    repo = Path(opts.repo).expanduser().resolve() if opts.repo else paths.code_dir()
    if not (repo / "data" / "jobs.db").is_file():
        raise ImportStopped(f"There is no old data to move: {repo / 'data' / 'jobs.db'} does not exist.")
    return repo


def _toml(path: Path) -> dict:
    try:
        return tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as e:
        raise ImportStopped(f"{path} cannot be read: {e}") from e


def _flatten(config: dict) -> dict[str, Any]:
    out = {}
    for key, value in config.items():
        if isinstance(value, dict):
            out.update({f"{key}.{k}": v for k, v in value.items()})
        else:
            out[key] = value
    return out


def _literal_assignments(path: Path) -> dict[str, Any]:
    """`NAME = <literal>` lines of a Python file, read with `ast`; the file is never imported."""
    out = {}
    for node in ast.parse(path.read_text(encoding="utf-8"), filename=str(path)).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            with contextlib.suppress(ValueError):
                out[node.targets[0].id] = ast.literal_eval(node.value)
    return out


def _hal_conf(path: Path) -> dict[str, str]:
    """`HAL_*=value` lines of hal.conf, as the old llm module read them."""
    conf = {}
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            m = re.match(r"^\s*(HAL_\w+)=(.*)$", line)
            if m:
                conf[m.group(1)] = (shlex.split(m.group(2)) or [""])[0]
    return conf


def exclusions(text: str) -> tuple[list[str], list[str]]:
    """(avoid_employers lines with `# Section` headings, training providers) from excluded-companies.txt.

    A section whose heading mentions training or train-and-place lists training providers;
    its entries go to `goal.training_providers` instead of the list of employers to avoid.
    """
    avoid, training, section = [], [], ""
    for raw in text.splitlines():
        line = raw.strip()
        if not line:
            continue
        if line.startswith("#"):
            section = line.lower()
            if not ("training" in section or "train-and-place" in section):
                avoid.append("# " + line.lstrip("#").strip())
        elif "training" in section or "train-and-place" in section:
            training.append(line)
        else:
            avoid.append(line)
    return avoid, training


def new_topic() -> str:
    """A fresh phone-alert topic: the old one is in every old commit, so it is treated as public."""
    return "hal-" + "".join(token_source.choice(TOPIC_CHARS) for _ in range(TOPIC_LENGTH))


def _store(name: str):
    """The secrets or private store, looked up when used (stand-ins replace them in tests)."""
    try:
        return importlib.import_module(f"jobhunt.core.settings.{name}")
    except ImportError as e:
        raise ImportStopped(f"This version of Hal has no {name} store yet, so it cannot move your {name}.") from e


class _Plan:
    """Every setting the import will write, worked out before anything is copied, so a bad key stops early."""

    def __init__(self, repo: Path, prefs_path: Path, config_path: Path):
        from jobhunt.core import settings

        self.settings_mod = settings
        self.values: list[tuple[str, Any, str]] = []   # (key, value, step)
        self.private: dict[str, str] = {}
        self.removed: list[str] = []
        self.config = _toml(config_path) if config_path.is_file() else {}
        if not prefs_path.is_file():
            raise ImportStopped(f"{prefs_path} is missing. Run `python scripts/dev/export_owner_prefs.py` first.")
        self.prefs = _toml(prefs_path)
        flat = _flatten(self.config)
        meta = self.prefs.get("import", {})
        self.site = Path(str(flat.get("cv_site") or meta.get("cv_site") or "")).expanduser()
        if not str(flat.get("cv_site") or meta.get("cv_site") or ""):
            raise ImportStopped("Hal does not know where the website's CV is: config.toml has no cv_site and "
                                "owner-prefs.toml has no [import] cv_site.")
        self.hal_root = Path(str(flat.get("hal_root") or meta.get("hal_root") or "")).expanduser()
        self._config(flat)
        self._prefs()

    def _check(self, key: str) -> None:
        try:
            self.settings_mod.setting(key)
        except KeyError:
            raise ImportStopped(f"No setting holds {key!r}, so the import stopped rather than drop it.") from None

    def add(self, key: str, value: Any, step: str) -> None:
        """Queues one setting after checking that it is registered."""
        self._check(key)
        self.values.append((key, value, step))

    def _config(self, flat: dict[str, Any]) -> None:
        for old, value in flat.items():
            try:
                entry = legacy_map.lookup(old)
            except KeyError:
                raise ImportStopped(f"config.toml has {old!r}, which no setting holds; the import stopped "
                                    f"rather than drop it.") from None
            if "L" not in entry.layouts:
                raise ImportStopped(f"config.toml has {old!r}, which only the stick's Hal used.")
            if entry.store == "settings":
                self.add(entry.setting, legacy_map.new_value(entry, value), "settings")
            elif entry.store == "private":
                self.private[entry.setting] = str(value)
            elif entry.store in ("removed", "importer"):
                self.removed.append(old)
            else:
                raise ImportStopped(f"config.toml has {old!r}, which this import does not know how to move.")

    def _prefs(self) -> None:
        for table in TABLE_PREFIXES:
            for key, value in (self.prefs.get(table) or {}).items():
                self.add(f"{table}.{key}", value, "identity")
        you = dict(self.prefs.get("you") or {})
        unknown = sorted(set(you) - set(YOU_MAP))
        if unknown:
            raise ImportStopped(f"owner-prefs.toml lists {unknown[0]!r} in [you], which no setting holds; "
                                f"the import stopped rather than drop it.")
        sponsorship = _yes(you.pop("sponsorship", "No"))
        right = you.pop("right_to_work", "Yes")
        if not sponsorship and not _yes(right):
            raise ImportStopped("The old answers say you have no right to work and need no sponsorship "
                                "(keys: right_to_work, sponsorship). Fix one in owner-prefs.toml.")
        self.add("you.right_to_work", "need_sponsorship" if sponsorship else "uk_no_visa", "identity")
        for key, value in you.items():
            setting, convert = YOU_MAP[key]
            value = convert(value) if convert else value
            if setting is not None:
                self.add(setting, value, "identity")
        for key, value in OWNER_DEFAULTS.items():
            self.add(key, value, "hal")


class _Run:
    """One import, step by step."""

    def __init__(self, opts: Options, system: System):
        self.opts, self.system = opts, system
        self.report = Report(say=system.say)
        self.repo = source_repo(opts)
        self.old = self.repo / "data"
        self.target, self.config_dir = target_dirs()
        if self.target.resolve() == self.old.resolve():
            raise ImportStopped("The old data folder and the new one are the same folder.")
        self.prefs_path = opts.prefs or self.config_dir / "import" / "owner-prefs.toml"
        self.legacy_secrets = opts.legacy_secrets or Path.home() / ".config" / "jobhunt"
        self.day = clock.local_day().isoformat()
        self.counts: dict[str, Any] = {}
        self.activity: list[tuple[str, str]] = []
        self.replaced: Path | None = None

    # 1. Preconditions -------------------------------------------------------------------------------------
    def preconditions(self) -> None:
        port = int(os.environ.get("HAL_EMPLOYMENT_PORT") or _flatten(self._config()).get("web_port") or DEFAULT_PORT)
        if self.system.port_answers(port):
            if not (self.opts.force and self.system.new_app_answers(port)):
                raise ImportStopped(f"Hal is running (something answers on port {port}). Stop it first: "
                                    "systemctl --user stop hal-employment.service")
        active = [t for t in TIMERS if self.system.timer_active(t)]
        if active:
            raise ImportStopped("Stop the old timers first: systemctl --user stop " + " ".join(active))
        marker = self.target / paths.MARKER
        if marker.exists():
            if self._jobs_in(self.target / "jobs.db"):
                raise ImportStopped(f"Your data is already in {self.target}. To go back to the old folder, run "
                                    "`python -m jobhunt import --undo`.")
            self.replaced = self.target
        elif self.target.exists() and any(self.target.iterdir()):
            if not self.opts.force:
                raise ImportStopped(f"{self.target} already holds files but was never sealed by an import. "
                                    "Move it away, or run again with --force to put it in backups.")
            self.replaced = self.target
        need = DISK_FACTOR * (_size(self.old) + _size(self.repo / "packs"))
        free = self.system.free_bytes(self.target.parent)
        if free < need:
            raise ImportStopped(f"There is not enough free space: the move needs {need // 2**20} MB and "
                                f"{free // 2**20} MB is free.")
        if self.opts.retire_timers and not self.system.platform_call("retire_legacy_timers"):
            raise ImportStopped("This version of Hal cannot retire the old timers yet. Run without --retire-timers.")
        self.plan = _Plan(self.repo, self.prefs_path, self.repo / "config.toml")
        self.cv_json = self.plan.site.joinpath(*CV_JSON)
        if not self.cv_json.is_file():
            raise ImportStopped(f"The website's CV is not at {self.cv_json}.")
        self.step("preconditions", "Checked: Hal and the old timers are stopped, and there is room for the copy.")

    def _config(self) -> dict:
        path = self.repo / "config.toml"
        return _toml(path) if path.is_file() else {}

    @staticmethod
    def _jobs_in(db: Path) -> int:
        if not db.is_file():
            return 0
        con = sqlite3.connect(db)
        try:
            return con.execute("SELECT count(*) FROM jobs").fetchone()[0]
        except sqlite3.Error:
            return 0
        finally:
            con.close()

    def step(self, name: str, line: str, **counts: Any) -> None:
        """Reports a finished step and lets a watching test look at the world."""
        self.report.add(name, line, **counts)
        self.activity.append((name, line))
        self.system.after_step(name)

    # 3. Copy ----------------------------------------------------------------------------------------------
    def copy(self, data: Path) -> None:
        src = sqlite3.connect(self.old / "jobs.db", timeout=30)
        dest = sqlite3.connect(data / "jobs.db")
        try:
            src.backup(dest)
        finally:
            dest.close()
            src.close()
        (data / "jobs.db").chmod(0o600)
        self.copied: dict[Path, Path] = {}
        handled = {"jobs.db", "jobs.db-wal", "jobs.db-shm", "private.toml", "browser-profile", "apply",
                   "signed-in.json"}
        private = self.old / "private.toml"
        if private.is_file():
            shutil.copyfile(private, data / "private.toml")
            (data / "private.toml").chmod(0o600)
            if _sha(private) != _sha(data / "private.toml"):
                raise ImportStopped("The copy of your private answers does not match the original.")
        for name in ("browser-profile", "apply"):
            if (self.old / name).is_dir():
                shutil.copytree(self.old / name, data / name, symlinks=True,
                                ignore=shutil.ignore_patterns(*SKIPPED_PROFILE_FILES))
                self.copied[self.old / name] = data / name
        self._file(self.old / "signed-in.json", data / "signed-in.json")
        cv_import = data / "import" / "cv"
        for entry in sorted(self.old.iterdir()):
            if entry.name in handled:
                continue
            if entry.name.startswith("cv-"):
                dest_path = cv_import / entry.name
            else:
                dest_path = data / "import" / "legacy-data" / entry.name
            if entry.is_dir():
                shutil.copytree(entry, dest_path, symlinks=True)
            else:
                dest_path.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(entry, dest_path)
            self.copied[entry] = dest_path
        # the last time anything reads the website repo: read once, hashed from the same bytes
        site_cv = self.cv_json.read_bytes()
        cv_import.mkdir(parents=True, exist_ok=True)
        (cv_import / "site-cv.json").write_bytes(site_cv)
        self.site_cv_sha = hashlib.sha256(site_cv).hexdigest()
        if (self.repo / "packs").is_dir():
            shutil.copytree(self.repo / "packs", data / "legacy-packs", symlinks=True)
            self.copied[self.repo / "packs"] = data / "legacy-packs"
        for name in ("boards.toml", "companies.txt"):
            self._file(self.repo / name, data / "import" / name)
        self._file(self.prefs_path, data / "import" / "owner-prefs.toml")
        folders = sum(1 for s in self.copied if s.is_dir())
        self.step("copy", f"Copied your records, private answers (unread), {folders} folders and the website's CV.",
                  folders=folders, files=sum(len(_files(d)) if d.is_dir() else 1 for d in self.copied.values()))

    def _file(self, src: Path, dest: Path) -> None:
        if src.is_file():
            dest.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dest)
            self.copied[src] = dest

    # 4. Verify --------------------------------------------------------------------------------------------
    def verify(self, data: Path) -> None:
        con = sqlite3.connect(data / "jobs.db")
        old = sqlite3.connect(self.old / "jobs.db", timeout=30)
        try:
            if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                raise ImportStopped("The copied records failed SQLite's integrity check; nothing was changed.")
            tables = [r[0] for r in old.execute("SELECT name FROM sqlite_master WHERE type = 'table'")]
            rows = {}
            for table in tables:
                before = old.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                after = con.execute(f'SELECT count(*) FROM "{table}"').fetchone()[0]
                if before != after:
                    raise ImportStopped(f"The copy of the {table} table has {after} rows, not {before}.")
                rows[table] = before
        finally:
            con.close()
            old.close()
        hashes = {}
        for src, dest in self.copied.items():
            if src.is_dir():
                want = {k: v for k, v in _files(src).items() if not Path(k).name.startswith("Singleton")}
                got = _files(dest)
                if want != got:
                    raise ImportStopped(f"The copy of {src.name} does not match the original.")
                hashes[src.name] = len(got)
            elif _sha(src) != _sha(dest):
                raise ImportStopped(f"The copy of {src.name} does not match the original.")
            else:
                hashes[src.name] = _sha(dest)
        if _sha(data / "import" / "cv" / "site-cv.json") != self.site_cv_sha:
            raise ImportStopped("The copy of the website's CV does not match what was read.")
        hashes["site-cv.json"] = self.site_cv_sha
        if (self.old / "private.toml").is_file():
            hashes["private.toml"] = _sha(data / "private.toml")
        self.counts = {"rows": rows, "files": hashes}
        self.step("verify", f"Checked the copy: {sum(rows.values())} rows in {len(rows)} tables, every file the same.",
                  rows=rows, hashes=hashes)

    # 5. Migrate -------------------------------------------------------------------------------------------
    def migrate(self, data: Path) -> None:
        con = sqlite3.connect(data / "jobs.db", timeout=30)
        try:
            done = migrate.migrate(con, db_path=data / "jobs.db", backup_dir=data / "backups")
            row = con.execute("SELECT v FROM meta WHERE k = 'answers2:private_not_copied'").fetchone()
        finally:
            con.close()
        skipped = int(row[0]) if row else 0
        self.step("migrate", f"Updated the copy to version {migrate.LATEST}; {skipped} private saved answers were "
                  "left out of the new list.", migrations=len(done), private_answers_not_copied=skipped)

    # 6, 8, 10. Settings -------------------------------------------------------------------------------------
    def write_settings(self, step: str) -> int:
        from jobhunt.core import settings

        n = 0
        for key, value, which in self.plan.values:
            if which != step:
                continue
            try:
                settings.set(key, value, by="import")
            except ValueError as e:     # SettingError is a ValueError whose words say what is wrong
                raise ImportStopped(f"The old value for {key} cannot be used: {e}") from e
            n += 1
        return n

    def settings_step(self) -> None:
        n = self.write_settings("settings")
        saved, unsaved = self.save_private()
        removed = ", ".join(self.plan.removed)
        self.step("settings", f"Moved {n} settings from config.toml"
                  + (f"; {saved} answer went to your private answers" if saved else "")
                  + (f"; {unsaved} old private answer is not one of the choices, so answer it again in Settings, "
                     "Private answers" if unsaved else "")
                  + (f"; not moved: {removed} (the phone alert topic is replaced, signed-in Apply is gone, "
                     "the website is read once below)" if removed else "") + ".",
                  settings=n, private=saved, private_left=unsaved, not_moved=self.plan.removed)

    def save_private(self) -> tuple[int, int]:
        """Saves the private answers config.toml held, one by one; (saved, left for the person).

        The private store takes only one of its choices for a choice question, and the old file
        took any words, so an answer it refuses is left out rather than stopping the import. The store's
        error never holds the answer, and nothing here prints or logs it.
        """
        if not self.plan.private:
            return 0, 0
        store = _store("private")
        saved = unsaved = 0
        for key, value in self.plan.private.items():
            try:
                store.save({key: value})
            except ValueError:      # PrivateAnswerError, whose words name no part of the answer
                unsaved += 1
            else:
                saved += 1
        return saved, unsaved

    def identity_step(self) -> None:
        contact = self.plan.site.joinpath(*CONTACT_MODULE)
        found = _literal_assignments(contact) if contact.is_file() else {}
        name, email, phone = (str(found.get(k) or "") for k in ("NAME", "EMAIL", "PHONE"))
        if name:
            self.plan.add("you.name", name, "identity")
            self.plan.add("you.preferred_name", name.split(" ")[0], "identity")
        if email:
            self.plan.add("you.email", email.lower(), "identity")
        if phone:
            self.plan.add("you.phone", phone, "identity")
        n = self.write_settings("identity")
        self.step("identity", f"Saved your name, contact details and {n} preferences from the old code.", settings=n)

    def rescope_step(self, data: Path) -> None:
        from jobhunt.core import answers_rescope

        con = sqlite3.connect(data / "jobs.db", timeout=30)
        try:
            moved = answers_rescope.run_now(con)
        finally:
            con.close()
        self.step("rescope", answers_rescope.summary(moved), **dict(moved))

    def lists_step(self) -> None:
        excluded = self.repo / "excluded-companies.txt"
        avoid, training = exclusions(excluded.read_text(encoding="utf-8")) if excluded.is_file() else ([], [])
        if avoid:
            self.plan.add("goal.avoid_employers", avoid, "lists")
        if training:
            self.plan.add("goal.training_providers", training, "lists")
        self.write_settings("lists")
        employers = sum(1 for line in avoid if not line.startswith("#"))
        self.step("lists", f"Saved {employers} employers to avoid and {len(training)} training providers; kept the "
                  "board and company lists for Hal's sources.", employers=employers, training=len(training))

    def hal_step(self) -> None:
        flat = _flatten(self.plan.config)
        conf = _hal_conf(self.plan.hal_root / "hal.conf")
        models = 0
        for conf_key, (setting, old_key) in HAL_CONF_MODELS.items():
            if conf.get(conf_key) and old_key not in flat:
                self.plan.add(setting, conf[conf_key], "hal")
                models += 1
        n = self.write_settings("hal")
        self.step("hal", f"Set Hal's tools: {models} local models from hal.conf, local AI only and "
                  "developer mode on.", settings=n, models=models)

    def rescore_step(self, data: Path) -> None:
        con = sqlite3.connect(data / "jobs.db", timeout=30)
        try:
            with con:
                con.execute("INSERT OR IGNORE INTO data_tasks (name, after, state) VALUES (?, ?, 'pending')",
                            (RESCORE_TASK, RESCORE_AFTER))
            jobs = con.execute("SELECT count(*) FROM jobs").fetchone()[0]
        finally:
            con.close()
        self.step("rescore", f"Hal will check all {jobs} jobs again with their reasons when it next starts.", jobs=jobs)

    # 7. Secrets -------------------------------------------------------------------------------------------
    def secrets_step(self) -> None:
        found = {SECRET_FILES[p.name]: p for p in sorted(self.legacy_secrets.glob("*")) if p.name in SECRET_FILES} \
            if self.legacy_secrets.is_dir() else {}
        if self.opts.dry_run:
            self.step("secrets", f"Would save {len(found)} passwords and keys, and a new phone alert topic.",
                      secrets=len(found))
            return
        store = _store("secrets")
        moved = "Your data was moved, but "
        undo_words = " Add it in Settings, or go back with `python -m jobhunt import --undo`."
        for name, path in found.items():
            value = path.read_text(encoding="utf-8").strip()
            try:
                store.put(name, value)
            except Exception as e:      # the store's words name the key, never the value
                raise ImportStopped(f"{moved}Hal could not save {name}: {e}.{undo_words}") from e
            if store.get(name) != value:
                raise ImportStopped(f"{moved}Hal saved {name} but could not read it back.{undo_words}")
        store.put("notify.ntfy_topic", new_topic())
        self.step("secrets", f"Saved {len(found)} passwords and keys, and a new phone alert topic (subscribe your "
                  "phone again from Setup, Phone alerts).", secrets=len(found))

    # 11. Seal ---------------------------------------------------------------------------------------------
    def write_records(self, data: Path, extra: list[tuple[str, str]] = ()) -> None:
        """report.json, the `imports` row and one Activity row per step, into the copy."""
        report_path = data.joinpath(*REPORT)
        report_path.parent.mkdir(parents=True, exist_ok=True)
        body = {"at": clock.iso(), "source": str(self.repo), "dry_run": self.opts.dry_run, "steps": self.report.steps}
        report_path.write_text(json.dumps(body, indent=1) + "\n", encoding="utf-8")
        con = sqlite3.connect(data / "jobs.db", timeout=30)
        try:
            with con:
                for name, line in [*self.activity, *extra]:
                    con.execute("INSERT INTO activity (at, area, kind, icon, tone, text, why) "
                                "VALUES (?, 'settings', ?, 'settings', 'info', ?, ?)",
                                (clock.iso(), f"import.{name}", line, WHY))
                if not extra:
                    con.execute("INSERT INTO imports (at, kind, source, report) VALUES (?, 'owner', ?, ?)",
                                (clock.iso(), str(self.repo), json.dumps(body)))
            con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        finally:
            con.close()
        self.activity = []

    def marker(self, data: Path) -> None:
        """Writes `.hal-data` with the SHA-256 of the sealed database."""
        from jobhunt import __version__

        body = {"created": clock.iso(), "kind": "imported", "source": str(self.repo), "code_version": __version__,
                "counts": self.counts.get("rows", {}), "sha256": _sha(data / "jobs.db"), "locked": self.locked}
        (data / paths.MARKER).write_text(json.dumps(body) + "\n", encoding="utf-8")

    # 12, 13. After the seal -------------------------------------------------------------------------------
    def retire(self) -> list[tuple[str, str]]:
        if not self.opts.retire_timers:
            line = "Kept your timers. Start your timers again: systemctl --user start " + " ".join(TIMERS)
            self.report.add("timers", line)
            return [("timers", line)]
        retired = self.system.platform_call("retire_legacy_timers")()
        self.locked["timers_retired"] = True
        line = f"Retired the old timers ({len(retired or TIMERS)}); Hal's own schedule runs them now."
        self.report.add("timers", line)
        return [("timers", line)]

    def lock_old(self) -> list[tuple[str, str]]:
        tag = f"{LOCKED_SUFFIX}{self.day}"
        moves = [(self.old, self.repo / f"data{tag}"), (self.repo / "packs", self.repo / f"packs{tag}"),
                 (self.repo / "config.toml", self.repo / f"config{tag}.toml"),
                 (self.legacy_secrets, self.legacy_secrets.with_name(self.legacy_secrets.name + tag))]
        for src, dest in moves:
            if src.exists():
                if dest.exists():
                    raise ImportStopped(f"{dest} already exists, so the old copy was not renamed. Move it away and "
                                        "run `python -m jobhunt import --undo`, then import again.")
                src.rename(dest)
                self.locked[str(src)] = str(dest)
                _read_only(dest)
        line = f"Locked the old copy read-only as data{tag}, packs{tag} and config{tag}.toml."
        self.report.add("lock", line)
        return [("lock", line)]


def _read_only(path: Path) -> None:
    """chmod -R a-w, leaving folders enterable."""
    targets = [path, *path.rglob("*")] if path.is_dir() else [path]
    for p in reversed(targets):
        if not p.is_symlink():
            p.chmod(stat.S_IMODE(p.stat().st_mode) & ~(stat.S_IWUSR | stat.S_IWGRP | stat.S_IWOTH))


def _writable(path: Path) -> None:
    """Gives the owner write permission back on a locked copy, so it can be renamed and used again."""
    for p in [path, *path.rglob("*")] if path.is_dir() else [path]:
        if not p.is_symlink():
            p.chmod(stat.S_IMODE(p.stat().st_mode) | stat.S_IWUSR)


@contextlib.contextmanager
def _import_lock(target: Path) -> Iterator[None]:
    try:
        with migrate.file_lock(target.parent / f".{target.name}.import.lock", timeout=LOCK_TIMEOUT_S):
            yield
    except migrate.MigrationError as e:
        raise ImportStopped("Another import is running. Wait for it to finish.") from e


@contextlib.contextmanager
def _pointed_at(home: Path) -> Iterator[None]:
    """Every folder under `home` for the block, with the settings cache dropped on the way in and out."""
    from jobhunt.core.settings import store

    store.drop_cache()
    try:
        with paths.set_override(home):
            yield
    finally:
        store.drop_cache()


def _staging(target: Path, dry_run: bool) -> Path:
    if dry_run:
        return Path(tempfile.mkdtemp(prefix="hal-import-dry-run-"))
    home = target.parent / f".{target.name}.importing"
    if home.exists():
        if not (home / "data" / INCOMPLETE).exists() and any(home.iterdir()):
            raise ImportStopped(f"{home} is in the way and was not made by an import. Move it away first.")
        shutil.rmtree(home)
    home.mkdir(parents=True)
    return home


def run(opts: Options, system: System | None = None) -> Report:
    """Runs the import (or a dry run of it) and returns its report; raises `ImportStopped` with the reason."""
    system = system or System()
    job = _Run(opts, system)
    with _import_lock(job.target):
        job.preconditions()
        staging = _staging(job.target, opts.dry_run)
        data = staging / "data"
        job.locked = {}
        try:
            data.mkdir(parents=True)
            (data / INCOMPLETE).write_text(clock.iso() + "\n", encoding="utf-8")
            job.step("start", "Started the move; the old data stays in use until it is finished.")
            job.copy(data)
            job.verify(data)
            with _pointed_at(staging):
                job.migrate(data)
                job.settings_step()
                job.identity_step()
                job.rescope_step(data)
                job.lists_step()
                job.hal_step()
                job.rescore_step(data)
            if opts.dry_run:
                job.secrets_step()
            job.write_records(data)
            job.marker(data)
            (data / INCOMPLETE).unlink()
        except BaseException:
            if opts.dry_run:
                shutil.rmtree(staging, ignore_errors=True)
            raise
        if opts.dry_run:
            job.report.add("seal", "Dry run: nothing was changed. Run again without --dry-run to move your data.")
            shutil.rmtree(staging, ignore_errors=True)
            return job.report
        _seal(job, data)
        sealed = f"Your data now lives in {job.target}."
        job.report.add("seal", sealed)
        system.after_step("seal")
        job.secrets_step()
        extra = [("seal", sealed), *job.retire(), *job.lock_old()]
        job.write_records(job.target, extra)
        job.marker(job.target)
        shutil.rmtree(staging, ignore_errors=True)
        system.after_step("done")
    return job.report


def _seal(job: _Run, data: Path) -> None:
    if job.replaced is not None and job.replaced.exists():
        aside = data / "backups" / f"replaced-{clock.iso().replace(':', '')}"
        aside.parent.mkdir(parents=True, exist_ok=True)
        job.replaced.rename(aside)
    elif job.target.exists():
        job.target.rmdir()
    job.target.parent.mkdir(parents=True, exist_ok=True)
    data.rename(job.target)
    if os.name != "nt":
        job.target.chmod(paths.PRIVATE_MODE)


def undo(*, force: bool = False, system: System | None = None) -> Report:
    """Puts the old layout back: the old copy writable and in place, the marker aside."""
    system = system or System()
    report = Report(say=system.say)
    target, _ = target_dirs()
    marker_path = target / paths.MARKER
    if not marker_path.is_file():
        raise ImportStopped(f"There is no finished import to undo in {target}.")
    marker = json.loads(marker_path.read_text(encoding="utf-8"))
    if marker.get("kind") != "imported":
        raise ImportStopped(f"{target} was not made by an import, so there is nothing to undo.")
    con = sqlite3.connect(target / "jobs.db", timeout=30)
    try:
        con.execute("PRAGMA wal_checkpoint(TRUNCATE)")
    finally:
        con.close()
    if _sha(target / "jobs.db") != marker.get("sha256"):
        if not force:
            raise ImportStopped("Hal has saved changes in the new folder since the import. Changes made in the new "
                                "folder will be left behind. Run again with --force to go back anyway.")
        report.add("undo", "Changes made in the new folder will be left behind.")
    locked = dict(marker.get("locked") or {})
    if locked.pop("timers_retired", False):
        restore = system.platform_call("restore_legacy_timers")
        if restore:
            restore()
            report.add("timers", "Put the old timers back.")
        else:
            report.add("timers", "Put the old timers back yourself: systemctl --user enable --now " + " ".join(TIMERS))
    for original, renamed in locked.items():
        src, dest = Path(renamed), Path(original)
        if src.exists() and not dest.exists():
            _writable(src)
            src.rename(dest)
    marker_path.rename(target / f"{paths.MARKER}.undone-{clock.iso().replace(':', '')}")
    report.add("undo", f"Put the old data back in {marker.get('source')}; Hal uses it again.")
    return report
