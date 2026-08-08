# vidtheque

Self-hosted MCP server that turns videos you've watched into a searchable
multimodal corpus — transcript, on-screen text, and frame search with
timestamped citations.

Point it at a video, a channel, or a playlist; it transcribes with word-level
timestamps, OCRs what is on screen, embeds keyframes, and keeps the whole thing
in a local index. Your MCP client then searches across everything you have ever
indexed and cites answers with deep links that land on the exact second
(`https://youtu.be/ID?t=123`).

> **Early development.** Nothing here is stable yet: no releases, no published
> images, schemas and endpoints change without notice. The GPU worker skeleton
> and the deployment scaffolding are in; the MCP server itself is not written
> yet. Watch the repo rather than depending on it.

## Architecture

Two services, one repo, HTTP between them — never a shared Python import.

```
                   ┌──────────────────────────────────────────┐
  MCP client       │ mcp/  (CPU, multi-arch — runs on a Pi)    │
  (Claude, …)      │                                          │
        │          │   OAuth ── yt-dlp ── pipeline orchestr.   │
        │  MCP     │       SQLite + sqlite-vec + FTS5          │
        └─────────▶│       keyframe JPEGs, job queue           │
                   └───────────────────┬──────────────────────┘
                                       │  HTTP (OpenAI-compatible)
                                       │  /v1/audio/transcriptions
                                       │  /v1/embeddings
                                       │  /v1/ocr
                                       ▼
                   ┌──────────────────────────────────────────┐
                   │ worker/  (GPU, single box)               │
                   │                                          │
                   │   LifecycleManager — one job queue,      │
                   │   load-on-demand, idle-TTL unload,       │
                   │   NVML VRAM check, acquire/release hooks │
                   │                                          │
                   │   STTBackend   EmbedBackend   OCRBackend │
                   │   whisperX     bge-m3         RapidOCR   │
                   └──────────────────────────────────────────┘

  optional: cloudflared ── tunnels the mcp service to a public hostname
            (compose profile `tunnel`)
```

The worker is a **stateless inference API**. No GPU? Skip the worker service
entirely and point `WORKER_URL` at any OpenAI-compatible provider — the
endpoints are the contract, not the implementation.

## Quickstart

```bash
git clone https://github.com/T0mSIlver/vidtheque.git
cd vidtheque/deploy
cp .env.example .env      # read it: every knob is documented there
docker compose up -d      # mcp + worker
```

With a Cloudflare tunnel for remote access:

```bash
TUNNEL_TOKEN=… docker compose --profile tunnel up -d
```

Check the worker:

```bash
curl localhost:8081/healthz
curl localhost:8081/status     # loaded models, VRAM, queue depth
```

## Development

Requires [uv](https://docs.astral.sh/uv/) and Python 3.12.

```bash
uv sync            # workspace: mcp + worker + dev tools (CPU-only deps)
make test          # pytest, CPU-only, no model downloads
make bench         # backend-vs-backend comparisons on your own hardware
```

Heavy inference dependencies live in the worker's `[gpu]` extra, so CI and a
laptop checkout install cleanly without CUDA:

```bash
uv sync --extra gpu     # whisperX, sentence-transformers, RapidOCR, NVML
```

## Layout

| Path                 | What                                                          |
| -------------------- | ------------------------------------------------------------- |
| `mcp/`               | MCP server (placeholder — framework choice pending)            |
| `worker/`            | GPU inference worker: FastAPI + backend registry + lifecycle   |
| `deploy/`            | docker-compose, `.env.example`, tunnel wiring                  |
| `bench/`             | benchmark harness — backend comparisons on real hardware       |
| `research/`          | design notes and landscape survey (private-ish working notes)  |

## License

MIT — see [LICENSE](LICENSE).
