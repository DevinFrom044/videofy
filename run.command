#!/bin/bash

set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$PROJECT_DIR"

echo "Starting Videofy..."

if ! command -v python3 >/dev/null 2>&1; then
  echo "Error: Python 3 is not installed."
  echo "Install Python 3 and try again."
  read -r -p "Press Enter to close..."
  exit 1
fi

if ! command -v node >/dev/null 2>&1; then
  echo "Error: Node.js is not installed."
  echo "Install Node.js and try again."
  read -r -p "Press Enter to close..."
  exit 1
fi

if ! command -v ffmpeg >/dev/null 2>&1; then
  echo "Error: ffmpeg is not installed."
  echo "Install ffmpeg and try again."
  read -r -p "Press Enter to close..."
  exit 1
fi

CHROME_PATH="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
if [ ! -x "$CHROME_PATH" ]; then
  echo "Error: Google Chrome was not found at:"
  echo "  $CHROME_PATH"
  echo "Install Google Chrome and try again."
  read -r -p "Press Enter to close..."
  exit 1
fi

if [ ! -d "$PROJECT_DIR/node_modules" ]; then
  echo "Installing Node dependencies..."
  npm install
fi

if ! python3 - <<'PY' >/dev/null 2>&1
import flask
PY
then
  echo "Installing Python dependencies..."
  python3 -m pip install -r requirements.txt
fi

echo "Opening Videofy in your browser..."
python3 web_app.py --host 127.0.0.1 --port 8765 &
SERVER_PID=$!

cleanup() {
  if kill -0 "$SERVER_PID" >/dev/null 2>&1; then
    kill "$SERVER_PID" >/dev/null 2>&1 || true
  fi
}

trap cleanup EXIT INT TERM

sleep 2
open "http://127.0.0.1:8765"

echo "Videofy is running."
echo "Leave this window open while you use the app."
echo "To stop the app later, close this window or press Control + C."
echo

wait "$SERVER_PID"
