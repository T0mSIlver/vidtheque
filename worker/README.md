# vidtheque-worker

Stateless GPU inference API for vidtheque. Speaks OpenAI shapes where they fit,
so that a self-hoster without a GPU can drop this service and point `WORKER_URL`
at a hosted provider for the transcript leg — `/v1/embeddings` is the standard
contract and the swap is real. The other three paths below are this project's
own, and frame search and on-screen text do not work without something that
answers them.

| Method | Path                       | Shape                                                    |
| ------ | -------------------------- | -------------------------------------------------------- |
| POST   | `/v1/audio/transcriptions` | OpenAI multipart; `response_format=verbose_json` returns segments with word timestamps |
| POST   | `/v1/embeddings`           | OpenAI JSON; `input` is a string or a list of strings     |
| POST   | `/v1/embeddings/image`     | custom multipart; images in, one vector each, upload order |
| POST   | `/v1/embeddings/frame-query` | custom JSON; a text query embedded for *frame* retrieval |
| POST   | `/v1/ocr`                  | custom multipart; images in, `{text, confidence, bbox}` out |
| GET    | `/status`                  | loaded models, VRAM free/used, queue depth               |
| GET    | `/healthz`                 | liveness                                                 |

## One vector space, three endpoints

**Shipped default: `Qwen/Qwen3-VL-Embedding-2B`, one checkpoint, 2048 dims,
Apache-2.0, ~4.4 GB of bf16 weights, 32K text context.** Transcripts, metadata,
OCR text *and* keyframes land in **one** comparable space — the model reads a
slide or a terminal as a document rather than as a picture, the axis where a
CLIP-style dual encoder measures 1.3–3.6× worse and where a corpus of
conference talks actually lives (`research/multimodal-embedding-2026-08-09.md`,
Tom's decision of 2026-08-09).

It replaced two models: `Qwen3-Embedding-0.6B` (1024 dims) on the transcript leg
and `SigLIP 2 NaFlex so400m` (1152 dims) on the frame leg. **Both are still
selectable** — `EMBED_BACKEND=qwen3-embedding` or `bge-m3`,
`IMAGE_EMBED_BACKEND=siglip2` — and that configuration is genuinely *two*
spaces, never mixed. It is a smaller-card fallback, not the default, and it
needs `config['text_embed.dim']` and the `vec_chunks` DDL moved back to 1024
(`docs/design/index-schema.md` §3.1).

**One model, one slot.** `embed` and `image_embed` stay separate lifecycle
tasks — indexing needs both at once and one slot would force the pipeline to
choose — but when `EMBED_BACKEND`, `IMAGE_EMBED_BACKEND` **and** the two model
ids all agree, `build_backends` hands the manager the *same instance* twice and
`LifecycleManager` gives it **one shared Slot**: one 4.4 GB VRAM charge instead
of 8.8, one eviction clock, one GPU-lease bracket, and one load on a cold
`content_type=all` search instead of two (§5.5 of the same research note). Point
them at different backends and you get two models in two slots — supported,
correct, and twice the cold start.

Three endpoints survive the collapse to one space, for reasons that are now
about *interface* and *instruction* rather than about incomparable vectors:

- **`/v1/embeddings` is the OpenAI contract**, and its whole point is that a
  GPU-less deployment can repoint `WORKER_URL` at a hosted provider. A
  multipart branch on it would break that swap, so images get their own path.
- **A path, not a flag.** `space=frame` on `/v1/embeddings` would be silently
  ignored by a hosted provider after that swap, which then answers from its own
  text space at some other width — a silent index corruption. An unknown path
  404s instead.
- **The three paths carry three instructions, and the instruction is part of
  the vector.** This model is instruction-aware, so the same text under two
  instructions is two different vectors.

| path | instruction applied |
| --- | --- |
| `/v1/embeddings`, `input_type=document` (default) | none — documents are embedded bare |
| `/v1/embeddings`, `input_type=query` | `EMBED_QUERY_PROMPT`, default *"Given a search query, retrieve the transcript passage that answers it"* |
| `/v1/embeddings/image` | none — frames are the document side too |
| `/v1/embeddings/frame-query` | `FRAME_QUERY_PROMPT`, default *"Given a search query, retrieve the video frame that matches it"* |

`input_type` is the one non-OpenAI extra on `/v1/embeddings`. Index one side
with the wrong setting and recall degrades with no error anywhere; the prefix
text is config, never the caller's, and a symmetric model like bge-m3 accepts
the field and ignores it. Both instructions are echoed back on every embeddings
response (`instruction`) and under `instructions` on `GET /status`, because the
corpus records what indexing assumed in `config['text_embed.query_prefix']` and
`config['frame_embed.query_prefix']` — and that record drifted from behaviour
once already. `curl $WORKER_URL/status` is the whole reconciliation.

`/v1/embeddings/frame-query` embeds a *text* query into the frame space, so "a
terminal showing a stack trace" is answerable with no second model, no second
slot and no second load. **SigLIP 2's 64-token query ceiling went with the text
tower it belonged to**: frame queries now run through a 32K-context model and a
long descriptive query is embedded whole.

`/v1/embeddings/image` still accepts `max_num_patches` — **a SigLIP-2-only
knob**, NaFlex's per-request resolution budget (trained values
128/256/576/784/1024, `IMAGE_EMBED_MAX_PATCHES` for the default). The unified
embedder ignores it: it takes the frame at its stored resolution (1280×720 →
~880 merged visual tokens, measured), which the paper's own sweep puts at the
knee of the scaling curve. There is nothing to tune and nothing to under-feed.

`EMBED_DIM` is the MRL truncation width, `0` meaning the native 2048 — the
shipped decision. It must equal `config['text_embed.dim']` and
`config['frame_embed.dim']` in the corpus, or `mcp/`'s drift check disables both
vector legs and search answers FTS-only.

## The two ideas worth knowing

**Backend abstraction.** Each task (`stt`, `embed`, `image_embed`, `ocr`) is a
protocol with `load()` / `unload()` / `infer()` / `vram_estimate_mb` (plus
`embed_text()` on `image_embed`, which under the unified default is the same
weights under a different instruction and under `siglip2` is the checkpoint's
text tower). Implementations register themselves in `backends/registry.py` and
are selected by env (`STT_BACKEND=whisperx`, `EMBED_BACKEND=qwen3-vl-embedding`,
`IMAGE_EMBED_BACKEND=qwen3-vl-embedding`, `OCR_BACKEND=rapidocr` — the shipped
defaults). Adding a backend is one class plus one registry entry — nothing else
in the worker knows which implementation is live. This is the experimentation
surface that `bench/` measures; `qwen3-embedding`, `bge-m3` and `siglip2` are
the alternatives to benchmark against. Switching either embedding backend
changes the vector space, so it is a re-embed of the whole corpus rather than a
restart — migrate the database *first* (`deploy/.env.example` explains why
worker-first latches the re-embed off).

**LifecycleManager.** One object owns the GPU:

- a single asyncio job queue, so GPU work is serialized no matter how many HTTP
  requests arrive at once;
- load-on-demand — models are only pulled into VRAM when a request needs them;
- idle-TTL unload (`IDLE_UNLOAD_SECONDS`, default 300);
- an optional resident embedding slot (`EMBED_RESIDENT=1`, default `0`) that is
  exempt from eviction, for deployments where query-time latency matters more
  than the ~4.4 GB it holds standing. Under the shipped unified default that one
  flag covers **both** legs, because they are one slot. It is off by default and
  the reason is the lease, not the VRAM: a resident model never holds
  `GPU_ACQUIRE_CMD`'s bracket, so `GPU_RELEASE_CMD` fires only at shutdown and a
  llama.cpp co-tenant is stopped at the first embedding request and never
  restarted (measured, `research/gpu-validation-2026-08-08.md` §5.3). Going
  unified already collects most of what residency buys: a cold
  `content_type=all` search used to load two models and now loads one;
- an NVML free-VRAM check before every load, with LRU eviction of non-resident
  backends when the headroom is short (NVML missing → log and proceed);
- `GPU_ACQUIRE_CMD` / `GPU_RELEASE_CMD` shell hooks around the first load and
  the last unload, so a box that also runs llama.cpp can lease the GPU without
  that policy leaking into this code.

OCR sits outside all of that. RapidOCR is CPU-only here — PP-OCR det/rec are
small dynamic-shape convnets whose CUDA path the maintainers themselves gave up
on — so its VRAM estimate is 0, it never triggers eviction, and it never joins
the GPU lease. Under the shipped unified default only whisperX and the one
shared embedding slot contend for the card; split the two embedding backends and
it is three.

Every environment variable is documented in `deploy/.env.example`.

## Running it

```bash
uv sync --extra gpu --extra nvml
uv run vidtheque-worker            # honours HOST/PORT, defaults 0.0.0.0:8081
```

Without the `gpu` extra the app still starts, `/healthz` and `/status` answer,
and inference endpoints return 503 with the missing dependency named — which is
exactly what CI exercises.

## Contract

`openapi.json` is generated from the app (`make openapi`) and is the interface
the `mcp/` service codes against. No Python import ever crosses between the two
packages.
