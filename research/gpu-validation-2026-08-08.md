# GPU validation on the 3090 (2026-08-08)

Companion to `research/e2e-smoke-2026-08-08.md`, which proved the loop on CPU
with no GPU on the box at all. This one is the other half: the same worker, the
same endpoints, on an RTX 3090 (24 GB, driver 550.163.01, `torch 2.8.0+cu128`),
asking the question CPU could not — **is the lifecycle manager's VRAM discipline
real on hardware?**

Measurements and tables: `bench/results/gpu-3090-2026-08-08.md`, raw JSON in
`bench/results/raw/`. Harness: `bench/gpu_validation.py` + `bench/harness.py`
(new, this session), plus `bench/ballast.py`, which squats on VRAM in a separate
process so admission control has a co-tenant to refuse.

**Verdict: yes, with one hole.** Load-on-demand, idle unload, LRU eviction,
serialisation, residency and the lease hooks all do on hardware exactly what
`lifecycle.py` says they do, and the memory comes back within ~1 s of an unload
with zero growth across cycles. The hole is not in the manager — it is in the
numbers the manager is given. `WhisperXBackend.default_vram_mb = 3200` is 2.5x
below the measured inference peak, so admission control will happily admit a
load that then OOMs, and a whisperX job that OOMs **poisons its slot until the
process restarts**.

---

## 1. What was validated

| claim (from `lifecycle.py`'s docstring) | held? | evidence |
|---|---|---|
| nothing loads until a job needs it | yes | device at 2 MB through boot; no CUDA context until the first job |
| an idle model is evicted after the TTL | yes | 4993 → 343 MB, 15.7 s after the last job, all three GPU backends |
| a resident embedder is exempt | yes | `EMBED_RESIDENT=1`: `unload_count: 0` while the frame embedder and OCR were reaped |
| one GPU job at a time | yes | running spans `stt [1.8→13.9]`, `embed [14.1→18.6]`, in-flight 2, overlap none |
| LRU eviction before a load | yes | under 4212 MB free, evicted `embed` (older `last_used`), kept `stt`, then loaded |
| nothing evictable → `InsufficientVRAM` | yes | 503, `Retry-After: 30`, `type: insufficient_vram`, nothing loaded, lease released |
| acquire before the *first* load, release after the *last* unload | yes | one acquire at 2 MB device, one release at 345 MB, for two models |
| a 0 MB backend skips admission control | yes | OCR never probes and never allocates |
| a 0 MB backend stays out of the GPU lease | **no** | §5.2 |

## 2. VRAM math versus `pipeline-tooling-research.md`

That doc's §1.5 and §2.4 were third-party estimates with an explicit "measure on
the 3090 before trusting it". Measured:

| component | research doc said | measured (idle / peak) | reading |
|---|---|---|---|
| whisperX large-v3 fp16 b16, ASR only | ~5 GB (whisperx-asr-service, on a 3090) | **4993 MB idle** | the 5 GB number is the *resident* figure and it is right |
| whisperX, transcription | ~10 GB (RunPod) | **7941 MB peak**, 324 s clip, batch 16 | RunPod's number is the right order; it is a peak, not a residency |
| whisperX + alignment | (bundled into 16–20 GB with diarization) | alignment is inside the 4993 MB; wav2vec2 base is 360 MB | the doc's "alignment is not your constraint" holds |
| SigLIP 2 so400m weights | 2.27 GB bf16 (arithmetic) | **2489 MB right after load** | the arithmetic was right to ~9% |
| SigLIP 2 total at batch 64 | 4–6 GB | **2870 MB at 44 frames/request, 256 patches** | the activation estimate was 2–3x pessimistic at this batch |
| Qwen3-Embedding 0.6B | "~1–2 GB resident" (handoff) | **1975 MB** at batch 64 × 575 chars | right at the top of the range |
| handoff: "whisper+embed+OCR peak ~3–4 GB, often no eviction needed" | — | **whisperX alone peaks at 7.9 GB** | the handoff was optimistic by ~2x even with diarization off |

Two deviations worth carrying forward:

1. **The doc warned about diarization (pyannote 4.x, ~12 GB) as the VRAM risk.
   Diarization is off by default, and whisperX still peaks near 8 GB.** The
   ~12 GB lease sizing the doc recommends for diarization-on is about right for
   *diarization-off* too if you size on peak rather than residency: 8 GB for
   STT, ~2 GB for the text embedder, ~3 GB for the frame embedder.
2. **SigLIP 2's estimate is the one that is too big.** At 5000 MB it forces
   evictions it does not need — in the eviction check it evicted a model to make
   room for 5512 MB and then used 2869 MB. Nothing breaks; the card is just
   idler than it needs to be.

### The CUDA context floor

Device VRAM after an idle unload is **337–341 MB above an empty card**, in
every backend, and exactly 2 MB after the process exits. That is the CUDA
primary context: created by the first `.to("cuda")`, freed only at process
exit. `_empty_cuda_cache()` cannot touch it, and no amount of `del model` will.

This matters for the lease story: a worker that has ever loaded anything holds
~340 MB of the card until it is stopped, even while "idle". For a 24 GB card
shared with llama.cpp that is noise, and it is worth knowing rather than
chasing.

### NVML is not `nvidia-smi`

The worker's `NvmlProbe` (`nvmlDeviceGetMemoryInfo`) reports **322 MB more used**
than `nvidia-smi memory.used`, constantly, on an idle card — the driver's own
reserve, counted by one and not the other. Admission control gates on the NVML
number, so it is 322 MB conservative. Harmless, and a trap for anyone comparing
a `/status` figure to what they see in `nvidia-smi`.

## 3. Performance, in one paragraph

whisperX large-v3 transcribes at **~50x realtime** on this card once loaded
(324 s of audio in 6.5 s of inference; 43x sustained on repeated 149 s clips
including the HTTP round trip), with all 766 words carrying timestamps.
Qwen3-Embedding does **200 embeddings/s** at 64 × 575-character chunks and
answers a single query in 15 ms. SigLIP 2 does **103 frames/s** at 256 patches
and a frame query in 10 ms. RapidOCR, on CPU, does 3.4 frames/s. Against the
CPU smoke's 0.94 s of pipeline per second of video, the GPU legs are no longer
the cost — for the Fireship clip, STT (3.4 s), frame embedding (0.4 s) and text
embedding (0.3 s) together are under 5 s of the ~140 s job, and OCR at 13 s for
44 frames is now the largest inference stage in the pipeline.

## 4. Cold-start cost, which is the thing residency actually buys

| | load | reload after idle unload |
|---|---|---|
| whisperX large-v3 | 5.8 s (14.3 s the first time, incl. a 2.9 GB download) | 2.5 s |
| Qwen3-Embedding | 6.8 s | 3.7 s |
| SigLIP 2 so400m | 6.9 s | 5.8 s |

So `EMBED_RESIDENT=1` buys ~3.7 s off the first transcript search after an idle
period, for 1483 MB of device (1146 MB weights + the context) — against
`deploy/.env.example`'s advertised ~1.8 GB. On a card shared with llama.cpp the
more interesting number is the frame embedder's **5.8 s reload**, paid by the
first frame search after any idle gap; it is not covered by `EMBED_RESIDENT` by
design (`config.py` says so explicitly) and the measurement supports that call —
2.9 GB standing to save 5.8 s is a worse trade than 1.5 GB to save 3.7 s.

---

## 5. Findings

### 5.1 whisperX's VRAM estimate is 2.5x low, and an OOM poisons the slot

**Severity: high.** This is the one that can take the worker down on a shared
card.

`worker/src/vidtheque_worker/backends/whisperx_stt.py:26`

```python
# large-v3 in float16 plus the alignment model, with room for the batch.
default_vram_mb = 3200
```

Measured on the 3090: 4993 MB resident with the alignment model warm, **7941 MB
peak** during inference at the default `STT_BATCH_SIZE=16` (5352 MB at batch 4).
So admission control (`lifecycle._admit`) can pass a load with as little as
3712 MB free that then needs more than twice that.

Repro (`bench/gpu_validation.py stt-underestimate`, raw in
`bench/results/raw/stt-underestimate.json`):

```
ballast leaves 4197 MB free   ->  4197 >= 3200 + 512, load admitted (6.8 s)
POST /v1/audio/transcriptions ->  RuntimeError: CUDA failed with error out of memory
                                  HTTP 500 "Internal Server Error"
ballast released, 20 GB free  ->  HTTP 500 parallel_for failed:
                                  cudaErrorInvalidDevice: invalid device ordinal
40 s later (idle reaper ran)  ->  HTTP 200
```

Three separate problems in that trace:

1. **The estimate.** 3200 MB is roughly the CTranslate2 weights alone. A number
   that reflects the peak — call it 8000 MB at batch 16, or make it a function
   of `STT_BATCH_SIZE` — would have refused this load with a 503 the MCP side
   already knows how to retry.
2. **A failed job leaves the slot `loaded`.** `LifecycleManager._execute`
   catches the exception, sets the future's exception and moves on; nothing
   unloads the backend. The CTranslate2 model is dead but `slot.loaded` is
   `True`, so every later STT job goes straight to it and gets
   `cudaErrorInvalidDevice`. Suggested fix, in `_execute`'s exception path:
   unload the slot when the job raised (or at least when the exception looks
   like a CUDA/OOM failure), so the next job reloads clean.
3. **The error escapes the envelope.** `RuntimeError` is not a `BackendError`,
   so none of `app.py`'s handlers catch it: the caller gets a bare
   `500 Internal Server Error` with an HTML-ish body instead of
   `{"error": {"type": …}}`. `mcp/`'s worker client is written against the 503
   + `Retry-After` contract; this path gives it neither. Suggested fix: a
   catch-all handler in `_install_error_handlers` that wraps anything unhandled
   into the same envelope, and ideally maps a recognised CUDA OOM to 503 +
   `Retry-After` since it *is* transient.

Recovery today is accidental: the idle reaper unloads the wedged backend after
`IDLE_UNLOAD_SECONDS`, and the next request reloads it. With
`IDLE_UNLOAD_SECONDS=0` — a documented, supported setting — the slot never
recovers and STT is down until someone restarts the process.

### 5.2 A CPU-only OCR request takes the GPU lease

**Severity: medium** (only bites a box using the lease hooks, i.e. Tom's).

`rapidocr_ocr.py`'s module docstring:

> So this backend's VRAM estimate is 0 and it never contends for the GPU lease
> — only whisperX and the two embedders do.

It does contend. `lifecycle._ensure_loaded` runs `_acquire_lease()` before every
first load, unconditionally:

```python
await self._admit(slot)          # returns early for a 0 MB backend
acquired_here = await self._acquire_lease()   # runs regardless
```

Measured (`bench/results/raw/hooks-ocr.json`): a worker whose only request is
`POST /v1/ocr` fires `GPU_ACQUIRE_CMD` with the device at 2 MB, and holds the
lease for as long as the OCR engine stays loaded — so on a box where acquire
stops llama.cpp, **OCR-only work stops llama.cpp**, keeps it stopped for the
idle TTL, and hands nothing to the GPU in return. `_any_loaded()` has the same
blind spot: it counts the loaded OCR engine, so the release hook waits for it.

Suggested fix (one line each): gate the acquire on
`slot.backend.vram_estimate_mb > 0`, and have `_any_loaded()` only count
backends with a non-zero estimate.

### 5.3 `EMBED_RESIDENT=1` means the release hook never runs

**Severity: low, but it is a real interaction between two documented features.**

A resident backend is exempt from the reaper, so something is always loaded, so
`_any_loaded()` is always true, so `_release_lease()` never fires while the
worker is up. Measured in `bench/results/raw/resident.json`: after the reaper
took the frame embedder and OCR, `/status` still reports
`lease.acquired: true`, indefinitely.

That is arguably correct — the card *is* still in use — but the combination
"`EMBED_RESIDENT=1` + `GPU_ACQUIRE_CMD`" means the co-tenant is stopped at the
first embedding request and never restarted. Worth a sentence in
`deploy/.env.example` next to `EMBED_RESIDENT`, since the two knobs are
documented in separate sections and nothing connects them.

### 5.4 Smaller notes

- **`/status` `vram.used_mb` reads 322 MB higher than `nvidia-smi`** (§2). Not a
  bug; worth a word in the `NvmlProbe` docstring so nobody "fixes" a phantom
  322 MB leak later.
- **SigLIP 2's 5000 MB estimate is ~1.7x its measured peak** and causes
  unnecessary evictions. 3200 MB would still be conservative at 256 patches;
  note that `IMAGE_EMBED_MAX_PATCHES=1024` is ~4x the work and was not measured
  here.
- **`vram_estimate_mb` is a constant per backend**, but three of the four
  backends' real peaks scale with a knob the operator controls
  (`STT_BATCH_SIZE`, `IMAGE_EMBED_MAX_PATCHES`, embedding batch). If the
  estimates are revisited, making them a function of those settings is the
  version that stays true.

---

## 6. Harness notes for the next run

- `bench/gpu_validation.py <check>…` — one worker process per check, so any
  check re-runs alone. Checks: `stt`, `embed`, `image_embed`, `ocr`,
  `resident`, `queue`, `evict`, `no-room`, `hooks`, `hooks-ocr`,
  `stt-underestimate`.
- **Never sample only at request boundaries.** A warm embedding request is
  0.3 s and the VRAM sampler polls at 0.5 s; the first pass produced empty
  windows and a nonsense peak. `--repeat` exists to make the measured window
  outlive the poll interval.
- **A worker left running from a crashed run is the classic bad measurement**:
  it answers `/healthz`, holds the port and holds a CUDA context, so the next
  run measures a ghost and reads a 324 MB "baseline". `harness.Worker.start()`
  now refuses to start if anything already answers on the port.
- The environment on this box is synced with
  `uv sync --package vidtheque-worker --extra gpu --extra nvml`; every command
  must be `uv run --no-sync` or uv re-resolves the venv mid-run. yt-dlp is not
  in that env — fetch media outside it.
