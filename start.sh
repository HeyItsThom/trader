#!/usr/bin/env bash
# Start the Flask API backend and Vite dev server
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"
VENV="$ROOT/.venv"

# ── Python virtualenv + deps ──────────────────────────────────────────────────
if [[ ! -d "$VENV" ]]; then
  echo "→ Creating virtual environment..."
  python3 -m venv "$VENV"
fi
source "$VENV/bin/activate"
pip install -q --upgrade pip
pip install -q flask flask-cors requests
echo "→ Python deps ready"

# ── Node deps ─────────────────────────────────────────────────────────────────
(cd "$ROOT/frontend" && npm install --silent 2>/dev/null)

# ── Launch ────────────────────────────────────────────────────────────────────
echo "Starting Flask API on http://localhost:5050 ..."
python "$ROOT/api.py" &
FLASK_PID=$!

echo "Starting React dev server on http://localhost:5173 ..."
(cd "$ROOT/frontend" && npm run dev) &
VITE_PID=$!

cleanup() {
  kill $FLASK_PID $VITE_PID 2>/dev/null
}
trap cleanup EXIT INT TERM

echo ""
echo "  Open: http://localhost:5173"
echo "  Press Ctrl+C to stop."
echo ""

wait
