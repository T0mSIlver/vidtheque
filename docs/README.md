# docs/ — the map

How to work in the repo: `AGENTS.md` (root). What the product says in public:
`docs/design/positioning.md` (LOCKED). Everything below is where each surface
lives and which document owns it.

| Surface | Route | Code | Owning doc |
|---|---|---|---|
| MCP tools + resources | `/mcp` | `mcp/src/vidtheque_mcp/tools/` | `docs/design/tool-surface.md` |
| Index (SQLite + sqlite-vec + FTS5) | — | `mcp/src/vidtheque_mcp/db/` | `docs/design/index-schema.md` |
| Pipeline + jobs | — | `mcp/src/vidtheque_mcp/{pipeline,jobs}/` | `docs/design/index-schema.md`, `docs/design/DECISIONS.md` |
| Auth (none / token / oauth) | `/{authorize,token,register,revoke}`, `/auth/*`, `/.well-known/*` | `mcp/src/vidtheque_mcp/auth/` | `docs/design/DECISIONS.md` #1 |
| Landing | `/` | `mcp/src/vidtheque_mcp/public/static/landing/` | `DESIGN.md` |
| Demo (read-only projection) | `/demo` | `mcp/src/vidtheque_mcp/public/static/demo/` | `docs/design/demo-site.md` |
| Dashboard | `/dashboard` | `mcp/src/vidtheque_mcp/dashboard/` | `docs/design/dashboard.md` |
| Worker HTTP API | `:8081/v1/*` | `worker/` | `worker/openapi.json`, `worker/README.md` |
| Deploy | — | `deploy/`, `deploy/staging/` | `docs/deploy-public.md` |
| Bench | — | `bench/` | `bench/README.md` |

Three sentences of orientation: the system is **two processes** — the MCP
server (state: SQLite, keyframes, jobs) and the worker (stateless GPU
inference) — talking **HTTP only**. The public web surfaces are served by the
MCP server's own Starlette app; `/demo` and the read-only tool surface exist
only under `VIDTHEQUE_PUBLIC_READONLY=1`. Every env var is documented in
`deploy/.env.example`, the document of record.

Also here: `docs/security.md` (the map of security material),
`docs/takedown.md` (creator removal), `docs/deploy-public.md` (the go-public
runbook), `docs/history/` (the launch, as it happened — read, don't update).
