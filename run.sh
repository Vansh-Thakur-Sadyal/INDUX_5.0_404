#!/usr/bin/env bash
# Saarthi 2.0 - start the backend and serve the dashboard on one origin.
set -e
cd "$(dirname "$0")"
if [ ! -x ".venv/bin/python" ] && [ ! -x ".venv/Scripts/python.exe" ]; then
  python3 -m venv .venv
  ./.venv/bin/python -m pip install --upgrade pip
  ./.venv/bin/python -m pip install -r requirements.txt
fi
PY=".venv/bin/python"; [ -x "$PY" ] || PY=".venv/Scripts/python.exe"
echo "Saarthi 2.0 -> http://127.0.0.1:8010/"
cd backend && exec "../$PY" -m uvicorn app.main:app --host 127.0.0.1 --port 8010 --reload
