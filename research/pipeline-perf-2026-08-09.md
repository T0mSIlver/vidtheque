# Where the pipeline's time goes, and whether the embedder should be bigger (2026-08-09)

Two questions off the new dashboard's per-stage breakdown, which Tom read as
"scene detection dominates by a long shot, and the 0.6B embedder is almost
instant — there's headroom for a bigger one."

**Both readings are correct. Only one of them implies an action.**

Scene detection is 59% of all pipeline wall time and the next stage is six times
cheaper. The embedder is 0.3%, which does mean indexing throughput would not
notice a model ten times slower — but indexing throughput was never what
constrains that choice, and §7 is about the three things that do.

Method: the corpus is the live database at `/home/dev/vidtheque-data`, read-only,
**75 videos / 26.1 hours** of indexed video, mid-way through a 64-video batch. No
GPU process, model download or benchmark was run for this document; every GPU
number here is cited from `research/gpu-validation-2026-08-08.md`, which measured
them on the 3090.

Prior art this builds on directly: `research/keyframe-decode-bench-2026-08-08.md`
(the two-pass decode profile and the dual-stream rejection). That document
answered "is the stage decoding pixels nobody looks at". This one answers "how
much of the corpus's time is that, and what is left to take".

---

## 1. The stage table

`video_stages.finished_at - started_at`, summed over all 75 videos, against
1566.7 minutes of video. All 75 are `index_state = 'ready'`.

| stage | model_key | total | share | per video | **s / video-minute** |
|---|---|---:|---:|---:|---:|
| **keyframe** | `scenedetect-screencast-w1280` | **13,837 s** | **58.5%** | 184.5 s | **8.83** |
| fetch | `yt-dlp-2026.07.04` | 6,316 s | 26.7% | 84.2 s | 4.03 |
| stt | `large-v3` (74) / `youtube-asr-en-orig` (1) | 1,850 s | 7.8% | 24.7 s | 1.18 |
| ocr | `rapidocr-default` | 1,355 s | 5.7% | 18.1 s | 0.86 |
| frame_embed | `google/siglip2-so400m-patch16-naflex` | 217 s | 0.9% | 2.9 s | 0.14 |
| text_embed | `Qwen/Qwen3-Embedding-0.6B` | 77 s | **0.3%** | 1.0 s | 0.05 |
| chunk | `chunk-45-15` | 0 s | 0.0% | 0.0 s | 0.00 |
| **total** | | **23,652 s** | | 315.4 s | 15.1 |

Six and a half hours of compute for twenty-six hours of video — the pipeline
runs at about **4x realtime overall**, and keyframes alone at 6.8x realtime.

Three readings:

- **Keyframes are 178x the text embedder** and 6.4x the next-largest *compute*
  stage. Tom's reading is right, and understated.
- **`fetch` is second, and most of it is deliberate.** The 30–60 s
  between-videos gap is *not* in this number — `_between_videos()` runs before
  `_stage_running(run, "fetch")` (`pipeline/runner.py:262`, `:359`), so the
  clock starts after it. But yt-dlp's own politeness sleeps *are* inside the
  window: `sleep_interval` 10–20 s on each of the two downloads (audio and
  media), plus `sleep_subtitles` 5 s and `sleep_requests` 0.75 s per request
  (`pipeline/settings.py`). That is ~25–45 s of the 84 s average spent asleep on
  purpose. Real transfer is roughly half this row and it is network-bound, not
  ours to optimise. **Treat `fetch` as ~2 s/video-minute of actual work.**
- **The GPU stages are already free.** stt at 1.18 and frame_embed at 0.14
  s/video-minute are what `gpu-validation-2026-08-08.md` §3 predicted (whisperX
  at ~50x realtime, SigLIP 2 at 103 frames/s). Nothing on the GPU is worth
  attacking. The dominant stage is pure CPU and never touches the card.

Keyframe cost per video-minute is tight across the corpus — median 7.87,
p25 7.54, p75 9.34, min 3.72, max 15.63 — so this is a property of the stage,
not of a few outliers.

## 2. Splitting the keyframe stage without a profiler

The stage is two passes (`pipeline/keyframes.py:227-260`): pass 1 decodes the
whole video for shot detection, pass 2 seeks `candidates_per_shot` (9) times
into each shot and keeps the sharpest frame. Pass 1 should scale with *video
minutes*, pass 2 with *shot count*. Both are in the database, so the split can be
fitted rather than instrumented:

```
keyframe_seconds ~ a x video_minutes + b x shots
```

Least squares over all 75 videos:

| fit | a (s / video-minute) | b (s / shot) | R² | detection share | extraction share |
|---|---:|---:|---:|---:|---:|
| with intercept (−18.3 s) | 6.67 | 1.037 | 0.785 | 76% | 34% |
| **through the origin** | **5.72** | **1.095** | — | **65%** | **36%** |

So **pass 1 is roughly two-thirds of the stage and pass 2 roughly one-third.**
That brackets the two hand-timed videos in
`keyframe-decode-bench-2026-08-08.md` §2 (56/44 and 81/19) and is the corpus-wide
version of them.

The interesting number is **b ÷ 9 = 115 ms per candidate seek**, and it has an
exact mechanical explanation. `_sharpest_in` (`pipeline/keyframes.py:346-373`)
does `capture.set(CAP_PROP_POS_MSEC, t)` then `read()`. OpenCV's FFmpeg backend
implements that as a seek to the preceding H.264 keyframe and a decode forward
to the target — single-threaded, at full resolution. The prior bench measured
this box's single-threaded 1080p decode at **406 frames/s** (§4); a 2-second GOP
at 50 fps means ~50 frames of decode-forward per seek on average, and
50 ÷ 406 = **123 ms**. Against a fitted 115 ms, that is the whole of pass 2
accounted for.

**Pass 2 is not doing 9 units of work per shot. It is doing ~450 decoded frames
per shot and throwing away 441 of them.**

## 3. The option space, ranked by what it does to the output

This is the axis that matters here, and it is why the prior bench rejected a
measured 4x. `keyframes.shot_id`, `shot_start_s` and `shot_end_s` are stored
columns; `t_s` is the deep link, which is the product. A detector swap that moves
boundaries re-keys every keyframe row, invalidates the dedup clustering, and
means a full corpus reindex — 13,837 s of the very stage being optimised, plus
re-OCR (1,355 s) and re-embed of every frame (217 s), *and* a re-download of
every source video, because `keep_source=audio` deleted the mp4s
(`runner.py:1070`).

| option | what it attacks | expected | **output** |
|---|---|---|---|
| **pass-2 thread pool** | 34% of stage | ~1.3x stage | **identical** |
| `CAP_PROP_N_THREADS` on pass 2 | inside each seek | unmeasured | **identical** (asserted) |
| `candidates_per_shot` 9 → 5 | 34% of stage | ~1.18x stage | frame *within* a shot may differ |
| PyAV fused `reformat` in pass 1 | 65% of stage | ~2.8x stage | **cuts move** |
| ffmpeg `scdet` single pass | 65% of stage | ~4x pass 1 | **cuts move**, and worse |
| TransNetV2 on the GPU | 65% of stage | decode-bound | **cuts move** |
| NVDEC hardware decode | ~11% of stage | ≤1.12x stage | identical, if it ran at all |

### 3.1 What is free: pass 2 parallelises

Shots are independent and `_sharpest_in` seeks *absolutely* — the frame it
returns is a function of the file and the timestamp, not of where the capture
was before. So the *search* half of pass 2 can run on N threads with one
`VideoCapture` each while the *commit* half (the `seen_ms` collision guard, the
ordinal, the JPEG, the phash) stays on one thread walking shots in order. Every
ordering-dependent thing — ordinals, `<ord>-<t_ms>.jpg` filenames, first-wins
dedup, `UNIQUE(video_id, t_s)` — depends only on the commit order, which does
not change.

Two caveats found in the research and handled in the implementation:

- **OpenCV serialises `open()` behind a global mutex** and documents that
  `isOpened()` may return false when several threads open concurrently
  ([`cap_ffmpeg_impl.hpp`](https://github.com/opencv/opencv/blob/4.x/modules/videoio/src/cap_ffmpeg_impl.hpp)).
  Opening lazily inside the workers would have produced a rare, load-dependent
  "cannot open" several minutes into a job. All captures are opened serially,
  before any thread exists.
- **One `VideoCapture` per thread, never shared.** OpenCV states plainly that
  `VideoCapture` is not a thread-safe class
  ([docs](https://docs.opencv.org/4.13.0/d0/db6/tutorial_orbbec_astra_openni.html)).
  That is about sharing one object; independent instances on the same file each
  own their own `AVFormatContext`. `read()` releases the GIL, so threads suffice
  and processes are not needed.

Arithmetic at 4 workers on a 10-core box, assuming imperfect (3x) scaling:
stage → 0.65 + 0.36/3 = **0.77, i.e. ~1.30x**. Over this corpus that is **~59
minutes saved**, and the whole pipeline goes 23,652 s → ~20,100 s (**1.18x**).

`CAP_PROP_N_THREADS` is the same lever one level down — frame-threaded decode
*inside* each seek, the pass-2 analogue of the `threading_mode="AUTO"` that was
worth 1.45x on pass 1 (§4 of the prior bench). It multiplies with the pool rather
than adding to it, so raise one or the other. Its size is unmeasured here.

### 3.2 What is nearly free: fewer candidates

`VIDTHEQUE_SHOT_CANDIDATES=9` already exists. Dropping to 5 cuts pass 2 by 44%
(stage ×0.84) and *does not move a single shot boundary* — it only risks picking
a slightly less sharp frame from within the same shot. Combined with 4 workers:
stage ×0.71, **1.41x**.

Worth noting what the 9 buys. `sharpness()` is variance-of-Laplacian compared
only within a shot, and the file's own docstring explains why that is the right
metric: across a slide build the sharpest frame is the one with the most text on
screen, which is the frame worth OCR-ing. Nine samples over the middle 76% of a
shot is a fairly dense search for that maximum; five is still 5. **Recommend
testing 5, not adopting it blind** — it is the one knob here whose cost is a
quality question rather than an arithmetic one.

### 3.3 The big one, and why it needs Tom

The prior bench's §5 pointed at it and the new research confirms the mechanism
precisely. PySceneDetect's PyAV backend calls
`frame.to_ndarray(format="bgr24")` at **full 1920x1080** (`backends/pyav.py:357`
in the installed 0.7.1) — a swscale YUV→BGR conversion over 2,073,600 pixels
producing a 6.2 MB array — and *then* `SceneManager._decode_thread` runs
`cv2.resize(...)` down to 256x144 (`scene_manager.py:663-668`), reading all
6.2 MB again. The detector only ever sees 36,864 pixels.

PyAV's [`VideoFrame.reformat(width=, height=, format=)`](https://pyav.basswood.io/docs/stable/api/video.html)
fuses scale and convert into one libswscale call, so the colour conversion
happens at the *output* resolution: **56x fewer conversion operations and a
110 KB buffer instead of 6.2 MB**. (PyAV's docs add that a persistent
reformatter object should be reused — calling `reformat()` per frame
reconfigures the internal context.) That directly targets the ~75 s of
per-frame Python/OpenCV work that the prior bench isolated inside a 94 s
detection pass, and would take pass 1 close to its 19 s bare-decode floor.
Stage 8.83 → roughly 3.1 s/video-minute, **~2.8x**.

**It moves the cuts.** swscale's `SWS_BILINEAR` and OpenCV's `INTER_LINEAR` are
"bilinear" in name only — different fixed-point coefficients, different chroma
siting, different rounding — and the `screencast` preset weights `delta_edges`
at 1.0, a Canny map being exactly what resampling differences perturb. This is
the same substitution the 360p experiment made (§3.2 of the prior bench: it is
the *resolution/filtering*, not the frame rate) and it broke equivalence on one
of two videos there.

So: **highest-value item on the list, and not a flag.** It needs a reference clip
set, an agreed equivalence bar, and a one-time full reindex — including
re-downloading every source video, since `keep_source=audio` has already deleted
them.

### 3.4 The ones to say no to

**ffmpeg `scdet` / `select='gt(scene,…)'`.** Both use the same `ff_scene_sad`
kernel over the luma plane
([`vf_scdet.c`](https://github.com/FFmpeg/FFmpeg/blob/master/libavfilter/vf_scdet.c),
[`f_select.c`](https://github.com/FFmpeg/FFmpeg/blob/master/libavfilter/f_select.c)) —
a single global luma SAD with **no structural or edge term at all**. The
`screencast` preset exists precisely because luma-only under-detects on
near-greyscale slide content; `scdet` is that failure mode with no Canny channel
to rescue it. Fast (a pure-C pass at near decode speed) and wrong for this
corpus. Reject.

**TransNetV2 on the GPU.** Genuinely attractive on paper: MIT-licensed, PyTorch
weights on PyPI, ~4.2M params, operates at **48x27** — a *smaller* input than
the 256x144 we already build — and detects dissolves, which the current detector
cannot. F1 96.2 on BBC Planet Earth, 77.9 on ClipShots
([repo](https://github.com/soCzech/TransNetV2)). Reject anyway, on three grounds
in increasing order of decisiveness:

1. Quality we do not need. Keyframes feed OCR and embeddings, not a film editor.
   The pipeline **deliberately over-detects** and lets the phash pass clean up
   (33% of this corpus's 4,586 keyframes are already marked `dup_of`). Missing a
   dissolve costs approximately nothing here.
2. It re-keys every `shot_id` in the schema, for the reindex cost in §3.
3. **It does not fit the architecture.** The worker is a stateless inference API
   and all state lives in mcp/. Shot detection needs the *whole video*, so a
   worker-side detector means either shipping a 150 MB mp4 over the HTTP seam per
   video, or letting the worker read mcp's media directory — which breaks the
   invariant outright. And the decode would still happen mcp-side, which is the
   actual cost: the model is not the bottleneck, the frames are.

**NVDEC.** Already rejected in `keyframe-decode-bench-2026-08-08.md` §5 —
`h264_cuvid` cannot even load on this box (`Cannot load libnvcuvid.so.1`), and a
complete threaded CPU decode is only ~19 s inside a 94 s pass, so an infinitely
fast decoder buys ~11% of the stage. The new research sharpens one point worth
recording: the win survives *only* with
`-hwaccel_output_format cuda -vf scale_cuda=256:144,hwdownload`, downscaling
before the PCIe crossing (110 KB/frame instead of 3 MB/frame); a plain
`hwdownload` at 1080p throws it away. torchcodec's own docs also note CUDA decode
is **not bit-exact** against CPU decode, which would put it in the "moves the
cuts" row anyway. Still no, and now for a fourth reason.

Also noted and not pursued: **decord is effectively abandoned** (last release
2021), and **torchcodec is explicitly weakest at sequential decode from the
start of a file** — which is exactly pass 1's access pattern.

## 4. Recommended plan

| # | action | speedup (stage) | output | status |
|---|---|---|---|---|
| 1 | `VIDTHEQUE_KEYFRAME_EXTRACT_WORKERS=4` | ~1.30x | identical | **implemented**, default 1 |
| 2 | `VIDTHEQUE_KEYFRAME_DECODE_THREADS` | unmeasured | identical | **implemented**, default 0 |
| 3 | `VIDTHEQUE_SHOT_CANDIDATES=5` | ~1.18x | frame-within-shot | knob exists; test first |
| 4 | fused PyAV `reformat` in pass 1 | ~2.8x | **cuts move** | needs Tom + reindex |
| 5 | `scdet` / TransNetV2 / NVDEC | — | — | **rejected**, §3.4 |

**Top recommendation: ship 1, benchmark it on the box, then have the
conversation about 4.** Items 1–3 together are ~1.4x on the stage and ~1.2x on
the whole pipeline for zero corpus risk. Item 4 is worth more than all of them
combined and cannot be smuggled in behind a flag.

### 4.1 What was implemented

`extract_from_shots` now takes `workers` and `decode_threads`. At `workers=1`
(the default) the code path is byte-for-byte today's — no `ThreadPoolExecutor` is
even constructed, which is asserted. Above 1, the search half runs on a pool and
the commit half does not.

**Neither knob is in `_keyframe_model_key()`** (`runner.py:707`), on purpose:
both promise identical output, so putting them in the stage key would reindex the
corpus for a thread count. A test asserts they stay out of it.

Six new tests (`mcp/tests/test_pipeline_keyframes.py`), all CPU, all on the
existing ffmpeg-synthesized 8-second fixture:

- pooled vs serial produce identical drafts **and identical bytes on disk**,
  with `max_shot_seconds=1.0` so the shot list spans several pool chunks rather
  than fitting in one;
- ordinals stay dense and in time order, timestamps stay unique;
- `workers=1` never constructs a pool;
- `decode_threads=4` does not shift a timestamp (frame threading must not
  perturb `CAP_PROP_POS_MSEC` — a drifting one would silently produce wrong deep
  links, which is the product);
- `extract_workers=0` / `decode_threads=-1` raise `ConfigError` at boot rather
  than inside a pool minutes into a job;
- the model key ignores both.

`uv run pytest -q`: **780 passed** (774 before).

### 4.2 Validating on the box

The fixture proves the identity claim on an 8-second 320x240 clip. It does not
prove it on a 1080p50 talk with 2-second GOPs, which is where seeking actually
behaves interestingly, so:

```bash
uv run --no-sync python bench/keyframe_decode.py \
    --pair full=/scratch/ID-1080p.mp4,detect=/scratch/ID-1080p.mp4 --no-dual \
    --extract-workers 1,2,4,8 --decode-threads 0,2 \
    --out bench/results/raw/keyframe-workers-2026-08-09.json
```

Detection runs once; every configuration extracts from the same shot list and is
diffed against the 1-worker answer with the existing `pair_keyframes` comparison.
**`all_identical: true` is the line that decides whether this may be raised in
production.** If any configuration differs, the pool is wrong and the default
stays 1.

## 5. Two things found on the way, for Tom

**`VIDTHEQUE_MAX_SHOT_SECONDS` is not a maximum.** `subdivide`
(`pipeline/keyframes.py:170-193`) computes `pieces = max(1, int(span // max))`,
which floors — so a 49.9-second scene stays one shot and only 50.0 splits.
Verified directly:

| span | shots | longest |
|---|---|---|
| 26.0 s | 1 | 26.0 s |
| 49.9 s | 1 | **49.9 s** |
| 50.0 s | 2 | 25.0 s |

The corpus shows it: shot spans have **median 25.3 s and max 50.0 s** against a
configured 25. So an unedited 49-second stretch of screencast yields **one**
keyframe where the setting promises two. `ceil` is the one-character fix, but it
*increases* shot count — more keyframes, more OCR, more pass-2 cost — so it is a
coverage decision, not a perf one. Flagging, not fixing.

**`config['text_embed.query_prefix']` reads `'query: '`** in the live database.
That is the E5/BGE convention, not Qwen3-Embedding's (`Instruct: {task}\nQuery:
{q}`). It is not a live bug — `index-schema.md:79,102` documents the value as a
*record of what indexing assumed*, applied by the worker via `input_type="query"`
and never prepended by mcp — and `EMBED_QUERY_PROMPT` is empty, so the worker
uses the checkpoint's own. But the record and the behaviour describe different
prefixes, which is exactly the silent-drift class that column exists to prevent.

---

# 6. The embedder: is 0.6B leaving quality on the table?

## 6.1 The real baseline

- `EMBED_MODEL=Qwen/Qwen3-Embedding-0.6B`, 1024 dims, `EMBED_BACKEND=qwen3-embedding`
  (`deploy/.env.example:44`), recorded in `config` as `text_embed.model` /
  `text_embed.dim=1024` / `normalized=1`.
- Frames are a **separate model and a separate vector space**:
  `google/siglip2-so400m-patch16-naflex`, 1152 dims. A text-embedder swap does
  not touch a single frame vector.
- Measured on the 3090 (`gpu-validation-2026-08-08.md` §2–§4): **1,975 MB** at
  batch 64 x 575 chars, **1,483 MB** resident, **200 embeddings/s**, **15 ms for
  a single query**, 6.8 s cold load / 3.7 s reload.

Tom's premise checks out and then some: text_embed is 77 s across the entire
corpus. A model **20x slower** would add 1,463 s to a 23,652 s pipeline — 6% —
while the stage above it wastes 3,500 s on redundant decoding. **Indexing
throughput is not a reason to stay at 0.6B.** It is also not a reason to move,
and the three constraints that actually bind are VRAM against the llama.cpp
lease, query latency, and the reload cliff.

## 6.2 What the family actually offers

Qwen3-Embedding, Apache 2.0, three sizes. There is **no Qwen3.5-Embedding and no
v2** — Qwen3.5 shipped as LLMs only, so this is still the current generation.

| | params | dims | MRL range | max seq |
|---|---:|---:|---|---:|
| **0.6B** (current) | 0.6B | 1024 | 32–1024 | 32K |
| **4B** | 4B | 2560 | 32–2560 | 32K |
| **8B** | 8B | 4096 | 32–4096 | 32K |

Retrieval subscores from the model cards
([0.6B](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B),
[4B](https://huggingface.co/Qwen/Qwen3-Embedding-4B),
[8B](https://huggingface.co/Qwen/Qwen3-Embedding-8B)):

| | MTEB English v2 retrieval | MMTEB retrieval |
|---|---:|---:|
| 0.6B | **61.83** | 64.64 |
| 4B | **68.46** (+6.63) | 69.60 (+4.96) |
| 8B | **69.44** (+0.98) | 70.88 (+1.28) |

**The shape of that table is the whole answer: 0.6B → 4B is the jump, 4B → 8B is
noise.** And English retrieval — where 0.6B is weakest in relative terms — is
precisely what searching English conference-talk transcripts is.

Weights-only memory, anchored on published artifact sizes
([Ollama tags](https://ollama.com/library/qwen3-embedding),
[0.6B-GGUF](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B-GGUF)):

| | bf16 | int8 / Q8_0 | Q4_K_M |
|---|---:|---:|---:|
| 0.6B | ~1.2 GB | 639 MB | ~0.4 GB |
| 4B | ~8 GB | ~4 GB | 2.5 GB |
| 8B | ~15–16 GB | ~8 GB | 4.7 GB |

Two alternatives checked and not recommended:

- **jina-embeddings-v5-text-small** (677M, 1024 dims, built on Qwen3-0.6B-Base
  and distilled from Qwen3-Embedding-4B) genuinely beats 0.6B at the same size
  and the same dimension — MTEB English v2 71.7 vs 70.70 — which would be a free
  swap with no schema change. It is **CC BY-NC 4.0**. This repo is MIT and ships
  a public `deploy/.env.example`; a non-commercial default is Tom's call, not a
  drive-by.
- **EmbeddingGemma-300m** scores *below* 0.6B on both benchmarks and caps at
  2048 tokens. Smaller and faster, not better.

Separately flagged for a future conversation, not this one:
**Qwen3-VL-Embedding-2B/8B** (Jan 2026) embeds text, images and video into *one*
shared space, which would collapse the two-model text+frame split entirely. At
2B/8B it is far heavier than so400m's 400M, and it is an architecture question.

## 6.3 Migration cost: much smaller than it looks

| | count |
|---|---:|
| chunks | 2,728 (1,722,832 chars, mean 632) |
| chunk vectors in `vec_chunks` | 2,691 |
| frame vectors in `vec_frames` | 3,060 (**untouched** by a text swap) |

At 0.6B's measured 200 embeddings/s — at batch 64 x 575 chars, within 10% of our
mean chunk — a full corpus re-embed is **14 seconds**. Scaling by parameters,
4B lands near 30/s: **~90 seconds**. Even ten times pessimistic, it is a quarter
of an hour.

**Nothing is re-downloaded and nothing is re-transcribed.** `_should_run`
(`runner.py:1117-1134`) re-runs a stage exactly when its recorded `model_key`
differs from the current one, and `_stage_text_embed` keys on
`config['text_embed.model']` (`runner.py:652-653`). Change that value and only
`text_embed` goes stale; `fetch`, `stt`, `chunk`, `keyframe`, `ocr` and
`frame_embed` all stay `done`. `want_media` is gated on the *keyframe* stage
being stale (`runner.py:329-331`), so the deleted mp4s are never missed.

The **real** cost is dimensional. `vec_chunks` is a `vec0` table with a declared
width, and `Database` checks it against `config['text_embed.dim']` at boot
(`db/database.py`, `_declared_dim`). 4B's native 2560 dims means a migration that
drops and recreates the vector table, plus a matching change to
`index-schema.md` in the same commit.

**MRL removes that.** Qwen3-Embedding is Matryoshka-trained across 32–2560, so a
4B vector truncated to 1024 and re-normalised is a valid 4B vector — same width
as today, no schema change, no contract change, no storage or ANN cost change.
Caveat stated plainly: **Qwen publishes no MRL degradation table**, and the
"~95% of performance at 1024" figures in circulation are third-party and generic
to MRL rather than measured on Qwen3. With a +6.6-point head start, a couple of
points of truncation loss still leaves a clear win — but it is a measurement, not
an assumption.

One ordering trap, from the code. `note_worker_drift` (`db/database.py:181-199`)
disables the vector leg the moment the worker reports a model that differs from
`config['text_embed.model']`, and `_stage_text_embed` **skips itself** when
`db.vectors.enabled` is false (`runner.py:661-666`). So changing `EMBED_MODEL` on
the worker *before* `config['text_embed.model']` gives you a re-embed that
silently skips every video. **Config first, worker second, then reindex.** Search
degrades to FTS-only in between, which is the designed behaviour and is
announced in a `note:`.

## 6.4 What actually constrains the choice

**VRAM, against the lease.** Measured peaks on the 3090: whisperX **7.9 GB**,
SigLIP 2 **2.9 GB**, plus ~340 MB of CUDA context that never comes back until the
process exits (`gpu-validation-2026-08-08.md` §2, §5.1). `HANDOFF-2026-08-08.md`
sizes the llama.cpp lease at ~12 GB. 4B at bf16 adds **8 GB** to an embedder
that costs 1.5 GB today; 8B at bf16 (~16 GB) does not co-exist with the lease at
all and is disqualified on this line alone, independent of its +1.0 points.

**The `EMBED_RESIDENT` trap, which is where this gets sharp.**
`gpu-validation-2026-08-08.md` §5.3 records that a resident backend keeps
`_any_loaded()` true forever, so `GPU_RELEASE_CMD` **never fires while the worker
is up** — on Tom's box, llama.cpp is stopped at the first embedding request and
never restarted. Today that is a defensible 1.5 GB standing. **`EMBED_RESIDENT=1`
with 4B at bf16 means 8 GB standing and a co-tenant that never comes back.**

**Query latency, and the reload cliff.** 0.6B answers a query in 15 ms and
reloads in 3.7 s. There is **no published single-query latency for Qwen3-Embedding
at any size** — not in the cards, the blog, the paper or the repo — so this needs
measuring rather than estimating. Structurally, a 20-token query is one forward
pass with no decode, so it is bandwidth-bound on weights and should scale nearer
the 6.7x weight ratio (~100 ms) than anything worse. The reload is the bigger
risk: 6.7x the weights to page in means a first-query-after-idle cost well past
3.7 s, and the way out of that is residency, which is the trap above.

## 6.5 Verdict

**Move the text embedder to Qwen3-Embedding-4B — quantised, MRL-truncated to
1024 — after one bench answers two numbers. Do not go to 8B. Leave the frame
embedder alone.**

Reasoning:

- The quality case is real, concentrated in one hop (+6.63 English retrieval),
  and lands on exactly this corpus's workload. 8B adds +0.98 for double the VRAM
  on a shared card: no.
- The migration is ~90 seconds of compute, no re-download, no re-transcription,
  and with MRL@1024 **no schema or contract change at all**.
- **Q8_0 (~4 GB), not bf16 (~8 GB).** That keeps the standing cost within sight
  of today's 1.5 GB and keeps the lease viable. Tom's box already runs
  llama.cpp, so a GGUF served through the existing `llama-server --embedding
  --pooling last` is the option that adds no second CUDA context — worth
  considering as a backend, noting `--pooling last` is mandatory (these are
  last-token-pooled causal models and mean pooling silently produces wrong
  vectors).
- Keep `EMBED_RESIDENT=0` unless the bench shows the reload is intolerable. If it
  does, the fix is a resident *quantised* model, and §5.3's "the co-tenant never
  comes back" has to be accepted explicitly rather than discovered.
- The frame leg stays SigLIP 2. It is 0.14 s/video-minute, its vectors are a
  different space, and nothing in this analysis touches it.

**The two numbers to bench first** (both on Tom's box, neither runnable here):

1. **Single-query latency** for 4B at Q8_0, warm and after an idle unload,
   against 0.6B's 15 ms / 3.7 s. Search embeds a query on every call; this is the
   number a user feels.
2. **Retrieval on this corpus at MRL 1024 vs 2560**, since Qwen publishes no
   truncation ablation. `bench/mcp_design_bench` territory — a held-out set of
   real queries against the existing 2,691 chunks.

If (1) comes back over ~150 ms warm, or (2) shows MRL@1024 giving back most of
the +6.63, **stay at 0.6B** — it is a genuinely strong model for its size,
already beating BGE-M3 (59.56 MMTEB) and multilingual-e5-large-instruct (63.22)
outright.

---

## 7. Open questions for Tom

1. **Is 1.30x for free worth turning on?** `VIDTHEQUE_KEYFRAME_EXTRACT_WORKERS=4`
   is shipped and defaulted off. It needs one bench run on a real 1080p talk
   (§4.2) confirming `all_identical: true` before it goes in the deploy env.
2. **Do we want the 2.8x badly enough to reindex the corpus?** §3.3 is the only
   item that changes the answer to "how many hours does a 64-video night take" by
   a lot, and it costs a full re-download plus re-detect plus re-OCR of
   everything already indexed. Worth scoping the reference-clip set now if the
   answer is ever going to be yes.
3. **`VIDTHEQUE_SHOT_CANDIDATES=5`?** ~1.18x, no boundary movement, but it is a
   quality knob and I have not measured what it costs on a slide build.
4. **`max_shot_seconds` floors instead of ceiling** (§5) — a 49-second scene gets
   one keyframe, not two. Fix and accept more keyframes, or document the setting
   as "subdivide above 2x"?
5. **4B at Q8 — worth the bench time?** The two numbers in §6.5 are maybe an
   hour of GPU work between batches, and they decide a +6.6-point retrieval
   question.
6. **jina-embeddings-v5-text-small is better than 0.6B at the same size and
   dimension, and is CC BY-NC.** A licensing call, not a technical one.

---

# 8. Addendum, 2026-08-09 (evening): §3.3 shipped

Append-only addendum. §3.3 above is left exactly as written; this section
records the decision that overtook it, what was implemented, and one finding
about `_should_run` that changed the shape of the work.

## 8.1 Tom's decision

> The fused path becomes the method for all future indexing. Shot boundaries are
> allowed to move relative to the old method. The existing 75 videos are **not**
> reindexed — mixed `model_key` provenance in the corpus is accepted.
> Bit-exactness with the old path is explicitly no longer a requirement.

That resolves open question §7.2 without paying its price. §3.3 assumed the
change implied "a one-time full reindex — including re-downloading every source
video, since `keep_source=audio` has already deleted them", and priced the item
accordingly. The decision buys the 65%-of-the-stage win on everything indexed
from now on and simply declines the reindex; the corpus ends up carrying two
provenances at once, which is a database property rather than a defect.

**Corollary the reference-clip set is not needed for.** With no equivalence bar
to clear, nothing had to be proven before shipping. What is still worth
measuring is how *large* the divergence is on real content — not as a gate, but
because "the changes would be minimal" is currently an expectation and not a
number (§8.5).

## 8.2 The seam: a `VideoStream` subclass, not a patch

PyAV worked; the ffmpeg-piped-rawvideo fallback was not needed and is not
implemented.

PySceneDetect's backend abstraction turned out to be the whole answer.
`SceneManager.detect_scenes(video=...)` accepts any `VideoStream`, and its decode
thread makes exactly two decisions about the frames it receives
(`scene_manager.py:618-677`):

- it resizes only when `compute_downscale_factor(max(video.frame_size)) > 1.0`;
- it compares each decoded array's shape against `video.frame_size` and logs a
  corruption warning when they disagree.

So a stream that *reports* the detection size and *returns* frames at it
satisfies both — no resize, no warning, no monkeypatching of the manager and no
fork of its loop. `keyframes._fused_stream_class()` builds one lazily:

```python
class FusedDetectionStream(VideoStreamAv):     # the shipped 0.7.1 backend
    BACKEND_NAME = "pyav-fused"

    @property
    def frame_size(self): return self._detect_size      # e.g. (256, 144)

    def read(self, decode=True):
        advanced = super().read(decode=False)           # decode, no conversion
        if advanced is False or not decode:
            return advanced
        return self._reformatter.reformat(
            self._frame, width=w, height=h, format="bgr24"
        ).to_ndarray()
```

Four properties of that, each of which was a reason not to write a fresh
backend:

- **Everything else is inherited whole**: PTS-backed `position` (deep links are
  the product), corrupt-frame skipping and `decode_failures`, `seek`, and
  `_handle_eof`'s re-open when AUTO threading stops short of the last frame.
- **`decode=False` still costs nothing.** It is the seek and frame-skip path,
  and the parent's own EOF recovery re-enters through it, which is why `read`
  delegates rather than reimplementing the decode loop.
- **The reformatter is per stream, not per frame**, as PyAV's docs require: one
  swscale context configured once per video.
- **The geometry is unchanged.** `detection_size()` mirrors 0.7.1's
  `compute_downscale_factor` *and* its `round(dim / factor)`, so the detector
  receives the same shape it always did, from the same preset, with the same
  thresholds. Only the pixels differ. A test asserts `DETECTION_MIN_WIDTH`
  against the upstream constant, which is what catches 0.8 changing it.

## 8.3 The `_should_run` finding — the part that needed care

**A changed `model_key` on a `done` row does force a re-run**
(`runner.py`, `_should_run`: `return recorded != model_key`). Taken as-is, a new
key would have contradicted the decision immediately: the next job to touch any
of the 75 videos would have re-detected it, and because `want_media` is gated on
that same call (`runner.py:329-331`), it would first have re-downloaded the
source mp4 that `keep_source=audio` deleted. "Do not reindex" is not the default
behaviour; it had to be built.

The resolution is one new piece of grammar in the key, and one clause:

| | |
|---|---|
| key before | `scenedetect-screencast-w1280` |
| key now | `scenedetect-screencast-w1280`**`+fused`** |
| rule | everything before `+` is the **contract**; everything after is **provenance** |

`_should_run` compares only the contract half for `keyframe`. Consequences,
each of them asserted in `mcp/tests/test_pipeline_keyframes.py`:

- a `done` row reading `scenedetect-screencast-w1280` is **not** re-run, and no
  mp4 is fetched for it;
- `talking_head`, or `w960`, or a NULL key, **is** re-run — a different detector
  or width is a genuinely different set of keyframes and still invalidates;
- a `failed` row still retries, and `force_reindex=true` still overrides
  everything, which is how a video is deliberately moved onto the fused path;
- no other stage forgives anything: for `ocr`, `frame_embed`, `text_embed` the
  key is a model id and a changed model must go stale.

This is deliberately narrower than "keyframes never re-run on a key change",
which would have silently broken the detector-swap workflow that
`index-schema.md` §1.3 documents. Both design docs now describe the `+`
convention (`index-schema.md` §1.3, `dashboard.md` §9's caveat list — a
provenance panel that flags staleness by string inequality would light up the
whole corpus).

## 8.4 What was implemented, and what was removed

- `keyframes.detect_spans(..., fused=True)` is the pipeline's only path. There
  is **no env var**: an escape hatch whose two settings produce different
  boundaries would need its own `model_key`, and Tom's decision says there is
  one method, not two.
- `fused=False` survives as a keyword argument with exactly one caller —
  `bench/keyframe_decode.py --fused-probe`. Deleting it would have deleted the
  only way to measure the thing this section is about.
- `av` became a **direct** dependency of `mcp/` (its own commit). No new wheel:
  `scenedetect-headless[pyav]` already resolved av 18.0.0. The pipeline imports
  it now, so it declares it.
- Pass 2 is untouched, thread pool and all (§4.1).

## 8.5 Numbers taken here, and the ones that are Tom's

Measured on this box, CPU only, on synthetic clips — the corpus and the GPU were
not touched.

**The conversion itself**, isolated over 800 frames of 1080p25, decode excluded:

| frame preparation | per frame | decode + prepare |
|---|---:|---:|
| `to_ndarray("bgr24")` at 1920x1080 + `cv2.resize` | 421 us | 1,197 frames/s |
| fused `reformat(256x144, "bgr24")` | **17.5 us** | **2,315 frames/s** |

**24x on the step §3.3 identified**, and it is the step that scales with source
resolution: 1080p pays 421 us, the detector reads 36,864 pixels either way.

End to end on a 1080p25 synthetic clip, via the bench's own `--fused-probe`:
detection **2.39 s → 1.41 s (x1.7)**, stage x1.42, **0 of 4 boundaries moved**,
**4/4 kept frames bit-identical**. On the 320x240 test fixture one boundary of
four moves by 200 ms — which is the expected behaviour, not a bug, and the tests
assert the cuts are *found within a couple of frames* rather than asserting
equality.

Two honest caveats on those figures:

1. **Synthetic content decodes unusually fast**, so the fused share of the pass
   is understated relative to a real H.264 talk — but the 421 us is a fixed
   per-frame cost, so the *absolute* saving transfers.
2. **§3.3's "stage 8.83 → 3.1 s/video-minute, ~2.8x" remains unverified.** It
   assumed pass 1 falls to near its bare-decode floor; the detector's own
   per-frame work (`ContentDetector` plus the Canny map) runs on the main thread
   and is unchanged, so the wall is `max(decode+prepare, detect)` and the
   ceiling is whichever of those is left. Expect real gains between the x1.4 and
   the x2.8, nearer the top of that range the higher the source resolution.

**What only Tom's box can answer**, one command
(`bench/README.md`, `--fused-probe`):

```bash
uv run --no-sync python bench/keyframe_decode.py \
    --pair full=/scratch/ID-1080p.mp4,detect=/scratch/ID-1080p.mp4 --no-dual \
    --fused-probe --repeats 2 \
    --out bench/results/raw/keyframe-fused-2026-08-09.json
```

It prints the pass-1 speedup on a real talk, `boundaries.moved` /
`moved_over_100ms` / the ten worst, and `identical_kept_frames` — the
product-level number, since a boundary that slid 40 ms changed nothing if the
frame the stage kept, and the `youtu.be/ID?t=` it points at, is the same frame.
Unlike `--extract-workers` there is no `all_identical` gate: this one is already
shipped, and the measurement tells us how much of the corpus's future differs
from its past, not whether it may.

## 8.6 What this changes in the table in §4

| # | action | speedup (stage) | output | status |
|---|---|---|---|---|
| 4 | fused PyAV `reformat` in pass 1 | x1.4 measured here, x2.8 predicted | **cuts move** | **shipped**, `+fused` in the key, no reindex |

Items 1–3 are unaffected: the pass-2 pool, `CAP_PROP_N_THREADS` and
`SHOT_CANDIDATES` all still apply on top of a fused pass 1, and multiply with it
rather than adding. Open questions §7.1, §7.3 and §7.4 are untouched. §7.2 is
answered.

# 9. Addendum, 2026-08-09 (GPU window): §7.1 measured on a real 1080p talk

Append-only. Everything above is left as written; this section reports one run of
the committed `bench/keyframe_decode.py --extract-workers` on Tom's box and what
it changes in §3.1, §4 and §7.1.

**The gate passes. The speedup does not.** `all_identical: true` across all
twelve configurations — so `VIDTHEQUE_KEYFRAME_EXTRACT_WORKERS=4` may ship — but
it is worth **x1.25 on pass 2 and ~x1.06 on the keyframe stage**, not the x1.30
on the *stage* that §3.1's arithmetic predicted. The reason is the interesting
part: **pass 2 was never serial.**

## 9.1 What was run

| | |
|---|---|
| file | a real 1080p conference talk already on the box (`1lgFGaHoGq8.mp4`, AI Engineer World's Fair) — **not** synthetic, and not downloaded: YouTube is rate-limiting this box, which is why the batch is parked |
| stream | H.264 High, 1920x1080, 59.94 fps, **1301 s (21.7 min)** |
| detector | `--kind screencast`; 22 scenes → **51 shots** after `subdivide`/`thin` |
| settings | `candidates_per_shot=9`, `budget=600`, `max_shot_seconds=25` (the bench's own constants, i.e. today's defaults) |
| pipeline code | `33c8cd6` — the fused pass 1 of §8 is *in*, so these are post-§8 numbers |
| box | 10 cores, shared with the live demo stack (worker + mcp) |

Detection runs once; all twelve configurations extract from that one shot list,
so the only variable is the extractor. Every configuration produced 51 keyframes
and `pair_keyframes` matched all 51 against the 1-worker baseline as
**identical frames**.

## 9.2 The matrix

Wall seconds / CPU seconds for pass 2, one invocation, baseline first:

| `decode_threads` \ `workers` | 1 | 2 | 4 | 8 |
|---|---|---|---|---|
| 0 (OpenCV default) | **61.3** / 280.4 | 51.0 / 301.8 | **49.2** / 326.5 | 50.3 / 349.1 |
| 1 | 65.6 / 283.8 | 53.8 / 300.3 | 49.6 / 323.4 | 50.5 / 348.1 |
| 2 | 60.1 / 279.3 | 52.4 / 299.0 | 48.3 / 323.1 | 49.4 / 352.1 |

- `all_identical`: **true** (12/12).
- Best configuration: `workers=4`; `x1.25` at the default decode threads, `x1.27`
  against the probe's own baseline.
- **8 workers is not better than 4** — same wall, 7% more CPU. On a 10-core box
  that also runs the worker, 4 is the number.

A second, earlier invocation of the same command (while a sibling agent's job was
running) gave 63.8 / 52.0 / 58.3 / 54.9 — same `all_identical: true`, but
non-monotonic and 18% slower at `w=4`. **Pass 2 is contention-sensitive**, which
is itself a reason not to raise the pool higher than 4: the gain is small enough
that a busy box eats it.

## 9.3 Why it is x1.25 and not x3: pass 2 was already using 4.6 cores

Look at the CPU column at `workers=1`: **280 s of CPU in 61 s of wall — 4.6
cores, with one worker thread.** `cv2.VideoCapture`'s FFmpeg backend frame-threads
the decode by default, and on this box its default `CAP_PROP_N_THREADS` is
**10, one per core**. §3.1's "the *search* half of pass 2 can run on N threads"
is true, but it was already running on ~5.

So the pool is not turning a serial pass parallel; it is overlapping the *seek*
latency that the decoder threads cannot hide. That is worth something real —
20% — and it costs 16% more total CPU (280 s → 326 s) to get it, which is the
signature of exactly that: more redundant decode, less waiting.

**Consequence for the numbers in §3.1 and §4**, on §3.1's own stage shares
(pass 1 ≈ 0.65, pass 2 ≈ 0.36):

| | §3.1 predicted | measured here |
|---|---|---|
| pass 2 | x3 (assumed) | **x1.25** |
| keyframe stage | x1.30 | **x1.066** |
| corpus saving | ~59 min | **~18 min** (23,652 s → ~22,590 s, x1.047) |

The corpus figure is §3.1's own arithmetic re-run: if a 3x pass 2 saved 59 min,
pass 2 over the corpus is ~5,325 s, and a 1.25x pass 2 saves ~1,065 s.

**It is worth relatively more after §8, not less.** The fused pass 1 shrinks the
half of the stage the pool does not touch, so on a post-§8 stage
(pass 1 ≈ 0.38 after x1.7) the same x1.25 on pass 2 is **x1.11 on the stage**.
The two multiply, as §8.6 said they would.

## 9.4 `VIDTHEQUE_KEYFRAME_DECODE_THREADS` is inert — the `set()` is refused

Item 2 of the §4 table is "unmeasured". It is now measured, and the answer is
that **it does nothing**, because it never reaches the decoder:

```python
cap = cv2.VideoCapture(path)          # OpenCV 5.0.0, FFmpeg backend
cap.get(cv2.CAP_PROP_N_THREADS)       # -> 10.0   (one per core, the default)
cap.set(cv2.CAP_PROP_N_THREADS, 1.0)  # -> False  (refused)
cap.get(cv2.CAP_PROP_N_THREADS)       # -> 10.0   (unchanged)
```

The evidence in the matrix agrees with the API: at `workers=1`, CPU is 280.4 s,
283.8 s and 279.3 s at `decode_threads` 0, 1 and 2. If the setting had landed,
`decode_threads=1` would have dropped CPU by ~4x, not 1%. The knob's own comment
in `keyframes.py:501` ("the FFmpeg backend honours it") is the thing that is
wrong, not the knob's plumbing.

**The fix, if the lever is wanted, is the constructor** — OpenCV applies
`params` at open time and refuses them afterwards:

```python
cv2.VideoCapture(path, cv2.CAP_FFMPEG, [int(cv2.CAP_PROP_N_THREADS), 1])
# opened True, N_THREADS -> 1.0
```

Verified on this box, same OpenCV build. Whether it is *worth* wanting is a
different question: the reason the pool only buys 1.25x is that those decoder
threads are already doing the work, so turning them down to hand the cores to the
pool is at best a wash and at worst §3.1's arithmetic in reverse. **Recommend:
leave the default at 0, and either fix the comment or move the `set()` into the
constructor so the env var stops being a promise the code cannot keep.**

## 9.5 What this changes

- **§7.1 is answered: yes, ship it.** `all_identical: true` on a real 1080p60
  talk with 2-second GOPs, 51 shots, 459 absolute seeks — the identity claim
  survives outside the 8-second fixture. `VIDTHEQUE_KEYFRAME_EXTRACT_WORKERS=4`
  can go in the deploy env.
- **§4's table should read x1.25 (pass 2) / ~x1.06 (stage), not ~x1.30.** The
  recommendation does not change — it is still free and still identical — but it
  is a 5-6% pipeline win, not a 20% one, and nobody should size a night's
  indexing on the old figure.
- **§4's item 2 is now measured: no effect, and inert on this build** (§9.4).
- Item 3 (`SHOT_CANDIDATES=5`) is untouched and is now the largest un-taken win
  in pass 2, precisely because the pool turned out to be small: cutting the
  candidates cuts the seeks, and seeks are what pass 2 is actually spending its
  wall clock on.

### A wrinkle in the bench script, for whoever owns it

`extract_workers_probe` crashes **after** every configuration has run and
printed, in its own summary:

```
best = min(runs, key=lambda r: r["wall"])   # keyframe_decode.py:445
KeyError: 'wall'
```

`Timing.as_dict()` emits `wall_s` / `cpu_s` (line 84), and lines 445, 449 and 451
read `wall`. So the per-configuration lines print but `--out` never gets written
and `all_identical` never prints. Both runs above were recovered from stdout,
which is why this section has no `bench/results/raw/*.json` beside it. Left
unfixed on purpose: `bench/keyframe_decode.py` was being edited by another agent
in this same session, and a two-line fix landing under them was not worth the
collision.

---

## §10 — fused probe on real footage, and the phantom-cut verdict (2026-08-09, evening)

`--fused-probe` on `1lgFGaHoGq8` (21.7 min, 1080p60, the same talk §9 used;
orchestrator run, raw JSON lost to a scratchpad rotation — numbers preserved
here):

- **detect ×1.65** (155.0 s → 94.0 s best-of-2), **stage ×1.26** (189.5 s →
  150.5 s). Fused extraction ran 56.5 s vs legacy 34.5 s on its own shot list
  (51 vs 49 shots; unexplained gap, possibly seek-pattern noise — worth a look
  if extraction ever dominates again).
- Boundaries: 24 → 22 scenes, 5 moved >100 ms (3 >1 s), kept frames 35/42
  pixel-identical, phash256 mean 6.5.
- **Every large delta inspected visually and ruled a *legacy* phantom.** Frames
  either side of both lost cuts (1165.3 s, 1182.8 s) show the same static wide
  stage shot; either side of the 28.7 s "moved" boundary (368.9 s) shows the
  same unchanged terminal slide. The legacy full-res convert-then-`cv2.resize`
  chain passes more compression noise/flicker to the detector; the fused
  swscale path low-passes it. The merges remove junk keyframes (same family as
  the 190 zero-OCR black fade frames §9 flagged), and fused kept *more*
  keyframes overall (51 vs 49) via shot subdivision — coverage did not shrink.

Verdict (Tom, 2026-08-09): not a regression, a correction. Decision to adopt
fused for future indexing stands; old corpus stays under its old key per the
`+fused` provenance grammar.
