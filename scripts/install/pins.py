"""Keeps the install files' pinned downloads equal to `pins.toml`, and makes the copies the owner hands over.

    python scripts/install/pins.py --check              the install files hold exactly the pins (tests run this)
    python scripts/install/pins.py --write              copy the pins from pins.toml into the install files
    python scripts/install/pins.py --fill               download each file once, record its SHA-256, then --write
    python scripts/install/pins.py --make DIR [--slug account/repo]
                                                        filled-in install files for handing over, in DIR

The Windows install file is sent on its own, so its pins are copied into it between `# PINS-START` and
`# PINS-END` rather than read at run time. The copies in the repository say OWNER/REPO, because the account
name is a personal string that is never committed; `--make` takes it from this checkout's `origin` remote.
`--fill` is the only part that downloads anything; it uses the standard library with a timeout.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import subprocess
import sys
import tomllib
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PINS = HERE / "pins.toml"
REPO = HERE / "repo.toml"
WINDOWS = HERE / "install-hal-windows.cmd"
LINUX = HERE / "install-hal-linux.sh"
PLACEHOLDER = "https://github.com/OWNER/REPO.git"
BLOCK = re.compile(r"(# PINS-START[^\n]*\n)(.*?)(# PINS-END)", re.S)
DOWNLOAD_TIMEOUT_S = 300
GIT_TIMEOUT_S = 10
SLUG = re.compile(r"github\.com[:/]([\w.-]+/[\w.-]+?)(?:\.git)?/?$")


def load() -> dict:
    """pins.toml as {tool: {os: {version, url, sha256}}}."""
    return tomllib.loads(PINS.read_text(encoding="utf-8"))


def windows_block(pins: dict) -> str:
    """The PowerShell `$Pins` table for the Windows install file."""
    lines = ["$Pins = @{"]
    for tool in ("uv", "git", "gh"):
        p = pins[tool]["windows"]
        lines.append(f"  '{tool}' = @{{ Version = '{p['version']}'; Url = '{p['url']}'; Sha256 = '{p['sha256']}' }}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def linux_block(pins: dict) -> str:
    """The shell variables for the Linux install file."""
    lines = []
    for tool in ("uv", "gh"):
        p = pins[tool]["linux"]
        name = tool.upper()
        lines += [f'{name}_VERSION="{p["version"]}"', f'{name}_URL="{p["url"]}"', f'{name}_SHA256="{p["sha256"]}"']
    return "\n".join(lines) + "\n"


def _read(path: Path) -> str:
    return path.read_bytes().decode("ascii").replace("\r\n", "\n")


def _write(path: Path, text: str) -> None:
    data = text.replace("\n", "\r\n") if path.suffix == ".cmd" else text
    path.write_bytes(data.encode("ascii"))


def with_pins(text: str, block: str) -> str:
    """`text` with its pins block replaced."""
    if not BLOCK.search(text):
        raise ValueError("no PINS-START ... PINS-END block")
    return BLOCK.sub(lambda m: m.group(1) + block + m.group(3), text, count=1)


def pinned_block(text: str) -> str:
    """The pins block of an install file's text."""
    m = BLOCK.search(text)
    if not m:
        raise ValueError("no PINS-START ... PINS-END block")
    return m.group(2)


def check() -> list[str]:
    """The install files whose pins differ from pins.toml (empty when all match)."""
    pins = load()
    out = []
    for path, block in ((WINDOWS, windows_block(pins)), (LINUX, linux_block(pins))):
        if pinned_block(_read(path)) != block:
            out.append(path.name)
    return out


def write() -> None:
    """Copies pins.toml into both install files."""
    pins = load()
    for path, block in ((WINDOWS, windows_block(pins)), (LINUX, linux_block(pins))):
        _write(path, with_pins(_read(path), block))


def fill() -> None:
    """Downloads each pinned file, writes its SHA-256 into pins.toml, then into the install files."""
    text = PINS.read_text(encoding="utf-8")
    for tool, systems in load().items():
        for system, pin in systems.items():
            with urllib.request.urlopen(pin["url"], timeout=DOWNLOAD_TIMEOUT_S) as reply:
                digest = hashlib.sha256(reply.read()).hexdigest()
            section = re.compile(rf'(\[{tool}\.{system}\][^\[]*?sha256 = ")[0-9a-f]*(")', re.S)
            text = section.sub(lambda m, d=digest: m.group(1) + d + m.group(2), text, count=1)
            print(f"{tool} {system} {pin['version']}: {digest}")
    PINS.write_text(text, encoding="utf-8")
    write()


def slug(given: str | None) -> str:
    """account/repo: `--slug`, else repo.toml's, else this checkout's `origin` remote."""
    if given:
        return given
    configured = tomllib.loads(REPO.read_text(encoding="utf-8")).get("slug")
    if configured:
        return configured
    done = subprocess.run(["git", "-C", str(HERE), "remote", "get-url", "origin"], capture_output=True, text=True,
                          timeout=GIT_TIMEOUT_S, check=False)
    m = SLUG.search(done.stdout.strip())
    if not m:
        raise SystemExit("This checkout has no GitHub origin remote. Pass --slug account/repo.")
    return m.group(1)


def make(folder: Path, given: str | None) -> list[Path]:
    """Filled-in copies of both install files in `folder`, ready to hand over."""
    url = f"https://github.com/{slug(given)}.git"
    folder.mkdir(parents=True, exist_ok=True)
    out = []
    for path in (WINDOWS, LINUX):
        text = _read(path)
        if PLACEHOLDER not in text:
            raise SystemExit(f"{path.name} has no {PLACEHOLDER} to fill in.")
        dest = folder / path.name
        _write(dest, text.replace(PLACEHOLDER, url))
        out.append(dest)
    return out


def main() -> int:
    """The command line above."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--fill", action="store_true")
    mode.add_argument("--make", type=Path, metavar="DIR")
    p.add_argument("--slug", help="the GitHub account/repo, when not the origin remote")
    args = p.parse_args()
    if args.check:
        bad = check()
        for name in bad:
            print(f"{name}: its pins differ from pins.toml; run pins.py --write")
        return 1 if bad else 0
    if args.write:
        write()
    elif args.fill:
        fill()
    else:
        for path in make(args.make, args.slug):
            print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
