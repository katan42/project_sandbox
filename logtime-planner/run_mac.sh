#!/bin/sh
# macOS launcher. On Linux use ./run.sh instead.

PORT=8042

# Free the port if a previous run was killed without cleaning up.
# Note: BSD xargs has no -r, and doesn't need it.
lsof -ti:"$PORT" 2>/dev/null | xargs kill 2>/dev/null

PYTHONPATH=. python3 -m uvicorn app.main:app --reload --port "$PORT" &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' INT TERM

sleep 1.5
# The timestamp defeats any stale browser cache of the page.
open "http://127.0.0.1:$PORT/?v=$(date +%s)"

wait $SERVER
