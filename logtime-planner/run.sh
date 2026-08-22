# Linux launcher. On macOS use ./run_mac.sh instead.

#!/bin/bash

cd "$(dirname "$0")" || exit 1

PORT=8042

# Free the port if a previous run was killed without cleaning up.
lsof -ti:"$PORT" 2>/dev/null | xargs -r kill 2>/dev/null

PYTHONPATH=. python3 -m uvicorn app.main:app --reload --port "$PORT" &
SERVER=$!
trap 'kill $SERVER 2>/dev/null' INT TERM

sleep 1.5
# The timestamp defeats any stale browser cache of the page.
if command -v xdg-open >/dev/null 2>&1; then
  xdg-open "http://127.0.0.1:$PORT/?v=$(date +%s)" >/dev/null 2>&1
else
  echo "Open http://127.0.0.1:$PORT/ in your browser."
fi

wait $SERVER
