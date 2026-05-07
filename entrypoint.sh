#!/bin/sh
# POSIX-compatible launcher: run webui.py + monitor.py; exit if either dies.
set -e

cleanup() {
  echo "[entrypoint] shutting down children"
  kill $WEB_PID $MON_PID 2>/dev/null || true
}
trap cleanup INT TERM EXIT

python -u /app/webui.py &
WEB_PID=$!
python -u /app/monitor.py &
MON_PID=$!

# Poll: if either child exits, tear down the other and exit with its code.
while true; do
  if ! kill -0 $WEB_PID 2>/dev/null; then
    wait $WEB_PID
    EXIT_CODE=$?
    echo "[entrypoint] webui.py exited with code $EXIT_CODE"
    kill $MON_PID 2>/dev/null || true
    exit $EXIT_CODE
  fi
  if ! kill -0 $MON_PID 2>/dev/null; then
    wait $MON_PID
    EXIT_CODE=$?
    echo "[entrypoint] monitor.py exited with code $EXIT_CODE"
    kill $WEB_PID 2>/dev/null || true
    exit $EXIT_CODE
  fi
  sleep 2
done
