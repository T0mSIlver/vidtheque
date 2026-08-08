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
├─ pipeline/       the seven indexing stages behind that seam
└─ tools/          the nine tools and the three resources
```

Inside `pipeline/`, the split that matters is between what can be tested on a
laptop and what cannot:

```
pipeline/
├─ sources.py       yt-dlp. Info dicts in, normalized records out — the parsing
│                   half is pure functions over canned dicts.
├─ captions.py      whisperX verbose_json, YouTube json3, WebVTT -> one cue type
├─ chunking.py      45 s windows, 15 s overlap, from `config`
├─ keyframes.py     PySceneDetect -> sharpest per shot -> JPEG -> phash dedup
├─ worker_client.py the indexing half of the worker seam (retry, streaming)
├─ store.py         every write, as plain functions over a connection
├─ settings.py      the pipeline's environment, resolved once
├─ paths.py         the on-disk layout from index-schema §6, in one place
└─ runner.py        run_item: the stage order, the failure policy, the fan-out
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

The tool surface and the indexing pipeline are both implemented.
`index-video` creates real job rows, the runner executes them, and what it
writes comes back out of `search` — there is an end-to-end test that asserts
exactly that, against canned yt-dlp info dicts, an ffmpeg-synthesized clip and
a fake worker.

The seven stages, and what each records in `video_stages`:

| stage | does | `model_key` |
|---|---|---|
| `fetch` | info dict (title, chapters, subtitle inventory, heatmap), then audio and — when frames are wanted — video at the height cap | yt-dlp version |
| `stt` | whisperX via the worker, YouTube auto-captions (word-timed `json3`), or manual subs, in the order `VIDTHEQUE_STT_POLICY` asks for | `config['stt.model']`, or the caption track |
| `chunk` | 45 s windows, 15 s overlap, both from `config` | `chunk-45-15` |
| `text_embed` | `POST /v1/embeddings` → `vec_chunks` | `config['text_embed.model']` |
| `keyframe` | PySceneDetect → sharpest per shot → JPEG → phash dedup | detector + width |
| `ocr` | `POST /v1/ocr` → `ocr_lines` | `config['ocr.model']` |
| `frame_embed` | `POST /v1/embeddings/image` → `vec_frames` | `config['frame_embed.model']` |

**Failure is per stage.** A stage that fails records its own failure and leaves
finished stages alone, so resume means "re-run the failed stages". Only `fetch`
and `stt` abort the item: without a video row there is nothing to attach to, and
without cues there is nothing to search. A video with no OCR is still a video
you can find by what was said in it, and `data_status` says which.

**The zero-GPU path is real.** With the worker unreachable and the policy
allowing it, the pipeline never downloads the audio, indexes YouTube's
auto-captions (whose `json3` track carries per-word offsets, so deep links stay
precise), records `stt.model_key = youtube-asr-<lang>`, and logs a job event
saying the video was indexed CPU-only. The per-stage `model_key` is what makes
"upgrade it to whisperX later" a reindex of one stage rather than a rebuild.

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

5. **The frame leg is live** (resolved in commit `81440e4`). index-schema
   §4.5's `:q_img_vec` is "the *text* query run through SigLIP's text tower —
   same shared embedding space, which is the entire point of using SigLIP over
   a captioning pass". `worker/openapi.json` carries
   **`POST /v1/embeddings/frame-query`**, `{"input": str | [str], "model"?}`
   answering with the usual `EmbeddingsResponse`;
   `pipeline/worker_client.py::embed_frame_query` speaks it for indexing and
   `tools/base.py::embed_query(space="frame")` speaks it for search. A worker
   that predates the endpoint 404s → `FrameQueryUnsupported`, latched until
   restart with a `note:` in every affected search; transient outages are
   noted but never latched. `all` still means all — a skipped leg is always
   announced.

   A sibling **path** rather than a `space=frame` **field** on `/v1/embeddings`
   is the right shape for one reason worth keeping: point `WORKER_URL` at a
   hosted OpenAI-compatible provider and an unknown *field* is ignored — you
   ask for frame space, get text space at some other width, and write it into
   the frame index. An unknown *path* 404s, and a 404 degrades to the same
   `note:`.

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

11. **`fetch_metadata` and `fetch_media` are one stage, not two.** The task
    description splits them; `video_stages.stage` is a `CHECK` constraint with
    seven values and `fetch` is the one that covers both. They stay one stage
    (progress 0.0 → 0.25 → 1.0 within it) rather than earning a migration:
    splitting them buys a finer resume boundary for the cheaper of the two
    halves, since the metadata probe is one request and the download is
    hundreds of megabytes.

12. **`keyframes.phash` stores the 64-bit hash; dedup runs on a 256-bit one.**
    The column is an `INTEGER` (index-schema §1.6, "64-bit dct hash, stored
    signed") and research §4.4 is emphatic that `hash_size=8` is too narrow to
    separate two slides from one deck — at 64 bits their hashes are frequently
    identical, so dedup at that width silently drops distinct slides. Both are
    computed: the wide one clusters in memory during the stage, the narrow one
    is stored for "find frames that look like this one" over an already-capped
    candidate set. Widening the column would be a schema change for a query
    nobody issues yet.

13. **Extracted audio is opus, not 16 kHz WAV.** whisperX wants 16 kHz mono and
    research §5.2 has yt-dlp produce exactly that — but the two halves of this
    system talk over HTTP, so that WAV is a ~256 MB upload per two-hour lecture
    against ~20 MB of opus, and the worker re-decodes with ffmpeg either way.
    index-schema §6.1 sizes the disk budget on opus as well.
    `VIDTHEQUE_AUDIO_CODEC=wav` restores the letter of the research doc.

14. **`ocr_lines.poly_json` is always NULL.** §1.7 keeps the original RapidOCR
    quad for rotated text; the worker's `OCRItemOut` returns an axis-aligned
    `bbox` only, so there is no quad to store. Normalization to 0-1 happens
    here, from the keyframe's own width and height.
