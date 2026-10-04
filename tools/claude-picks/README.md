# Claude's picks routine

The daily task behind companion.md §6.4: Claude, with its memory of the owner,
picks at most five new videos through the `recommend` tool. It runs on the
machine that holds that memory, on Sonnet 5.5, at 07:30 local, after the
nightly update (04:00) and the scout (05:00).

- `SKILL.md`: what the routine does, also loadable as the `claude-picks` skill.
- `memory.py`: the memory digest it reads, filtered by the profile's deny list.
- `run.sh`: one run; `--dry-run` reads and lists, and writes nothing.
- `claude-picks.service`, `claude-picks.timer`: the systemd user units.

## Install

The project's `vidtheque` name collides with a claude.ai connector of the same
name, so the routine names its own server, and that entry carries the
`X-Vidtheque-Signals: off` header, which takes its own OAuth sign-in once.

1. Write `~/.config/vidtheque/picks-mcp.json`:

   ```json
   {"mcpServers": {"vidtheque-picks": {"type": "http",
     "url": "https://private.vidtheque.dev/mcp",
     "headers": {"X-Vidtheque-Signals": "off"}}}}
   ```

2. Sign it in once: `claude --strict-mcp-config --mcp-config ~/.config/vidtheque/picks-mcp.json`,
   then `/mcp`, `vidtheque-picks`, Authenticate.
3. Check it: `tools/claude-picks/run.sh --dry-run`.
4. Install the timer:

   ```bash
   cp tools/claude-picks/claude-picks.{service,timer} ~/.config/systemd/user/ && systemctl --user daemon-reload && systemctl --user enable --now claude-picks.timer
   ```

State lives in `~/.local/state/vidtheque/claude-picks/`: `learnings.md` (the
routine's own notes, never sent to the server), `memory.md` (the digest of
the last run) and `runs.jsonl` (each run's report).
