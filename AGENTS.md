# AGENTS.md — how to work in this repo

vidtheque turns the videos you follow into timestamped, receipt-backed
knowledge for you and your agents. Owner: Tom Vaucourt. MIT. This file is the
working conventions, kept short because it loads into every session; the
product itself is explained in `README.md` and `docs/README.md`.

## Ground truth, in order

1. `docs/design/*.md` — **the contracts** (`DECISIONS.md` outranks the rest).
   If implementation must diverge, amend the contract in the same commit and
   say why. `positioning.md` is LOCKED: all public-facing copy follows it.
2. `research/` — append-only evidence (index: `research/README.md`; founding
   decisions: `research/HANDOFF-2026-08-08.md`). Add dated sections; never
   rewrite or delete.
3. `DESIGN.md` + `PRODUCT.md` — the visual contract for the web surfaces.
4. This file — conventions only. Any doc above wins.

`docs/ROADMAP.md` is the only list of open work — a backlog inside a dated
document is a snapshot and goes stale silently. `docs/LESSONS.md` holds the
operational rules an incident or a launch paid for.

## Invariants — never change these without Tom

- `mcp/` ↔ `worker/` is **HTTP only**. No Python imports across the boundary,
  not even in tests. The worker is stateless inference; all state lives in mcp/.
- Two time axes, never overloaded: `published_*` picks videos, `offset_*`
  picks positions inside one.
- `all` means all — a filter that can't apply to a leg prints a `note:`,
  never silently narrows.
- Token discipline: middle-truncation with a documented `0` opt-out,
  pagination hints printed in payloads, double caps (items AND chars),
  expensive paths bounded independently of `limit`, server-side clamps —
  never prompt-only limits.
- Frames by authenticated URL by default; inline base64 is the opt-in,
  with the correct mimeType.
- `has_more` over exact totals; relevance-first ordering, `order` explicit.

## Working here

- Python 3.12, `uv` workspace (`mcp/`, `worker/`). `make test` is CPU-only
  and must stay green — tests never download models or need a GPU.
  `make bench` is manual, Tom's box only.
- Deterministic builds: committed `uv.lock`, digest-pinned CUDA base image.
  Dependency bumps are deliberate commits, not side effects.
- `deploy/.env.example` is the document of record for every env var — a new
  env without an entry there is a bug.
- No self-hosted CI runners, ever (public repo, drive-by PR risk).
- Tom's box (3090, llama.cpp VRAM lease via `GPU_ACQUIRE_CMD` hooks) is
  context for deploy work, not a shipped default — see the founding handoff.

## Git — multi-agent, incident-derived; follow exactly

- Small logical commits. `git add` only the paths you created or edited —
  never `git add -A`. Commit with an explicit pathspec
  (`git commit -m "…" -- <paths>`): the index is shared, and a bare commit
  sweeps up whatever a sibling agent had staged (it happened, 2026-08-09).
- Pathspec commits still snapshot the working tree of the named paths, so a
  sibling's in-flight edit to a *shared file* rides along. Contract docs:
  keep the edit small, commit promptly, say if you may have swept hunks.
  Never stage-and-wait.
- Never rewrite history (`reset`/`amend`) on a commit that exists — the
  branch may already be pushed under you; fix forward with a new commit.
- Never use bare `git stash` — the stash stack is shared across worktrees.
- Substantive changes land via **PR**, never a direct push to `main`.
  Subagents never push, never touch anything outside this repo.
- PRs merge by **rebase and merge**, never squash: the branch's small
  logical commits are the history, and squashing melts them into one.

## Don'ts

- Don't change architecture, contracts, or invariants unless Tom asked.
- Don't add an env var, endpoint, or tool without its contract-doc entry.
- Don't write public-facing copy that breaks `docs/design/positioning.md`;
  site posts about vidtheque follow the website repo's framing guards.
