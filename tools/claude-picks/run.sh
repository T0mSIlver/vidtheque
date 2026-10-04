#!/usr/bin/env bash
# The daily Claude's picks routine (companion.md §6.4). Runs on the machine that
# holds Claude's memory, never in the cloud.
#
#   tools/claude-picks/run.sh            pick, and update the notes
#   tools/claude-picks/run.sh --dry-run  read only: list what it would pick
#
# The MCP config names one server, `vidtheque-picks`, the owner's instance with
# `X-Vidtheque-Signals: off`, so the routine's reads are not the owner's signals.
set -euo pipefail

repo=$(cd "$(dirname "$0")/../.." && pwd)
state=${VIDTHEQUE_PICKS_STATE:-$HOME/.local/state/vidtheque/claude-picks}
mcp=${VIDTHEQUE_PICKS_MCP:-$HOME/.config/vidtheque/picks-mcp.json}
model=${VIDTHEQUE_PICKS_MODEL:-claude-sonnet-5-5}
mkdir -p "$state"

# The memory the routine may read, filtered by the deny list before it sees any.
(cd "$repo" && uv run --quiet tools/claude-picks/memory.py) > "$state/memory.md"

if [ "${1:-}" = "--dry-run" ]; then
  mode="This is a dry run."
  edit=()
else
  mode="This is the daily run."
  edit=("Write(/$state/learnings.md)" "Edit(/$state/learnings.md)")
fi

prompt="Follow the claude-picks instructions. $mode
Memory digest: $state/memory.md
Learnings file: $state/learnings.md
Today is $(date +%F)."

started=$(date +%s)
status=0
out=$(claude -p "$prompt" \
  --model "$model" \
  --append-system-prompt-file "$repo/tools/claude-picks/SKILL.md" \
  --strict-mcp-config --mcp-config "$mcp" \
  --allowedTools "mcp__vidtheque-picks__recommend" "mcp__vidtheque-picks__get-transcript" \
    "mcp__vidtheque-picks__video-summary" "Read(/$state/**)" "${edit[@]}" \
  < /dev/null) || status=$?
printf '%s\n' "$out"
python3 -c 'import json,sys; print(json.dumps({"at": int(sys.argv[1]), "mode": sys.argv[2], "status": int(sys.argv[3]), "report": sys.argv[4]}))' \
  "$started" "${1:-run}" "$status" "$out" >> "$state/runs.jsonl"
exit "$status"
