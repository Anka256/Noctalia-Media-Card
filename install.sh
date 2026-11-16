#!/usr/bin/env bash
# Copies the plugin into Noctalia's local plugin directory.
set -euo pipefail
SRC="$(cd "$(dirname "$0")" && pwd)"
DEST="${XDG_DATA_HOME:-$HOME/.local/share}/noctalia/plugins/seekbar"

for bin in python3 playerctl pactl; do
  command -v "$bin" >/dev/null || echo "warning: '$bin' not found - the card needs it" >&2
done

mkdir -p "$DEST/tests"
cp "$SRC/plugin.toml" "$SRC/desktop.luau" "$SRC/probe.py" "$DEST/"
cp "$SRC"/tests/* "$DEST/tests/"
echo "Installed to $DEST"
echo "Next: noctalia msg plugins enable onur/seekbar   (then add it as a lock-screen widget, see README)"
