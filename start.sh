#!/usr/bin/env bash
# Start the Flask API backend and Vite dev server
set -e

ROOT="$(cd "$(dirname "$0")" && pwd)"

# Install Python deps if needed
pip install flask flask-cors requests -q --break-system-packages --ignore-installed blinker 2>/dev/null || true

# Install Node deps if needed
(cd "$ROOT/frontend" && npm install --silent 2>/dev/null)

echo "Starting Flask API on http://localhost:5050 ..."
python3 "$ROOT/api.py" &
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
