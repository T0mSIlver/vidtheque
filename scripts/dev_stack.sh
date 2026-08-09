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

port_answers() { curl -fsS -m 2 "http://127.0.0.1:$1/healthz" >/dev/null 2>&1; }

start_one() { # name, pidfile, logfile, port, cmd...
  local name="$1" pidfile="$2" logfile="$3" port="$4"; shift 4
  if port_answers "$port"; then
    echo "$name: something already answers on port $port — refusing to double-start" >&2
    return 1
  fi
  # setsid: the whole service (uv wrapper + python child) is one process
  # group, so stop can kill the group. The pidfile stores the group leader.
  ( cd "$REPO" && setsid nohup "$@" >>"$logfile" 2>&1 & echo $! >"$pidfile" )
  echo "$name started (pgid $(cat "$pidfile"), log $logfile)"
}

stop_one() { # name, pidfile, port
  local name="$1" pidfile="$2" port="$3"
  if pid_alive "$pidfile"; then
    kill -- "-$(cat "$pidfile")" 2>/dev/null || kill "$(cat "$pidfile")" 2>/dev/null || true
  fi
  # Fallback: kill whoever LISTENS on this instance's port, by process group.
  # History of this line: a bare "python -m vidtheque_mcp" pkill killed every
  # instance on the box; a "VIDTHEQUE_PORT=<port>" pattern matched nothing
  # because `env VAR=x cmd` execs away the wrapper and the var never appears
  # in the final cmdline. The port's listener is the only identity that is
  # both accurate and instance-scoped.
  local lpid
  lpid=$(ss -tlnp 2>/dev/null | sed -n "s/.*:$port .*pid=\([0-9]*\).*/\1/p" | head -1)
  if [ -n "$lpid" ]; then
    local pgid
    pgid=$(ps -o pgid= -p "$lpid" 2>/dev/null | tr -d ' ')
    [ -n "$pgid" ] && kill -- "-$pgid" 2>/dev/null || kill "$lpid" 2>/dev/null
    echo "$name stopped (listener $lpid)"
  else
    echo "$name not running"
  fi
  rm -f "$pidfile"
}

case "${1:-status}" in
  start)
    start_one worker "$RUN_DIR/worker.pid" "$RUN_DIR/worker.log" "$VIDTHEQUE_WORKER_PORT" \
      env VIDTHEQUE_HOST=127.0.0.1 VIDTHEQUE_PORT="$VIDTHEQUE_WORKER_PORT" \
      uv run --no-sync python -m vidtheque_worker
    start_one mcp "$RUN_DIR/mcp.pid" "$RUN_DIR/mcp.log" "$VIDTHEQUE_MCP_PORT" \
      env VIDTHEQUE_HOST=127.0.0.1 VIDTHEQUE_PORT="$VIDTHEQUE_MCP_PORT" \
      VIDTHEQUE_DATA_DIR="$DATA_DIR" \
      WORKER_URL="http://127.0.0.1:$VIDTHEQUE_WORKER_PORT" \
      PUBLIC_URL="${PUBLIC_URL:-http://127.0.0.1:$VIDTHEQUE_MCP_PORT}" \
      uv run --no-sync python -m vidtheque_mcp
    ;;
  stop)
    stop_one mcp "$RUN_DIR/mcp.pid" "$VIDTHEQUE_MCP_PORT"
    stop_one worker "$RUN_DIR/worker.pid" "$VIDTHEQUE_WORKER_PORT"
    ;;
  status)
    for svc in worker mcp; do
      if pid_alive "$RUN_DIR/$svc.pid"; then echo "$svc: running (pgid $(cat "$RUN_DIR/$svc.pid"))"
      else echo "$svc: no live pidfile"; fi
    done
    port_answers "$VIDTHEQUE_WORKER_PORT" && echo "worker /healthz: ok" || echo "worker /healthz: no answer"
    port_answers "$VIDTHEQUE_MCP_PORT" && echo "mcp /healthz: ok" || echo "mcp /healthz: no answer"
    pgrep -af "python -m vidtheque_(worker|mcp)" | sed 's/^/  proc: /' || true
    ;;
  logs)
    tail -n 40 "$RUN_DIR/worker.log" "$RUN_DIR/mcp.log"
    ;;
  *)
    echo "usage: DATA_DIR=... $0 start|stop|status|logs" >&2; exit 2
    ;;
esac
