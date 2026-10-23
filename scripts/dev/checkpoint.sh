#!/usr/bin/env bash
# Runs the test gates in one command, from any folder of a checkout.
#
#   scripts/dev/checkpoint.sh --quick        before each commit: staged lines, tests without the browser
#   scripts/dev/checkpoint.sh                before a pull request: lines since the base, every test, the goldens
#
# Both modes also run the personal-string scan (gate G8); it skips itself when the private list is
# absent, so any clone can run the gates. `--personal` is still accepted and changes nothing.
#
# The base is $HAL_CHECK_BASE, else the merge base with the `rebuild` branch, else the root commit.
# Tests always run in a temporary HAL_EMPLOYMENT_HOME with HAL_TEST_MODE=1, so no gate can reach real data.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$ROOT"
PY="${HAL_PYTHON:-$ROOT/.venv/bin/python}"
[ -x "$PY" ] || PY="$ROOT/.venv/Scripts/python.exe"
[ -x "$PY" ] || { echo "no .venv here; run: uv sync --frozen --extra browser --extra documents" >&2; exit 2; }

QUICK=0
for arg in "$@"; do
  case "$arg" in
    --quick) QUICK=1 ;;
    --personal) ;;
    *) echo "unknown option: $arg" >&2; exit 2 ;;
  esac
done

BASE="${HAL_CHECK_BASE:-}"
if [ -z "$BASE" ]; then
  BASE="$(git merge-base HEAD rebuild 2>/dev/null || git rev-list --max-parents=0 HEAD | tail -n 1)"
fi

TEST_HOME="$(mktemp -d)"
trap 'rm -rf "$TEST_HOME"' EXIT
export HAL_EMPLOYMENT_HOME="$TEST_HOME" HAL_TEST_MODE=1
# A random port in 20000-29999, so a gate never meets the owner's server on 8737.
export HAL_EMPLOYMENT_PORT="${HAL_EMPLOYMENT_PORT:-$((20000 + RANDOM % 10000))}"

failed=()
gate() {
  local name="$1"; shift
  echo "== $name"
  if "$@"; then echo "   ok"; else echo "   FAILED: $name"; failed+=("$name"); fi
}

if [ "$QUICK" = 1 ]; then
  gate "G8 ascii (staged)" "$PY" scripts/dev/check_ascii.py
  gate "G8 ruff (staged)" "$PY" scripts/dev/check_ruff.py
  gate "G8 personal strings (staged)" "$PY" scripts/dev/check_personal.py --staged
  gate "G1 tests" env -u HAL_TEST_BROWSER "$PY" -m unittest discover tests
else
  gate "G8 ascii (since $BASE)" "$PY" scripts/dev/check_ascii.py --base "$BASE"
  gate "G8 ruff (since $BASE)" "$PY" scripts/dev/check_ruff.py --base "$BASE"
  gate "G8 personal strings (tree)" "$PY" scripts/dev/check_personal.py --tree
  gate "G1 tests, browser on" env HAL_TEST_BROWSER=1 "$PY" -m unittest discover tests
  # G2, G3, G5, G6, G7, G9 and G10 also run inside G1; named here so a failure says which promise broke.
  gate "G2 UI smoke" env HAL_TEST_BROWSER=1 PYTHONPATH=tests "$PY" -m unittest ui.test_shell
  gate "G3 Classic on the new server" env PYTHONPATH=tests "$PY" -m unittest test_classic
  [ -f scripts/dev/golden_cli.py ] && gate "G4 CLI goldens" "$PY" scripts/dev/golden_cli.py --check
  gate "G5 privacy (guard, store, form fill)" env PYTHONPATH=tests "$PY" -m unittest test_ai_guard test_settings_private test_autofill.PrivatePaths test_autofill.WhyThisAnswer test_answers_routes
  gate "G6 never Submit" env HAL_TEST_BROWSER=1 PYTHONPATH=tests "$PY" -m unittest test_never_submit test_guard
  gate "G7 settings" env PYTHONPATH=tests "$PY" -m unittest test_settings_registry
  gate "G9 action kinds" env PYTHONPATH=tests "$PY" -m unittest test_action_kinds test_runner
  gate "G10 migrations" env PYTHONPATH=tests "$PY" -m unittest test_migrate test_backup
fi

if [ "${#failed[@]}" -gt 0 ]; then
  echo "failed gates: ${failed[*]}"
  exit 1
fi
echo "all gates passed"
