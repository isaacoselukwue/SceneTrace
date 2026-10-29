#!/usr/bin/env bash
# Run the API (port ${PORT:-8765}) and the Vite dev client (port 5173) together; Ctrl+C stops both.
set -euo pipefail
cd "$(dirname "$0")/.."
PYTHONPATH="$PWD" .venv/bin/python -m scenetrace serve --port "${PORT:-8765}" &
api=$!
trap 'kill "$api" 2>/dev/null || true' EXIT INT TERM
cd client && npm run dev
