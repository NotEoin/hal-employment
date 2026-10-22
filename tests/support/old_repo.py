"""A made-up checkout in the old layout, for the importer's tests.

`build(root)` writes what today's repo holds next to its code: `data/` with a made-up database, private
answers, browser profile, screenshots and CV files; `packs/`; `config.toml` and the three list files; and
the small parts of the old code that `scripts/dev/export_owner_prefs.py` reads (`score.py`'s constants
and `place()`, the aggregator searches, the email address, `cv.py` and `llm.py` defaults, and an
`autofill.py` whose `profile()` has the old shape). Beside it, a made-up website with `cv.json` and its
contact module, a made-up Hal folder with `hal.conf`, and the old key files of `~/.config/jobhunt`.

Everything here is invented: the person, the places, the employers, the answers, the keys and the topic.
The private answers and the carer answer are canaries, so a test can prove they never leak.
"""
from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path

from support import old_db

PRIVATE_CANARY = "SYNTH-PRIVATE-7731"
CARER_CANARY = "SYNTH-PRIVATE-CARER-4410"
OLD_TOPIC = "made-up-old-topic-q8w7e6r5"
GIT_TIMEOUT_S = 30
SECRETS = {"gmail-app-password": "made-up-app-password", "adzuna": "made-up-adzuna-key", "reed": "made-up-reed-key"}
PACKS = ("Aldermoor Trust - Graduate Analyst", "Birchwell Studio - Junior Developer", "_base-cv")

PRIVATE_TOML = f"""# Answers about health, never in git.
[applicant]
disability = "{PRIVATE_CANARY}"
adjustments = "{PRIVATE_CANARY} extra"
"""

SCORE_PY = '''"""Made-up scoring constants in the old shape."""
import re

SALARY_FLOOR = 24_000

BRISTOL_AREA = (
    r"\\bbristol\\b|\\bclifton\\b|\\bbedminster\\b|"
    r"\\bportishead\\b|\\bavon (and|&) somerset\\b"
)
LEEDS = r"\\bleeds\\b|\\bheadingley\\b|\\bkirkstalls?\\b"
SKILLS = {r"\\bpython\\b": 3, r"\\bsql\\b": 1, r"\\b(pandas|numpy)\\b": 2, r"\\bc\\+\\+\\b": 2}


def place(location, text):
    loc = (location or "").lower()
    if re.search(LEEDS, loc):
        return "Leeds", 6
    if re.search(BRISTOL_AREA, loc):
        return "Bristol", 10
    return None, 0


def score(job):
    train_and_place = False
    if train_and_place and not re.search(BRISTOL_AREA, (job.get("location") or "").lower()):
        return 0, "train-and-place outside Bristol"
    return 40, ""
'''

AGGREGATORS_PY = '''QUERIES = ["graduate analyst", "junior archivist"]
PLACES = ["Bristol", "Leeds"]
'''

GMAIL_PY = 'ADDRESS = "sam.example@example.org"\nFIRST_SYNC_DAYS = 400\n'

CV_PY = '''from pathlib import Path
from .config import CONFIG

SITE = Path(CONFIG.get("cv_site", "~/made-up-site")).expanduser()
'''

LLM_PY = '''from pathlib import Path
from .config import CONFIG

HAL_ROOT = Path(CONFIG.get("hal_root", "~/made-up-hal")).expanduser()
'''

AUTOFILL_PY = '''"""Made-up form filler in the old shape: profile() merges contact, cv.json, config and private()."""
from .config import CONFIG, private

HEALTH = ("disability", "disability_type", "guaranteed_interview", "adjustments")


def profile() -> dict:
    name, email, phone = contact()
    first, _, last = name.partition(" ")
    c = cv.load()["contact"]
    p = {
        "first_name": first, "last_name": last, "full_name": name, "preferred_name": first,
        "email": email.lower(), "phone": phone,
        "linkedin": f"https://www.{c['linkedin']}", "github": f"https://{c['github']}",
        "website": f"https://{c['site']}",
        "city": "Bristol", "country": "United Kingdom", "location": "Bristol, United Kingdom",
        "right_to_work": "Yes", "work_status": "British or Irish citizen", "sponsorship": "No",
        "nationality": "British",
        "notice": "None, available immediately", "start": "Immediately",
        "university": "University of Nowhere", "degree": "BA (Hons) Made-up Studies",
        "grade": "Upper second-class honours", "grad_year": "2026",
        "office": "Yes", "relocate": "Yes",
        "diversity": "Prefer not to say",
    }
    p.update(CONFIG.get("applicant", {}))
    p.update({k: v for k, v in private().items() if k in HEALTH})
    return p
'''

EXCLUDED = """# Employers Sam will not work for. One name per line.
# Made-up arms makers
Grimsteel Munitions Ltd
Ironfold Defence

# Made-up train-and-place providers that charge trainees
Quickskill Academy
"""


@dataclass
class OldRepo:
    """Where everything of one made-up old install lives."""

    root: Path            # the checkout: data/, packs/, config.toml, jobhunt/
    site: Path            # the made-up website (cv.json, tools/cv_contact.py)
    hal: Path             # the made-up Hal folder with hal.conf
    secrets: Path         # the made-up ~/.config/jobhunt

    @property
    def data(self) -> Path:
        """The old data folder."""
        return self.root / "data"


def _write(path: Path, text: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if isinstance(text, bytes):
        path.write_bytes(text)
    else:
        path.write_text(text, encoding="utf-8")


def config_toml(repo: OldRepo, *, carer: bool = True) -> str:
    """A made-up config.toml in the L shape."""
    lines = ["alert_score = 70", f'ntfy_topic = "{OLD_TOPIC}"', f'hal_root = "{repo.hal}"',
             f'hal_vault = "{repo.hal / "vault"}"', 'speak_command = "/usr/bin/true"',
             f'record_vault = "{repo.root.parent / "record"}"', "queue_size = 5", "queue_min_score = 55",
             'apply_browser = "extension"', 'apply_window = "same"', "signed_in_apply = []",
             'gpu_ignore = ["made-up-voice"]', f'cv_site = "{repo.site}"', 'digest_to = "sam.example@example.org"',
             "", "[applicant]", 'city = "Bristol"', 'notice = "None, available immediately"',
             'diversity = "Prefer not to say"', 'salary = "Around 26,000 pounds"']
    if carer:
        lines.append(f'carer = "{CARER_CANARY}"')
    return "\n".join(lines) + "\n"


def build(root: Path, *, carer: bool = True, git: bool = True) -> OldRepo:
    """Writes a made-up old checkout under `root/repo` (with its website, Hal folder and key files)."""
    repo = OldRepo(root=root / "repo", site=root / "site", hal=root / "hal", secrets=root / "old-config" / "jobhunt")
    old_db.build(repo.data / "jobs.db", "L")
    _write(repo.data / "private.toml", PRIVATE_TOML)
    (repo.data / "private.toml").chmod(0o600)
    for rel, body in (("Default/Preferences", "{}"), ("Default/Cookies", b"\x00made-up"), ("Local State", "{}")):
        _write(repo.data / "browser-profile" / rel, body)
    _write(repo.data / "apply" / "form-1.png", b"\x89PNG made-up")
    _write(repo.data / "signed-in.json", json.dumps({"example.org": True}))
    _write(repo.data / "cv-words.txt", "pandas\nnumpy\n")
    for name in ("cv-overrides.json", "cv-overrides.before-20261001T090000Z.json", "cv-built.json",
                 "cv-built-overrides.json", "cv-review.json"):
        _write(repo.data / name, json.dumps({"made_up": name}))
    _write(repo.data / "cv-origin", "built\n")
    _write(repo.data / "cv-sources" / "projects.txt", "A made-up project.\n")
    for name in PACKS:
        _write(repo.root / "packs" / name / "pack.md", f"# {name}\n\n- made up\n")
    _write(repo.root / "packs" / PACKS[0] / "letter.pdf", b"%PDF-1.4 made-up")

    _write(repo.root / "config.toml", config_toml(repo, carer=carer))
    _write(repo.root / "boards.toml", '[[board]]\nname = "Aldermoor Trust"\nats = "greenhouse"\n')
    _write(repo.root / "companies.txt", "Aldermoor Trust\nBirchwell Studio\n")
    _write(repo.root / "excluded-companies.txt", EXCLUDED)
    for rel, text in (("jobhunt/score.py", SCORE_PY), ("jobhunt/sources/aggregators.py", AGGREGATORS_PY),
                      ("jobhunt/gmail.py", GMAIL_PY), ("jobhunt/cv.py", CV_PY), ("jobhunt/llm.py", LLM_PY),
                      ("jobhunt/autofill.py", AUTOFILL_PY)):
        _write(repo.root / rel, text)

    contact = {"linkedin": "linkedin.com/in/sam-example", "github": "github.com/sam-example",
               "site": "sam-example.example.org"}
    _write(repo.site / "src" / "data" / "cv.json", json.dumps({"contact": contact, "summary": "Made up."}))
    _write(repo.site / "tools" / "cv_contact.py",
           'NAME = "Sam Example"\nEMAIL = "Sam.Example@example.org"\nPHONE = "07700 900123"\n')
    _write(repo.hal / "hal.conf", 'HAL_MODEL="made-up-main:7b"\nHAL_WRITER_MODEL=made-up-writer:14b\n'
                                  'HAL_FAST_MODEL=made-up-fast:3b\n')
    for name, value in SECRETS.items():
        _write(repo.secrets / name, value + "\n")
    if git:
        _commit(repo.root)
    return repo


def _commit(root: Path) -> None:
    """Commits the code files only (data/ and packs/ stay untracked, as in the real repo)."""
    def git(*args: str) -> None:
        subprocess.run(["git", "-C", str(root), *args], check=True, capture_output=True, timeout=GIT_TIMEOUT_S)

    (root / ".gitignore").write_text("data/\npacks/\n", encoding="utf-8")
    git("init", "-q")
    git("add", ".gitignore", "jobhunt", "config.toml", "boards.toml", "companies.txt", "excluded-companies.txt")
    git("-c", "user.name=Made Up", "-c", "user.email=made.up@example.org", "-c", "commit.gpgsign=false",
        "commit", "-q", "-m", "old code")


def tree_state(*roots: Path) -> dict[str, tuple[int, bytes]]:
    """Every file under `roots` with its mode and bytes, to prove a run changed nothing."""
    out = {}
    for base in roots:
        if not base.exists():
            continue
        for path in sorted(base.rglob("*")):
            if path.is_file() and ".git" not in path.parts:
                out[str(path)] = (path.stat().st_mode, path.read_bytes())
            elif path.is_dir():
                out[str(path)] = (path.stat().st_mode, b"")
    return out
