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
is reproducible enough to argue with.

## Status

Skeleton. The scenario format, the runner's plan/dry-run mode and the result
envelope are real; the measurement bodies are marked `TODO(bench)` and land
with the first backend comparison.
