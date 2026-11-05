# Third-party notices

Hal Employment uses the software and fonts below. Each keeps its own licence. Versions are the ones pinned
in `uv.lock` and `scripts/dev/vendor_web.py` on 2026-10-23; `uv.lock` is the full list of Python packages,
including the ones these bring with them.

## Files kept in this repository

These are copied into the repository, so their notices travel with it. The full licence texts are in the
folder beside each file.

| Software | Version | Licence | Copyright | Where |
| --- | --- | --- | --- | --- |
| Preact (with its hooks) | 10.24.3 | MIT | 2015-present Jason Miller | `jobhunt/web/static/app/lib/` (`LICENSES.txt`) |
| htm | 3.1.1 | Apache-2.0 | 2018 Google Inc. | `jobhunt/web/static/app/lib/` (`LICENSES.txt`) |
| Atkinson Hyperlegible Next (font, from @fontsource/atkinson-hyperlegible-next) | 5.3.0 | OFL-1.1 | 2020-2024 The Atkinson Hyperlegible Next Project Authors | `jobhunt/web/static/app/fonts/` (`OFL.txt`) |
| Schibsted Grotesk (font, from @fontsource/schibsted-grotesk) | 5.3.0 | OFL-1.1 | 2023 The Schibsted-Grotesk Project Authors | `jobhunt/web/static/app/fonts/` (`OFL.txt`) |
| IBM Plex Mono (font, from @fontsource/ibm-plex-mono) | 5.3.0 | OFL-1.1 | 2017 IBM Corp. | `jobhunt/web/static/app/fonts/` (`OFL.txt`) |
| Source Sans 3 (font for CVs and letters, from @fontsource/source-sans-3) | 5.3.0 | OFL-1.1 | Adobe, Reserved Font Name "Source" | `jobhunt/web/static/app/fonts/cv/` (`OFL.txt`) |
| Source Serif 4 (font for CVs and letters, from @fontsource/source-serif-4) | 5.3.0 | OFL-1.1 | Adobe, Reserved Font Name "Source" | `jobhunt/web/static/app/fonts/cv/` (`OFL.txt`) |
| axe-core (accessibility checks, tests only; never shipped in the app) | 4.10.3 | MPL-2.0 | 2015-2025 Deque Systems, Inc. | `tests/vendor/axe.min.js` (notice at the top of the file) |
| Helvetica character widths (Adobe Core14 AFM files) | | Adobe's AFM notice | 1985, 1987, 1989, 1990, 1997 Adobe Systems Incorporated | `jobhunt/pdfdraw.py` (only the width numbers; notice in the file's header) |

The AFM notice, as the files give it: "Copyright (c) 1985, 1987, 1989, 1990, 1997 Adobe Systems
Incorporated. All rights reserved. Adobe permits the AFM files to be used, copied and distributed for any
purpose and without charge, with or without modification, provided the copyright notices are retained."

## Python packages Hal installs

Installed by `uv sync` from `uv.lock`; each package ships its own licence file.

| Package | Version | Licence | Why Hal uses it |
| --- | --- | --- | --- |
| keyring | 25.7.0 | MIT | Keeps passwords and keys in Windows Credential Manager or the desktop keyring |
| pypdfium2 (with PDFium) | 5.14.0 | Apache-2.0 and BSD-3-Clause (PDFium: BSD-3-Clause and its own third-party notices) | Reads PDF text and draws page pictures |
| segno | 1.6.6 | BSD-3-Clause | The QR code for phone alerts |
| truststore (Windows only) | 0.10.5 | MIT | Trusts the same web certificates Windows trusts, so job sites load on a new Windows |
| playwright (extra `browser`) | 1.63.0 | Apache-2.0 | Hal's own browser window on Linux, printing on Linux, browser tests |
| markitdown (extra `documents`, Linux only) | 0.1.9 | MIT | Reads the documents in your record (Word, PDF, PowerPoint, Excel) with their headings |
| ruff (developers only) | 0.17.1 | MIT | Lint checks |

The `documents` extra brings magika (Apache-2.0) and onnxruntime (MIT), which need the Microsoft C++
runtime, so it is never installed on Windows. keyring brings jaraco.classes, jaraco.context,
jaraco.functools and more-itertools (MIT), with jeepney (MIT) and SecretStorage (BSD-3-Clause) on Linux and
pywin32-ctypes (BSD-3-Clause) on Windows.

## Tools the install file downloads

Not part of this repository. The install file downloads them from their makers' GitHub releases, each
pinned by version and SHA-256 in `scripts/install/pins.toml`, into Hal's own `tools` folder.

| Tool | Version | Licence | Why |
| --- | --- | --- | --- |
| uv | 0.8.22 | MIT or Apache-2.0 | Gets Python and Hal's packages, without admin rights |
| Git for Windows (PortableGit, Windows only) | 2.51.0 | GPL-2.0 | Downloads Hal and its updates |
| GitHub CLI | 2.79.0 | MIT | Signs you in to GitHub in your browser |

Playwright downloads its own Chromium build (BSD-3-Clause and others), and Claude Code, when you choose a
Claude subscription, is installed with Anthropic's own installer under Anthropic's terms.

## Writing rules: credits

Hal's writing rules (`jobhunt/writing/rules.py`) and its checker are Hal's own wording, written for CVs,
cover letters and form answers. No text or code is copied from the projects below; their catalogues of
the patterns that make writing read as machine-made informed which patterns Hal looks for, and they are
credited here with thanks. Each is under the MIT licence.

| Project | Licence | Copyright | Where |
| --- | --- | --- | --- |
| no-ai-slop | MIT | 2026 Peter Yang | https://github.com/petergyang/no-ai-slop |
| avoid-ai-writing | MIT | 2026 Conor Bronsdon | https://github.com/conorbronsdon/avoid-ai-writing |
| humanizer | MIT | 2025 Siqi Chen | https://github.com/blader/humanizer |
