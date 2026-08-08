#!/usr/bin/env bash
# Run the persistent vidtheque stack (worker + mcp) against ONE data directory.
#
# Migration story: everything stateful lives in $DATA_DIR (index, auth.db,
# keyframes) plus its stack.env. Moving to another box = rsync $DATA_DIR,
# clone the repo, `make sync-gpu` (or plain `uv sync` for CPU), run this.
# Models re-download to the HF cache, or rsync ~/.cache/huggingface too.
#
# Usage:
#   DATA_DIR=/home/dev/vidtheque-data scripts/dev_stack.sh start|stop|status|logs
#
# stack.env in $DATA_DIR overrides every default below. PID files and logs
# live in $DATA_DIR/run/.

set -euo pipefail

DATA_DIR="${DATA_DIR:-$HOME/vidtheque-data}"
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$DATA_DIR/run"
ENV_FILE="$DATA_DIR/stack.env"

# Defaults — override in $DATA_DIR/stack.env.
export VIDTHEQUE_WORKER_PORT="${VIDTHEQUE_WORKER_PORT:-8081}"
export VIDTHEQUE_MCP_PORT="${VIDTHEQUE_MCP_PORT:-8100}"
export DEVICE="${DEVICE:-cuda}"
export VIDTHEQUE_AUTH="${VIDTHEQUE_AUTH:-none}"
export EMBED_RESIDENT="${EMBED_RESIDENT:-1}"

mkdir -p "$RUN_DIR"
[ -f "$ENV_FILE" ] && { set -a; source "$ENV_FILE"; set +a; }

pid_alive() { [ -f "$1" ] && kill -0 "$(cat "$1")" 2>/dev/null; }

start_one() { # name, pidfile, logfile, cmd...
  local name="$1" pidfile="$2" logfile="$3"; shift 3
  if pid_alive "$pidfile"; then echo "$name already running (pid $(cat "$pidfile"))"; return; fi
  ( cd "$REPO" && nohup "$@" >>"$logfile" 2>&1 & echo $! >"$pidfile" )
  echo "$name started (pid $(cat "$pidfile"), log $logfile)"
}

case "${1:-status}" in
  start)
    start_one worker "$RUN_DIR/worker.pid" "$RUN_DIR/worker.log" \
      env VIDTHEQUE_HOST=127.0.0.1 VIDTHEQUE_PORT="$VIDTHEQUE_WORKER_PORT" \
      uv run --no-sync python -m vidtheque_worker
    start_one mcp "$RUN_DIR/mcp.pid" "$RUN_DIR/mcp.log" \
      env VIDTHEQUE_HOST=127.0.0.1 VIDTHEQUE_PORT="$VIDTHEQUE_MCP_PORT" \
      VIDTHEQUE_DATA_DIR="$DATA_DIR" \
      WORKER_URL="http://127.0.0.1:$VIDTHEQUE_WORKER_PORT" \
      PUBLIC_URL="${PUBLIC_URL:-http://127.0.0.1:$VIDTHEQUE_MCP_PORT}" \
      uv run --no-sync python -m vidtheque_mcp
    ;;
  stop)
    for svc in mcp worker; do
      if pid_alive "$RUN_DIR/$svc.pid"; then
        kill "$(cat "$RUN_DIR/$svc.pid")" && echo "$svc stopped"
      else
        echo "$svc not running"
      fi
      rm -f "$RUN_DIR/$svc.pid"
    done
    ;;
  status)
    for svc in worker mcp; do
      if pid_alive "$RUN_DIR/$svc.pid"; then echo "$svc: running (pid $(cat "$RUN_DIR/$svc.pid"))"
      else echo "$svc: stopped"; fi
    done
    curl -fsS "http://127.0.0.1:$VIDTHEQUE_WORKER_PORT/healthz" >/dev/null 2>&1 && echo "worker /healthz: ok" || echo "worker /healthz: no answer"
    curl -fsS "http://127.0.0.1:$VIDTHEQUE_MCP_PORT/healthz" >/dev/null 2>&1 && echo "mcp /healthz: ok" || echo "mcp /healthz: no answer"
    ;;
  logs)
    tail -n 40 "$RUN_DIR/worker.log" "$RUN_DIR/mcp.log"
    ;;
  *)
    echo "usage: DATA_DIR=... $0 start|stop|status|logs" >&2; exit 2
    ;;
esac
