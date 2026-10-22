#!/usr/bin/env bash
set -e
DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$DIR"

PID=$(lsof -ti :8990 2>/dev/null || true)
if [ -n "$PID" ]; then
    PROC_CMD=$(ps -p $PID -o command= 2>/dev/null || true)
    if [[ "$PROC_CMD" == *"app.py"* ]]; then
        kill -9 $PID 2>/dev/null || true
        sleep 0.5
    else
        echo "Error: Port 8990 is occupied by another process (PID: $PID: $PROC_CMD)."
        exit 1
    fi
fi

python3 app.py &
APP_PID=$!

sleep 1
if command -v open >/dev/null 2>&1; then
    open "http://127.0.0.1:8990"
fi

echo "OmniCleaner running at http://127.0.0.1:8990 (PID: $APP_PID)"
echo "Press Ctrl+C to stop."

wait $APP_PID
