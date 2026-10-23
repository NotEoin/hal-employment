#!/usr/bin/env bash
# Hal Employment installer and repair for Linux. Run it with: bash install-hal-linux.sh
# No admin rights are needed, except to install git itself if the computer has none.
# Everything goes into ~/.local/opt/hal-employment; nothing is added to PATH, and your global git config is
# not changed. Every download is pinned by version and SHA-256 (scripts/install/pins.toml, copied in).
# The whole script is one block, so a repair that pulls a new copy of this file cannot change it mid-run.
{
set -euo pipefail

HAL_REPO="https://github.com/OWNER/REPO.git"
# PINS-START (written by scripts/install/pins.py from pins.toml)
UV_VERSION="0.8.22"
UV_URL="https://github.com/astral-sh/uv/releases/download/0.8.22/uv-x86_64-unknown-linux-gnu.tar.gz"
UV_SHA256=""
GH_VERSION="2.79.0"
GH_URL="https://github.com/cli/cli/releases/download/v2.79.0/gh_2.79.0_linux_amd64.tar.gz"
GH_SHA256=""
# PINS-END

slug="${HAL_REPO#https://github.com/}"
slug="${slug%.git}"
owner="${slug%%/*}"
base="$HOME/.local/opt/hal-employment"
tools="$base/tools"
app="$base/app"
logs="${XDG_DATA_HOME:-$HOME/.local/share}/hal-employment/logs"
mkdir -p "$tools" "$logs"
log="$logs/install-$(date -u +%Y%m%dT%H%M%SZ).log"

say() { printf '%s\n' "$1"; printf '%s %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" >>"$log"; }
stop() { say "$1"; exit 1; }
quiet() { "$@" >>"$log" 2>&1; }

pinned() {   # pinned NAME URL SHA256 DEST
  [ -n "$3" ] || stop "This install file is not finished: it has no checksum for $1. Ask $owner for a new one."
  if [ -f "$4" ] && [ "$(sha256sum "$4" | cut -d' ' -f1)" = "$3" ]; then return 0; fi
  curl -fsSL --retry 2 -o "$4" "$2" 2>>"$log" ||
    stop "Hal could not reach GitHub. Check the internet connection, then run this file again."
  if [ "$(sha256sum "$4" | cut -d' ' -f1)" != "$3" ]; then
    rm -f "$4"
    stop "The download of $1 was not the file Hal expected, so Hal stopped. Run this file again."
  fi
}

say "Installing Hal Employment. This takes about 5 minutes."
case "$HAL_REPO" in *OWNER/REPO*) stop "This install file is not filled in yet. Ask the person who set Hal up for a new one." ;; esac
command -v git >/dev/null || stop "Hal needs git. Install it from your Software app (search for git), then run this file again."
command -v curl >/dev/null || stop "Hal needs curl. Install it from your Software app (search for curl), then run this file again."

say "1 of 6: Getting the tools Hal needs"
uv="$tools/uv"
if [ ! -x "$uv" ]; then
  pinned uv "$UV_URL" "$UV_SHA256" "$tools/uv.tar.gz"
  tar -xzf "$tools/uv.tar.gz" -C "$tools" --strip-components=1
  rm -f "$tools/uv.tar.gz"
fi
gh="$tools/gh"
if [ ! -x "$gh" ]; then
  pinned gh "$GH_URL" "$GH_SHA256" "$tools/gh.tar.gz"
  tar -xzf "$tools/gh.tar.gz" -C "$tools" --strip-components=2 --wildcards '*/bin/gh'
  rm -f "$tools/gh.tar.gz"
fi
export UV_NO_MODIFY_PATH=1 UV_PYTHON_INSTALL_DIR="$tools/python" UV_CACHE_DIR="$tools/uv-cache"

say "2 of 6: Signing you in to GitHub"
"$gh" auth status --hostname github.com >>"$log" 2>&1 ||
  "$gh" auth login --web --hostname github.com --git-protocol https ||
  stop "Hal could not sign you in to GitHub. Run this file again."
helper="!$gh auth git-credential"
if [ -d "$app/.git" ]; then
  quiet git -C "$app" pull --ff-only || stop "Hal could not get its newest files. Check the internet connection, then run this file again."
else
  if ! quiet git -c credential.helper= -c "credential.https://github.com.helper=$helper" clone "$HAL_REPO" "$app"; then
    if grep -qiE '403|404|not found|denied' "$log"; then
      stop "Your GitHub account cannot see Hal Employment yet. Ask $owner to add you, accept the invitation email from GitHub, then run this file again."
    fi
    stop "Hal could not reach GitHub. Check the internet connection, then run this file again."
  fi
  quiet git -C "$app" config credential.https://github.com.helper "$helper"
fi

say "3 of 6: Setting up Hal (about 2 minutes)"
quiet "$uv" sync --frozen --no-dev --extra browser --extra documents --project "$app" ||
  stop "Hal could not set itself up. Run this file again. If it happens again, send $owner the file $log"
py="$app/.venv/bin/python"
quiet "$py" -m playwright install chromium ||
  say "Hal could not get its own browser for printing. It will use another way; Is Hal working? says more."

say "4 of 6: Making the menu entry and starting Hal"
"$py" -m jobhunt install --linux --extras browser,documents >>"$log" 2>&1 ||
  stop "Hal could not finish installing. Run this file again. If it happens again, send $owner the file $log"

say "5 of 6: Opening the setup guide"
"$py" -m jobhunt open --next "/#setup" >>"$log" 2>&1 || say "Open Hal Employment from your applications menu."

say "6 of 6: Done. Hal Employment is installed. You can close this window."
exit 0
}
