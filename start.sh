#!/usr/bin/env bash
# Start the NSE Dividend Tracker. Extra arguments are passed to run.py.
set -e
cd "$(dirname "$0")"

if command -v python3 >/dev/null 2>&1; then
  exec python3 run.py "$@"
elif command -v python >/dev/null 2>&1; then
  exec python run.py "$@"
else
  echo "Python 3.11+ was not found. Install it from https://www.python.org/downloads/" >&2
  exit 1
fi
