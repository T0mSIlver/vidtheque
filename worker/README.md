# vidtheque-worker

Stateless GPU inference API for vidtheque. Speaks an OpenAI-compatible surface
so that a self-hoster without a GPU can drop it and point `WORKER_URL` at a
hosted provider instead.

| Method | Path                       | Shape                                                    |
| ------ | -------------------------- | -------------------------------------------------------- |
| POST   | `/v1/audio/transcriptions` | OpenAI multipart; `response_format=verbose_json` returns segments with word timestamps |
| POST   | `/v1/embeddings`           | OpenAI JSON; `input` is a string or a list of strings     |
| POST   | `/v1/ocr`                  | custom multipart; images in, `{text, confidence, bbox}` out |
| GET    | `/status`                  | loaded models, VRAM free/used, queue depth               |
| GET    | `/healthz`                 | liveness                                                 |

## The two ideas worth knowing

**Backend abstraction.** Each task (`stt`, `embed`, `ocr`) is a protocol with
`load()` / `unload()` / `infer()` / `vram_estimate_mb`. Implementations register
themselves in `backends/registry.py` and are selected by env
(`STT_BACKEND=whisperx`, `EMBED_BACKEND=bge-m3`, `OCR_BACKEND=rapidocr`).
Adding a backend is one class plus one registry entry — nothing else in the
worker knows which implementation is live. This is the experimentation surface
that `bench/` measures.

**LifecycleManager.** One object owns the GPU:

- a single asyncio job queue, so GPU work is serialized no matter how many HTTP
  requests arrive at once;
- load-on-demand — models are only pulled into VRAM when a request needs them;
- idle-TTL unload (`IDLE_UNLOAD_SECONDS`, default 300);
- an optional resident embedding model (`EMBED_RESIDENT=1`) that is exempt from
  eviction, for corpora where query-time embedding latency matters more than
  the ~1–2 GB it holds;
- an NVML free-VRAM check before every load, with LRU eviction of non-resident
  backends when the headroom is short (NVML missing → log and proceed);
- `GPU_ACQUIRE_CMD` / `GPU_RELEASE_CMD` shell hooks around the first load and
  the last unload, so a box that also runs llama.cpp can lease the GPU without
  that policy leaking into this code.

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
