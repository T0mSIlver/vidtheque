# CLAUDE.md — how we work on vidtheque

Self-hosted MCP server that turns videos you've watched into a searchable
multimodal corpus — transcript, on-screen text, and frame search with
timestamped citations (`youtu.be/ID?t=123`). Owner: Tom Vaucourt. MIT.

## Ground truth, in order

1. `docs/design/*.md` — **the contracts.** `tool-surface.md` is what the MCP
   server exposes; `index-schema.md` is the database. Implementation follows
   the contract; if implementation needs to diverge, update the contract in
   the same commit and say why.
2. `research/` — the evidence behind the contracts. `HANDOFF-2026-08-08.md`
   is the founding design decisions (architecture, model picks, Tom's box
   specifics); the survey/deep-dive docs carry the receipts, most of them
   paid for by screenpipe's issue tracker. Research docs are append-only:
   add clearly-headed sections, never rewrite others' findings.
3. This file — conventions only. When it disagrees with a design doc, the
   design doc wins.

## The invariants (don't relitigate without Tom)

- **mcp/ ↔ worker/ is HTTP only.** No Python imports across the boundary,
  not even in tests. The worker is a stateless inference API
  (OpenAI-compatible); all state (SQLite, keyframes, jobs) lives in mcp/.
- **Two time axes, never overloaded:** `published_*` picks videos,
  `offset_*` picks positions inside one.
- **`all` means all** — a content-type or filter that can't apply to a leg
  prints a `note:`, never silently narrows.
- **Token discipline everywhere:** middle-truncation with documented `0`
  opt-out, pagination hints printed in payloads, double caps (items AND
  chars), expensive paths bounded independently of `limit`, server-side
  clamps (never prompt-only limits).
- **Frames by authenticated URL by default** (Claude Code mangles MCP
  ImageContent — see the landscape survey §4); inline base64 is the opt-in,
  with the correct mimeType.
- **`has_more` over exact totals** — no duplicated count queries.
- **Relevance-first ordering**; `order` is explicit.

## Development

- Python 3.12, `uv` workspace (`mcp/`, `worker/`). `make test` runs CPU-only
  tests — tests must never download models or need a GPU. GPU validation is
  `make bench`, run manually on Tom's box only.
- Deterministic builds: committed `uv.lock`, digest-pinned CUDA base image.
  Dependency bumps are deliberate commits, not side effects.
- `deploy/.env.example` is the document of record for every env var — a new
  env without an entry there is a bug.
- No self-hosted CI runners on this public repo, ever (drive-by PR risk).

## Git

- Commit locally in small, logical commits. **Never push without Tom's
  explicit go-ahead** — pushing publishes to a public repo.
- Multi-agent sessions are common here: `git add` only the specific paths
  you created or edited — never `git add -A` / `git add .` — and expect
  other agents to be writing sibling files concurrently. If `index.lock`
  collides, wait and retry.
- Subagents never push, never touch anything outside this repo.

## Tom's box (context for lease/deploy work — not shipped defaults)

Proxmox host, RTX 3090 (24GB), llama.cpp LXC in router mode. The worker's
`GPU_ACQUIRE_CMD`/`GPU_RELEASE_CMD` hooks exist for his llama.cpp VRAM lease;
public defaults leave them unset. Details in `research/HANDOFF-2026-08-08.md`.

## Writing about this project

Site posts about vidtheque follow the website repo's framing guards:
independent-study framing, patterns not internals, benchmarks regenerate
from this public repo.
