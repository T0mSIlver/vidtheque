# vidtheque — indexing pipeline tooling research (2026-08-08)

Verification pass over the six tool picks in `HANDOFF-2026-08-08.md`. Everything
below was checked against PyPI JSON APIs, GitHub repos/releases/issues, HF model
APIs, and — where it mattered — by downloading the actual wheels and reading the
shipped source, or by running the tool against live YouTube on this box.

**Nothing was installed into any project. No dependency was added.**

Read the "Contradictions with the handoff" section first; the rest is reference.

---

## 0. Pinned-version summary

| Component | Pin | Released | Notes |
|---|---|---|---|
| `whisperx` | **3.8.6** | 2026-05-25 | forces `torch~=2.8.0`; py `>=3.10,<3.14` |
| `torch` / `torchaudio` / `torchvision` | **2.8.0 / 2.8.0 / 0.23.0** (`+cu128`) | — | hard pin from whisperX |
| `pyannote-audio` | 4.0.7 (transitive) | — | **VRAM regression, see 1.5** |
| `transformers` | **>=5.14** (floor for SigLIP2 is 4.50) | 5.14.1, 2026-07-16 | 5.x auto-applies SigLIP2 text preprocessing |
| `google/siglip2-so400m-patch16-naflex` | — | — | 1152-d, Apache-2.0 |
| `rapidocr` | **3.9.2** | 2026-07-21 | **not** `rapidocr-onnxruntime` |
| `onnxruntime` | **1.28.0** | 2026-07-25 | pin explicitly; rapidocr doesn't |
| `scenedetect-headless` | **0.7.1** (`<0.8`) | 2026-07-22 | 0.7 was a breaking release |
| `ImageHash` | **4.3.2** | 2025-02-01 | frozen but fine |
| `yt-dlp[default,deno]` | **2026.7.4** | 2026-07-04 | JS runtime now effectively mandatory |
| Text embeddings | see §6 — **recommend `Qwen/Qwen3-Embedding-0.6B`** over bge-m3 | | |

---

## Contradictions with the handoff

Six things the handoff gets wrong or under-specifies. Ordered by how much design
they change.

1. **whisperX is not always needed for word-level timestamps.** The handoff picks
   whisperX specifically for "word-level forced alignment → precise deep links."
   **Verified empirically today:** YouTube's *auto-caption* `json3` track already
   carries per-word offsets (`tStartMs` + per-segment `tOffsetMs`). One HTTP
   request, no GPU. Manual subtitles do *not* have this (cue-level only). This
   doesn't kill the whisperX pick — punctuation, casing and diarization are why
   you still want it — but it turns "GPU transcription" from a hard prerequisite
   into a quality upgrade, and it gives no-GPU self-hosters a working product.
   See §5.4. **This is the single biggest design consequence in this document.**

2. **`RapidOCR` is not `rapidocr-onnxruntime`.** The handoff says "RapidOCR (ONNX,
   easy to containerize)". The package to install is now `rapidocr` 3.9.2 —
   `rapidocr-onnxruntime` has been frozen at 1.4.4 since Jan 2025. The new package
   ships **no inference engine**; you install `onnxruntime` yourself. Good news:
   the default ONNX models are **bundled in the wheel**, so a default-config
   `RapidOCR()` makes zero network calls — better for containers than the handoff
   assumed. See §3.

3. **"NaFlex" has only two sizes, and the HF blog's table is wrong.**
   `google/siglip2-so400m-patch16-naflex` and `google/siglip2-base-patch16-naflex`
   exist. `-large-…-naflex` and `-giant-…-naflex` **do not**. Also: **open_clip
   cannot load NaFlex from any released version** — the configs landed on `main`
   in May 2026 and the latest PyPI release (3.3.0) predates them. transformers is
   the only shipping path. See §2.

4. **bge-m3 is no longer the accuracy leader at its size.** `Qwen3-Embedding-0.6B`
   is +4.7 MMTEB points, Apache-2.0, 32k context, with Matryoshka truncation that
   cuts index size 4×. bge-m3's remaining unique value is its **sparse/lexical
   head** — which for terminal/CLI-flag/error-code search is genuinely worth
   something. Recommendation in §6.

5. **PySceneDetect's `ContentDetector` defaults will under-detect on exactly the
   content this corpus targets.** Confirmed by reading the 0.7.1 source: the frame
   score is a *weight-normalised mean* of H/S/V deltas. Screencasts are
   near-greyscale, so hue and saturation deltas are ~0 and dilute the score by 3×
   — a full slide change can score 20 against a threshold of 27 and be missed. The
   handoff's "PySceneDetect shot detection" needs an explicit weights override, not
   defaults. See §4.2.

6. **The handoff's VRAM assumption ("whisper+embed+OCR peak ~3–4GB loaded
   sequentially — often no eviction needed") is optimistic if diarization is on.**
   pyannote-audio 4.x has an **open, unfixed** VRAM regression: a 72-minute file
   peaks at **9.54 GB** vs 1.59 GB on 3.3.2. whisperX 3.8.x requires pyannote
   `>=4.0.0`, so there is no version escape. Either run diarization strictly
   sequentially with `empty_cache()` between stages, or make it opt-in. The
   llama.cpp lease logic should assume a ~12 GB peak, not 4 GB, whenever
   diarization is enabled. See §1.5.

Minor notes that don't contradict but do extend the handoff:

- yt-dlp's info dict carries a **`heatmap`** (YouTube "most replayed", 100 buckets
  with 0..1 values). A free popularity prior over video time — nothing in the
  landscape survey uses it. See §5.1.
- yt-dlp now needs a **JavaScript runtime** (Deno) for full YouTube support. There
  is a PyPI redistribution of the Deno binary and yt-dlp declares it as an extra,
  so this is one line in `pyproject.toml`, not a Dockerfile change. See §5.0.
- **Rate limiting is tighter than you'd guess**: a single `--skip-download` run on
  *one* video got `HTTP 429` on its **third** subtitle request, from a cold
  residential IP. See §5.5.

---

## 1. whisperX

### 1.1 Version and maintenance

| Fact | Value |
|---|---|
| Latest stable | **3.8.6**, 2026-05-25 (3.8.7rc1 prerelease 2026-06-26) |
| Package | still `whisperx` — no rename, no fork has displaced it |
| Repo | still [m-bain/whisperX](https://github.com/m-bain/whisperX) (API returns 200, no redirect) |
| Stars / open issues | 23,482 / 174 |
| License | BSD-2-Clause |
| Last commit to `main` | 2026-07-13 |

**Alive but coasting.** Releases are cut by contributor Barabazs, and a striking
share of recent commits are authored by the GitHub user `claude` — dependency
maintenance and CI hardening, not feature work. The README TODO list hasn't moved
in years ("Improve diarization (word level)" still marked *"Harder than first
thought…"*). Maintained enough to depend on; do not expect it to grow. The README
now opens with a paid Recall.ai sponsorship block, which tells you where the
project's energy went.

### 1.2 Install under uv

`requires-python = ">=3.10, <3.14"` — 3.12 and 3.13 both fine.

Declared deps of 3.8.6: `ctranslate2>=4.5.0`, `faster-whisper>=1.2.0`,
`pyannote-audio>=4.0.0`, **`torch~=2.8.0`**, `torchaudio~=2.8.0`,
`torchvision~=0.23.0`, `torchcodec>=0.6.0,<0.8.0`, `numpy>=2.1.0`,
`transformers>=4.48.0`, `nltk>=3.9.1`, `omegaconf`, `pandas`, `huggingface-hub<1.0.0`,
`triton>=3.3.0` (linux x86_64).

`torch~=2.8.0` is a **hard compatible-release pin** — torch 2.9+ is not usable with
whisperX today ([#1374](https://github.com/m-bain/whisperX/issues/1374), unanswered).

A real `uv lock` (uv 0.11.24, py3.12) resolves cleanly to 135 packages:
`whisperx 3.8.6`, `torch 2.8.0+cu128`, `ctranslate2 4.8.1`, `faster-whisper 1.2.1`,
`pyannote-audio 4.0.7`, `nvidia-cudnn-cu12 9.10.2.21`, `numpy 2.5.1`,
`transformers 4.57.6`, `triton 3.4.0`. No conflicts, no `--index-strategy` hacks.

**The one silent trap.** `[tool.uv.sources]` only binds to **direct** dependencies.
With `whisperx` as the only direct dep and `torch` mapped to the cu128 index, uv
silently ignores the mapping and locks plain `torch 2.8.0` from PyPI. You must
redeclare the torch trio yourself:

```toml
[project]
requires-python = ">=3.12,<3.14"
dependencies = [
    "whisperx>=3.8.6",
    # redeclare these as DIRECT deps or [tool.uv.sources] below is ignored
    "torch~=2.8.0",
    "torchaudio~=2.8.0",
    "torchvision~=0.23.0",
]

[tool.uv.sources]
torch       = { index = "pytorch-cu128" }
torchaudio  = { index = "pytorch-cu128" }
torchvision = { index = "pytorch-cu128" }

[[tool.uv.index]]
name = "pytorch-cu128"
url = "https://download.pytorch.org/whl/cu128"
explicit = true
```

Note that PyPI's default `torch` 2.8.0 wheel is itself CUDA-enabled on Linux
x86_64, so a bare `uv add whisperx` *does* produce a working CUDA install. The
explicit index only matters if you want a specific CUDA minor or a smaller image.

**cuDNN 8 vs 9 hell is largely over** — this is the biggest change from 2024-era
advice:

- CTranslate2 **4.5.0** moved to cuDNN 9 and dropped cuDNN 8; whisperX's
  `ctranslate2>=4.5.0` floor puts you firmly in cuDNN-9 land, matching what torch
  2.8 bundles. The classic mismatch is structurally gone.
- CTranslate2 **4.6.3** went further —
  [PR #1949](https://github.com/OpenNMT/CTranslate2/pull/1949) made cuDNN an
  *optional* dependency via a pure-CUDA Conv1d (1.45ms → 3.13ms on that op, which
  is <5% of total compute).

whisperX still ships `CUDNN_TROUBLESHOOTING.md`, but its own note says *"This error
is unlikely on a clean install."* The residual failure mode is a **system cuDNN in
`LD_LIBRARY_PATH` shadowing torch's bundled one**. In Docker: do **not** install
system cuDNN, let the pip wheels own it, leave `LD_LIBRARY_PATH` unset. A plain
`python:3.12-slim` plus the pip nvidia wheels works.

### 1.3 Python API — transcribe + align

Signatures read from `asr.py` / `alignment.py` / `schema.py` at `main`.

```python
import whisperx

device, batch_size, compute_type = "cuda", 16, "float16"

# --- 1. ASR ---------------------------------------------------------------
model = whisperx.load_model(
    "large-v3",                 # whisper_arch (positional)
    device,                     # device (positional)
    device_index=0,
    compute_type=compute_type,  # "default" -> float16 on cuda, float32 on cpu
    asr_options=None,           # dict, merged over defaults
    language=None,              # pin to skip per-file language detection
    vad_method="pyannote",      # or "silero" (downloads from GitHub at runtime!)
    vad_options=None,           # {"chunk_size":30,"vad_onset":0.5,"vad_offset":0.363}
    task="transcribe",
    download_root=None,         # CT2 model cache dir
    local_files_only=False,     # offline
    threads=4,
)

audio = whisperx.load_audio("audio.wav")   # -> np.float32 mono 16 kHz

result = model.transcribe(
    audio,
    batch_size=batch_size,      # NOTE: batch_size lives HERE, not on load_model
    language=None, task=None, chunk_size=30,
    print_progress=False, progress_callback=None,
)
# -> {"segments": [{"text","start","end","avg_logprob"}, ...], "language": "en"}

lang = result["language"]       # CAPTURE THIS NOW — align() drops it

# --- 2. Forced alignment --------------------------------------------------
model_a, metadata = whisperx.load_align_model(
    language_code=lang, device=device,
    model_name=None,            # override wav2vec2 checkpoint
    model_dir=None,
    model_cache_only=False,     # True => local_files_only, for offline
)

result = whisperx.align(
    result["segments"], model_a, metadata, audio, device,
    interpolate_method="nearest",
    return_char_alignments=False,
)
```

Returned shape after `align()` (`AlignedTranscriptionResult`):

```python
{
  "segments": [
    {"start": float, "end": float, "text": str,
     "avg_logprob": float,                      # NotRequired
     "words": [{"word": str, "start": float, "end": float, "score": float}, ...],
     "chars": [...] | None,                     # only if return_char_alignments=True
     "speaker": str}                            # only after assign_word_speakers
  ],
  "word_segments": [{"word","start","end","score"}, ...]   # flat, all words
}
```

**Two gotchas for the OpenAI-compatible API layer:**

- `align()` returns exactly `{"segments", "word_segments"}` (`alignment.py:424`).
  The `"language"` key from `transcribe()` is **silently dropped**. OpenAI's
  `verbose_json` includes `language`, so stash it before aligning.
- Individual words can come back **without `start`/`end`** when they contain no
  characters in the alignment dictionary — whisperX's own `assign_word_speakers`
  guards with `if 'start' not in word: continue`. Your serializer must tolerate
  missing per-word timestamps.

`asr_options` merges over: `beam_size=5, best_of=5, patience=1, length_penalty=1,
repetition_penalty=1, no_repeat_ngram_size=0, temperatures=[0.0…1.0],
compression_ratio_threshold=2.4, log_prob_threshold=-1.0, no_speech_threshold=0.6,
condition_on_previous_text=False, without_timestamps=True, word_timestamps=False,
suppress_numerals=False, hotwords=None, initial_prompt=None`.

### 1.4 Diarization — the API moved and the model changed

Changed in **3.8.0** (2026-02-13). Verified by diffing release tags:

| | v3.7.9 and earlier | **v3.8.0+ (current)** |
|---|---|---|
| Default model | `pyannote/speaker-diarization-3.1` | **`pyannote/speaker-diarization-community-1`** |
| Token kwarg | `use_auth_token=` | **`token=`** |
| pyannote.audio | 3.x | **>=4.0.0** |

Import path is **`from whisperx.diarize import DiarizationPipeline`** — it is *not*
re-exported at top level (`__init__.py` lazily exports only `load_model`,
`load_audio`, `load_align_model`, `align`, `assign_word_speakers`, `setup_logging`,
`get_logger`). `assign_word_speakers` *is* top-level.

```python
from whisperx.diarize import DiarizationPipeline

diarize_model = DiarizationPipeline(
    model_name=None,          # defaults to "pyannote/speaker-diarization-community-1"
    token=HF_TOKEN,           # NOT use_auth_token= any more
    device=device,            # defaults to "cpu"! pass "cuda" explicitly
    cache_dir=None,
)

diarize_segments = diarize_model(
    audio,
    num_speakers=None, min_speakers=None, max_speakers=None,
    return_embeddings=False,  # True -> returns (df, {speaker: [floats]})
)  # -> pandas DataFrame [segment, label, speaker, start, end]

result = whisperx.assign_word_speakers(
    diarize_segments, result,
    speaker_embeddings=None,
    fill_nearest=False,       # True: label words with no time overlap too
)
```

**The HF token is still required.**
[`pyannote/speaker-diarization-community-1`](https://huggingface.co/pyannote/speaker-diarization-community-1)
is gated — "You need to agree to share your contact information to access this
model." CC-BY-4.0, by pyannoteAI. Accept the conditions on the Hub with the same
account that owns the token. Accuracy is genuinely better than 3.1 (AliMeeting DER
24.5% → 20.3%).

Two notes:
- `device` **defaults to `"cpu"`** — an easy way to accidentally run diarization on
  CPU and wonder why it takes forever.
- [#1457](https://github.com/m-bain/whisperX/issues/1457): the CLI leaks `hf_token`
  in the process list. Irrelevant for the Python API; just never shell out to the
  `whisperx` CLI with a token in argv.
- `assign_word_speakers` now uses an interval tree, documented as *"~228× speedup
  for long-form content (3+ hour podcasts)"* — a real fix if you were on an older
  version.

### 1.5 VRAM on a 24 GB card

**The README has no VRAM table.** Its only first-party number is "*requires <8GB
gpu memory for large-v2 with beam_size=5*". Everything below is third-party;
measure on the 3090 before trusting it.

| Component | VRAM | Source |
|---|---|---|
| large-v3, float16, batch 16 (ASR only) | ~5 GB | [whisperx-asr-service](https://github.com/murtaza-nasir/whisperx-asr-service), explicitly measured on an RTX 3090 |
| large-v3, transcription only | ~10 GB | [RunPod guide](https://www.runpod.io/articles/guides/whisperx-on-runpod) |
| large-v3 + alignment + diarization | **16–20 GB** | RunPod guide |
| medium / distil-large-v3.5 | ~3 GB | whisperx-asr-service |
| tiny / base | ~1 GB | whisperx-asr-service |

The wav2vec2 alignment model is small (base 960h ≈ 95M params) and is not your
constraint.

**Diarization is the real risk, and this is the finding that matters most for the
GPU lease design.**

[pyannote-audio #1963](https://github.com/pyannote/pyannote-audio/issues/1963) —
*"pyannote.audio 4.0.3 uses 6× more VRAM than 3.3.2 (>9.54GB vs 2.59GB peak)"* — is
**still OPEN** (filed 2025-12-07, active through 2026-04). whisperX 3.8.x requires
pyannote-audio >=4.0.0, so you are on the affected side.

- 72-minute file: peak **9.54 GB** on 4.0.3 vs **1.59 GB** on 3.3.2.
- Independent reproduction on 4.0.4: `max_allocated = 10.53 GiB`,
  `max_reserved = 12.09 GiB`, process peak 12,642 MiB.
- Affects **both** `community-1` and `3.1` under pyannote 4.x — downgrading the
  model does not help.
- Root cause per the thread: a single `_embedding` call on the **final partial
  batch** (shape `(29,1,160000)` vs the normal `(32,…)`) spikes to 10.53 GiB —
  most likely a different cuDNN algorithm/workspace for the odd batch size. Not a
  leak; memory returns to baseline afterwards (but `max_reserved` stays high, so it
  *looks* like one in monitoring).

**Consequence for the worker's lifecycle manager:** run the three stages
sequentially and free between them —
`del model; gc.collect(); torch.cuda.empty_cache()` — rather than holding ASR +
align + diarize resident concurrently. Size the NVML free-VRAM check against a
**~12 GB** diarization peak when diarization is enabled, not the handoff's 3–4 GB.
If you want models warm for latency, keep ASR+align resident and run diarization
out-of-process or on request only.

### 1.6 CPU fallback

```python
model = whisperx.load_model("large-v3", device="cpu", compute_type="int8", threads=8)
result = model.transcribe(audio, batch_size=4)
model_a, metadata = whisperx.load_align_model(language_code="en", device="cpu")
result = whisperx.align(result["segments"], model_a, metadata, audio, "cpu")
```

**Everything works.** Alignment is plain torchaudio/transformers wav2vec2 and runs
on CPU fine. Diarization runs on CPU too (`device="cpu"` is in fact the default),
just slowly. Nothing structurally breaks.

Watch: `compute_type="default"` resolves to **float32** on CPU, not int8 — pass
`int8` explicitly. `float16` on CPU fails outright
([#878](https://github.com/m-bain/whisperX/issues/878), long-standing). Your API
should map device→compute_type rather than letting a config default leak through.

**Realtime factor: roughly 3× realtime** for large-v3 int8 CPU-only (60-min file
≈ 20 min) on a decent modern desktop CPU
([PromptQuorum 2026 comparison](https://www.promptquorum.com/power-local-llm/local-whisper-stt-comparison-2026)).
Alignment adds meaningful CPU time on top and is *not* in that figure. A shared
container core will be worse. CPU is a viable **degraded fallback**, not a serving
tier.

### 1.7 Gotchas that will actually bite

**a) Offline containers — the good news.** The pyannote **VAD model ships inside
the wheel** (`whisperx/assets/pytorch_model.bin`, loaded by relative path in
`vads/pyannote.py`). No download, **no HF token for VAD**. Old guides telling you
to accept `pyannote/segmentation` on the Hub are stale.

**b) Offline containers — the bad news, three runtime downloads:**

1. **`nltk punkt_tab`** — downloaded *inside `align()`* at runtime
   (`alignment.py:147-157`). The single most likely thing to break an air-gapped
   container, and it fails **mid-request**, not at startup. The July 2026 commit
   `fix: raise actionable error when punkt_tab download fails` only improved the
   message. Bake it: `RUN python -m nltk.downloader punkt_tab`.
2. **wav2vec2 alignment model** — torchaudio pipelines (en/fr/de/es/it) or HF Hub
   (all others). Pre-warm, then `model_cache_only=True`.
3. **Diarization `community-1`** — gated HF download. Pre-warm with the token at
   build time, then run with `HF_HUB_OFFLINE=1`.

Also: `vad_method="silero"` calls `torch.hub.load('snakers4/silero-vad')` — a
**GitHub fetch at runtime**. Stick with the default `pyannote` VAD.

**c) The numerals/alignment regression is live and unresolved.**
[#1449](https://github.com/m-bain/whisperX/issues/1449) (open, 2026-07-11): the
wildcard-column CTC scoring removed in 3.8.2 was **reintroduced in 3.8.3** and is
present in 3.8.4/3.8.5/3.8.6. The tradeoff: without it, words like `"2014."` or
`"£13.60"` get **no timestamp at all** (README Limitations); with it they get
timestamps at the cost of the accuracy regression originally reported in #1220.
Related open reports: [#1451](https://github.com/m-bain/whisperX/issues/1451)
(inaccurate timestamps on a 25s segment),
[#1446](https://github.com/m-bain/whisperX/issues/1446) (fails on repeated
successive words), [#1425](https://github.com/m-bain/whisperX/issues/1425).
**If exact numeral timing matters for deep links, pin 3.8.2 or test both.**

**d) Long-audio host RAM.** [#1440](https://github.com/m-bain/whisperX/issues/1440)
(open): `load_audio()` buffers the whole ffmpeg output via
`subprocess.run(capture_output=True)`. A 10h file ≈ 1.15 GB raw PCM, and "*the
Python bytes buffer, numpy view, and float32 copy all coexist at peak*" — budget
~3–4× the PCM size in host RAM. Enforce a duration cap at the API boundary.

**e) Don't do a source install.**
[#1461](https://github.com/m-bain/whisperX/issues/1461) (open, 2026-07-30):
whisperX ships `exclude-newer = "1 week"` under `[tool.uv]`, which breaks
resolution for wheels lacking PEP 700 upload-date metadata. Only bites if you
`git clone` and resolve inside the repo; installing from PyPI is unaffected.

**f) No built-in HTTP server.** You write the `/v1/audio/transcriptions` layer.
[whisperx-asr-service](https://github.com/murtaza-nasir/whisperx-asr-service)
already exposes `/v1/models` and `/v1/audio/transcriptions`, supports
`PRELOAD_MODEL=large-v3` and `HF_HUB_OFFLINE=1`, and publishes a cu128/torch-2.8
image variant — the closest thing to a reference implementation of the worker's
target architecture. Worth reading before writing.

### 1.8 Is whisperX still the right pick?

**Yes, but as a mature, coasting dependency rather than a growing one.**

The genuine 2026 challenger is
[`nvidia/parakeet-tdt-0.6b-v3`](https://huggingface.co/nvidia/parakeet-tdt-0.6b-v3)
(CC-BY-4.0), which produces **word-level timestamps natively** —
`transcribe([...], timestamps=True)` → `output[0].timestamp['word']` — with no
separate alignment model. 6.32% WER vs large-v3's 7.44% on the Open ASR
Leaderboard, RTFx 3,332, **~2 GB VRAM**. Its limits: **25 European languages** vs
Whisper's 99, and **no diarization** (NeMo pairs it with TitaNet + clustering,
which is assembly rather than a `--diarize` flag).
[Canary-1B-v2](https://arxiv.org/abs/2509.14128) beats large-v3 on English at ~10×
the speed but its forced-aligner path gives **segment-level**, not word-level,
timestamps. Plain `faster-whisper` with `word_timestamps=True` remains the cheap
option but is cross-attention-DTW derived: **±100–500 ms per word vs ~±30 ms for
forced alignment**, 85.4% vs 93.2% word-timing precision on Switchboard.

For an OpenAI-compatible endpoint on one 3090 needing *word-level timestamps plus
diarization across arbitrary languages*, nothing else packages all four stages
coherently, and the two historically worst install problems are genuinely resolved.

**The one scenario to reconsider:** if you decide vidtheque's corpus is
English/European-only and you drop speaker labels, Parakeet TDT v3 gives
comparable timestamps, better WER, and ~2 GB of VRAM with a fraction of the
dependency surface. Given the worker's `STTBackend` abstraction already exists,
**a `parakeet` backend is a cheap second implementation and a good bench post** —
whisperX vs Parakeet on word-timing accuracy and VRAM is exactly the kind of
patterns-not-internals comparison the bench directory is for.

---

## 2. SigLIP 2 NaFlex

### 2.1 Model IDs (verified against the HF API, 2026-08-08)

**Only two NaFlex variants exist**, despite the HF launch blog implying four:

| ID | Params | Embed dim | Downloads/mo |
|---|---|---|---|
| [`google/siglip2-so400m-patch16-naflex`](https://huggingface.co/google/siglip2-so400m-patch16-naflex) | 1,135,670,962 (fp32 file 4.54 GB) | **1152** | 427k |
| [`google/siglip2-base-patch16-naflex`](https://huggingface.co/google/siglip2-base-patch16-naflex) | ~375M | 768 | 1.57M |

`google/siglip2-large-patch16-naflex` and `google/siglip2-giant-opt-patch16-naflex`
**do not exist** (401/not-found; absent from a full `search=siglip2` enumeration).
The [SigLIP 2 paper](https://arxiv.org/html/2502.14786v1) Table 7 confirms it —
NaFlex results are reported only for B/16 and So/16. The
[HF blog table](https://huggingface.co/blog/siglip2) listing naflex for large and
giant is **wrong**. There are `-jax` twins of both (weights only).

Fixed-resolution ids, all confirmed live, for comparison:
`siglip2-base-patch16-{224,256,384,512}`, `siglip2-base-patch32-256`,
`siglip2-large-patch16-{256,384,512}`, `siglip2-so400m-patch14-{224,384}`,
`siglip2-so400m-patch16-{256,384,512}`, `siglip2-giant-opt-patch16-{256,384}`.
FixRes checkpoints load with the older `SiglipModel`; **NaFlex requires
`Siglip2Model`**.

**There is no SigLIP 3.** SigLIP 2 (Feb 2025) is still the current generation.

### 2.2 transformers version and snippets

SigLIP2 shipped as a dedicated release tag
[`v4.49.0-SigLIP-2`](https://newreleases.io/project/github/huggingface/transformers/release/v4.49.0-SigLIP-2)
— it was **not** in plain 4.49.0. First normal release containing it is **4.50.0**.
Latest on PyPI is **5.14.1** (2026-07-16).

**Pin `transformers>=5.14`.** What 5.x changed that matters:
- New `Siglip2Tokenizer` — the processor now applies lowercasing +
  `padding="max_length", max_length=64, truncation=True` **automatically**. On 4.x
  you must pass those yourself or embeddings degrade. The model was trained on
  lowercased text.
- `torch_dtype=` → `dtype=` (v5 rename).
- `Siglip2ImageProcessorFast` is the default (it's what the checkpoint's
  `preprocessor_config.json` names).

```python
# transformers >= 5.14
import torch
from PIL import Image
from transformers import AutoModel, AutoProcessor

CKPT = "google/siglip2-so400m-patch16-naflex"
model = AutoModel.from_pretrained(
    CKPT, dtype=torch.bfloat16, device_map="cuda", attn_implementation="sdpa"
).eval()
processor = AutoProcessor.from_pretrained(CKPT)

# ---------- IMAGE (NaFlex-specific) ----------
frames = [Image.open(p).convert("RGB") for p in frame_paths]   # mixed aspect ratios OK
inputs = processor(images=frames, max_num_patches=256, return_tensors="pt").to(model.device)
# NaFlex-only input keys:
#   pixel_values         (B, max_num_patches, patch_size**2 * 3)  == (B, 256, 768)
#   pixel_attention_mask (B, max_num_patches)
#   spatial_shapes       (B, 2)  int64 (num_patches_h, num_patches_w)
with torch.inference_mode():
    img = model.get_image_features(**inputs)          # (B, 1152)
img = img / img.norm(p=2, dim=-1, keepdim=True)

# ---------- TEXT ----------
texts = ["a terminal showing a stack trace", "a slide with a bar chart"]
t_inputs = processor(text=texts, return_tensors="pt").to(model.device)  # auto lowercase + pad-64
with torch.inference_mode():
    txt = model.get_text_features(**t_inputs)         # (B, 1152)
txt = txt / txt.norm(p=2, dim=-1, keepdim=True)

# ---------- retrieval / zero-shot ----------
sim = img @ txt.T                                     # cosine — store/ANN on this
logits = sim * model.logit_scale.exp() + model.logit_bias
probs  = torch.sigmoid(logits)                        # SigLIP is sigmoid, NOT softmax
```

Gotchas verified against source:

- **The docs page is stale.** [The model_doc page](https://huggingface.co/docs/transformers/model_doc/siglip2)
  documents `pixel_values` as `(batch, channels, image_size, image_size)` and
  `pixel_attention_mask` as `(B, image_size, image_size)` — that's copied from
  plain SigLIP. The real NaFlex shapes are `(B, N, 768)` and `(B, N)`; confirmed in
  [`image_processing_siglip2.py`](https://github.com/huggingface/transformers/blob/v5.14.1/src/transformers/models/siglip2/image_processing_siglip2.py)
  line 131: `model_input_names = ["pixel_values", "pixel_attention_mask", "spatial_shapes"]`.
- On transformers 4.x you must write
  `processor(text=[t.lower() for t in texts], padding="max_length", max_length=64, truncation=True, ...)`
  explicitly.
- Scoring math confirmed in
  [`modeling_siglip2.py:886-889`](https://github.com/huggingface/transformers/blob/v5.14.1/src/transformers/models/siglip2/modeling_siglip2.py):
  `logits = (t @ i.T) * logit_scale.exp() + logit_bias`, then sigmoid. `logit_bias`
  is strongly negative, so absolute probabilities are calibrated per-label — fine
  for thresholding, irrelevant if you only rank by cosine (which vidtheque does).
- `attn_implementation="flash_attention_2"` is supported and is the right call for
  NaFlex, where sequences are padded to `max_num_patches`.

### 2.3 Dims, patch budget, tokenizer

- **Embedding dim:** so400m-naflex = **1152** (`hidden_size: 1152`,
  `projection_size: 1152`, both towers, 27 layers, 16 heads,
  `intermediate_size: 4304`). base-naflex = **768**. Cross-checked against the
  [open_clip config](https://github.com/mlfoundations/open_clip/blob/main/src/open_clip/model_configs/ViT-SO400M-16-SigLIP2-naflex.json)
  (`embed_dim: 1152`).
- **`max_num_patches`:** default **256**. Any integer is accepted, but the paper
  trained NaFlex by uniformly sampling from **{128, 256, 576, 784, 1024}** — stick
  to that set. `patch_size: 16`, so 256 patches ≈ a 256×256-equivalent area with
  aspect ratio preserved; 1024 ≈ 512×512-equivalent. Resize is aspect-preserving to
  h,w multiples of 16 such that `(h/16)*(w/16) <= max_num_patches`, then padded in
  the patch dimension.
- **Text:** `GemmaTokenizer`, vocab 256000, `do_lower_case: true`.
  `model_max_length` in the config is the sentinel `1e30` — **ignore it**; the
  trained context length is **64** (docs say "padding and truncation to length 64";
  open_clip says `"context_length": 64`).
  **64 tokens is a real constraint for vidtheque** — a natural-language frame query
  is fine, but you cannot embed a transcript sentence of any length into the same
  space. Keep the frame index's text side to short queries; long text goes to the
  bge-m3/Qwen3 index.

### 2.4 VRAM and throughput

No published 3090 numbers exist for this checkpoint. The following is an
**estimate with the arithmetic shown** — benchmark before committing.

**VRAM (the param count is measured, so this part is solid):**
- 1.1357B params × 2 bytes (bf16) = **2.27 GB** weights.
- Activations, inference-only, SDPA, batch 64 @ 256 patches: hidden state is
  64×256×1152×2B = 37.7 MB per tensor; peak transient ≈ **1–3 GB**.
- **Total ~4–6 GB at batch 64.** Batch 256 still fits comfortably in 24 GB. This
  can be co-resident with the text embedder and still leave room — consistent with
  the handoff's "embedding model stays resident (~1–2GB)" env option, though 2.3 GB
  is the honest floor for so400m, not 1–2 GB.

**Throughput estimate:**
- Vision tower non-embedding params: 27 × (4×1152² + 2×1152×4304) ≈ **411M**;
  ~416M with patch/pos embeds and the MAP head.
- FLOPs/frame @ 256 patches = 2 × 416M × 256 ≈ 213 GFLOP, plus attention
  27 × 4 × 256² × 1152 ≈ 8 GFLOP → **≈221 GFLOP/frame**.
- RTX 3090 (GA102) peak **FP16/BF16 tensor with FP32 accumulate = 35.6 TFLOPS
  dense** — consumer Ampere is deliberately half-rate on this path (the 71 TFLOPS
  figure is FP16-accumulate, which PyTorch matmul does not use). At a realistic
  50–60%: 18–21 TFLOPS.
- → **~80–95 frames/s at `max_num_patches=256`**. At 1024 patches, MLP ×4 and
  attention ×16 → ~980 GFLOP/frame → **~18–22 frames/s**.
- **At 300 frames/video that is ~3–4 seconds of GPU time.** Frame embedding is
  nowhere near the bottleneck; whisperX and OCR dominate. Practically you will be
  **JPEG-decode-bound, not GPU-bound**.
- The one real datapoint found: a vendor blog claims SigLIP-2 SO400M at batch 128
  goes from ~85ms to ~55ms per batch via FP16 ONNX on Ampere
  ([Spheron](https://www.spheron.network/blog/multimodal-embedding-models-gpu-cloud-siglip2-jinaclip-cohere/))
  — but on unspecified datacenter Ampere at unspecified resolution. The arithmetic
  above is the more defensible planning number.

### 2.5 open_clip vs transformers — use transformers

open_clip *does* support NaFlex, but **only on `main`**:

- Configs `ViT-SO400M-16-SigLIP2-naflex.json` and `ViT-B-16-SigLIP2-naflex.json`
  were added **2026-05-15/16**
  ([commit 294733a0](https://github.com/mlfoundations/open_clip/commit/294733a0)),
  pointing at [`timm/ViT-SO400M-16-SigLIP2-naflex`](https://huggingface.co/timm/ViT-SO400M-16-SigLIP2-naflex).
- **They are in no release.** Latest PyPI `open_clip_torch` is **3.3.0
  (2026-02-27)**, which predates the commit — the config file 404s at tags v3.0.0,
  v3.2.0 and v3.3.0. `pip install open_clip_torch` today gets you SigLIP2 **FixRes**
  but not NaFlex.
- open_clip's NaFlex path routes through `timm`'s
  `naflexvit_so400m_patch16_siglip` and adds token-budget batching (`--use-naflex`),
  which is nice for *training* and buys an inference worker nothing.

**ONNX/TensorRT is thin.** `onnx-community` has ONNX for most **FixRes** variants,
but for NaFlex only
[`siglip2-base-patch16-naflex-ONNX`](https://huggingface.co/onnx-community/siglip2-base-patch16-naflex-ONNX)
exists (24 downloads/mo) — **no so400m-naflex ONNX export on the Hub**.
transformers.js [issue #1402](https://github.com/huggingface/transformers.js/issues/1402)
(NaFlex support) was closed 2026-02-09, so the export path works; you'd run it
yourself with Optimum. Fixing `max_num_patches=256` makes it a static shape, which
is fine. No TensorRT engine published; NVIDIA's
[NGC TAO SigLIP 2](https://catalog.ngc.nvidia.com/orgs/nvidia/tao/models/siglip_v2)
covers `so400m-patch16-256` (FixRes), not NaFlex. There is a GGUF and a CoreML
export, both low-traffic community uploads.

Given §2.4 (frame embedding is ~4 s of GPU per video), **do not spend effort on
ONNX/TensorRT here.** It optimises the cheapest stage.

### 2.6 License

**Apache-2.0**, confirmed on the model card and in the HF API `cardData.license`.
**No additional use restrictions** — a genuine Apache grant, not a Gemma-style
custom license. Trained on WebLI. Stated intended use: "zero-shot image
classification and image-text retrieval, or as a vision encoder for VLMs." Nothing
constrains a self-hostable indexing product.

### 2.7 Is it still the right pick for screencast/slide/terminal frames?

**Yes for a single-vector frame index in Aug 2026 — with one challenger worth
benchmarking, and one architecture to deliberately *not* use as the primary index.**

**Why NaFlex specifically is the right SigLIP 2** (this validates the handoff's
reasoning with a citation): paper Table 7 shows NaFlex beating the fixed-resolution
variant on the majority of aspect-ratio/text-sensitive retrieval benchmarks —
**Screen2Words, SciCap, TextCaps, HierText** — "in particular for small sequence
lengths." Screen2Words is literally UI-screenshot captioning. A 16:9 screencast
frame square-resized to 384×384 mangles glyphs; NaFlex doesn't.

**Nothing has displaced it in the open single-vector class.** No SigLIP 3.
DINOv3 / Perception Encoder are vision-only or vision-first — no text tower, so no
natural-language frame search. MetaCLIP 2, JinaCLIP-v2 and MobileCLIP2 are all
weaker on dense/OCR-ish content (and JinaCLIP-v2 is CC-BY-NC).

**Benchmark against [`Qwen/Qwen3-VL-Embedding-2B`](https://huggingface.co/Qwen/Qwen3-VL-Embedding-2B)**
(Jan 2026, [arXiv 2601.04720](https://arxiv.org/abs/2601.04720)). Apache-2.0, 2B,
**single-vector** with MRL 64–2048 dims, 32k context, native dynamic resolution,
explicitly trained on screenshots **and video frames**, ViDoRe 84.4 / MMEB-V2 73.2.
Architecturally a better fit for "terminal frame with text in it" than a CLIP-family
dual encoder, and it keeps the single-vector storage story. Costs: ~4 GB bf16 plus
much larger activations (a VLM forward, not a 256-token ViT) — call it 5–15× slower
per frame, so ~30–60 s of GPU per video instead of 4 s. Still cheap next to
whisperX. **Ship SigLIP 2 NaFlex so400m now; benchmark Qwen3-VL-Embedding-2B on
held-out frames before the corpus gets large enough that reindexing hurts.**

**ColPali/ColQwen-style late interaction: no, not as the primary index.** They are
genuinely better at document-page retrieval
([ColNomic-embed-multimodal-7b](https://huggingface.co/nomic-ai/colnomic-embed-multimodal-7b)
hits 62.7 NDCG@5 on ViDoRe-v2), but the storage math kills it for video:

| | vectors/frame | dim | bytes/frame (fp16) | 1M frames |
|---|---|---|---|---|
| SigLIP 2 NaFlex so400m | 1 | 1152 | 2.3 KB | **2.3 GB** |
| SigLIP 2 @ 256-d (PCA) | 1 | 256 | 0.5 KB | 0.5 GB |
| ColPali / ColQwen2 | 768–1024 | 128 | 196–256 KB | **~200–256 GB** |

A **~100× blowup**, plus MaxSim instead of a dot product — you lose flat
HNSW/IVF and need a multi-vector engine. That is flatly incompatible with the
handoff's "SQLite + sqlite-vec" index. For a video corpus where adjacent frames are
near-duplicates anyway, it's the wrong trade.

**The pragmatic architecture** (and it matches the handoff): SigLIP 2 NaFlex so400m
at `max_num_patches=256` as the dense frame index — bump to 576 or 1024 for
keyframes with dense small text, same checkpoint, no re-model — OCR the frames
separately into the text index, and hybrid-fuse. That recovers most of ColPali's
text-in-image recall at 1/100th the storage. Note this makes `max_num_patches` a
**per-frame** decision you can drive off the OCR line count: a frame RapidOCR found
60 lines in is worth 1024 patches; a talking head is worth 256.

---

## 3. RapidOCR

### 3.1 Package identity — the handoff's package name is stale

`rapidocr` is canonical. The three backend-split packages are frozen.

| PyPI package | Latest | Released | Status |
|---|---|---|---|
| **`rapidocr`** | **3.9.2** | **2026-07-21** | **Canonical.** Unified, all engines |
| `rapidocr-onnxruntime` | 1.4.4 | 2025-01-17 | Frozen, superseded |
| `rapidocr-paddle` | 1.4.5 | 2025-01-17 | Frozen, superseded |
| `rapidocr-openvino` | 1.4.4 | 2025-01-17 | Frozen, superseded |
| `rapidocr-torch` | — | — | Never existed on PyPI |

The [install docs](https://rapidai.github.io/RapidOCRDocs/main/install_usage/rapidocr/install/)
state that `rapidocr` "is a merged version combining `rapidocr_onnxruntime`,
`rapidocr_openvino`, `rapidocr_paddle` and PyTorch inference support" and that the
old three "are gradually being phased out."

**The install story is not extras.** `rapidocr` 3.9.2 declares
`provides_extra: null` and its `requires_dist` contains **no inference engine**:
`pyclipper, opencv_python>=4.5.1.48, numpy<3.0.0,>=1.19.5, six, Shapely, PyYAML,
Pillow, tqdm, omegaconf, requests, colorlog`. You pick the engine:

```bash
uv add "rapidocr==3.9.2" "onnxruntime==1.28.0"
```

`requires-python` is `>=3.8,<4`. **3.8.2 is yanked.** Sanity check after install:
`rapidocr check`.

### 3.2 Current API

Constructor is `RapidOCR(config_path=None, params=None)`. `params` is a **flat
dotted-key dict** merged over the packaged `config.yaml`.

```python
import cv2
import numpy as np
from rapidocr import RapidOCR, EngineType, LangRec, ModelType, OCRVersion

engine = RapidOCR()                      # defaults: PP-OCRv6 small det+rec, ORT CPU

engine = RapidOCR(params={
    "Global.log_level": "warning",
    "Global.text_score": 0.5,
    "Global.use_cls": False,             # screen text is never 180-degree rotated
    "Det.limit_type": "max",             # see the resize gotcha in 3.6
    "Det.limit_side_len": 1280,
    "Rec.rec_batch_num": 16,             # default 6 is stingy for dense slides
    "EngineConfig.onnxruntime.intra_op_num_threads": 4,
})

# or from a YAML file:  rapidocr config --save_cfg_file my.yaml
engine = RapidOCR(config_path="my.yaml")
```

Call signature (`rapidocr/main.py`):

```python
result = engine(
    img_content,                 # str path | Path | bytes | np.ndarray (BGR) | http(s) URL
    use_det=None, use_cls=None, use_rec=None,
    return_word_box=None, return_single_char_box=None,
    text_score=None, box_thresh=None, unclip_ratio=None,
)
```

**Return type is `RapidOCROutput`** (dataclass in `rapidocr/utils/output.py`) — the
old list-of-tuples API is gone:

```python
@dataclass
class RapidOCROutput:
    img: Optional[np.ndarray]              # the ORIGINAL input image
    boxes: Optional[np.ndarray]            # (N, 4, 2) float, ORIGINAL image coords
    txts: Optional[Tuple[str]]
    scores: Optional[Tuple[float]]
    word_results: Tuple[Tuple[str, float, Optional[List[List[int]]]]]
    elapse_list: List[Union[float, None]]  # [det, cls, rec]
    elapse: float                          # sum of non-None entries
    viser: Optional[VisRes]
    # methods: __len__, to_json(), to_markdown(), vis(save_path=None)
```

Extraction:

```python
engine = RapidOCR()
frame = cv2.imread("keyframe_000123.jpg")      # BGR numpy array
res = engine(frame)                            # or engine("keyframe_000123.jpg")

lines = []
if len(res):                                   # __len__ is len(txts); 0 when txts is None
    for box, txt, score in zip(res.boxes, res.txts, res.scores):
        xs, ys = box[:, 0], box[:, 1]          # (4,2): TL, TR, BR, BL
        lines.append({
            "text": txt,
            "conf": float(score),
            "quad": box.tolist(),
            "bbox": [float(xs.min()), float(ys.min()), float(xs.max()), float(ys.max())],
        })

det_t, cls_t, rec_t = (res.elapse_list + [None, None, None])[:3]
```

Two behaviours from `main.py` worth relying on:
- Boxes are mapped **back to original image coordinates**
  (`map_boxes_to_original` undoes both the global resize and the vertical
  letterbox padding) — usable directly for crops and for the `get_frames`
  bounding-box story.
- Lines whose recognised text is empty/whitespace are **filtered out of all four
  arrays together**, so `boxes`/`txts`/`scores` stay index-aligned. They are `None`
  (not empty lists) when nothing is found — check `len(res)`.

When `use_det=False` or `use_rec=False` the return type changes to
`TextDetOutput`/`TextClsOutput`/`TextRecOutput` rather than `RapidOCROutput` —
[open issue #486](https://github.com/RapidAI/RapidOCR/issues/486). Don't write code
assuming `RapidOCROutput` unless the full pipeline is on.

### 3.3 Model download behaviour — good news for Docker

**The default models ship inside the wheel.** Listing
`rapidocr-3.9.2-py3-none-any.whl` (27.3 MB):

```
21,234,383  rapidocr/models/PP-OCRv6_rec_small.onnx
 9,929,594  rapidocr/models/PP-OCRv6_det_small.onnx
   585,532  rapidocr/models/ch_ppocr_mobile_v2.0_cls_mobile.onnx
```

The 3.9.0 release notes confirm the intent: package size "increased from 15MB to
29MB due to larger model files." **Any blog saying RapidOCR downloads from
ModelScope on first run is describing pre-3.x behaviour.**

Resolution logic (`rapidocr/main.py`, `inference_engine/onnxruntime/main.py`):

1. `Det/Cls/Rec.model_path` set → used directly, no network, `FileNotFoundError` if
   missing.
2. Else `Global.model_root_dir` (default `<site-packages>/rapidocr/models/`) is
   scanned for the filename resolved from `default_models.yaml`.
3. Only if absent does it `requests.get` from ModelScope.
   `DownloadFile._should_skip_download` short-circuits when the file exists *and*
   its SHA256 matches.

Since the bundled filenames match the default config's resolved names, **a
default-config `RapidOCR()` on a stock install makes zero network calls.** For the
ONNX path there is also **no separate character-dictionary file** — the charset
lives in the ONNX metadata (`custom_metadata_map["character"]`).

Airtight container (belt and braces — hard-code paths so a config change can never
trigger a download):

```dockerfile
FROM python:3.12-slim-bookworm

# rapidocr depends on opencv-python (NOT headless) -> needs GUI shared libs
RUN apt-get update && apt-get install -y --no-install-recommends \
      libgl1 libglib2.0-0 && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir "rapidocr==3.9.2" "onnxruntime==1.28.0"

ENV RAPIDOCR_MODELS=/opt/models
RUN python - <<'PY'
import shutil, pathlib, rapidocr
src = pathlib.Path(rapidocr.__file__).parent / "models"
dst = pathlib.Path("/opt/models"); dst.mkdir(parents=True, exist_ok=True)
for f in src.glob("*.onnx"): shutil.copy2(f, dst)
PY
COPY rapidocr.yaml /opt/rapidocr.yaml
```

`rapidocr.yaml` (generate the full file with
`rapidocr config --save_cfg_file rapidocr.yaml`, then edit):

```yaml
Global:
  model_root_dir: /opt/models
Det:
  model_path: /opt/models/PP-OCRv6_det_small.onnx
Cls:
  model_path: /opt/models/ch_ppocr_mobile_v2.0_cls_mobile.onnx
Rec:
  model_path: /opt/models/PP-OCRv6_rec_small.onnx
```

For a **non-default** model, pre-fetch at build time with
`RUN rapidocr download_models --config /opt/rapidocr.yaml`. There is **no
environment variable** for the model dir — the only levers are
`Global.model_root_dir` and per-task `model_path`.

**Real trap:** `RapidOCR._initialize()` unconditionally constructs `TextDetector`,
`TextClassifier` **and** `TextRecognizer`, regardless of `use_det/use_cls/use_rec`.
Even with `use_cls=False` the classifier model is loaded — so in an offline
container, a missing cls model is a hard failure at construction time
([#714](https://github.com/RapidAI/RapidOCR/issues/714),
[#537](https://github.com/RapidAI/RapidOCR/issues/537)). **Bake all three models
even if you only use two.**

The official `docker/README.md` mounts a named volume instead of baking; for a
hermetic no-egress image, prefer the explicit `model_path` approach above.

### 3.4 Default models and English-only recognition

Defaults in the shipped `config.yaml`:

| Stage | ocr_version | lang_type | model_type | File |
|---|---|---|---|---|
| Det | **PP-OCRv6** | `ch` | `small` | `PP-OCRv6_det_small.onnx` |
| Cls | PP-OCRv4 | `ch` | `mobile` | `ch_ppocr_mobile_v2.0_cls_mobile.onnx` |
| Rec | **PP-OCRv6** | `ch` | `small` | `PP-OCRv6_rec_small.onnx` |

PP-OCRv6 became the default in **3.9.0** (2026-06-23), replacing PP-OCRv4.
`model_type` for v6 is `tiny`/`small`/`medium`.

**There is no English-only PP-OCRv6 model.** From `utils/model_resolver.py`, every
v6 key is templated `multi_PP-OCRv6_{det,rec}_{model_type}` and all ~50 language
codes resolve to the *same* multilingual weights. `Rec.lang_type="en"` on v6 only
performs a validation check. PP-OCRv6 is a single 50-language model by design
([paper](https://arxiv.org/abs/2606.13108)).

To genuinely force English-only you must drop to v5/v4:

```python
engine = RapidOCR(params={
    "Rec.ocr_version": OCRVersion.PPOCRV5,
    "Rec.lang_type":   LangRec.EN,
    "Rec.model_type":  ModelType.MOBILE,     # -> en_PP-OCRv5_rec_mobile.onnx
})
```

These are **not bundled** — they download from ModelScope on first use.

**Recommendation: stay on the v6 multilingual default.** v6 small is stronger than
the v4/v5 English mobile models, the multilingual charset costs nothing at
inference, and you keep the zero-download property. If you see CJK hallucinations
on English content, raise `Global.text_score` (default 0.5) rather than swapping
models. This also keeps the corpus honest for non-English videos, which the
subscription feature will eventually pull in.

### 3.5 GPU vs CPU — don't chase CUDA here

**The maintainer's own position**
([Discussion #225](https://github.com/RapidAI/RapidOCR/discussions/225)): the
`use_cuda` flag is "a legacy from the previous attempt at onnxruntime-gpu
acceleration, which was later found to be not very good." Corroborated by
[#94](https://github.com/RapidAI/RapidOCR/issues/94) (GPU ≈ CPU) and a May 2026
report of CUDA at 63s vs DirectML at 11s on an RTX 5090
([onnxruntime #28305](https://github.com/microsoft/onnxruntime/issues/28305)).

The reason is structural: PP-OCR det/rec are tiny dynamic-shape convnets, so
per-frame you pay dozens of small kernel launches plus host↔device copies for a few
ms of actual compute, and cuDNN re-tunes on every new input shape.

If you try anyway:

```python
engine = RapidOCR(params={"EngineConfig.onnxruntime.use_cuda": True,
                          "EngineConfig.onnxruntime.cuda_ep_cfg.device_id": 0})
```

`ProviderConfig.is_cuda_available()` requires *both* `use_cuda: true` **and**
`onnxruntime.get_device() == "GPU"` with `CUDAExecutionProvider` present; otherwise
it logs a warning and **silently falls back to CPU**. Set
`cudnn_conv_algo_search: "HEURISTIC"` instead of the default `"EXHAUSTIVE"` to
avoid a very long first-inference stall on dynamic shapes.

**CUDA-version footgun:** `onnxruntime` and `onnxruntime-gpu` are mutually
exclusive in one env. `onnxruntime-gpu` 1.28.0 (2026-07-25) is built against
**CUDA 13 / cuDNN 9**; `pip install "onnxruntime-gpu[cuda,cudnn]"` pulls its own
CUDA userspace. Also **`onnxruntime` >= 1.24.4 requires Python >= 3.11**.

**The promising GPU path is TensorRT** (added 3.7.0; PP-OCRv6 accuracy verified
under TensorRT in 3.9.2). `config.yaml` ships a full `EngineConfig.tensorrt` block
with FP16 on by default and explicit dynamic-shape profiles
(`det_profile.opt_shape: [1,3,736,736]`, `rec_profile.opt_shape: [6,3,48,320]`).
Caveats: engine build takes **several minutes per model on first run**, is cached in
the model dir, and is **hardware- and TensorRT-version-specific** — you cannot bake
a 3090 engine and ship it to self-hosters. Official image pins
`tensorrt>=8.6,<8.7` and `cuda-python>=12.0,<13.0`; drift produces
`CUDA initialization failure with error: 35`.

**Throughput for 300 frames.** Vendor per-image numbers for PP-OCRv6
([PaddleOCR docs](https://www.paddleocr.ai/main/en/version3.x/algorithm/PP-OCRv6/PP-OCRv6.html)):

| Tier | A100 + TensorRT | Xeon 8350C + OpenVINO | Apple M4 + ORT |
|---|---|---|---|
| medium | 0.32 s | 1.40 s | 5.55 s |
| **small** (default) | **0.16 s** | **0.59 s** | **1.29 s** |
| tiny | — | 0.20 s | 0.35 s |

Extrapolated to 1280px keyframes, ORT CPU, `use_cls=False`:

- **Talking head** (2–8 lines: lower-thirds, captions): ~0.15–0.3 s/frame →
  **300 frames ≈ 45–90 s**
- **Dense screencast/slide** (30–80 lines): ~0.5–1.2 s/frame →
  **300 frames ≈ 3–6 min**

Detection cost is fixed per frame; **recognition scales linearly with line count**,
and `rec_batch_num` defaults to **6**, so a 60-line slide is 10 sequential rec
batches. Raising `Rec.rec_batch_num` to 16–32 is the single biggest free win on
text-dense frames.

Best GPU case (TensorRT FP16 on a 3090, post-build) is ~0.1–0.2 s/frame →
**300 frames in 30–60 s**. A 3–6× win, paid for with a CUDA 13/TensorRT 8.6 stack,
multi-minute cold starts, and a container that no longer runs CPU-only.

**Recommendation: run 4–6 worker processes, each with `intra_op_num_threads=2`,
over the frame list.** OCR is embarrassingly parallel per frame, ORT's intra-op
pool scales poorly past ~4 threads on these small graphs, and this gets near-linear
speedup with zero CUDA surface — and it satisfies the no-GPU self-hoster
requirement by construction. **It also means OCR does not need the GPU lease at
all**, which materially simplifies the handoff's `GPU_ACQUIRE_CMD` story: only
whisperX and the embedding model contend with llama-server.

### 3.6 Container gotchas

- **`opencv-python`, not headless.** `rapidocr` hard-depends on
  `opencv_python>=4.5.1.48`. On `python:*-slim` you need
  `apt-get install libgl1 libglib2.0-0` or you get `ImportError: libGL.so.1`.
- **numpy 2.x is fine** (`numpy<3.0.0,>=1.19.5`; current is 2.5.1). No numpy-2
  issues in the open tracker.
- **Pin `onnxruntime` yourself** — rapidocr deliberately doesn't. Floating it means
  a rebuild can silently cross a CUDA-major or Python-minimum boundary.
- **Threading.** `intra_op_num_threads`/`inter_op_num_threads` default to `-1`
  (ORT auto = all cores). In a cgroup-limited container ORT reads the **host** core
  count and oversubscribes badly. **Always set these explicitly** and mirror into
  `OMP_NUM_THREADS`. Values outside `1..os.cpu_count()` are silently ignored.
- **Memory.** `enable_cpu_mem_arena` defaults to `false` with
  `arena_extend_strategy: kSameAsRequested` — a deliberate low-RSS choice; leave
  it. All three models load regardless of `use_*`; budget ~32 MB of weights plus
  ORT arena per process when sizing the worker pool.
- **Do not multithread one engine** — crashes with shared sessions under GPU
  backends ([#327](https://github.com/RapidAI/RapidOCR/issues/327),
  [#303](https://github.com/RapidAI/RapidOCR/issues/303)). One instance per process.
- **Tall screenshots.** `Global.max_side_len: 2000` downscales longer inputs,
  `min_side_len: 30` upscales tiny ones, `use_vertical_padding` letterboxes when
  `w/h > width_height_ratio` (8). No sliding-window support
  ([#418](https://github.com/RapidAI/RapidOCR/issues/418)). Video keyframes are
  nowhere near this — non-issue.
- **⚠️ Detector resize asymmetry — a real perf bug for 1080p frames.**
  `Det.limit_type` defaults to **`min`** with `limit_side_len: 736`. Reading
  `ch_ppocr_det/utils.py`: under `min`, the detector **only ever upscales** (when
  the short side < 736) and **never downscales**. So a 1920×1080 keyframe is fed to
  the detector at full 1920×1088. Set explicitly:

  ```python
  params={"Det.limit_type": "max", "Det.limit_side_len": 1280}
  ```

  which caps the *long* side. On 1080p keyframes this cuts detector cost ~2× with
  no meaningful recall loss on screen text. **This interacts with the download
  resolution decision in §5.3** — download at 1080p so the source glyphs are
  legible, then let the detector work at 1280 long-side.
- **`use_cls=False`** for screencasts and talking heads — screen text is never
  180°-rotated. (The model is still loaded; see §3.3.)
- **Arabic/RTL is currently broken** without a manual `python-bidi` install
  ([#719](https://github.com/RapidAI/RapidOCR/issues/719), Aug 2026). Irrelevant
  here, but it tells you 3.9.2 is fresh enough to still have rough edges.

### 3.7 Is something else better in Aug 2026?

**PaddleOCR 3.7.0** (2026-06-11) is the upstream source of the same PP-OCRv6
weights, so accuracy is a wash; you'd trade a 27 MB pip install for the full
PaddlePaddle runtime and buy GPU inference that actually works. Only worth it if
you commit to the TensorRT/Paddle-GPU path.

**VLM-based OCR has not displaced this tier, and shouldn't here.** PaddleOCR-VL
(0.9B, Apache-2.0) and dots.ocr (3B, MIT) top the accuracy leaderboards
(PaddleOCR-VL-1.6 self-reports 96.33 on OmniDocBench v1.6), but they are
*document-understanding* models: seconds per page, a vLLM serving stack, solving
layout/table/reading-order problems vidtheque doesn't have. At ~300 frames/video
**throughput dominates completely**, and a 17M-param CTC recogniser at
0.2–0.6 s/frame beats a 900M-param VLM at 2–10 s/frame by an order of magnitude.
**Stay on RapidOCR + PP-OCRv6 small.** This also stays consistent with the
handoff's "VLM dropped for now — the client model is the VLM."

---

## 4. PySceneDetect + sharpest-frame + dedup

### 4.1 Version — 0.7 landed and it broke things

**`scenedetect` 0.7.1, released 2026-07-22.** 1.0 has *not* landed. Timeline:
0.6.7.1 (2025-09-25) → **0.7 (2026-05-03)** → **0.7.1 (2026-07-22)**.

Two distributions — **you want the second**:

| Package | Bundles |
|---|---|
| `scenedetect` | `opencv-python` (needs libGL) |
| **`scenedetect-headless`** | **`opencv-python-headless`** — "for servers without GUI libraries" |

Identical code. Requires-Python is now **`>=3.10`** (raised in 0.7). Extras:
`[pyav]` → `av>=9.2`, `[moviepy]`.

```bash
uv add "scenedetect-headless[pyav]==0.7.1"
```

The functional API is all still exported from the top level:

```python
from scenedetect import (
    detect, open_video, SceneManager, StatsManager,
    ContentDetector, AdaptiveDetector, ThresholdDetector,
    HistogramDetector, HashDetector,
    VideoStreamCv2, VideoStreamAv, AVAILABLE_BACKENDS,
    FrameTimecode, save_images, split_video_ffmpeg,
)
```

**0.7 breaking changes that will bite ported code:**

- Modules moved: `scenedetect.scene_detector` → **`scenedetect.detector`**;
  `scenedetect.frame_timecode` → **`scenedetect.common`**; image/HTML/CSV export →
  **`scenedetect.output`**. (Shims remain at the old paths.)
- **`FrameTimecode` getters are now properties**: `.frame_num`, `.seconds`,
  `.frame_rate`. `get_frames()`/`get_seconds()` emit `DeprecationWarning`.
  `.framerate` is soft-deprecated for **`.frame_rate`**. `previous_frame()` removed.
- **`SceneDetector.process_frame(timecode, frame_img)`** — first arg is now a
  `FrameTimecode`, not an int. `is_processing_required()` and
  `stats_manager_required` are gone; `SceneDetector` is a true ABC.
- `scenedetect.video_manager` **deleted**. `AdaptiveDetector.min_delta_hsv` and
  `.get_content_val()` removed.
- **VFR overhaul — the headline 0.7 feature.** Frame rates are `Fraction`
  internally (`Fraction(24000,1001)`, no float drift), all backends return
  **PTS-backed timestamps**, and `FrameTimecode` gained `.pts` and `.time_base`.
  **For a product whose entire value is timestamped deep links, this alone
  justifies 0.7 over 0.6.**
- New: `detect()` takes `backend=`; `min_scene_len`/`frame_margin` accept seconds
  (float) or timecode strings; `expand_scenes_to_bounds()`. 0.7.1 adds multi-file
  concat.

The docs still carry the standing warning: *"pin the scenedetect version in your
requirements to below the next major release: `scenedetect<0.8`."* Take it
literally.

**Undocumented bonus relevant to the handoff's "TransNetV2 only if gradual
transitions matter":** the 0.7.1 wheel already contains
`scenedetect/detectors/transnet_v2.py` — a `TransnetV2Detector` using a pretrained
ONNX shot-boundary network (CLI `detect-transnetv2`). It is **not exported** from
`scenedetect.detectors.__init__` and its `model_path` defaults to
`"tests/resources/transnetv2.onnx"`, so it is clearly experimental — but the
escape hatch is in-tree, not a separate integration:

```python
from scenedetect.detectors.transnet_v2 import TransnetV2Detector  # experimental, BYO model
```

### 4.2 Detector choice and parameters

Constructor signatures from 0.7.1 source:

```python
ContentDetector(
    threshold: float = 27.0,
    min_scene_len: TimecodeLike = 15,          # int frames | float secs | "1.5s"
    weights: ContentDetector.Components = Components(1.0, 1.0, 1.0, 0.0),
    luma_only: bool = False,
    kernel_size: int | None = None,            # odd, >=3; auto from resolution
    filter_mode: FlashFilter.Mode = FlashFilter.Mode.MERGE,
)

AdaptiveDetector(
    adaptive_threshold: float = 3.0,
    min_scene_len: TimecodeLike = 15,
    window_width: int = 2,
    min_content_val: float = 15.0,
    weights=..., luma_only=False, kernel_size=None,
)

ThresholdDetector(threshold=12, min_scene_len=15, fade_bias=0.0,
                  add_final_scene=False, method=Method.FLOOR, block_size=None)
HistogramDetector(threshold=0.05, bins=256, min_scene_len=15)
HashDetector(threshold=0.395, size=16, lowpass=2, min_scene_len=15)
```

**The scoring detail that drives everything.** From `_calculate_frame_score`, the
frame score is a **weight-normalised mean**:

```python
frame_score = sum(component * weight for component, weight in zip(components, weights)) \
              / sum(abs(w) for w in weights)
```

Components are mean per-pixel absolute deltas of **H, S, V(luma)** plus an optional
Canny **edge** map. Default weights `(1,1,1,0)`, so `threshold=27` means "the
average of hue, saturation and luma deltas exceeds 27."

**This is why defaults under-detect on screencasts.** IDE and slide content is
near-greyscale: `delta_hue` and `delta_sat` are ~0 regardless of what happens on
screen. A complete slide change might produce `delta_lum = 60`, but the score is
`(0 + 0 + 60)/3 = 20` — under 27, cut missed. The fix is not just lowering the
threshold; it is dropping the two dead channels and adding edges, which is the
signal that actually moves when a slide changes.

`FlashFilter.Mode`: `MERGE` (merge consecutive sub-`min_scene_len` cuts — good for
slide builds) vs `SUPPRESS`.

**Screencast / slides:**

```python
from scenedetect import ContentDetector
from scenedetect.detector import FlashFilter

SCREENCAST = ContentDetector(
    threshold=12.0,
    min_scene_len="1.0s",
    weights=ContentDetector.Components(
        delta_hue=0.0, delta_sat=0.0, delta_lum=1.0, delta_edges=1.0
    ),
    filter_mode=FlashFilter.Mode.MERGE,
)
```

Zeroing hue/sat removes two structurally-zero channels that were diluting the mean
3×. Adding `delta_edges` catches slide changes that preserve overall brightness
(white slide → white slide, different text) — pure luma is blind to those. With
weights summing to 2.0 and edge deltas running larger than luma deltas, ~12 is the
equivalent sensitivity to the default 27 on default weights. **Deliberately
over-detect here: the perceptual-hash dedup in §4.4 is far better at removing extra
keyframes than detection is at recovering missed slides.**

**Talking head:**

```python
TALKING_HEAD = AdaptiveDetector(
    adaptive_threshold=3.0,
    min_scene_len="2.0s",
    min_content_val=15.0,
    window_width=2,
)
```

`AdaptiveDetector` scores each frame against a rolling window average rather than a
fixed threshold, so slow lighting drift doesn't trip it while a genuine cut (which
spikes relative to neighbours) does. `min_content_val=15.0` suppresses a large
*ratio* over an essentially static baseline. `min_scene_len="2.0s"` kills
flash/blink false positives.

`ThresholdDetector` is for fades to/from black only — useful as a *segmenting* pass
on lecture recordings that fade between sections. `HistogramDetector` and
`HashDetector` are cheaper than `ContentDetector` and more robust to compression
noise; `HashDetector(threshold=0.395, size=16)` is worth trying on heavily
compressed screencasts where Canny edges are noisy.

**Performance knobs:**
- **`downscale`/`auto_downscale`.** `SceneManager` defaults to
  `auto_downscale=True`, targeting `DEFAULT_MIN_WIDTH = 256` px. Detection already
  runs on a heavily downscaled frame — **leave it on**. (Setting `.downscale` while
  `auto_downscale` is True logs a warning and is ignored.) It downscales
  *processing*, not *decoding*.
- **`frame_skip`** in `detect_scenes(..., frame_skip=N)`. Docs: "Not recommended
  except for extremely high framerate videos," and it's **incompatible with a
  `StatsManager`** (raises `ValueError`). For a 30fps screencast, `frame_skip=1`
  roughly halves detection time and costs ±1 frame of cut precision — acceptable
  when you sample 9 candidates per shot anyway. For talking heads, don't: you have
  so few cuts that missing one is expensive.

**Fixed-interval fallback** — both content types can legitimately produce one scene
for a whole video (unedited screen recording, single-take talking head). Detect and
subdivide:

```python
def with_interval_fallback(scenes, video, max_shot_secs=25.0, min_scenes=4):
    """Subdivide over-long shots so a 1-scene video still yields keyframes."""
    from scenedetect import FrameTimecode
    if not scenes:
        scenes = [(FrameTimecode(0, video.frame_rate), video.duration)]
    total = sum(e.seconds - s.seconds for s, e in scenes)
    if len(scenes) >= min_scenes and total / len(scenes) <= max_shot_secs:
        return scenes
    out = []
    for start, end in scenes:
        span = end.seconds - start.seconds
        n = max(1, int(span // max_shot_secs))
        step = span / n
        for i in range(n):
            a = FrameTimecode(start.seconds + i * step, video.frame_rate)
            b = FrameTimecode(start.seconds + (i + 1) * step, video.frame_rate) if i < n - 1 else end
            out.append((a, b))
    return out
```

25 s is reasonable for lecture content: dense enough that a slide can't hide
between samples, sparse enough that ~300 keyframes covers ~2 hours — which is
exactly the frame budget this pipeline assumes.

### 4.3 Full pipeline: detect → sample → sharpest → JPEG

Two passes, but only the **first** decodes the whole video. The second seeks to ~9
candidate positions per shot — for a 40-shot video, ~360 decoded frames instead of
100,000.

```python
"""Shot detection -> sharpest candidate frame per shot -> JPEG + timestamps.

pip install "scenedetect-headless[pyav]==0.7.1"
"""
from __future__ import annotations

import json
from dataclasses import dataclass, asdict
from pathlib import Path

import cv2
import numpy as np
from scenedetect import (
    AdaptiveDetector, ContentDetector, FrameTimecode, SceneManager, open_video,
)
from scenedetect.detector import FlashFilter

CANDIDATES_PER_SHOT = 9      # odd -> one lands mid-shot
EDGE_TRIM = 0.12             # skip first/last 12% of a shot (transitions, motion blur)
JPEG_QUALITY = 92


@dataclass
class Keyframe:
    shot_index: int
    frame_num: int
    seconds: float           # PTS-derived; use this for deep links
    timecode: str            # HH:MM:SS.nnn
    sharpness: float
    path: str


def make_detector(kind: str):
    if kind == "screencast":
        return ContentDetector(
            threshold=12.0,
            min_scene_len="1.0s",
            weights=ContentDetector.Components(
                delta_hue=0.0, delta_sat=0.0, delta_lum=1.0, delta_edges=1.0
            ),
            filter_mode=FlashFilter.Mode.MERGE,
        )
    return AdaptiveDetector(
        adaptive_threshold=3.0, min_scene_len="2.0s", min_content_val=15.0
    )


def sharpness(frame_bgr: np.ndarray, max_side: int = 720) -> float:
    """Variance of the Laplacian. Higher = sharper.

    Downscaled first: full-res Laplacian is dominated by sensor/compression noise
    and costs ~4x more. Comparable only *within* a shot -- absolute values are
    content-dependent (a text-heavy slide always outscores a blank one).
    """
    h, w = frame_bgr.shape[:2]
    scale = max_side / max(h, w)
    if scale < 1.0:
        frame_bgr = cv2.resize(frame_bgr, None, fx=scale, fy=scale,
                               interpolation=cv2.INTER_AREA)
    gray = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())


def extract_keyframes(video_path: str, out_dir: str,
                      kind: str = "screencast") -> list[Keyframe]:
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)

    # ---- Pass 1: detect. One full decode, threaded + auto-downscaled internally.
    video = open_video(video_path, backend="pyav")   # falls back to opencv automatically
    sm = SceneManager()                              # no StatsManager -> frame_skip allowed
    sm.add_detector(make_detector(kind))
    sm.auto_downscale = True
    sm.detect_scenes(video=video, show_progress=False)
    scenes = sm.get_scene_list(start_in_scene=True)  # never return an empty list
    scenes = with_interval_fallback(scenes, video)

    # ---- Pass 2: seek-sample. ~CANDIDATES_PER_SHOT decodes per shot.
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        raise RuntimeError(f"cannot open {video_path}")

    keyframes: list[Keyframe] = []
    try:
        for shot_i, (start, end) in enumerate(scenes):
            t0, t1 = start.seconds, end.seconds
            span = t1 - t0
            if span <= 0:
                continue
            lo, hi = t0 + span * EDGE_TRIM, t1 - span * EDGE_TRIM
            if hi <= lo:
                lo = hi = (t0 + t1) / 2.0
            targets = np.linspace(lo, hi, CANDIDATES_PER_SHOT)

            best = None
            for t in targets:
                cap.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
                ok, frame = cap.read()
                if not ok or frame is None:
                    continue
                # Trust the decoder's reported position, not our request: seeks land
                # on the nearest decodable frame, and on VFR they can be well off.
                actual_s = cap.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
                actual_n = int(cap.get(cv2.CAP_PROP_POS_FRAMES)) - 1
                s = sharpness(frame)
                if best is None or s > best[0]:
                    best = (s, frame, actual_s, actual_n)

            if best is None:
                continue
            score, frame, secs, fnum = best
            tc = FrameTimecode(secs, video.frame_rate)
            path = out / f"shot{shot_i:04d}_f{fnum:08d}.jpg"
            cv2.imwrite(str(path), frame,
                        [int(cv2.IMWRITE_JPEG_QUALITY), JPEG_QUALITY])
            keyframes.append(Keyframe(
                shot_index=shot_i, frame_num=fnum, seconds=round(secs, 3),
                timecode=tc.get_timecode(), sharpness=round(score, 2),
                path=str(path),
            ))
    finally:
        cap.release()

    (out / "keyframes.json").write_text(
        json.dumps([asdict(k) for k in keyframes], indent=2)
    )
    return keyframes
```

**Timestamp bookkeeping — the part deep links depend on.** Always carry
`FrameTimecode.seconds`, never `frame_num / fps`. In 0.7 `.seconds` is PTS-derived
and `frame_rate` is an exact `Fraction`, so 29.97 fps no longer accumulates drift
(at `30000/1001`, naive float arithmetic drifts ~1 frame per 1000 and visibly
desyncs an hour in — which is exactly the length of content this corpus targets).
For the YouTube deep link, `int(kf.seconds)` → `?t=1234`.

**Single-decode variant** if profiling shows the seek pass hurting: drive the
detector yourself over one sequential read with a per-shot sharpness ring buffer,
using the public ABC (`det.process_frame(timecode, frame_img) -> list[FrameTimecode]`,
then `det.post_process(last_timecode)`). Seeks are cheap relative to a second full
decode, so the two-pass version is the right default.

**Sharpest-frame caveat worth internalising:** Laplacian variance measures
*high-frequency content*, not focus. Within a shot that's the right proxy (blurred
frames are the motion-blurred ones). Across a screencast slide build, the
"sharpest" frame is the one with the **most text on screen** — precisely the frame
you want to OCR. Happy accident; **don't compare sharpness across shots.**

`scenedetect`'s own `save_images(scene_list, video, num_images=3, frame_margin="0.1s",
image_extension="jpg", encoder_param=95, threading=True)` works and is threaded —
but it picks frames by *position*, not sharpness, so it doesn't do what's needed.

### 4.4 Perceptual-hash dedup

**`ImageHash` remains the right answer in 2026.** Current: **4.3.2, 2025-02-01**.
Pure Python over Pillow + numpy + scipy — no compiled extension, so **numpy 2.x and
Pillow 12.3.0 are both fine** (no numpy-2 or Pillow reports in the tracker). Low
maintenance (last repo activity Nov 2025) but the algorithms are frozen mathematics.

Alternatives: **`imagededup` 0.3.3.post2** pulls **torch + torchvision +
scikit-learn + matplotlib** — several GB for what is a DCT and a popcount. Not
worth it. **`pdqhash` 0.2.8** wraps Facebook's PDQ; more robust to crops/rescales,
irrelevant when inputs are frames from one video at one resolution. Nothing has
displaced ImageHash.

```bash
uv add "ImageHash==4.3.2"
```

**Use `hash_size=16`, not the default 8.** `phash(hash_size=8)` produces a 64-bit
hash from the top-left 8×8 of a 32×32 DCT — gross layout only. Two consecutive
slides in a deck share the same template, title bar and colour scheme and differ
only in body text: at 64 bits their pHashes are frequently **identical**, and you'd
silently drop distinct slides. At `hash_size=16` (256 bits from a 64×64 DCT) you
retain enough mid-frequency structure to separate them.

**Threshold: ~24 of 256 bits (9.4%)** for "same screen, trivially different" — it
absorbs JPEG noise, a blinking cursor, a moving pointer and a clock tick, while
separating a genuine slide change. **Calibrate rather than trust it**: dump the
pairwise distance matrix for two or three representative videos and look for the
bimodal gap. Screencasts with static chrome around changing content push the
threshold *down* (chrome dominates the hash); full-bleed video pushes it *up*.

If pHash proves too layout-dominated for slide builds, **`dhash(hash_size=16)`** is
a better fit for screen content — it encodes horizontal gradients, so appearing
text lines move it more than they move a DCT — and it's ~5× faster (no DCT).
Consider requiring `phash` **and** `dhash` agreement before calling a duplicate.

```python
"""Perceptual-hash dedup over a keyframe set. NumPy >= 2.0."""
from __future__ import annotations

import numpy as np
import imagehash
from PIL import Image

HASH_SIZE = 16
THRESHOLD = 24


def hash_bits(paths: list[str], hash_size: int = HASH_SIZE) -> np.ndarray:
    """(N, hash_size**2 // 8) uint8 array of packed hash bits."""
    rows = []
    for p in paths:
        with Image.open(p) as im:
            h = imagehash.phash(im, hash_size=hash_size, highfreq_factor=4)
        rows.append(np.packbits(h.hash.flatten()))
    return np.vstack(rows).astype(np.uint8)


def hamming_matrix(bits: np.ndarray) -> np.ndarray:
    """Full (N, N) pairwise Hamming distance via np.bitwise_count (NumPy >= 2.0)."""
    xor = bits[:, None, :] ^ bits[None, :, :]          # (N, N, nbytes)
    return np.bitwise_count(xor).sum(axis=2).astype(np.int32)


def dedup(paths: list[str], threshold: int = THRESHOLD):
    """Greedy first-wins clustering in timeline order.

    Returns (kept_indices, {kept_index: [dropped_indices]}).
    """
    bits = hash_bits(paths)
    dist = hamming_matrix(bits)
    n = len(paths)
    kept, clusters, taken = [], {}, np.zeros(n, dtype=bool)
    for i in range(n):
        if taken[i]:
            continue
        dupes = np.flatnonzero((dist[i] <= threshold) & ~taken)
        dupes = dupes[dupes != i]
        taken[i] = True
        taken[dupes] = True
        kept.append(i)
        clusters[i] = dupes.tolist()
    return kept, clusters
```

`np.bitwise_count` is a **NumPy 2.0+** ufunc (current 2.5.1) — the clean popcount,
removing the old `np.unpackbits`-and-sum dance. For numpy 1.x, fall back to
`np.unpackbits(xor, axis=-1).sum(-1)`.

Memory: the `(N, N, nbytes)` XOR tensor is `N² × 32` bytes — 2.9 MB at N=300, fine;
800 MB at N=5000, so chunk rows above ~2000.

**Keeping the first of each cluster in timeline order** matters for this product:
it preserves the *earliest* occurrence of each screen, which is the timestamp a
user actually wants to jump to.

**Corpus-wide dedup:** within one video the full matrix is correct and simplest.
Across a corpus — up to ~100k hashes, still just numpy (chunk the query set in
blocks of ~1000 rows; a 256-bit XOR+popcount over 100k rows is milliseconds).
Beyond that, **`faiss.IndexBinaryFlat(256)`**. **Not `pybktree`**: last release
2017, and BK-trees prune poorly at 256 bits with a threshold as loose as 24 —
you end up visiting most of the tree.

**The dedup that actually works best here:** you are OCR-ing every frame anyway, so
run the cheap hash pass **before** OCR to cut the OCR bill (dropping 40% of 300
frames saves minutes — see §3.5 throughput), then do a **second dedup on the OCR
text** (normalised token-set similarity, or
`difflib.SequenceMatcher.ratio() > 0.95`) to catch slides that are visually
distinct but semantically identical. Text-space dedup is far more reliable than
pixel-space dedup for slide content, and it's free once you have the OCR output.
This also feeds directly into the handoff's "OCR-vs-transcript dedup (keep longer
text)" rule in the search tool.

### 4.5 Gotchas

- **Backends.** `AVAILABLE_BACKENDS`: `"opencv"` → `VideoStreamCv2` (default),
  `"pyav"` → `VideoStreamAv`, `"moviepy"` → `VideoStreamMoviePy`. `open_video()`
  **silently falls back to OpenCV** if the chosen backend is unavailable or fails —
  it only logs a warning, so assert `type(video).__name__` if you care.
  - **Use PyAV for anything VFR or with unusual containers.** True PTS, and 0.7.1
    added corrupt-frame skipping (tolerates up to 8 consecutive decode failures)
    plus normalisation of delayed-start files. `VideoStreamCv2` is faster on clean
    CFR H.264 with zero extra deps, but its frame-number arithmetic assumes CFR.
  - `VideoStream.decode_failures` (new in 0.7) counts skipped frames. **Log it** —
    a nonzero value explains otherwise-inexplicable timestamp drift, which for this
    product means wrong deep links.
- **VFR.** Screen recordings from OBS, Camtasia and browser capture tools are
  **variable frame rate** — precisely the case 0.7 was built for. Use
  `backend="pyav"`, use `.seconds`/`.pts`, never `frame_num / fps`, and treat
  `cv2.CAP_PROP_POS_FRAMES` as advisory. The snippet above seeks by `POS_MSEC` and
  re-reads the decoder's actual reported position for exactly this reason.
- **ffmpeg.** PySceneDetect needs **no** external ffmpeg for detection or
  `save_images` — that's OpenCV/PyAV. Only `split_video_ffmpeg()` and the
  `save-qp`/`save-edl`/`save-fcp` outputs need the binary; guard with
  `is_ffmpeg_available()`. (You need ffmpeg in the image anyway for yt-dlp.)
- **`libGL` and the opencv variant conflict.** Install `scenedetect-headless`, not
  `scenedetect` — **but** RapidOCR drags in plain `opencv-python` regardless
  (§3.6), so a combined image needs `libgl1` anyway. `opencv-python` and
  `opencv-python-headless` install the same `cv2` module and **silently clobber
  each other**. In the worker image, pick **one** variant explicitly and pin it, or
  you get a nondeterministic `cv2` depending on pip resolution order. Given
  RapidOCR forces the GUI build, the simplest correct choice is plain
  `scenedetect` + `libgl1`.
- **Memory on long videos.** `SceneManager` streams (frame queue capped at
  `MAX_FRAME_QUEUE_LENGTH = 4`), so RAM is flat regardless of duration — **unless
  you attach a `StatsManager`**, which accumulates per-frame metrics for the whole
  video in memory. Use one for threshold calibration on a sample, never in
  production. (It also forces `frame_skip=0`.)
- **Downscaled decode.** `auto_downscale` downscales *processing*, not *decoding*.
  If detection is the bottleneck on 4K, the fix is `frame_skip=1` or a proxy file
  via `ffmpeg -vf scale=640:-1` (timestamps are identical; only resolution changes).
  Capping the download at 1080p (§5.3) already avoids most of this.
- **`start_in_scene=True`** on `get_scene_list()`. Without it, a video with no
  detected cuts returns an **empty list**, not one whole-video scene — the single
  most common way this pipeline silently produces zero keyframes.
- **`min_scene_len` unit ambiguity.** `15` (int) = 15 **frames**; `15.0` (float) =
  15 **seconds**; `"15"` = frames but `"15s"` = seconds. A stray `.0` is a 900×
  error. **Always use the explicit string form** (`"1.5s"`).
- **`kernel_size`** is auto-derived from resolution (`4 + round(sqrt(w*h)/192)`,
  forced odd) and only matters when `delta_edges > 0`. Edge detection is the
  expensive part of `ContentDetector` — it's skipped entirely when
  `delta_edges == 0.0` *and* no `StatsManager` is attached. So attaching a
  `StatsManager` **forces edge computation even at zero weight**, silently slowing
  detection. Since the screencast preset turns edges on deliberately, budget for it.

---

## 5. yt-dlp as a library

**Verified on this box (2026-08-08) against installed `yt-dlp 2026.07.04` and live
YouTube.** Everything marked "verified" was executed, not read off a doc.

### 5.0 Version, install, and the new hard dependency: a JS runtime

| Thing | Value | Source |
|---|---|---|
| Latest stable | `2026.7.4` (2026-07-04) | https://pypi.org/pypi/yt-dlp/json |
| `requires_python` | `>=3.10` | ditto |
| Extras | `default`, `curl-cffi`, `deno`, `secretstorage`, `pin-*` | ditto |
| **`yt-dlp-ejs==0.8.0` is in the `default` extra** | not optional any more | `requires_dist` |

The big 2026 change: **YouTube extraction now needs a JavaScript runtime.** yt-dlp
ships `yt-dlp-ejs` in the `default` extra and runs it under Deno (Node/QuickJS/Bun
are opt-in via `--js-runtimes`). Without one you get, verbatim from a run on this
box:

```
WARNING: [youtube] No supported JavaScript runtime could be found. Only deno is
enabled by default; ... YouTube extraction without a JS runtime has been deprecated,
and some formats may be missing.
```

and the `web` client is silently dropped from the default client list (README: "If
no JavaScript runtime/engine is available, then `web` is omitted").

**The clean uv/Docker answer** — there is a PyPI redistribution of the Deno binary,
and yt-dlp declares it as an extra (`deno>=2.6.6; extra == "deno"`), so Deno never
has to be installed into the image separately:

```toml
# mcp/pyproject.toml
dependencies = ["yt-dlp[default,deno,curl-cffi]>=2026.7.4"]
```

`deno` on PyPI is at 2.9.5, "Python redistribution of Deno binaries"
(https://pypi.org/project/deno/). Minimum Deno for yt-dlp EJS is 2.0.0
(https://github.com/yt-dlp/yt-dlp/wiki/EJS).

Harden it for a server:
- `--extractor-args "youtube-ejs:jitless=true"` runs Deno JIT-less. Slower, but you
  are executing YouTube's JS on a box that also hosts the MCP server and
  llama-server; cheap insurance.
- Leave `--remote-components` **off** (the default). With `yt-dlp-ejs` installed
  from PyPI, yt-dlp never fetches JS from npm/GitHub at runtime — exactly what you
  want in a sealed container.
- `ffmpeg` + `ffprobe` binaries are still required for merging and audio extraction.

Default player clients are now `visionos,android_vr,web`. `visionos`/`android_vr`
need no PO token, which is why the live runs below succeeded with no token provider
configured at all.

### 5.1 (a) Metadata + chapters + subtitles without downloading

```python
import yt_dlp

BASE_OPTS = {
    "quiet": True,
    "no_warnings": True,
    "noplaylist": True,
    # Do not enumerate ~200 auto-translated caption languages:
    "extractor_args": {"youtube": {"skip": ["translated_subs"]}},
}

def probe(url: str) -> dict:
    with yt_dlp.YoutubeDL(BASE_OPTS) as ydl:
        info = ydl.extract_info(url, download=False)
        return ydl.sanitize_info(info)   # makes it JSON-serialisable
```

`extract_info(url, download=False)` is the documented no-download path, and
`sanitize_info` is required if you persist it — yt-dlp explicitly does **not**
guarantee the raw return value is JSON-serialisable (README §EMBEDDING YT-DLP).

Fields verified present on a real YouTube lecture (`zduSFxRajkE`, 8014 s):

| Field | Shape / example |
|---|---|
| `id`, `title`, `description`, `duration` (s), `language` (`"en"`) | scalars |
| `channel`, `channel_id`, `channel_url`, `uploader`, `uploader_id` (`"@AndrejKarpathy"`) | scalars |
| `upload_date` (`"YYYYMMDD"` str), `timestamp` (epoch int), `release_timestamp` | scalars |
| `tags`, `categories` | list[str] |
| `view_count`, `availability` (`"public"`), `live_status` (`"not_live"`) | scalars |
| `chapters` | `[{"start_time": 0, "title": "...", "end_time": 350}, …]` — **24, real** |
| `subtitles` | `{lang: [{"ext": "json3"|"srv1"|"srv3"|"ttml"|"srt"|"vtt", "url": …}, …]}` |
| `automatic_captions` | same shape; ASR track under the video's own language code |
| `heatmap` | `[{"start_time": 0.0, "end_time": 80.15, "value": 0.237}, …]` — 100 buckets |

Three things worth designing around:

- **`chapters` is free structure.** Store it; it makes `get-segment-context`
  ("which chapter is t in") and `video-summary` outlines nearly free, and gives the
  search tool a human-authored section label to cite alongside the timestamp.
- **`heatmap`** (YouTube "most replayed", 100 evenly-spaced buckets, 0..1) is a
  *free popularity prior over video time*. Cheap uses: bias the keyframe budget
  toward hot buckets; break relevance ties; surface "the bit everyone rewinds" in
  `video-summary`. **Nothing in the landscape survey does this.**
- `live_status` and `availability` are the guard rails for the subscription cron —
  skip `is_live`/`is_upcoming`, treat `availability != "public"` as a reindex
  signal.

`automatic_captions` normally lists ~200 languages (every machine translation).
`youtube:skip=translated_subs` collapses that to the real ASR track; without it the
info dict is enormous.

### 5.2 (b) Audio-only download for STT

whisperX resamples to 16 kHz mono anyway, so bitrate above ~64 kbps buys nothing.
Have yt-dlp hand you a 16 kHz mono WAV directly and skip a decode pass:

```python
AUDIO_OPTS = BASE_OPTS | {
    "format": "bestaudio[abr<=80]/bestaudio/best",
    "outtmpl": {"default": "%(id)s.%(ext)s"},
    "paths": {"home": str(audio_dir), "temp": str(tmp_dir)},
    "postprocessors": [{"key": "FFmpegExtractAudio", "preferredcodec": "wav"}],
    # ExtractAudio's ffmpeg call gets these -> 16 kHz mono, whisper-native
    "postprocessor_args": {"extractaudio": ["-ac", "1", "-ar", "16000"]},
}
```

`--postprocessor-args NAME:ARGS` documents `ExtractAudio` as a supported PP name;
the Python key is the lowercased PP name. `FFmpegExtractAudio` + `preferredcodec`
is the README's own "Extract audio" example.

Real sizes for that 2h13m lecture (verified from the format list): opus 251 =
100 MB, m4a 140 = 130 MB, m4a 139 (48 kbps) = 49 MB. The 16 kHz mono WAV you
convert to is ~256 MB for the same duration — write it to `paths["temp"]` and
delete after transcription, or use `preferredcodec: "flac"` (~half) to keep it.
Note the whisperX host-RAM gotcha in §1.7(d) — `load_audio()` buffers the whole
thing, so ~3–4× the PCM size in RAM.

### 5.3 (c) Video download for frame extraction — resolution recommendation

**Recommendation: cap at 1080p H.264. Not `bestvideo`, and not 720p.**

Real numbers from the same 2h13m video:

| Selector | height | codec | size |
|---|---|---|---|
| `134` | 360 | avc1 | 45 MB |
| `135` | 480 | avc1 | 68 MB |
| `136` | 720 | avc1 | **104 MB** |
| `137` | 1080 | avc1 | **270 MB** |
| `399` | 1080 | av01 | 232 MB |
| `313` | 2160 | vp9 | 2.9 GB |

- **`bestvideo` is wrong**: 4K VP9 at 2.9 GB for one video, 99.9% of those pixels
  discarded. Hard no.
- **720p is wrong for the OCR half.** The embedding half doesn't care — SigLIP 2
  NaFlex at 256 patches sees roughly a 256×256-pixel budget (§2.3), so anything
  ≥480p is oversupply. But the OCR leg exists to read *code and terminal text in
  screencasts*, and 1080p content downscaled to 720p puts a 14 px editor font under
  10 px, where PP-OCR recall falls off. Slides and talking heads survive 720p fine;
  screencasts don't, and screencasts are the content class this corpus exists for.
- **H.264 over AV1** despite AV1 being smaller: you seek and decode a few hundred
  frames per video with OpenCV, and CPU AV1 decode is materially slower with no
  benefit here.

```python
VIDEO_OPTS = BASE_OPTS | {
    # H.264 <=1080p video only; no audio track (STT has its own copy)
    "format": "bv*[vcodec^=avc1][height<=1080]/bv*[height<=1080]/b[height<=1080]",
    "merge_output_format": "mp4",
    "outtmpl": {"default": "%(id)s.%(ext)s"},
    "paths": {"home": str(video_dir), "temp": str(tmp_dir)},
}
```

Make the cap a config value (`INDEX_MAX_HEIGHT`, default 1080) and drop to 720 for
videos whose OCR pass returns almost nothing — fine on talking-head content, halves
disk.

**The video file is scratch.** Once keyframes are picked and OCR'd you keep the
JPEGs and delete the mp4; a 300-frame JPEG set at quality 92 is ~15–30 MB versus
270 MB. Storing the mp4 is also the only part of this pipeline that looks like
redistribution rather than personal indexing — deleting it is the better default
for a project you intend to publish.

### 5.4 (d) Subtitle priority — and when to skip whisperX entirely

**The finding that most affects the pipeline design, verified empirically.**

YouTube's **auto-caption `json3` track carries word-level timings.** Fetched live
from `automatic_captions["en"][ext="json3"]` for `zduSFxRajkE`:

```json
{"tStartMs": 4080, "dDurationMs": 4200, "wWinId": 1, "segs": [
   {"utf8": "large",              "acAsrConf": 0},
   {"utf8": " language", "tOffsetMs": 239,  "acAsrConf": 0},
   {"utf8": " models",   "tOffsetMs": 520,  "acAsrConf": 0},
   {"utf8": " now",      "tOffsetMs": 1480, "acAsrConf": 0},
   {"utf8": " you",      "tOffsetMs": 1840, "acAsrConf": 0},
   {"utf8": " see",      "tOffsetMs": 2000, "acAsrConf": 0},
   {"utf8": " here",     "tOffsetMs": 2239, "acAsrConf": 0}]}
```

Word start = `tStartMs + tOffsetMs`. That is exactly the deep-link precision the
handoff bought whisperX for — free, one HTTP request, no GPU.

Equally verified: **manual subtitles do not have it.** The same `json3` request
against a video with human-authored English subs (`jNQXAC9IVRw`) returns one `utf8`
blob per cue with no `tOffsetMs` — cue-level only, ~2–5 s granularity.

| Source | Word timings | Text quality | Cost |
|---|---|---|---|
| Manual subs (`subtitles`) | ✗ cue-level (~2–5 s) | best — real punctuation, casing, correct proper nouns | 1 request |
| Auto-caps (`automatic_captions`, json3) | ✓ **per word** | ASR-grade, **no punctuation, no casing**, weak on jargon/names | 1 request |
| whisperX large-v3 | ✓ per word (forced alignment) | best ASR available, punctuated, cased, diarizable | GPU minutes |

**Recommended policy** — make it a config enum, `STT_POLICY=auto|always|never`:

1. **`always` for the corpus you care about.** whisperX's transcript is the one
   that makes *search* work: punctuation and casing are what let FTS5 and the dense
   embedder both behave, and "no capital letters anywhere" quietly wrecks
   proper-noun recall, which is most of what you search a talk corpus for.
   Auto-caps also have no diarization and no `[MUSIC]`/speaker structure.
2. **`auto` = the pragmatic default.** Run whisperX, but skip it when
   (a) a **manual** sub exists in a wanted language — take it for the text and
   accept cue-level links, or use it as whisperX's `initial_prompt`; or (b) the
   video is long, the GPU is leased out, and an auto-caption json3 exists — index
   it immediately, mark `transcript_source='youtube_asr'`, and let a background
   pass upgrade it later. **This is the right answer for a corpus that grows by
   cron**: searchable-with-deep-links in seconds, quality catches up.
3. **`never`** for no-GPU self-hosters pointing `WORKER_URL` at nothing —
   auto-caps alone make the whole product work, degraded. A genuinely good README
   story, and it means vidtheque has a zero-GPU install path.

Store `transcript_source` per video and expose it in `data_status` on
`video-summary` — the handoff already has a `data_status` self-diagnosis slot, and
"this video's transcript is YouTube ASR, not whisper" is exactly what the client
model should be told before it quotes it.

Fetching subs without touching the download machinery:

```python
SUB_OPTS = BASE_OPTS | {
    "skip_download": True,
    "writesubtitles": True,          # manual
    "writeautomaticsub": True,       # ASR
    "subtitleslangs": ["en", "en-orig"],
    "subtitlesformat": "json3",
    "sleep_interval_subtitles": 5,   # see 5.5 — you WILL get 429 without this
    "outtmpl": {"default": "%(id)s.%(ext)s"},
    "paths": {"home": str(subs_dir)},
}
```

**Option-name gotcha:** the CLI is `--sleep-requests`, the **Python key is
`sleep_interval_requests`** (`yt_dlp/YoutubeDL.py` docstring ~line 444). Same for
`--sleep-subtitles` → `sleep_interval_subtitles`. `sleep_interval` /
`max_sleep_interval` keep their names. Use `devscripts/cli_to_api.py` if in doubt.

You can also skip file-writing entirely and `urlopen` the `url` from the info
dict's `subtitles`/`automatic_captions` entry — that is what was done to verify the
above. One request, no temp files. Apply your own throttle if you do.

**Dedup gotcha for auto-caps:** the ASR json3 stream is a *rolling window* — it
emits `aAppend` events and repeats text as the caption line grows. Filter to events
that have `segs`, drop segs whose `utf8` is just `"\n"`, and rebuild from
`tStartMs + tOffsetMs` per word rather than concatenating event text, or you will
index every sentence two or three times.

### 5.5 (e) Rate limiting and abuse posture on a residential box

**Empirically, the limit is tighter than you'd guess.** On this box, a single
`yt-dlp --skip-download --write-subs --write-auto-subs` invocation on **one video**
fetched two subtitle tracks fine and got `HTTP Error 429: Too Many Requests` on the
**third** — three timedtext requests, no download, cold IP. The subtitle endpoint
is rate-limited far more aggressively than the player endpoint.

Consequences:

- **Never fetch more than one or two subtitle tracks per video**, and never
  speculatively enumerate languages. `youtube:skip=translated_subs` + an explicit
  `subtitleslangs` of at most 2 entries.
- **Adopt yt-dlp's own `-t sleep` preset as the floor** for anything automated. It
  expands to (README §Preset Aliases):

  ```
  --sleep-subtitles 5 --sleep-requests 0.75 --sleep-interval 10 --max-sleep-interval 20
  ```

  in API terms:

  ```python
  THROTTLE = {
      "sleep_interval_subtitles": 5,
      "sleep_interval_requests": 0.75,
      "sleep_interval": 10,
      "max_sleep_interval": 20,
  }
  ```

  For a subscription cron indexing a channel's new uploads, go further: serialise
  through the existing job queue (you already need that for the GPU lease) and put
  a 30–120 s randomised gap **between videos**, not just between downloads.
  Indexing is not latency-sensitive; a channel backfill at 1 video/minute overnight
  is invisible.
- **Retries**: `--retry-sleep extractor:exp=5:120` / `--extractor-retries`, and
  treat a 429 as a *job-level* backoff — requeue the video with exponential delay
  and surface it via `job_status`. Hammering through a 429 is how a residential IP
  earns a longer block.
- **PO tokens, 2026 state**: still real, but *not* something to solve on day one.
  The default client set (`visionos,android_vr,web`) includes two clients that need
  no PO token, which is why both live runs worked with zero token configuration.
  The [wiki](https://github.com/yt-dlp/yt-dlp/wiki/PO-Token-Guide) documents
  `gvs`/`player`/`subs` token contexts and notes tokens are now **bound to the
  video ID**, so manual extraction is dead — if you need them, the answer is a
  provider plugin (`bgutil-ytdlp-pot-provider`, or `yt-dlp-getpot-wpc`). yt-dlp's
  knobs: `youtube:fetch_pot=auto|always|never` (default `auto`),
  `youtube:pot_trace=true` to debug, `youtube:formats=missing_pot` to *see* the
  formats you're being denied. **Design implication:** put `player_client` and any
  PO-token provider behind env vars in the MCP image from the start, and log which
  client actually served each video. When YouTube tightens, that's a config change,
  not a rewrite.
- **Cookies**: `cookiesfrombrowser`/`cookiefile` unlocks members-only and age-gated
  content and changes the default client set to `tv_downgraded,web`. **Don't.**
  Authenticated automated downloading is the fastest route to an account action,
  and vidtheque is a personal-corpus tool, not an archiver. If a self-hoster wants
  it, expose `YTDLP_COOKIEFILE` and document the risk in one sentence.
- **Impersonation**: both live runs emitted `WARNING: The extractor specified to
  use impersonation for this download, but no impersonate target is available`.
  Adding the `curl-cffi` extra silences it and makes the TLS fingerprint look like
  a browser, reducing soft-blocking. Cheap; take it.

### 5.6 Other yt-dlp notes

- Everything above is site-agnostic except the YouTube specifics — the corpus
  framing ("later: any yt-dlp site") survives, because
  `chapters`/`subtitles`/`automatic_captions`/`duration` are generic info-dict
  fields. Only `heatmap` and json3 word timings are YouTube-only; make them
  **optional enrichments, not schema requirements.**
- `extract_flat: "in_playlist"` + `playlist_items` is how channel/playlist
  subscriptions enumerate new uploads cheaply (one request, no per-video player
  calls). Combine with `daterange` or a `match_filter` closure to stop at
  already-indexed IDs.
- `download_ranges` / `--download-sections` exists if you ever want to index only
  the chapters a user asked about without pulling the whole file.
- yt-dlp releases are frequent and YouTube-breaking changes land in them. Pin the
  version as a Docker build arg but **plan on bumping monthly** — a stale yt-dlp is
  the single most likely cause of "indexing suddenly stopped working" in a
  self-hosted deployment. Worth a `data_status`-style health check: if extraction
  fails on a known-good video, say "your yt-dlp is probably out of date" rather
  than a stack trace.

---

## 6. Text embeddings: bge-m3 vs alternatives

[`BAAI/bge-m3`](https://huggingface.co/BAAI/bge-m3) (MIT, 568M, 1024-d, 8192
tokens, 100+ languages, and uniquely **dense + sparse (lexical) + multi-vector
ColBERT** heads from one forward pass) is no longer the accuracy leader but is
still a defensible default, and for transcript search its *hybrid* capability is
worth more than the leaderboard gap. On MMTEB it sits around **59.6**, versus
**64.33** for [`Qwen/Qwen3-Embedding-0.6B`](https://huggingface.co/Qwen/Qwen3-Embedding-0.6B)
(Apache-2.0, 0.6B, MRL 32–1024 d, 32k context, 100+ languages, dense-only, needs
`Instruct: …\nQuery: …` prefixes on queries), **61.15** for
[`google/embeddinggemma-300m`](https://huggingface.co/google/embeddinggemma-300m)
(768-d truncatable to 512/256/128, only **2048** max tokens, mandatory
`task: search result | query:` prefixes, **Gemma custom license**), and **67.7** for
[`jinaai/jina-embeddings-v5-text-small`](https://huggingface.co/jinaai/jina-embeddings-v5-text-small)
(Feb 2026, 677M, 1024-d MRL, 32k, 119 languages) — which Jina's
[own writeup](https://jina.ai/news/jina-embeddings-v5-text-distilling-4b-quality-into-sub-1b-multilingual-embeddings/)
reports as the strongest sub-1B model on MTEB Multilingual v2 as of 2026-02-21, but
which is **CC-BY-NC-4.0** and therefore off the table for a self-hostable product.
No clearly-better sub-1B open release appeared in Jun–Jul 2026, and **no bge-m4 or
bge-m3 successor exists** — BAAI's newer multilingual model is
`bge-multilingual-gemma2` at 9B, a different weight class.

**Recommendation: switch the dense index to `Qwen3-Embedding-0.6B`** — Apache-2.0,
+4.7 MMTEB points at the same size, 32k context handles whole transcripts, and MRL
lets you store 256-d and cut index size 4× (which matters directly for the
handoff's sqlite-vec brute-force KNN caveat) — **and keep `bge-m3` alongside purely
for its sparse head** if you want lexical hybrid on proper nouns, error codes and
CLI flags, which for terminal/screencast transcripts is a real win no dense-only
model replaces. If you want exactly one model and one index, `bge-m3` remains a
legitimate keep-it choice; if you want one model and best accuracy, take
Qwen3-Embedding-0.6B. **Avoid EmbeddingGemma here** — 2048 tokens is too short for
transcripts and the Gemma license is a needless constraint next to two Apache/MIT
options.

Two operational notes for whichever you pick, both consistent with the handoff's
"same model at index and query time, pinned in one shared config value":
Qwen3-Embedding requires an **instruction prefix on queries but not on documents**
— get that asymmetry wrong and recall degrades silently, so it belongs in the
shared config next to the model id, not in calling code. And if you adopt MRL
truncation, the truncation length is part of the index format: pin it in the same
config value and refuse to serve a query embedded at a different width.

Leaderboards/pages checked: [MTEB leaderboard](https://huggingface.co/spaces/mteb/leaderboard),
[Qwen3-Embedding blog](https://qwenlm.github.io/blog/qwen3-embedding/) /
[arXiv 2506.05176](https://arxiv.org/pdf/2506.05176),
[MMTEB arXiv 2502.13595](https://arxiv.org/html/2502.13595v1), plus the four model
cards linked above.

---

## 7. Consolidated dependency sketch

Not a lockfile — a starting point for `worker/pyproject.toml` and
`mcp/pyproject.toml`. Note the split: **OCR and keyframe extraction need no GPU**
(§3.5), so they can live in the MCP image or a CPU worker, and only whisperX +
SigLIP 2 + the text embedder contend for the llama.cpp lease.

```toml
# ---------------- worker/ (GPU) ----------------
[project]
requires-python = ">=3.12,<3.14"
dependencies = [
    "whisperx>=3.8.6",
    "torch~=2.8.0", "torchaudio~=2.8.0", "torchvision~=0.23.0",  # direct: see 1.2
    "transformers>=5.14",
    "pillow>=11",
    "numpy>=2.1",
]

[tool.uv.sources]
torch       = { index = "pytorch-cu128" }
torchaudio  = { index = "pytorch-cu128" }
torchvision = { index = "pytorch-cu128" }

[[tool.uv.index]]
name = "pytorch-cu128"
url = "https://download.pytorch.org/whl/cu128"
explicit = true

# ---------------- mcp/ or a CPU worker ----------------
# dependencies = [
#   "yt-dlp[default,deno,curl-cffi]>=2026.7.4",
#   "rapidocr==3.9.2", "onnxruntime==1.28.0",
#   "scenedetect[pyav]==0.7.1",     # plain, NOT headless — RapidOCR forces opencv-python
#   "ImageHash==4.3.2",
# ]
```

Build-time warm-up that must happen for an offline image:

```dockerfile
RUN python -m nltk.downloader punkt_tab          # whisperX align() downloads this at RUNTIME
RUN python -c "import whisperx; whisperx.load_align_model('en','cpu')"
RUN python -c "from transformers import AutoModel, AutoProcessor; \
    AutoModel.from_pretrained('google/siglip2-so400m-patch16-naflex'); \
    AutoProcessor.from_pretrained('google/siglip2-so400m-patch16-naflex')"
# diarization: pre-fetch with HF_TOKEN at build, then run with HF_HUB_OFFLINE=1
# RapidOCR: default models are already in the wheel (3.3) — copy them to /opt/models
ENV HF_HUB_OFFLINE=1
```

---

## Method note

Where docs and reality disagreed, reality won. Specifically:

- `rapidocr-3.9.2-py3-none-any.whl` and `scenedetect-0.7.1-py3-none-any.whl` were
  downloaded and their source read. Published claims that turned out wrong:
  RapidOCR **bundles** its default models (search results still say it downloads
  them), and `ContentDetector`'s scoring is a weight-normalised **mean** — the
  mechanical reason the default threshold under-detects on greyscale screencasts.
  Two findings not in any doc: the detector's `limit_type: "min"` default never
  downscales, and `scenedetect-headless` exists as a separate distribution.
- The transformers `model_doc/siglip2` page documents the wrong tensor shapes for
  NaFlex (copied from plain SigLIP); the real shapes came from
  `image_processing_siglip2.py`. The HF SigLIP 2 blog's variant table lists two
  NaFlex checkpoints that do not exist.
- A real `uv lock` was run to confirm the whisperX resolution and to catch the
  `[tool.uv.sources]`-only-binds-direct-dependencies trap.
- yt-dlp was run against live YouTube from this box to confirm the info-dict
  fields, the format/size table, the json3 word-timing structure (auto-caps yes,
  manual subs no), and the 429 behaviour.

Nothing was installed into any project; no dependency was added anywhere.
