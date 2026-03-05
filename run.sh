#!/usr/bin/env bash
# Boston Temperature Tracker - quick start
set -e

# Create venv if it doesn't exist
if [ ! -d ".venv" ]; then
    echo "Creating virtual environment..."
    python3 -m venv .venv
fi

source .venv/bin/activate

echo "Installing dependencies..."
pip install -q -r requirements.txt

echo "Launching Boston Temp Tracker..."
python tracker.py
