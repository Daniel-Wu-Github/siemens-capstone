#!/bin/sh
# Start the prototype: sh run.sh [database-file] [port]
# Defaults: siemens_uat.sqlite3 in this folder, port 8765.
cd "$(dirname "$0")" || exit 1
DB="${1:-siemens_uat.sqlite3}"
PORT="${2:-8765}"

if [ ! -f "$DB" ]; then
  echo "Database not found: $DB"
  echo "Put siemens_uat.sqlite3 next to run.sh, or pass its path: sh run.sh path/to/file.sqlite3"
  exit 1
fi

PY=""
for candidate in python3.13 python3.12 python3.11 python3 python; do
  if command -v "$candidate" >/dev/null 2>&1 && "$candidate" -c "import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)" >/dev/null 2>&1; then
    PY="$candidate"; break
  fi
done
if [ -z "$PY" ]; then
  echo "Python 3.11 or newer was not found. Install it from python.org and run this again."
  exit 1
fi

if [ ! -x .venv/bin/python ]; then
  echo "First start: creating a private Python environment (.venv)..."
  "$PY" -m venv .venv || { echo "Could not create .venv"; exit 1; }
fi
if ! .venv/bin/python -c "import openpyxl" >/dev/null 2>&1; then
  echo "Installing the one required package (openpyxl)..."
  .venv/bin/python -m pip install --disable-pip-version-check --quiet -r requirements.txt || { echo "pip could not install openpyxl. Check your internet access or pip mirror."; exit 1; }
fi

URL="http://127.0.0.1:$PORT"
( sleep 3; if command -v open >/dev/null 2>&1; then open "$URL"; elif command -v xdg-open >/dev/null 2>&1; then xdg-open "$URL"; fi ) >/dev/null 2>&1 &
echo "Starting on $URL with database $DB. Press Ctrl+C to stop."
exec .venv/bin/python server.py --db "$DB" --port "$PORT"
