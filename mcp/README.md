# vidtheque-mcp

The CPU half of vidtheque: the SQLite + sqlite-vec + FTS5 index, the keyframe
directory, the job queue, the OAuth authorization server, and the nine-tool MCP
surface. It talks to the GPU worker over HTTP only — no Python import ever
crosses that boundary, including in tests.

Contracts: `docs/design/tool-surface.md` (what the server exposes),
`docs/design/index-schema.md` (the database), `docs/design/DECISIONS.md` (wins
when the two disagree).

## Shape

```
src/vidtheque_mcp/
├─ app.py          build_app(settings) -> Starlette. Our routes, then Mount("/", mcp_app).
├─ server.py       MCPServer("vidtheque"): tools + resources + token verifier
├─ config.py       every env var, resolved once, validated at boot
├─ errors.py       the typed error contract (E_* codes, `next:` hints)
├─ timeparse.py    the two time axes, normalized in one place
├─ text.py         truncation, clamps, deep links, TSV, pagination lines
├─ embeddings.py   the worker seam (HTTP; faked in tests)
├─ db/             migrations, connections, and the query shapes from §4
├─ auth/           three modes; the self-contained AS (CIMD + DCR)
├─ http/           /frames/{id}.jpg and /healthz
├─ jobs/           job rows, the state machine, and the pipeline seam
└─ tools/          the nine tools and the three resources
```

The host app owns the ASGI root and MCP is a mount. That is not a style choice:
custom routes are never authenticated in either framework (health checks and
OAuth callbacks must be reachable before any token exists), and
`/frames/<id>.jpg` must be authenticated.

## Running it

```bash
uv run vidtheque-mcp          # reads the environment; see deploy/.env.example
curl localhost:8080/healthz
```

`make test` runs the suite: CPU only, no model downloads, no network.

## Auth in one table

| `VIDTHEQUE_AUTH` | What runs | Who it is for |
|---|---|---|
| `none` | no PRM, no 401, frames unsigned | localhost / trusted LAN |
| `token` | static bearer over `VIDTHEQUE_TOKEN` | tunnel without OAuth |
| `oauth` | the self-contained AS: CIMD first, DCR retained | claude.ai custom connector |

In `oauth` mode vidtheque **is** its own authorization server. It reuses the
SDK's `/authorize`, `/token`, `/register` and `/revoke` handlers but serves its
own `/.well-known/oauth-authorization-server`, because the SDK's builder never
sets `client_id_metadata_document_supported` and never advertises `"none"` —
and Claude selects CIMD only when both are right. `offline_access` goes in the
AS metadata and stays out of the PRM, or there is no refresh token.

Two things that eat an afternoon if you get them wrong, both checked at boot:
`PUBLIC_URL` must be exactly what the user types into their client, and its host
must be in `VIDTHEQUE_PUBLIC_HOSTNAME` or the transport's DNS-rebinding guard
answers `421 Misdirected Request` to every request.

## State of the milestone

Everything in the tool surface is implemented against the database **except the
indexing pipeline itself**. `index-video` creates real job rows and
`job-status` reports real state; a claimed item then fails with
`E_NOT_IMPLEMENTED` and says so plainly, because download → whisperX →
keyframes → OCR → embeddings is the next task. The seam is
`jobs/runner.py::Pipeline` — one method, one item. Claiming, per-stage
recording, heartbeats, cancellation, retries and the rollup triggers are
already here and already tested.

## Deviations from the contracts

Each of these is a place where the implementation and a design doc differ, with
why. They are candidates to fold back into the docs.

1. **Tool descriptions are trimmed, not verbatim.** tool-surface §4 says the
   description blocks "ship verbatim", but they run 120–190 words each because
   every one restates the shared rules; DECISIONS.md caps them at ~120 words and
   says shared rules live in the `guide` resource. DECISIONS wins, so the two
   time axes, case-insensitivity, ordering and never-fabricate-ids moved into
   `vidtheque://guide` and each description points there. A test asserts the
   budget.

2. **`t_start`/`t_end` everywhere the doc says `offset_start`/`offset_end`**,
   per DECISIONS.md's naming resolution. Also in `get-frames` and
   `video-summary`, which the doc had not renamed.

3. **No `salience` table.** tool-surface §4.4's token-discipline note describes
   `video-summary` reading a precomputed per-video salience table; the schema
   has none. Key texts are sampled with an `NTILE` bucket query bounded by
   `max_key_texts` (O(caps) rows out, one index scan in) and OCR highlights come
   from the `dup_of IS NULL` partial index. Same bound, no new table — but the
   schema doc should either add the table or drop the claim.

4. **`list-videos` `cues`/`frames` fields are empty.** The doc lists them as
   optional columns fed by "denormalized per-video counters", which the schema
   does not carry. Rather than run an unbounded `COUNT(*)` per row on a list
   path, the columns are present and blank until counters exist.

5. **The frame leg has no encoder to call yet — a real contract gap.**
   index-schema §4.5 says `:q_img_vec` is "the *text* query run through
   SigLIP's text tower — same shared embedding space, which is the entire point
   of using SigLIP over a captioning pass". The worker exposes no such
   endpoint: `POST /v1/embeddings` answers with the transcript model whatever
   `model` asks for, and `POST /v1/embeddings/image` takes image bytes. So
   there is no path from a text query into the 1152-d frame space, and
   `search content_type=frame` cannot return anything.

   We ask for `config['frame_embed.model']` once, see the wrong dimension
   come back, remember it for the process lifetime, and print a `note:` naming
   the missing endpoint — `all` still means all, and a skipped leg is still
   announced. A worker that gains the encoder is picked up on the next restart
   with no change here.

   **The fix belongs on the worker**: a text→frame-space route (a `space=frame`
   switch on `/v1/embeddings`, or a sibling endpoint) running SigLIP 2's text
   tower. Until then the frame leg is inert, and both design docs describe a
   capability the system does not have.

10. **The asymmetric query prefix is the worker's, not ours.** The worker's
    `/v1/embeddings` takes `input_type=document|query` and applies the model's
    prompt itself, so we send the switch rather than prepending
    `config['text_embed.query_prefix']` — doing both would apply it twice. The
    config key stays as the record of what indexing assumed, which is what
    index-schema §1.1 wants it for.

6. **`get-frames` does not resize.** `width`/`quality` are accepted, clamped,
   and bound into the URL signature, but the route serves the stored keyframe.
   The `derived/` LRU cache from index-schema §6 is not built yet; adding it
   changes one function and no contract.

7. **The `return` parameter needs an alias.** `return` is a Python keyword, so
   the handler's parameter is `return_` with a pydantic alias, plus a one-line
   wrapper mapping the key the SDK dumps by alias. The wire name is `return`, as
   the contract says; a test asserts it.

8. **Access-token revocation is best-effort.** Tokens are stateless JWTs, so an
   individual one cannot be withdrawn early; `/revoke` kills the client's
   refresh tokens and the short access TTL closes the gap. Worth a line in the
   research doc.

9. **The SDK's 401 omits `scope`.** The spec SHOULDs a `scope` parameter in the
   `WWW-Authenticate` challenge; `RequireAuthMiddleware` emits `error`,
   `error_description` and `resource_metadata` only. Our own `/frames` 401 does
   include it. Fixing the MCP path means either patching the SDK or wrapping its
   middleware.
