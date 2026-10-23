"""Keeps the install files' pinned downloads and repository address equal to `pins.toml` and `repo.toml`.

    python scripts/install/pins.py --check              the install files hold exactly the pins (tests run this)
    python scripts/install/pins.py --write              copy the pins and the address into the install files
    python scripts/install/pins.py --fill               download each file once, record its SHA-256, then --write

The Windows install file runs on its own, before any clone exists, so its pins and the repository address are
copied into it (the pins between `# PINS-START` and `# PINS-END`) rather than read at run time.
`--fill` is the only part that downloads anything; it uses the standard library with a timeout.
"""
from __future__ import annotations

import argparse
import hashlib
import re
import sys
import tomllib
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
PINS = HERE / "pins.toml"
REPO = HERE / "repo.toml"
WINDOWS = HERE / "install-hal-windows.cmd"
LINUX = HERE / "install-hal-linux.sh"
# the line of each install file that names the repository
REPO_LINE = {WINDOWS: re.compile(r'(?m)^set "HAL_REPO=[^"\n]*"'), LINUX: re.compile(r'(?m)^HAL_REPO="[^"\n]*"')}
BLOCK = re.compile(r"(# PINS-START[^\n]*\n)(.*?)(# PINS-END)", re.S)
DOWNLOAD_TIMEOUT_S = 300


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


def repo_line(path: Path, url: str) -> str:
    """The line of install file `path` that names the repository `url`."""
    if path == WINDOWS:
        return f'set "HAL_REPO={url}"'
    return f'HAL_REPO="${{HAL_REPO:-{url}}}"'


def repo_url() -> str:
    """The repository address, built from the slug in repo.toml."""
    return f"https://github.com/{tomllib.loads(REPO.read_text(encoding='utf-8'))['slug']}.git"


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
        text = _read(path)
        if pinned_block(text) != block or repo_line(path, repo_url()) not in REPO_LINE[path].findall(text):
            out.append(path.name)
    return out


def write() -> None:
    """Copies pins.toml and the repository address into both install files."""
    pins = load()
    for path, block in ((WINDOWS, windows_block(pins)), (LINUX, linux_block(pins))):
        line = repo_line(path, repo_url())
        _write(path, REPO_LINE[path].sub(lambda _m, line=line: line, with_pins(_read(path), block), count=1))


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


def main() -> int:
    """The command line above."""
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = p.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--write", action="store_true")
    mode.add_argument("--fill", action="store_true")
    args = p.parse_args()
    if args.check:
        bad = check()
        for name in bad:
            print(f"{name}: its pins or repository address differ from pins.toml or repo.toml; run pins.py --write")
        return 1 if bad else 0
    if args.write:
        write()
    else:
        fill()
    return 0


if __name__ == "__main__":
    sys.exit(main())
