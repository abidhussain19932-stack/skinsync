#!/usr/bin/env sh
# Start/stop a local dev server in the background. Usage: scripts/dev_server.sh start|stop [port]
cd "$(dirname "$0")/.." || exit 1
PORT="${2:-8077}"
PIDFILE="/tmp/skinsync_${PORT}.pid"
case "$1" in
  start)
    [ -f "$PIDFILE" ] && kill "$(cat "$PIDFILE")" 2>/dev/null
    nohup python3 -m uvicorn app.main:app --host 0.0.0.0 --port "$PORT" > "/tmp/skinsync_${PORT}.log" 2>&1 < /dev/null &
    echo $! > "$PIDFILE"; sleep 2; echo "started on :$PORT (pid $(cat "$PIDFILE"))" ;;
  stop)
    [ -f "$PIDFILE" ] && kill "$(cat "$PIDFILE")" 2>/dev/null && rm -f "$PIDFILE" && echo stopped ;;
  *) echo "usage: $0 start|stop [port]" ;;
esac
