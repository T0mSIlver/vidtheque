# Keyframe stage: what is it actually decoding? (2026-08-08)

The keyframe stage dominated the first real indexing run — roughly ten minutes
of pegged CPU for a 21-minute 1080p talk, with every other stage a rounding
error next to it. The hypothesis under test: the stage pays for pixels nothing
ever looks at, because PySceneDetect's detection math is already downscaled. If
so, detecting on a small second copy of the same upload should be several times
faster *for identical results*.

The speedup is real (4x on the detection pass). The identical results are not.
This file is the measurement and the decision.

Driver: `bench/keyframe_decode.py`; raw envelopes in `bench/results/raw/`.
Box: the 10-core CPU of Tom's Proxmox host (its RTX 3090 idle — nothing in this
stage touches the GPU), with the dev stack's own mcp and worker processes
running alongside, which is where the run-to-run variance below comes from.

| | |
|---|---|
| videos | `youtu.be/RjfbvDXpFls` (18:25, 1104.74 s) and `youtu.be/Sir59K8ZDPU` (21:18, 1277.96 s) |
| full-res | yt-dlp format **299** both — `avc1` High, 1920x1080, **50 fps** and **59.94 fps**, 153 MiB / 60 MiB |
| detection stream | yt-dlp format **134** both — `avc1` Main, 640x360, **25 fps** and **29.97 fps**, 10.4 MiB / 5.6 MiB |
| detector | the shipped `screencast` preset (luma + Canny edges, threshold 12, MERGE) |
| scenedetect | 0.7.1, PyAV backend, `av` 18.0.0, OpenCV 5.0.0 |

**Both sides of every comparison are H.264.** That is deliberate: at 360p
YouTube also offers VP9 (`243`) and AV1 (`396`), and both decode slower on CPU
than AVC, which would flatter or flatten the result for the wrong reason. Note
that the pipeline's own selector string (`bv*[vcodec^=avc1][height<=H]`) picks
format **18** at 360p — the legacy *muxed* rendition, 45.5 MiB instead of
10.4 MiB — because `bv*` ranks by bitrate and 18 carries an audio track. A
detection stream would want `bv` (video only).

---

## 1. What resolution does detection run at today?

**256x144, for every input from 144p to 4K.** Both files in a dual-stream setup
are analysed at exactly the same resolution — which is what made the idea look
free.

The rule, from `scenedetect/scene_manager.py` (0.7.1, in the venv):

```python
DEFAULT_MIN_WIDTH: int = 256

def compute_downscale_factor(frame_width, effective_width=DEFAULT_MIN_WIDTH) -> float:
    if frame_width < effective_width:
        return 1
    return frame_width / float(effective_width)
```

`SceneManager.detect_scenes` calls it as
`compute_downscale_factor(max(effective_frame_size))` — the **long edge**, so
portrait video is handled the same way — and `_decode_thread` then runs
`cv2.resize(frame, (round(w / f), round(h / f)), INTER_LINEAR)` on every frame
before any detector sees it.

| input | factor | analysis resolution | pixels scored |
|---|---|---|---|
| 1920x1080 | 7.5 | **256x144** | 36,864 |
| 1280x720 | 5.0 | 256x144 | 36,864 |
| 854x480 | 3.336 | 256x144 | 36,864 |
| **640x360** | 2.5 | **256x144** | 36,864 |
| 426x240 | 1.664 | 256x144 | 36,864 |
| 256x144 | 1.0 | 256x144 | 36,864 |
| 320x240 (the CPU test fixture) | 1.25 | 256x192 | 49,152 |

Confirmed at runtime, not only by reading: with `pyscenedetect` at DEBUG both
files log `Processing resolution: 256 x 144` — `downscale: 7.5` for the 1080p
copy, `downscale: 2.5` for the 360p one.

Two things follow, and the second one is the whole story:

1. **0.7 changed this.** The factor is a `float` now; 0.6 used integer division,
   so the docstring's "effective width will be between `frame_width` and
   1.5 × `frame_width`" is stale — in 0.7.1 the long edge lands on 256 exactly.
2. **Same resolution is not the same image.** 1080p reaches 256x144 through one
   `INTER_LINEAR` resize by 7.5x, which samples a 2x2 neighbourhood out of every
   7.5x7.5 one: it aliases, and most of the frame never contributes. A 360p file
   reaches it through ffmpeg's properly filtered downscale to 640x360 (done once,
   by YouTube's encoder) and then a 2.5x bilinear step. Those two 256x144 images
   are visibly different to a Canny edge map — and `delta_edges` carries half the
   weight in our screencast preset. §3 is that difference, measured.

## 2. Dual stream: the timings

Both paths extract the *frames* from the 1080p file; only the detection pass
differs. Two repeats each, page cache warmed first; `min` used for the ratios.

| | RjfbvDXpFls (18:25) | Sir59K8ZDPU (21:18) |
|---|---|---|
| frames in the 1080p file | 55,237 | 76,601 |
| bare threaded decode, 1080p (no detection) | **19.0 s** | **30.8 s** |
| bare threaded decode, 360p | 1.2 s | 2.1 s |
| detect on 1080p (shipped path) | 140.7 s · 145.3 s | 196.6 s · 257.3 s |
| detect on 360p | 42.0 s · 35.2 s | 48.3 s · 47.7 s |
| **detection speedup** | **4.0x** | **4.1x** |
| extract pass (1080p, from those shots) | 72.6 s · 74.0 s | 29.5 s · 34.2 s |
| extract pass, dual (more shots to visit) | 111.6 s · 109.2 s | 29.7 s |
| **whole-stage speedup** | **1.48x** | **2.92x** |

The first row is the headline nobody expected: **a full threaded decode of the
1080p file is 19 s of a 140 s pass.** The decode is not the cost. The cost is
the per-frame Python/OpenCV pipeline behind it — colour-convert to BGR, resize
to 256x144, hand it across a queue, score it — and that scales with frame count
and source resolution, which is why the 360p file (a quarter of the pixels *and*
half the frames) is 4x faster.

Note also that the dual path made the *extract* pass 50% slower on the first
video: it found 45% more shots, and the extract pass seeks ~9 times per shot.
The equivalence failure below is not just a quality question, it eats the win.

## 3. Equivalence: the decision criterion

The bar, agreed before the run: same shot count ±1, boundary drift under
~0.3 s, and the frame each path chooses either bit-identical (phash distance ~0)
or of equivalent sharpness.

| | RjfbvDXpFls | Sir59K8ZDPU |
|---|---|---|
| detected cuts, 1080p → 360p | 123 → **183** | 23 → 23 |
| cuts drifting > 0.3 s | **22 of 123** (mean 0.39 s, max 9.2 s) | 1 of 23 |
| shots after `subdivide`, 1080p → 360p | 126 → **183** | 51 → **52** |
| keyframes matched by timestamp | 119 of 126 (64 extra, 7 lost) | 50 of 51 (2 extra, 1 lost) |
| chosen frames bit-identical (phash256 = 0) | **60 of 119** | 41 of 50 |
| phash256 distance, mean / max | **19.0 / 132** | **0.56 / 8** |
| sharpness ratio, mean | 1.014 | 1.000 |

**Sir59K8ZDPU passes** on every line: one extra shot, one cut moved, and the
frames the two paths keep are the same screens (a max distance of 8 bits in 256
is the same slide re-encoded, not a different slide).

**RjfbvDXpFls fails on every line.** 60 extra cuts, a fifth of the boundaries
moved by more than the bar, and only half the kept frames identical.

One in two videos passing is not "no degradation". **Verdict: dual-stream
detection is not shipped.**

### 3.1 PTS alignment — verified, not assumed

Worth recording because it was the thing most likely to be silently wrong, and
it turned out to be right:

- both containers start at PTS 0, `start_time = 0.000000`, and their durations
  agree to 0.02 s (1104.740 vs 1104.760; 1277.960 vs 1277.945) despite unrelated
  time bases (1/60000 against 1/11988 on the second video);
- seeking both files to the same five timestamps (5/25/50/75/95% of the
  duration) landed within **0.02 s** every time, and the frames that came back
  hash to a phash256 distance of **0–6 bits** — the same instant on screen.

So the boundaries transfer straight across as seconds. The divergence in §3 is
not a timeline problem; it is a detection problem.

### 3.2 Why it diverges: resolution, not frame rate, not YouTube's bitrate

The 360p stream is also half the frame rate (YouTube only offers HFR at 720p and
above), so there were two suspects. Four controls, all encoded locally from the
same 1080p master at CRF 20 — so no YouTube bitrate confound — and all detected
with the same preset:

| stream detection ran on | cuts | not in the 1080p50 list (>0.3 s) | 1080p50 cuts missed |
|---|---|---|---|
| **1920x1080 @ 50** (reference) | **123** | – | – |
| 1920x1080 @ 25 | 124 | 20 | 19 |
| 640x360 @ 50 | **168** | 72 | 27 |
| 640x360 @ 25 | 151 | 50 | 22 |
| YouTube 640x360 @ 25 (format 134, 37 kbps) | **183** | 82 | 22 |

Halving the frame rate at full resolution keeps the count (123 → 124, though 20
boundaries move). Dropping the resolution at the *same* frame rate adds 45 cuts.
**It is the resolution.** YouTube's low bitrate adds more on top, but a
good-quality local 360p encode already breaks equivalence on its own.

The honest reading is not "360p is worse". It is that the two analyses see
different images (§1.2), and the 360p one — properly filtered rather than
aliased — is arguably the *more* faithful of the two. It finds cuts the 1080p
pass misses, and the pipeline deliberately over-detects and lets the phash pass
clean up (research §4.2). But "arguably better" is not the bar Tom set, and
changing what the detector sees is a corpus-wide decision about correctness, not
a performance tweak to be smuggled in for a 1.5x.

## 4. What did ship: the decode was single-threaded

Chasing §2's first row turned up the actual bug. `open_video(path,
backend="pyav")` leaves the codec context on PyAV's default, which for H.264 is
`ThreadType.SLICE` — slice threading only, no frame threading.

Raw read throughput on the 1080p file of the first video:

| `threading_mode` | frames/s |
|---|---|
| default (SLICE) | **406** |
| `"AUTO"` (FRAME + SLICE) | **1211** |

406 frames/s over 55,237 frames is 136 s — essentially the entire 140 s
detection pass. End to end, with everything else unchanged:

| video | `NONE` | `SLICE` (the default) | **`AUTO`** | cut lists |
|---|---|---|---|---|
| RjfbvDXpFls | 187.0 s | 138.1 · 140.2 · 140.7 · 145.3 s | **94.3 · 94.7 s** | identical — 123 cuts, same list, same last frame |
| Sir59K8ZDPU | 189.6 s | 187.4 · 196.6 · 255.7 · 257.3 s | **131.2 · 137.8 s** | identical — 23 cuts, same list, same last frame |

**~1.45x, for one keyword argument, with a bit-identical answer** — the same
frames reach the same detector in the same order, which is exactly what
dual-stream could not promise. Shipped in `pipeline/keyframes.py`, with a test
that asserts the argument is still there and a second that asserts the cut list
does not move under it.

Two caveats on those numbers. The second video's spread (187 s to 257 s on the
same file and the same mode) is the co-tenant dev stack, not the decoder — take
the minima, and note that `SLICE` and `NONE` are within each other's noise while
`AUTO` is clear of both on every run. And the win is capped by what is left: at
1211 frames/s the decode is no longer the bottleneck, the per-frame trip into
Python is (§5).

The historical reason to avoid `AUTO` (frame threading could stop short of the
last frame) is handled inside 0.7.1: `VideoStreamAv._handle_eof` re-opens and
seeks when the decoded frame count comes up short. Both runs above ended on the
same last frame, which is that path working.

## 5. NVDEC: not the lever

`h264_cuvid` is compiled into this box's ffmpeg but **cannot run on it** —
`Cannot load libnvcuvid.so.1`, i.e. the container has the driver's compute
libraries and not its video ones. So there is no measured NVDEC throughput here,
only the CPU control: ffmpeg decodes 120 s of the 1080p50 file in **2.58 s**,
**46x realtime**, using all cores.

That control is enough to answer the question without the GPU:

- A complete threaded CPU decode of the 18-minute file costs **19 s inside a
  94 s detection pass** (§2, §4) — about **20%**, and about 8% of the whole
  stage once the extract pass is counted. An infinitely fast decoder cannot beat
  that.
- Whatever decodes the frames still has to hand OpenCV a BGR array. NVDEC's
  output is NV12 in device memory; reaching `cv2.resize` means a colour convert
  and a PCIe copy of every 1080p frame — 55,000 of them — which is most of the
  cost that would have been saved.
- The 3090 is a shared, leased resource here (`GPU_ACQUIRE_CMD` and the
  llama.cpp co-tenant). Putting a CPU-only stage into the lease to chase 20%
  contradicts the lease rule this project already settled
  (`gpu-validation-2026-08-08.md` §5.2–5.3: CPU backends stay outside the
  bracket precisely so they never stop a co-tenant).

**Recommendation: no NVDEC.** Install the video libraries if someone wants the
number for completeness, but do not wire the keyframe stage to the GPU.

If the stage has to get several times faster later, the lever the numbers point
at is to stop moving 1080p frames into Python at all: `ffmpeg -i in.mp4 -vf
scale=256:144 -f rawvideo -pix_fmt bgr24 -` behind a custom `VideoStream`, so
the decode, the colour convert and the downscale all happen in ffmpeg's threaded
C and Python only ever sees 110 KB frames. That is a bigger win than either idea
here — and §3.2 says it will move the cuts, exactly as the 360p stream did,
because it is the same substitution of a filtered image for an aliased one. It
is therefore the same decision, and it needs Tom, a reference clip set, and a
one-time reindex plan rather than a flag.

## 6. Reproducing

```bash
# the two comparisons, on your own downloads
uv run --no-sync python bench/keyframe_decode.py \
    --pair full=ID-1080p.mp4,detect=ID-360p.mp4 \
    --repeats 2 --decode-only --pts-probe --nvdec-probe 120 \
    --out bench/results/raw/keyframe-decode-2026-08-08.json

uv run --no-sync python bench/keyframe_decode.py \
    --pair full=ID-1080p.mp4,detect=ID-360p.mp4 \
    --threading NONE,SLICE,AUTO --no-dual \
    --out bench/results/raw/keyframe-threading-2026-08-08.json
```

The detection stream must be fetched with `bv[...]`, not `bv*[...]`, or yt-dlp
hands you the muxed format 18 (see the header).

§3.2's controls are not in the harness — they are three `ffmpeg` calls off the
same master plus one `detect_spans` each:

```bash
ffmpeg -i ID-1080p.mp4 -vf scale=640:360        -c:v libx264 -preset veryfast -crf 20 -an 360at50.mp4
ffmpeg -i ID-1080p.mp4 -vf scale=640:360,fps=25 -c:v libx264 -preset veryfast -crf 20 -an 360at25.mp4
ffmpeg -i ID-1080p.mp4 -vf fps=25               -c:v libx264 -preset veryfast -crf 20 -an 1080at25.mp4
```
