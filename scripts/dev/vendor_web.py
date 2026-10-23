"""Fetch the new app's vendored web files: Preact, its hooks, htm, the fonts, the CV fonts and axe-core.

Every file comes from an npm registry tarball whose SHA-256 is pinned below, so a changed upload
can never slip in. The script writes `jobhunt/web/static/app/lib/`, `jobhunt/web/static/app/fonts/`
(the CV fonts in its `cv/` folder) and `tests/vendor/axe.min.js`, and is run by hand only when a pin
changes; the results are committed, so nothing needs Node, npm or the network at runtime or in CI.

Non-ASCII characters in vendored JavaScript (axe-core has four, all inside string literals) are
rewritten as `\\u` escapes (four hex digits), which mean the same in JavaScript and keep every committed text file
ASCII.

Run with: python scripts/dev/vendor_web.py [--from DIR]   (DIR holds tarballs downloaded earlier)
"""
from __future__ import annotations

import argparse
import hashlib
import io
import sys
import tarfile
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
APP = ROOT / "jobhunt" / "web" / "static" / "app"
REGISTRY = "https://registry.npmjs.org"
TIMEOUT_S = 60  # the largest tarball (axe-core) is about 1 MB


@dataclass(frozen=True)
class Package:
    """One pinned npm tarball and the files taken from it (member path -> destination)."""

    name: str
    version: str
    sha256: str
    files: dict[str, Path]
    licence: str = "package/LICENSE"

    @property
    def tarball(self) -> str:
        """The tarball's file name, as the registry serves it."""
        return f"{self.name.split('/')[-1]}-{self.version}.tgz"

    @property
    def url(self) -> str:
        """The registry address of the tarball."""
        return f"{REGISTRY}/{self.name}/-/{self.tarball}"


def _fonts(stem: str, weights: tuple[int, ...], folder: Path = APP / "fonts") -> dict[str, Path]:
    return {f"package/files/{stem}-{subset}-{w}-normal.woff2": folder / f"{stem}-{subset}-{w}.woff2"
            for w in weights for subset in ("latin", "latin-ext")}


CV_FONTS = APP / "fonts" / "cv"   # printed CVs and letters; bold text uses the 600 weight


PACKAGES = (
    Package("preact", "10.24.3", "d8f5d1d6bb058659079b26044ff532a77eb7060b9ae001faef80257891151880",
            {"package/dist/preact.module.js": APP / "lib" / "preact.mjs",
             "package/hooks/dist/hooks.module.js": APP / "lib" / "hooks.mjs"}),
    Package("htm", "3.1.1", "2425b9bee11409177bcabc7f32e319926fc6690c1701c0b257c88bdff2d5ba90",
            {"package/dist/htm.module.js": APP / "lib" / "htm.mjs"}),
    Package("@fontsource/atkinson-hyperlegible-next", "5.3.0",
            "33dcb7d0d4e2ea98a96985ca7448f738c3b5b370cfc5230b1e40e4f728920cc2",
            _fonts("atkinson-hyperlegible-next", (400, 500, 700))),
    Package("@fontsource/schibsted-grotesk", "5.3.0",
            "2dd3c8fc0a5f5731bf5e01904fef6ce67aad9b00873ca7a1b4c731c46d003acd",
            _fonts("schibsted-grotesk", (500, 700, 800))),
    Package("@fontsource/ibm-plex-mono", "5.3.0",
            "60d3c0cfa549cb06fcb8867ee83f994d78e212abfac3bef3275436acb5c4d029",
            _fonts("ibm-plex-mono", (400, 500))),
    Package("@fontsource/source-sans-3", "5.3.0",
            "f8a0e1b603f5c85a468946a51af4a761d726c28baf79200aafb8736fe9e7d7a7",
            _fonts("source-sans-3", (400, 600), CV_FONTS)),
    Package("@fontsource/source-serif-4", "5.3.0",
            "8e80168e17a799995fb6cae53f2bde848d6f9ca4e5d282b297b5d40c86d4d545",
            _fonts("source-serif-4", (400, 600), CV_FONTS)),
    Package("axe-core", "4.10.3", "0f2b4d7dcdf7d1219df8d1959ad68e565f51d14c3f0d88bb71cd59abeb956292",
            {"package/axe.min.js": ROOT / "tests" / "vendor" / "axe.min.js"}),
)
LIB_LICENCES = ("preact", "htm")
FONT_LICENCES = ("@fontsource/atkinson-hyperlegible-next", "@fontsource/schibsted-grotesk",
                 "@fontsource/ibm-plex-mono")
CV_FONT_LICENCES = ("@fontsource/source-sans-3", "@fontsource/source-serif-4")
CV_FONT_INTRO = ("The fonts in this folder are under the SIL Open Font License 1.1. They print CVs and letters.\n"
                 "Only the latin and latin-ext subsets of the weights CVs use are kept, so the folder stays small.\n")


class PinMismatch(RuntimeError):
    """A downloaded tarball does not match its pinned SHA-256."""


def fetch(pkg: Package, cache: Path | None) -> bytes:
    """The package's tarball, from `cache` when it holds a copy, else from the registry; checked against its pin."""
    local = cache / pkg.tarball if cache else None
    if local and local.is_file():
        data = local.read_bytes()
    else:
        with urllib.request.urlopen(pkg.url, timeout=TIMEOUT_S) as res:
            data = res.read()
    digest = hashlib.sha256(data).hexdigest()
    if digest != pkg.sha256:
        raise PinMismatch(f"{pkg.name} {pkg.version}: SHA-256 {digest}, pinned {pkg.sha256}")
    return data


def ascii_js(text: str) -> str:
    """`text` with every non-ASCII character written as a JavaScript `\\u` escape."""
    out = []
    for ch in text:
        code = ord(ch)
        if code < 0x80:
            out.append(ch)
        elif code <= 0xFFFF:
            out.append(f"\\u{code:04x}")
        else:  # astral characters become a surrogate pair, as JavaScript strings store them
            code -= 0x10000
            out.append(f"\\u{0xD800 + (code >> 10):04x}\\u{0xDC00 + (code & 0x3FF):04x}")
    return "".join(out)


def unpack(pkg: Package, data: bytes) -> str:
    """Writes the package's files to their destinations and returns its licence text."""
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as tar:
        for member, dest in pkg.files.items():
            body = tar.extractfile(member)
            if body is None:
                raise FileNotFoundError(f"{pkg.name}: {member} is not in the tarball")
            raw = body.read()
            dest.parent.mkdir(parents=True, exist_ok=True)
            if dest.suffix in (".js", ".mjs"):
                dest.write_text(ascii_js(raw.decode("utf-8")), encoding="ascii", newline="\n")
            else:
                dest.write_bytes(raw)
        licence = tar.extractfile(pkg.licence)
        if licence is None:
            raise FileNotFoundError(f"{pkg.name}: {pkg.licence} is not in the tarball")
        return licence.read().decode("utf-8")


def licence_file(names: tuple[str, ...], texts: dict[str, str], intro: str) -> str:
    """One text file with each package's licence under a heading naming the package and version."""
    versions = {p.name: p.version for p in PACKAGES}
    parts = [intro]
    for name in names:
        parts.append(f"{'=' * 72}\n{name} {versions[name]}\n{'=' * 72}\n\n{texts[name].strip()}\n")
    return "\n".join(parts)


def main(argv: list[str] | None = None) -> int:
    """Fetches, checks and writes every vendored file; returns the exit code."""
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--from", dest="cache", type=Path, help="folder of tarballs downloaded earlier")
    args = ap.parse_args(argv)
    texts = {}
    for pkg in PACKAGES:
        texts[pkg.name] = unpack(pkg, fetch(pkg, args.cache))
        print(f"{pkg.name} {pkg.version}: {len(pkg.files)} file(s)")
    (APP / "lib" / "LICENSES.txt").write_text(
        licence_file(LIB_LICENCES, texts, "Licences of the JavaScript libraries in this folder.\n"),
        encoding="ascii", newline="\n")
    (APP / "fonts" / "OFL.txt").write_text(
        licence_file(FONT_LICENCES, texts, "The fonts in this folder are under the SIL Open Font License 1.1.\n"),
        encoding="ascii", newline="\n")
    (CV_FONTS / "OFL.txt").write_text(licence_file(CV_FONT_LICENCES, texts, CV_FONT_INTRO),
                                      encoding="ascii", newline="\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
