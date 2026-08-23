#!/usr/bin/env bash

set -e

cd "$(dirname "$0")" || exit 1

PORT=8042
OS="$(uname -s)"

echo "==> LogTime Planner"
echo "==> OS detected: $OS"

# Free the port if a previous run was killed without cleaning up.
if command -v lsof >/dev/null 2>&1; then
    lsof -ti:"$PORT" 2>/dev/null | xargs kill 2>/dev/null || true
fi

# Dependency setup
if command -v uv >/dev/null 2>&1; then
    echo "==> Syncing dependencies with uv..."

    if [ "$OS" = "Linux" ]; then
        # Useful for /sgoinfre where hardlinks may not work.
        uv sync --quiet --link-mode=copy
    else
        uv sync --quiet
    fi

    RUNNER=(uv run python)

else
    echo "==> uv not found — using Python/pip"

    if [ ! -d ".venv" ]; then
        echo "==> Creating .venv..."
        python3 -m venv .venv
    fi

    PYTHON=".venv/bin/python"

    echo "==> Installing dependencies from pyproject.toml..."
    "$PYTHON" -m pip install --quiet .

    RUNNER=("$PYTHON")
fi

# Start server
echo "==> Starting server on port $PORT..."

PYTHONPATH=. "${RUNNER[@]}" -m uvicorn \
    app.main:app \
    --reload \
    --host 127.0.0.1 \
    --port "$PORT" &

SERVER=$!

trap 'kill "$SERVER" 2>/dev/null || true' INT TERM EXIT

sleep 1.5

# Cache-busting timestamp
URL="http://127.0.0.1:$PORT/?v=$(date +%s)"

# Open browser depending on OS
case "$OS" in
    Darwin)
        # macOS
        open "$URL"
        ;;

    Linux)
        # Linux desktop
        if command -v xdg-open >/dev/null 2>&1; then
            xdg-open "$URL" >/dev/null 2>&1 || true
        else
            echo "Open $URL in your browser."
        fi
        ;;

    *)
        echo "Unknown OS: $OS"
        echo "Open $URL in your browser."
        ;;
esac

wait "$SERVER"