# vidtheque-mcp — placeholder

This package is a stub on purpose. **The MCP framework has not been chosen
yet** — a parallel research task is comparing the options against the
requirements that actually bind here:

- remote transport (streamable HTTP), not stdio;
- OAuth per the MCP spec, including **dynamic client registration**, because
  claude.ai-hosted clients need it on day one;
- tool annotations (`readOnlyHint` / `idempotentHint`) and MCP **resources**,
  not just tools;
- a route for authenticated non-MCP endpoints (`/frames/<id>.jpg`), since
  `get_frames` defaults to returning URLs rather than inline images.

Nothing should import from this package until that decision lands. Do not add
a framework dependency here as a "temporary" choice — the pyproject's empty
dependency list is the signal that the decision is still open.

## What will live here

The CPU half of vidtheque: OAuth, yt-dlp ingestion, the SQLite + sqlite-vec +
FTS5 index, keyframe JPEGs, the job queue, and pipeline orchestration. It talks
to the GPU worker over HTTP only.

## The boundary rule

**No Python import ever crosses between `mcp/` and `worker/`, including in
tests.** The worker's OpenAPI document is the contract. That rule is what lets
a self-hoster without a GPU delete the worker service entirely and point
`WORKER_URL` at a hosted OpenAI-compatible provider.
