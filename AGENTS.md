# AGENTS.md

Agent instructions for this repo live in [CLAUDE.md](CLAUDE.md) — read that.

Quick version: `docs/design/` is the contract, `research/` is the evidence
(append-only), mcp/↔worker/ is HTTP-only, CPU-only tests via `make test`,
commit locally in small commits with explicit paths (never `git add -A`),
and never push without Tom's go-ahead.
