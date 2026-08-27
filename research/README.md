# research/ — the evidence behind the contracts

Append-only: never rewrite or delete a doc; add clearly-headed sections.
This index is the one navigational file — when you add a doc, add its line.

## Founding & landscape

- `HANDOFF-2026-08-08.md` — the founding design contract: architecture, model picks, Tom's box (ground truth #2)
- `landscape-survey-video-mcp.md` — YouTube MCP servers / video RAG surveyed; the gap vidtheque fills
- `screenpipe-tool-surface-deep-dive.md` — screenpipe's query surface, dissected for lessons
- `mcp-framework-oauth-research.md` — Python MCP framework + spec-compliant OAuth options
- `pipeline-tooling-research.md` — the six pipeline tool picks, verified

## Validation & bench

- `e2e-smoke-2026-08-08.md` — first end-to-end CPU smoke
- `gpu-validation-2026-08-08.md` — lifecycle manager on the 3090: VRAM discipline, lease hooks
- `keyframe-decode-bench-2026-08-08.md` — what the keyframe stage actually decodes
- `pipeline-bench-2026-08-09.md` — CPU vs GPU, the full index-video loop
- `pipeline-perf-2026-08-09.md` — where the pipeline's time goes; embedder sizing

## Embeddings & search quality

- `multimodal-embedding-2026-08-09.md` — one model for both legs, scored against this box
- `embedding-random-init-2026-08-10.md` — every vector was a random projection: root cause, checked-load fix
- `vec-floor-calibration-2026-08-10.md` — calibrating the vector legs' relevance floor
- `reranker-research-2026-08-10.md` — Qwen3-VL reranker adoption; verdict "later"

## Tool surface & evals

- `mcp-design-bench-2026-08-09.md` — four Sonnet agents stress-testing the tool surface
- `mcp-eval-terra-2026-08-10.md` — six independent terra consumers eval the live surface
- `demo-queries-2026-08-09.md` — verified demo queries, executed against the live stack
- `demo-queries-2026-08-10.md` — demo queries harvested from the terra eval
- `demo-queries-2026-08-13.md` — the announcement ask set: four rounds of candidates receipt-checked against the live 310-talk corpus
- `website-test-2026-08-09.md` — real-browser QA of the public demo
- `ytdlp-usage-audit-2026-08-10.md` — yt-dlp usage audit + effective rate limits

## Positioning & design

- `positioning-2026-08-10.md` — evidence and rejected directions behind the LOCKED contract
- `dashboard-design-research-2026-08-09.md` — dashboard visual directions + agent-frontend tooling verdicts

## Release

- `release-staging-2026-08-11.md` — the fresh LXC, the tunnel, the migration reasoning (executed 2026-08-11; the launch checklist it fed has been retired, its durable rules folded into `docs/LESSONS.md`)
