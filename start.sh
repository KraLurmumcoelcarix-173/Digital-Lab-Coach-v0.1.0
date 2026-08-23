#!/usr/bin/env bash
# Digital Lab Coach launcher (macOS / Linux).
# Windows users: double-click START_HERE.bat instead.
set -e
cd "$(dirname "$0")"

if ! command -v uv >/dev/null 2>&1; then
  echo "Installing the uv package manager - one-time setup..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi

echo "Preparing packages - the first run can take a few minutes..."
uv sync

export DLC_ENFORCE_LIMITS=1
echo "Starting Digital Lab Coach at http://127.0.0.1:8765 ..."
( sleep 4
  if command -v open >/dev/null 2>&1; then open http://127.0.0.1:8765
  elif command -v xdg-open >/dev/null 2>&1; then xdg-open http://127.0.0.1:8765
  fi ) &
uv run python -m dlc.web.server
