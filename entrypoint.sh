#!/bin/sh
# Run both the polling monitor and the Flask UI; if either dies, exit so Docker restarts us.
set -e
python -u /app/webui.py &
WEB_PID=$!
python -u /app/monitor.py &
MON_PID=$!

# Wait for either process to exit
wait -n $WEB_PID $MON_PID
EXIT_CODE=$?
echo "[entrypoint] one process exited (code=$EXIT_CODE), shutting down the other"
kill $WEB_PID $MON_PID 2>/dev/null || true
exit $EXIT_CODE
