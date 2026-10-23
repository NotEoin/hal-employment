#!/usr/bin/env bash
# Updates Hal Employment from a terminal, for when the app cannot update itself.
# Runs scripts/updater.py under uv's own Python, outside .venv, with the uv folders the install file used.
# The whole script is one block, so the update replacing this file cannot change it mid-run.
{
set -u
code="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
tools="$(dirname "$code")/tools"
export UV_PYTHON_INSTALL_DIR="$tools/python" UV_CACHE_DIR="$tools/uv-cache"
uv="$tools/uv"
[ -x "$uv" ] || uv="$(command -v uv || true)"
if [ -z "$uv" ]; then
  echo "Hal was not installed with its install file. Run that file again."
  status=1
else
  "$uv" run --no-project --python 3.13 "$code/scripts/updater.py" "$@"
  status=$?
fi
if [ -t 0 ]; then read -r -n 1 -s -p "Press any key to close." || true; echo; fi
exit "$status"
}
