# CPU vs GPU, end to end (2026-08-09)

`research/gpu-validation-2026-08-08.md` measured the backends. This measures the
**product**: the same 2:29 screencast indexed through the real
`index-video` → `job-status: done` loop three times, once per configuration,
each with its own worker, its own mcp process and a fresh data dir.

| # | configuration | what changes |
|---|---|---|
| 1 | `cpu-autocaps` | `DEVICE=cpu`, `VIDTHEQUE_STT_POLICY=captions_only` |
| 2 | `gpu-autocaps` | `DEVICE=cuda`, `captions_only` — the GPU only ever touches the two embedders |
| 3 | `gpu-whisperx` | `DEVICE=cuda`, `whisperx_only` — whisperX large-v3 actually transcribing |

Harness: `bench/pipeline_bench.py` (new, this session). Raw envelope, all
runs, all traces: `bench/results/raw/pipeline-bench-2026-08-09.json`. Every run
here is against the pipeline as of `3cef225`; this was a multi-agent session and
`mcp/` moved afterwards, so §6's line numbers are the tree at the time of
writing and the symbol names are the durable reference.

Video: `https://youtu.be/5C_HPTJg5ek` — Fireship, "Rust in 100 Seconds",
149 s, the smoke's canonical subject, so the CPU column is comparable with
`research/e2e-smoke-2026-08-08.md` §2.

**Verdict in one line: the GPU takes 33 s out of a 96 s job and cannot touch
the other 58.** Details in §7; the honest number underneath it is that on
these three runs the card was busy for **6–13% of the wall clock**.

---

## 1. Method, and what is held equal

One worker process and one mcp process per configuration, both started by the
harness with a scrubbed environment, both stopped before the next one starts.
Fresh data dir each time — no run inherits another's database, media or model
residency. Everything that is not the variable under test is pinned in
`COMMON_WORKER` / `COMMON_MCP`: `screencast` detector, `IMAGE_EMBED_MAX_PATCHES=256`
(the budget gpu-validation measured SigLIP 2 at), `OCR_THREADS=8`,
`IDLE_UNLOAD_SECONDS=20`, `EMBED_BATCH`/`FRAME_EMBED_BATCH` at their defaults.

Three clocks, because one is not enough:

* **`video_stages.started_at/finished_at`** — the pipeline's own per-stage wall
  clock. It is `unixepoch()`, so **1 s resolution**, which is fine for a 36 s
  CPU stage and is most of the number for a 5 s GPU one. This is the table's
  spine because it is what any operator can read back out of any database.
* **the worker's `/status` queue at 250 ms** — the *inference* span inside a
  stage, with the fetch, the JPEG decode and the SQLite writes stripped off.
  Reported as `inference_seconds`.
* **whole-device VRAM from `nvidia-smi` at 500 ms**, plus a baseline sample
  before each configuration starts.

### Two deviations, both deliberate, both load-bearing

1. **`EMBED_RESIDENT=1` and a pre-job warm request.** Not a preference — the
   run does not complete without it. See §6.1: the indexing `text_embed` call
   is bounded by a 20 s budget that no environment variable reaches, and a cold
   Qwen3-Embedding load on this box is 7.8–19.2 s. So the harness loads the
   text embedder with one direct `/v1/embeddings` call before `index-video`,
   times it, and keeps it resident. Every `text_embed` row below is therefore a
   **warm-model** number, and the load it excludes is reported separately.
2. **`captions_only` rather than the smoke's `prefer_whisperx`-with-no-whisperX**
   for configurations 1 and 2. The fallback path is already proven
   (e2e-smoke §1); replaying it here would put ~10 s of 503-and-backoff inside
   the `stt` row, which is not a CPU cost and would not appear in the GPU rows.
   The side effect is real and is itself a finding — see §6.3.

### The box was not quiet

A sibling agent's stack was indexing a batch on the same machine and the same
GPU throughout. Every run records `loadavg` and the VRAM baseline; runs 1 and 2
below are two full rounds, and the spread between them is the contention, not
the configurations. **Four of the seven stages are CPU-only in every
configuration** (`fetch`, `chunk`, `keyframe`, and `ocr` — RapidOCR is CPU on
purpose, `rapidocr_ocr.py`'s docstring says why), so their variation across
columns is noise by construction. Read the table with that in mind; §3 says
which rows are signal.

---

## 2. The table

**Run 1** — the least contended round. Both `autocaps` configurations ran
back to back at `loadavg` ≈ 5.8; `gpu-whisperx` ran at `loadavg` 2.85 but with
the co-tenant's GPU at 100% util.

| stage | cpu-autocaps | gpu-autocaps | gpu-whisperx |
|---|---|---|---|
| `fetch` | 16 s | 21 s | 39 s (audio **and** video) |
| `stt` | 5 s | 5 s | **13 s** (7.9 s of it the whisperX load) |
| `chunk` | <1 s | <1 s | <1 s |
| `text_embed` | 2 s | **0 s** | **0 s** |
| `keyframe` | 23 s | 26 s | 54 s |
| `ocr` | 14 s | 14 s | 20 s |
| `frame_embed` | **36 s** | **5 s** | **7 s** |
| **stage total** | **96 s** | **71 s** | **133 s** |
| job wall clock | 99.7 s | 99.8 s¹ | 139.2 s |
| realtime factor (149 s ÷ stage total) | **1.55x** | **2.10x** | **1.12x** |
| peak VRAM over baseline | 0 MB | 3787 MB | **7863 MB** |
| loadavg (1 m, before) | 5.77 | 5.82 | 2.85 |

¹ the `gpu-autocaps` job wall clock includes ~25 s of a first attempt that died
on a yt-dlp `403` and was retried from the top (§6.5). Its stage rows are the
successful attempt.

**Run 2** — same three configurations, ~15 minutes later, box at `loadavg`
5.9–14.6. Nothing changed but the neighbours:

| stage | cpu-autocaps | gpu-autocaps | gpu-whisperx |
|---|---|---|---|
| `fetch` | 15 s | 18 s | 34 s |
| `stt` | 5 s | 5 s | 14 s |
| `text_embed` | 10 s | 0 s | 0 s |
| `keyframe` | 54 s | 45 s | 55 s |
| `ocr` | 56 s | 22 s | 27 s |
| `frame_embed` | 37 s | 9 s | 10 s |
| **stage total** | **177 s** | **99 s** | **140 s** |
| loadavg (1 m, before) | 14.59 | 5.85 | 13.33 |

`ocr` moved 14 s → 56 s on identical code, identical device, identical 44
frames. That is the size of the contention, and it is why §3 separates the rows
that answer the question from the rows that only answer "what else was the box
doing".

### Worker-side inference, which the contention moves less

`inference_seconds` from the `/status` queue — the model actually computing,
excluding the stage's IO. Run 1:

| task | cpu-autocaps | gpu-autocaps | gpu-whisperx | per unit |
|---|---|---|---|---|
| `embed` (text, 5 chunks, warm) | 2.55 s | <0.25 s | <0.25 s | 0.51 s/chunk CPU → below the poll floor on GPU |
| `stt` (149 s of audio) | — | — | 5.0 s | **29.8x realtime** (excl. the 7.9 s load) |
| `ocr` (44 frames) | 13.69 s | 13.69 s | 19.49 s | 0.311 s/frame, **CPU in every column** |
| `image_embed` (44 frames) | 33.20 s | 3.87 s | 4.66 s | 0.755 s/frame CPU → **0.088 s/frame GPU**, 8.6x |

Model load times, same runs (the worker times these itself, around
`backend.load()`):

| | cpu | cuda |
|---|---|---|
| Qwen3-Embedding-0.6B | 7.8 / 16.3 / 10.3 s | 7.5 / 7.6 / 9.0 / 12.8 s |
| SigLIP 2 so400m | 4.7 / 8.5 / 4.4 s | 4.9 / 6.3 / 8.6 / 9.1 s |
| whisperX large-v3 | — | 7.9 / 8.6 s |
| RapidOCR | 0.4–0.9 s | 0.4–0.7 s |

The spread inside each cell is the box, not the device: model loading here is
dominated by reading weights and building the graph, and both are CPU work.

---

## 3. Which rows are signal

**Three rows move with the device, and only three.**

* `frame_embed`: **36 s → 5 s**. SigLIP 2 so400m over 44 keyframes at 256
  patches. 0.755 s/frame on ten CPU cores, 0.088 s/frame on the 3090, end to
  end including the HTTP round trip and the JPEG decode. This is the largest
  single win in the pipeline and it is bigger than everything else combined.
* `text_embed`: **2 s → 0 s**. Five 45 s chunks. Real, and irrelevant at this
  scale — at 120 chunks per hour of video it is a minute per hour on CPU and
  seconds on the card.
* `stt`: **5 s → 13 s**, going the "wrong" way, because it is not the same
  work. Auto-captions are a yt-dlp subtitle request; whisperX large-v3 is
  transcription, at 29.8x realtime on a card that a co-tenant was already
  pinning at 100% (gpu-validation §3 measured 43–50x on an idle card).

**Four rows do not move**, and two of them are the pipeline's biggest costs:

* `keyframe` (23 s) is one full decode of a 149 s 1080p file plus JPEG
  extraction. No GPU path; `bench/keyframe_decode.py` measured the NVDEC option
  and it is not where the time goes.
* `ocr` (14 s) is RapidOCR on onnxruntime **CPU in every configuration** —
  deliberately, and the backend's docstring argues the case. On the GPU
  configurations it is therefore the **largest inference stage in the
  pipeline**, three times `frame_embed`. gpu-validation §3 predicted exactly
  this; here it is, in a whole-pipeline run.
* `fetch` is network and yt-dlp politeness.
* `chunk` is SQLite.

So the GPU's effect on this job is **-33 s** (`frame_embed` -31, `text_embed`
-2) against a 96 s CPU baseline: 38 s of GPU-able work becomes 5 s, and the
other 58 s (`fetch` 16 + `stt` 5 + `chunk` + `keyframe` 23 + `ocr` 14) is
untouched.

### Against the smoke's CPU baseline

e2e-smoke §2/§7 measured the same video, same corpus, same box, on an idle
machine: `keyframe` **28 s / 31 s**. This run, on a box at `loadavg` 5.8:
**23–24 s**. That is the PyAV `threading_mode="AUTO"` fix (`bf8828f`) showing up
end to end. The commit measured the *decode* at 1.45x with bit-identical cut
lists; the stage is decode plus extraction, so predicted ≈22 s if detection was
~20 s of the smoke's 28 s. Observed 23 s, under worse conditions. The other
stages match the smoke within their noise (`ocr` 13–14 s vs 13 s,
`frame_embed` 36 s vs 27 s — the frame stage is the one this box's contention
hit hardest).

---

## 4. Transcript quality: whisperX vs the auto-caption track

Same 149 s of audio. Auto-captions produce **74 cues / 492 words**, whisperX
**31 cues / 486 words** — the same speech, cut into 2.0 s rolling caption lines
versus 4.8 s sentences.

**Word timestamps: both. 74/74 and 31/31 cues carry `words_json`.** The
captions' come from YouTube's `json3` track, whisperX's from wav2vec2 forced
alignment (`STT_ALIGN=1`, the default). So the deep-link claim in
`mcp/README.md` — per-second `?t=` even with no GPU — holds in both
configurations, and word timings are *not* a reason to run whisperX.

The reason to run whisperX is that the auto-caption track gets the technical
vocabulary wrong, and this is a video about a programming language. Caption
quotes below are joined across the 2 s cue lines they are split over — the
error is in the words, not in the joining:

| t | auto-captions | whisperX | why it matters |
|---|---|---|---|
| 58 s | "objects with an unknown size at compile time are stored in the **keep** memory" | "…stored in the **heap** memory" | a wrong word, not a missing one. `search q="heap memory"` finds this moment in one corpus and not the other |
| 93 s | "to get started install **rest** then run cargo new" | "To get started, install **Rust**, then run cargo new" | the subject of the video, misheard |
| 117 s | "use a macro like **print line**" | "use a macro like **println**" | the API name is destroyed — two common words instead of one identifier |
| 16 s | "a side project of **gr on** in 2007" | "a side project of **Gradon Hoare** in 2007" | whisperX is also wrong (it is Graydon Hoare) but wrong in a way that still retrieves |
| 29 s | "its fans being known as **restation**" | "…known as **Rustations**" | |
| 81 s | "the **r borrow Checker** will validate" | "the **Rust Borrow Checker** will validate" | |
| 95 s | "in the **main. RS** file" | "in the **main.rs** file" | the FTS tokenizer keeps `main.rs` as one token (`tokenchars '_-./'`); `main. RS` is two useless ones |
| 14 s | "targeting **web assembly**" | "targeting **WebAssembly**" | |
| 123 s | "modules to handle **IO**" | "modules to handle **I.O.**" | the one place whisperX is worse |

Plus punctuation and casing throughout: whisperX emits sentences, the caption
track emits a lowercase stream. On a screencast corpus that is not cosmetic —
the chunk text is what gets embedded, and "install rest" and "install Rust"
embed to different places.

**What the captions are better at**: granularity. 2.0 s cues versus 4.8 s
sentences means `get-segment-context` prints a tighter window and a cue-level
deep link lands closer. Both carry word timings, so this is a display
difference, not a precision one.

Chunking was identical (5 chunks, 45 s windows) in all three configurations, so
the retrieval difference is entirely the words.

---

## 5. Projection: 1 h, 10 h, 100 h of video

Built from per-unit rates, not by multiplying a 149 s job. Assumptions, all of
them arguable and all of them stated:

* **Keyframe density is this video's**: 44 keyframes / 149 s = 17.7 per minute
  = **1063 per hour**. This is a fast-cut screencast and it is the high end; a
  lecture with a static camera produces a fraction of that, and `ocr` and
  `frame_embed` scale with *frames*, not seconds. Sensitivity below.
* Chunks: one per 30 s (45 s window, 15 s overlap) = 120/h.
* `keyframe` and `fetch` scale with duration; `ocr` and `frame_embed` with
  keyframe count; `text_embed` with chunks; whisperX with audio seconds at the
  29.8x measured here; caption fetch is ~5 s per *video* regardless of length.
* Device-independent stages take their least-contended observed value
  (`keyframe` 23 s/149 s, `ocr` 0.311 s/frame, `fetch` 16 s video-only /
  34 s audio+video per 149 s). Using one column's value for all three is the
  point: those stages *are* the same work.
* Jobs run one at a time, which is what the pipeline does.

| per hour of video | cpu-autocaps | gpu-autocaps | gpu-whisperx |
|---|---|---|---|
| `fetch` | 387 s | 387 s | 821 s |
| `stt` | 5 s | 5 s | 128 s |
| `text_embed` | 61 s | ~3 s | ~3 s |
| `keyframe` | 556 s | 556 s | 556 s |
| `ocr` | 331 s | 331 s | 331 s |
| `frame_embed` | 803 s | 94 s | 94 s |
| **total** | **2144 s (35.7 min)** | **1377 s (23.0 min)** | **1934 s (32.2 min)** |
| pipeline only, no download | 29.3 min | 16.5 min | 18.6 min |

| corpus | cpu-autocaps | gpu-autocaps | gpu-whisperx |
|---|---|---|---|
| 1 h | 36 min | **23 min** | 32 min |
| 10 h | 6.0 h | **3.8 h** | 5.4 h |
| 100 h | 59.6 h (2.5 days) | **38.3 h (1.6 days)** | 53.7 h (2.2 days) |
| 100 h, download excluded | 48.8 h | 27.5 h | 30.9 h |
| **of which the GPU is busy** | 0 | **2.7 h (7%)** | **6.3 h (12%)** |

Two things fall out of that table.

**The download is the biggest single line in the whisperX column** (821 s/h,
because that configuration needs the audio file as well as the video) and the
second biggest in the others. It is network and yt-dlp politeness, not
hardware, and it is the least trustworthy row here: this box was fighting a
sibling agent for YouTube's patience and collected two `403`s during the runs.

**With the download excluded, whisperX costs 2 minutes per hour of video over
auto-captions** (18.6 vs 16.5 min/h). On a GPU, real transcription is nearly
free relative to keyframes and OCR. The auto-caption shortcut is not buying
transcription time — it is buying the audio download.

Sensitivity, since keyframe density is the assumption doing the most work: at
300 keyframes/hour (a talking head rather than a screencast), `ocr` +
`frame_embed` fall from 1134 s to 320 s on CPU and from 425 s to 120 s on GPU,
and the 100 h figures become 37 h (CPU) and 30 h (GPU). The GPU's advantage is
proportional to how much *screen* there is to look at.

---

## 6. Findings

### 6.1 The indexing `text_embed` call is bounded by the query timeout, and nothing can raise it ⚠ blocker on CPU

**Severity: high.** This failed a run before it was worked around, and on a CPU
install it fails the job every time the model is cold.

* **Where**: `mcp/src/vidtheque_mcp/pipeline/worker_client.py:91`.
  `HTTPWorkerClient` overrides `transcribe`, `ocr`, `embed_images` and
  `embed_frame_query` — each of which goes through `_send` and therefore honours
  `op_timeout_s`, the retry loop and the `Retry-After` contract. It does **not**
  override `embed`. So `_stage_text_embed`'s `self.worker.embed(...)`
  (`runner.py:520`) — the indexing path — lands on `embeddings.py:90`,
  `HTTPEmbeddingClient.embed`, which uses
  `self._timeout`: the **query** budget, defaulting to 20.0 s, never passed by
  `pipeline/__init__.py:51`'s `worker_client()` and not readable from any
  environment variable. `VIDTHEQUE_WORKER_TIMEOUT_S` does not reach it.
  The docstring one line above says *"the shared client: query-time embedding
  budget, indexing-time everything else"* — the intent is right there; `embed`
  is on the wrong side of it.
* **What happens**: a cold Qwen3-Embedding load is 7.8–19.2 s on this box
  (14.6 s on the first attempt of this session). Load plus the first batch
  exceeds 20 s, `httpx2.ReadTimeout` becomes `EmbeddingUnavailable`, and because
  this path is not `_send` there is **no retry and no 503 handling** — the
  exception escapes to the job runner, which retries the whole *item* three
  times, each attempt re-running `fetch` and dying at the same place:

  ```
  +  30s  stt        transcript from yt_auto (74 cues)
  +  50s  warn       retrying after E_INTERNAL:
  +  75s  warn       retrying after E_INTERNAL:
  +  98s  error      E_INTERNAL:
  job_state: failed after 100.2s
  ```
* **Why the smoke did not see it**: e2e-smoke §2 records a 12.1 s cold Qwen3
  load and a `text_embed` stage of 10 s. It passed by about four seconds.
* **The GPU is not immune**: the same path also loses the 503 +`Retry-After`
  contract that `mcp/`'s worker client was written against, so an
  `InsufficientVRAM` refusal on a busy card fails `text_embed` outright instead
  of retrying — the one path where admission control's backpressure is
  guaranteed to be ignored.
* **Suggested fix**: pass the indexing budget where it belongs. Either give
  `HTTPWorkerClient` an `embed` override that goes through `_send` (which gets
  retries and `Retry-After` for free, and is what the other four do), or have
  `worker_client()` construct the client with `timeout_s=resolved.worker_timeout_s`
  and keep a separate short-timeout client for query-time embedding. The first
  is the honest one: the difference between the two call sites is indexing vs
  querying, not one number.
* **Workaround used here**: `bench/pipeline_bench.py` sends one direct
  `/v1/embeddings` request before the job and runs with `EMBED_RESIDENT=1`, so
  the cold load happens outside the 20 s window. It is logged and timed, not
  hidden.

### 6.2 `captions_only` skips the audio download entirely

Not a bug — a saving worth knowing, and it is why the `fetch` rows differ by
configuration. `runner.py:277`: `need_audio = run.wants_transcript and
self.settings.wants_whisperx`, and `wants_whisperx` is False for
`captions_only`. So a captions-only install never downloads the opus at all:
`fetch` 16 s versus 39 s here, and `audio_dir` 0 bytes versus 900 KB. Over 100 h
of video that is the difference between the two `fetch` rows in §5 — about
12 hours of wall clock, all of it network.

### 6.3 A stage that fails and retries overwrites its own timings

`store.stage_running` sets `started_at = unixepoch()` on every entry, and the
job runner retries a failed item from the top. After the §6.1 failure the
`video_stages` table read `fetch started_at 1786230235`, *after* `stt`'s
`finished_at 1786230187` — the third attempt's fetch had overwritten the
first's. The row is not wrong (it describes the last attempt) but a reader
reconstructing a timeline from `video_stages` alone will get an impossible
one. `job_events` is the honest record when a job retried; it keeps every
attempt. Worth a sentence in `index-schema.md` next to `video_stages`.

### 6.4 `scripts/dev_stack.sh stop` killed every worker and mcp on the box — fixed the same night

**Severity: low, but it destroyed a measurement.** `stop_one` ended with
`pkill -f "python -m vidtheque_mcp"` — a pattern match against every process on
the machine, with no regard for data dir, port or pidfile. A sibling agent
restarting their stack took this run's mcp and worker down mid-job; the client
saw `httpx2.ConnectError: All connection attempts failed` and the mcp log a
clean uvicorn shutdown it never asked for. The belt-and-braces `pkill` was added
because a stop that only killed the wrapper left orphans holding the ports
(`728feea`), which is a real problem.

Fixed independently in `df345ad` while this was being written: the pattern is
now `pkill -f "VIDTHEQUE_PORT=$port .*$module"`, i.e. instance-scoped. Recorded
here anyway because the symptom — a bench dying on `ConnectError` with a clean
shutdown in the log — is worth recognising, and because it is the failure mode
any box-wide pattern kill reintroduces.

### 6.5 yt-dlp `403` on repeat downloads of the same video

Two of seven runs hit `ERROR: unable to download video data: HTTP Error 403:
Forbidden` on the media download and recovered on the pipeline's own item
retry, at a cost of ~25 s. Both happened while a second agent on the same IP was
downloading through yt-dlp. Nothing in the pipeline is at fault — the retry did
its job — but it is worth recording that the `fetch` row is the one number here
that a benchmark cannot control, and that `E_RATE_LIMIT` vs `E_INTERNAL`
classification is worth a look: this arrived as `E_INTERNAL`, so it burned the
generic retry budget rather than the rate-limit one.

### 6.6 1 s stage resolution is now most of a GPU stage

`video_stages.started_at/finished_at` are `unixepoch()`. On CPU that is 1% of a
36 s stage. On GPU, `text_embed` reads `0 s` and `frame_embed` reads `5 s` —
the quantisation is 20% of the number. The `/status` spans in §2 exist because
of this. If the per-stage figures are ever surfaced to users (a "why is this
slow" answer), they want sub-second storage; `REAL` epoch seconds would be a
one-migration change and `job_events` already carries the ordering.

### 6.7 Smaller notes

* `EMBED_RESIDENT=1` + `IDLE_UNLOAD_SECONDS=20` behaved exactly as
  gpu-validation §5.3 describes: the text embedder stayed loaded for the whole
  job while OCR and SigLIP 2 were reaped around it. On the CPU configuration
  that means Qwen3 (fp32) and SigLIP 2 are resident together in RAM; on a
  16 GB box it held, but the smoke's warning stands for smaller ones.
* Peak VRAM over baseline for `gpu-whisperx` was **7863 MB** (run 1) and
  7189 MB (run 2), against gpu-validation's measured 7941 MB peak for whisperX
  alone — two independent measurements of the number
  `WhisperXBackend.default_vram_mb = 3200` is supposed to describe. §5.1 of
  that document is unchanged: the estimate is ~2.5x low.
* Row counts were identical in all six successful runs: 5 chapters, 5 chunks,
  44 keyframes (0 near-duplicates, 42 with OCR), 419 OCR lines. Only the cue
  count moved with the STT policy (74 auto-caption cues, 31 whisperX cues).
  Nothing about the GPU changes what gets indexed.
* Data dir on disk: 6.0 MB captions-only, 6.9 MB with the kept opus.

---

## 7. What the GPU buys you

On a 2:29 screencast, indexing takes 96 s of pipeline on ten CPU cores and 71 s
with an RTX 3090 in the box — and the entire difference is one stage. Frame
embedding falls from 36 s to 5 s (0.755 s → 0.088 s per keyframe, 8.6x), text
embedding falls from 2 s to nothing, and every other stage is exactly as fast as
it was, because fetching is network, chunking is SQLite, keyframe extraction is
one full CPU decode of the video, and OCR is a CPU backend on purpose — which
makes OCR, at 14 s, the largest *inference* stage on the GPU machine, three
times the frame embedder it is queued behind. Adding real transcription costs
8 s of model load and 5 s of whisperX at 30x realtime, plus an audio download
the captions-only path skips entirely; scaled up, that is 2 extra minutes per
hour of video for a transcript that says "heap memory", "install Rust" and
"println" where the auto-caption track says "keep memory", "install rest" and
"print line". Extrapolated at this video's keyframe density, 100 hours of
corpus is 60 hours of CPU-only indexing, 38 hours with the GPU doing the
embedding, and 54 hours with the GPU also transcribing — during which the card
is busy for **2.7 and 6.3 hours respectively**, 7% and 12% of the wall clock.
That is the number to design around: the GPU is not what makes this pipeline
fast, it is what stops one stage from being absurd, and it spends the rest of
the run idle enough that sharing it with a co-tenant — which is exactly what
`GPU_ACQUIRE_CMD` and the lifecycle manager's idle unload are for — is not a
compromise but the intended operating point.

---

## 8. Harness notes for the next run

* `bench/pipeline_bench.py --list`, then any subset of `cpu-autocaps`,
  `gpu-autocaps`, `gpu-whisperx`. `--out` writes after every configuration, so a
  round that dies on the third still leaves the first two.
* The configurations live in the file, not in `bench/scenarios/*.toml`: a
  variant here is a *pair* of environments (worker and mcp) plus an STT policy,
  and the case is a URL, so nothing about it fits `run.py`'s variants-x-cases
  shape. `COMMON_WORKER` / `COMMON_MCP` are where "held equal" is enforced —
  changing one of those invalidates comparison with this document.
* It refuses to start if the shell exports any `VIDTHEQUE_*`, `DEVICE`,
  `WORKER_*`… : `harness.Worker` merges its environment *over* `os.environ`, so
  a stripped copy cannot un-set an inherited `DEVICE=cuda`, and a bench that
  silently measured the wrong device is worse than one that did not run.
* Ports 8340/8341 by default, well clear of the live stacks. Data dirs under
  `~/.cache/vidtheque-pipeline-bench/<config>`, wiped at the start of each
  configuration.
* **Record `nvidia-smi` and `loadavg` before every configuration, and do not
  compare CPU stage rows across rounds without them.** `ocr` moved 4x on
  identical work between two rounds fifteen minutes apart. The device-dependent
  rows (§3) survived that; nothing else did.
* Three downloads of the same video in fifteen minutes is enough to earn a
  `403`. If the `fetch` row matters, cache the media and point the pipeline at
  it instead of re-fetching.
