#!/usr/bin/env bash
# Boston Temp Tracker — run script
# Usage: bash run.sh
set -euo pipefail

# ── Locate python3 ────────────────────────────────────────────────────────────
PYTHON=$(command -v python3 || true)

if [[ -z "$PYTHON" ]]; then
    echo ""
    echo "❌  python3 not found."
    echo "    Install Python 3.9+ from: https://www.python.org/downloads/"
    exit 1
fi

PY_VER=$("$PYTHON" -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "→ Python $PY_VER  ($PYTHON)"

# ── Check tkinter BEFORE creating the venv ────────────────────────────────────
# A venv inherits the parent Python's Tcl/Tk linkage — no point building one
# from a Python that lacks tkinter.
if ! "$PYTHON" -c "import tkinter" 2>/dev/null; then
    echo ""
    echo "❌  tkinter is not available in this Python installation."
    echo ""
    echo "   Option A — python.org installer (easiest):"
    echo "     https://www.python.org/downloads/"
    echo "     Their macOS build bundles Tcl/Tk automatically."
    echo ""
    echo "   Option B — Homebrew:"
    echo "     brew install python-tk@${PY_VER}"
    echo "     Then re-run this script."
    echo ""
    exit 1
fi
echo "→ tkinter OK"

# ── Virtual environment ────────────────────────────────────────────────────────
VENV=".venv"

if [[ ! -d "$VENV" ]]; then
    echo "→ Creating virtual environment at $VENV/ …"
    "$PYTHON" -m venv "$VENV"
fi

# shellcheck disable=SC1091
source "$VENV/bin/activate"

# ── Install / update dependencies ─────────────────────────────────────────────
echo "→ Installing dependencies…"
pip install -q --upgrade pip
pip install -q -r requirements.txt
echo "→ Dependencies ready"

# ── Launch ────────────────────────────────────────────────────────────────────
echo "→ Launching Boston Temp Tracker…"
echo ""
# -u = unbuffered so any crash output appears immediately
python -u tracker.py
