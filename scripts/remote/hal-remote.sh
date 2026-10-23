#!/usr/bin/env bash
# Opens Hal Employment, running on the desktop, through an SSH tunnel.
#
# Made by `hal-employment-cli remote-launcher` on the desktop, which fills in HOST, PORT and HAL below.
# Run it once with --install to add "Hal Employment (desktop)" to this computer's menu. Each run makes
# one SSH login through a control connection, so a key with a passphrase and no agent asks once. Hal stays
# on 127.0.0.1 on the desktop: only the tunnel reaches it, and closing this window ends the tunnel.
set -u
HOST="USER@DESKTOP"; PORT=8737
HAL='~/.local/bin/hal-employment-cli'
NAME="Hal Employment (desktop)"
CTL="$HOME/.ssh/hal-employment-%C"
SSH_OPTS=(-o StrictHostKeyChecking=accept-new -o ServerAliveInterval=15 -o ServerAliveCountMax=3
          -o ControlMaster=auto -o "ControlPath=$CTL" -o ControlPersist=60)
HEALTH_TRIES=80   # 80 x 0.25 s: the tunnel gets 20 s to carry Hal's first answer
SELF="$(cd "$(dirname "$0")" && pwd)/$(basename "$0")"

install_entry() {
  if [ "$(uname -s)" = "Darwin" ]; then
    # a .command file opens in Terminal, so there is always a window that holds the tunnel
    mkdir -p "$HOME/Applications"
    printf '#!/bin/sh\nexec "%s"\n' "$SELF" > "$HOME/Applications/$NAME.command"
    chmod +x "$HOME/Applications/$NAME.command"
    echo "Added \"$NAME\" to Applications in your home folder."
  else
    # Terminal=true: the tunnel lives in that window; without one it would run on with nothing to close
    local apps="${XDG_DATA_HOME:-$HOME/.local/share}/applications"
    mkdir -p "$apps"
    printf '[Desktop Entry]\nType=Application\nName=%s\nComment=Open Hal Employment from the desktop computer\nExec="%s"\nTerminal=true\nCategories=Office;\n' \
      "$NAME" "$SELF" > "$apps/hal-employment-desktop.desktop"
    echo "Added \"$NAME\" to this computer's menu."
  fi
}

open_app() {
  local url="$1" b app
  if [ "$(uname -s)" = "Darwin" ]; then
    for app in "Google Chrome" "Microsoft Edge" "Brave Browser" "Opera" "Vivaldi" "Chromium"; do
      if [ -d "/Applications/$app.app" ]; then open -na "$app" --args --app="$url"; return; fi
    done
    open "$url"; return
  fi
  for b in chromium chromium-browser google-chrome brave-browser microsoft-edge opera vivaldi; do
    if command -v "$b" >/dev/null 2>&1; then "$b" --app="$url" >/dev/null 2>&1 & return; fi
  done
  xdg-open "$url" >/dev/null 2>&1 &
}

if [ "${1:-}" = "--install" ]; then install_entry; exit 0; fi

[ -d "$HOME/.ssh" ] || mkdir -m 700 "$HOME/.ssh"
LOCAL_PORT="${HAL_LOCAL_PORT:-$PORT}"
# pick the next free local port when this one is taken
while (exec 3<>"/dev/tcp/127.0.0.1/${LOCAL_PORT}") 2>/dev/null; do LOCAL_PORT=$((LOCAL_PORT + 1)); done

# refresh the helper files when the desktop's version changed (a visible folder, so any browser can load it)
HELPER_DIR="$HOME/Hal Employment/Browser helper"
mkdir -p "$HELPER_DIR.new"
if ssh "${SSH_OPTS[@]}" "$HOST" "$HAL helper-files --tar" | tar -x -C "$HELPER_DIR.new"; then
  rm -rf "$HELPER_DIR" && mv "$HELPER_DIR.new" "$HELPER_DIR"
else
  rm -rf "$HELPER_DIR.new"
  echo "Could not copy the browser helper" >&2
fi

ssh "${SSH_OPTS[@]}" -o ExitOnForwardFailure=yes -N -L "127.0.0.1:${LOCAL_PORT}:127.0.0.1:${PORT}" "$HOST" &
TUNNEL=$!
# the forward lives in the control connection, so closing that ends the tunnel too
cleanup() { kill "$TUNNEL" 2>/dev/null; ssh -o "ControlPath=$CTL" -O exit "$HOST" 2>/dev/null; }
trap cleanup EXIT
trap 'exit 130' INT TERM HUP

URL="$(ssh "${SSH_OPTS[@]}" "$HOST" "$HAL open-url --remote --local-port ${LOCAL_PORT}")" \
  || { echo "Hal is not answering on $HOST. Is the desktop on and awake?" >&2; exit 1; }
for _ in $(seq 1 "$HEALTH_TRIES"); do
  curl -fs "http://127.0.0.1:${LOCAL_PORT}/healthz" >/dev/null && break
  sleep 0.25
done
open_app "$URL"
echo "Hal is open. Close this window when you have finished."
wait "$TUNNEL"
