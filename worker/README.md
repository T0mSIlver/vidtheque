# vidtheque-worker

Stateless GPU inference API for vidtheque. Speaks an OpenAI-compatible surface
so that a self-hoster without a GPU can drop it and point `WORKER_URL` at a
hosted provider instead.

| Method | Path                       | Shape                                                    |
| ------ | -------------------------- | -------------------------------------------------------- |
| POST   | `/v1/audio/transcriptions` | OpenAI multipart; `response_format=verbose_json` returns segments with word timestamps |
| POST   | `/v1/embeddings`           | OpenAI JSON; `input` is a string or a list of strings     |
| POST   | `/v1/embeddings/image`     | custom multipart; images in, one vector each, upload order |
| POST   | `/v1/embeddings/frame-query` | custom JSON; a short text query into the *frame* space |
| POST   | `/v1/ocr`                  | custom multipart; images in, `{text, confidence, bbox}` out |
| GET    | `/status`                  | loaded models, VRAM free/used, queue depth               |
| GET    | `/healthz`                 | liveness                                                 |

## Two vector spaces, three endpoints

Text and frames are embedded by different models into spaces that are not
comparable — 1024 dims from `Qwen3-Embedding-0.6B` for transcripts, metadata and
OCR text, 1152 dims from `SigLIP 2 NaFlex so400m` for keyframes. They get
separate endpoints rather than one polymorphic `/v1/embeddings` for two reasons:
`/v1/embeddings` is the OpenAI contract whose entire point is that a GPU-less
deployment can repoint `WORKER_URL` at a hosted provider, and a multipart branch
would break that swap; and a single endpoint returning vectors from two spaces
is an index-corruption bug waiting for a caller to make it.

The frame space has two endpoints of its own because it has two towers.
`/v1/embeddings/image` is the indexing side; `/v1/embeddings/frame-query` runs
the *text* tower of the same checkpoint, which is the entire reason frames are
embedded with SigLIP rather than captioned — "a terminal showing a stack trace"
lands in the same 1152-d space as the frames themselves, with no second model,
no second slot and no second load. Two things follow:

- **It is a path, not a flag.** `space=frame` on `/v1/embeddings` would be
  ignored by a hosted OpenAI provider after the `WORKER_URL` swap, which then
  answers with its own text space at some other width — a silent index
  corruption. An unknown path 404s instead.
- **Keep queries short.** SigLIP 2's trained text context is 64 tokens
  (`model_max_length` in the config is the `1e30` sentinel — ignore it), and
  everything past it is dropped with no error. The backend passes the
  lowercasing and `padding="max_length", max_length=64, truncation=True` that
  transformers 4.x does not apply itself, and logs when a query fills the
  window. Long text belongs on `/v1/embeddings`, in the other space.

`/v1/embeddings` takes one non-OpenAI optional extra, `input_type`
(`document` — the default — or `query`). An instruction-tuned embedder prefixes
queries and not documents; index one side with the wrong setting and recall
degrades with no error anywhere. The prefix text itself is config
(`EMBED_QUERY_PROMPT`, empty to use the checkpoint's own), never the caller's.
A symmetric model like bge-m3 accepts the field and ignores it.

`/v1/embeddings/image` takes an optional `max_num_patches` — NaFlex's resolution
knob, and a per-frame decision rather than a global one: a talking head is worth
the default 256, a slide OCR found sixty lines in is worth 1024. Same
checkpoint either way. Trained budgets are 128/256/576/784/1024.

## The two ideas worth knowing

**Backend abstraction.** Each task (`stt`, `embed`, `image_embed`, `ocr`) is a
protocol with `load()` / `unload()` / `infer()` / `vram_estimate_mb` (plus
`embed_text()` on `image_embed`, the second tower of the same checkpoint).
Implementations register themselves in `backends/registry.py` and are selected
by env (`STT_BACKEND=whisperx`, `EMBED_BACKEND=qwen3-embedding`,
`IMAGE_EMBED_BACKEND=siglip2`, `OCR_BACKEND=rapidocr`). Adding a backend is one
class plus one registry entry — nothing else in the worker knows which
implementation is live. This is the experimentation surface that `bench/`
measures; `EMBED_BACKEND=bge-m3` is the shipped alternative to benchmark
against.

**LifecycleManager.** One object owns the GPU:

- a single asyncio job queue, so GPU work is serialized no matter how many HTTP
  requests arrive at once;
- load-on-demand — models are only pulled into VRAM when a request needs them;
- idle-TTL unload (`IDLE_UNLOAD_SECONDS`, default 300);
- an optional resident *text* embedding model (`EMBED_RESIDENT=1`) that is
  exempt from eviction, for corpora where query-time embedding latency matters
  more than the ~1.8 GB it holds. The frame embedder stays evictable even
  though `/v1/embeddings/frame-query` made it a query-time model too: at ~5 GB
  it is the largest of the four, so a cold load on a frame search is the trade
  `EMBED_RESIDENT` deliberately does not extend to it;
- an NVML free-VRAM check before every load, with LRU eviction of non-resident
  backends when the headroom is short (NVML missing → log and proceed);
- `GPU_ACQUIRE_CMD` / `GPU_RELEASE_CMD` shell hooks around the first load and
  the last unload, so a box that also runs llama.cpp can lease the GPU without
  that policy leaking into this code.

OCR sits outside all of that. RapidOCR is CPU-only here — PP-OCR det/rec are
small dynamic-shape convnets whose CUDA path the maintainers themselves gave up
on — so its VRAM estimate is 0, it never triggers eviction, and it never joins
the GPU lease. Only whisperX and the two embedders contend for the card.

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
