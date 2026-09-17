#!/usr/bin/env bash
# Launches the flatstitch GUI. Can be double-clicked in a file manager
# (if marked executable) or run from a terminal from anywhere.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONDONTWRITEBYTECODE=1
cd "$DIR" && exec python3 flatstitch_gui.py
