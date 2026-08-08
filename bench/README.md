# bench/

Backend-vs-backend comparisons on real hardware.

The whole point of the worker's backend abstraction is that swapping an
implementation is an env var. That is only worth something if the swap can be
*measured* — otherwise picking whisperX over faster-whisper, or int8 over
float16, is taste. This harness exists to turn those choices into numbers on
the machine that will actually run them.

## What it measures

Per backend variant, over a fixed set of inputs:

- **wall-clock** per request and realtime factor for STT (`audio_seconds /
  elapsed`) — the number that decides whether indexing a 3-hour talk is a
  coffee break or an overnight job;
- **peak VRAM** and **load time**, sampled from `/status`, because a backend
  that is 15% faster but does not fit alongside a resident embedding model is
  not faster in practice;
- **throughput under queueing**, since the lifecycle manager serialises GPU
  work: the interesting figure is jobs/minute end to end, not a single
  request's latency.

Accuracy is deliberately *not* faked here. Where a scenario needs it (WER for
STT, retrieval quality for embeddings) it takes a reference file and reports
against it; without one it reports timings only and says so.

## Why it talks HTTP

The harness drives a **running worker over HTTP** rather than importing it.
Two reasons: it honours the same boundary rule the `mcp` service follows, and
it means the same scenarios can be pointed at a hosted OpenAI-compatible
endpoint for a "is self-hosting actually worth it" comparison.

Because backend selection happens at worker startup, a scenario with several
variants is run one variant at a time: the harness prints the environment for
the next variant and waits for the worker to come back with it. GPU state that
is not reset between variants makes the numbers meaningless.

## Running

```bash
make bench                                    # default scenario, dry run plan
uv run python bench/run.py --list             # available scenarios
uv run python bench/run.py bench/scenarios/stt-backends.toml \
    --worker-url http://localhost:8081 --out bench/runs/
```

Media files are not committed. Point a scenario at your own clips; the
scenario file records what they were (duration, source, language) so a result
is reproducible enough to argue with. A case's `input` is resolved against
`[media] root`, then against the repo, then taken as a literal string — which
is how an `embed` case says "one short query string" and means it.

Case types: `stt`, `embed`, `frame_query`, `image_embed`, `ocr`, `queue`. Each
records elapsed time, its own throughput figure, peak VRAM over the case, and
which models the manager had to load *during* the case — the first case of a
variant pays for a load the rest do not, and a number that hides that is worse
than no number.

## The files

| file | what it is |
|---|---|
| `run.py` | the scenario runner: variants x cases, one result envelope |
| `harness.py` | measurement plumbing — VRAM sampler, `/status` poller, HTTP, worker process control. Stdlib only |
| `gpu_validation.py` | the lifecycle manager against real hardware: load/unload VRAM discipline, residency, eviction, lease hooks |
| `ballast.py` | squats on VRAM in a separate process, so admission control has a co-tenant to refuse |
| `results/` | committed measurements, with the raw JSON they came from |

`gpu_validation.py` answers a different question from `run.py`: not "which
backend is faster" but "does the memory come back". It runs one worker process
per check, drives the real endpoints, and traces device VRAM (`nvidia-smi`,
500 ms) alongside the manager's own view (`/status`, 250 ms) on one clock.

```bash
uv run --no-sync python bench/gpu_validation.py --list
uv run --no-sync python bench/gpu_validation.py stt embed image_embed ocr \
    --audio talk.opus --frames /path/to/keyframes --out bench/results/raw
```

**The two VRAM sources disagree by design.** NVML's `used` counts the driver's
own reserve; `nvidia-smi memory.used` does not. On this box that is a constant
~322 MB, and it is the *NVML* number admission control gates on — so the worker
believes 322 MB less is free than the card reports. Never mix the two in one
figure.

## Status

The runner and its measurement bodies are real; the first hardware run is
`results/gpu-3090-2026-08-08.md` (RTX 3090, whisperX large-v3 + Qwen3-Embedding
+ SigLIP 2 + RapidOCR). WER is implemented but unexercised — no scenario here
ships a reference transcript yet.
