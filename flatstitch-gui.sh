#!/usr/bin/env bash
# Launches the flatstitch GUI, choosing the interface that fits your
# desktop (GTK / Qt / Tk; override with --gui gtk|qt|tk). Can be
# double-clicked in a file manager (if marked executable) or run from a
# terminal from anywhere.
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONDONTWRITEBYTECODE=1
PY=/usr/bin/python3
[ -x "$PY" ] || PY=python3
cd "$DIR" && exec "$PY" flatstitch_launcher.py "$@"
