#!/usr/bin/env bash
# Convenience wrapper so you can run ./flatstitch.sh from anywhere,
# with input/output paths resolved relative to your current directory
# (not this script's directory).
DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
export PYTHONDONTWRITEBYTECODE=1
PYTHONPATH="$DIR${PYTHONPATH:+:$PYTHONPATH}" python3 -m flatstitch.cli "$@"
