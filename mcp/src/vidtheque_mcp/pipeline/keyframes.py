"""Shot detection -> sharpest frame per shot -> JPEG -> perceptual dedup.

All of it synchronous and CPU-bound; the stage calls it through
``asyncio.to_thread``. Nothing here touches the database or the network, which
is what makes it testable against a 6-second ffmpeg-synthesized clip.

The three findings from research §4 that this file exists to encode:

1. **Default `ContentDetector` weights under-detect on screencasts.** The frame
   score is a weight-normalised mean of hue, saturation and luma deltas. IDE and
   slide content is near-greyscale, so two of the three channels are
   structurally zero: a full slide change with `delta_lum = 60` scores
   `(0+0+60)/3 = 20`, under the default threshold of 27, and the cut is missed.
   The fix is to drop the dead channels and add the Canny edge delta, which is
   the signal that actually moves when a slide changes.
2. **Deliberately over-detect.** The phash pass is far better at removing extra
   keyframes than detection is at recovering missed slides.
3. **Sharpest-per-shot is `cv2.Laplacian(...).var()`, compared only *within* a
   shot.** Across a slide build the "sharpest" frame is the one with the most
   text on screen — precisely the frame worth OCR-ing. Across shots the number
   means nothing (a text-heavy slide always outscores a blank one).
4. **Pass 1 converts colour at the detection resolution, not the source's.**
   The detector never sees more than 256 px on the long edge, so decoding a
   1080p frame to a 6.2 MB BGR array and resizing it down to 110 KB converts
   56x more pixels than anyone reads. `_fused_stream_class` folds both steps
   into one libswscale call (research/pipeline-perf-2026-08-09.md §3.3 and its
   2026-08-09 addendum). It is not bit-equivalent to the old path and was not
   required to be: shot boundaries may move by a frame or two, and the corpus
   carries mixed provenance in `video_stages.model_key` instead of being
   reindexed.

Timestamps come from the decoder's reported position, never `frame_num / fps`.
0.7 made frame rates exact `Fraction`s and timestamps PTS-backed for this
reason: at 30000/1001, naive float arithmetic drifts about a frame per thousand
and visibly desyncs an hour in — which is the length of content this corpus is
for, and deep links are the product.
"""

from __future__ import annotations

import logging
import struct
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any, Callable, Sequence

import numpy as np

logger = logging.getLogger(__name__)

EDGE_TRIM = 0.12  # skip the first/last 12% of a shot: transitions, motion blur
STORED_HASH_SIZE = 8  # 64 bits — what `keyframes.phash` can hold
DEDUP_HASH_SIZE = 16  # 256 bits — what telling two slides apart needs
# `scenedetect.scene_manager.DEFAULT_MIN_WIDTH` (0.7.1). Mirrored rather than
# imported so this module's imports stay lazy; asserted against the real value
# in the tests, which is what catches an upstream change to it.
DETECTION_MIN_WIDTH = 256


@dataclass
class Shot:
    index: int
    start_s: float
    end_s: float

    @property
    def span(self) -> float:
        return max(0.0, self.end_s - self.start_s)


@dataclass
class KeyframeDraft:
    ordinal: int
    t_s: float
    shot: Shot
    sharpness: float
    width: int
    height: int
    relpath: str
    jpeg_bytes: int
    phash: int  # signed 64-bit, ready for the column
    absolute: Path | None = None
    dup_of: int | None = None  # ordinal of the frame this one repeats
    _bits: Any = field(default=None, repr=False)


def signed64(value: int) -> int:
    """SQLite integers are signed; a raw 64-bit hash overflows on insert.

    Round-trips exactly: ``struct.unpack('<q', struct.pack('<Q', h))[0]``.
    """
    return struct.unpack("<q", struct.pack("<Q", value & 0xFFFF_FFFF_FFFF_FFFF))[0]


# ------------------------------------------------------------------ detection


def make_detector(kind: str):
    """The two presets. `screencast` is the default because that is the corpus."""
    from scenedetect import AdaptiveDetector, ContentDetector
    from scenedetect.detector import FlashFilter

    if kind == "talking_head":
        # Scores each frame against a rolling window rather than a fixed
        # threshold, so slow lighting drift does not trip it while a real cut,
        # which spikes relative to its neighbours, does.
        return AdaptiveDetector(
            adaptive_threshold=3.0, min_scene_len="2.0s", min_content_val=15.0, window_width=2
        )
    return ContentDetector(
        # ~12 on two channels is the sensitivity equivalent of the default 27
        # on three, and edges catch white-slide-to-white-slide changes that
        # pure luma is blind to.
        threshold=12.0,
        min_scene_len="1.0s",
        weights=ContentDetector.Components(
            delta_hue=0.0, delta_sat=0.0, delta_lum=1.0, delta_edges=1.0
        ),
        # MERGE folds sub-minimum cuts together, which is what a slide build is.
        filter_mode=FlashFilter.Mode.MERGE,
    )


def detection_size(
    source: tuple[int, int], min_width: int = DETECTION_MIN_WIDTH
) -> tuple[int, int]:
    """The resolution PySceneDetect would have analysed this frame at.

    Reproduces `compute_downscale_factor` (which divides the *long* edge down to
    256 px) followed by the `round(dim / factor)` the decode thread resizes
    with, so the fused path hands the detector the same geometry the old one
    did — the pixels differ, the shape does not.
    """
    width, height = source
    longest = max(width, height)
    if longest <= 0 or longest < min_width:
        return (width, height)
    factor = longest / float(min_width)
    return (max(1, round(width / factor)), max(1, round(height / factor)))


@lru_cache(maxsize=1)
def _fused_stream_class() -> type:
    """PySceneDetect's PyAV backend with the convert and the downscale fused.

    The backend abstraction is the seam: `SceneManager.detect_scenes` takes any
    `VideoStream`, its decode thread resizes only when
    `compute_downscale_factor(max(video.frame_size)) > 1.0`, and it compares
    each decoded array's shape against `video.frame_size` to spot corruption. A
    stream that *reports* the detection size and *returns* frames at it
    therefore satisfies both checks and skips the resize entirely — no
    monkeypatching, no fork of the manager.

    Everything else is inherited, deliberately: PTS-backed `position`, the
    corrupt-frame skipping and `decode_failures`, `seek`, and `_handle_eof`'s
    re-open when AUTO threading stops short of the last frame. Only `read`
    changes, and only in *how* the frame becomes an array:

        stock:  frame.to_ndarray(format="bgr24")   -> 1920x1080 BGR (6.2 MB)
                cv2.resize(..., INTER_LINEAR)      -> 256x144    (110 KB)
        fused:  reformatter.reformat(frame, 256, 144, "bgr24")  -> one swscale

    One `VideoReformatter` is kept per stream because PyAV's docs are explicit
    that a fresh `reformat()` call reconfigures the internal scaler context;
    reusing it configures swscale once per video instead of once per frame.

    The class is built lazily (and cached) because `av` and `scenedetect` are
    heavy imports and this module is imported by the runner at boot.
    """
    from av.video.reformatter import VideoReformatter
    from scenedetect.backends.pyav import VideoStreamAv

    class FusedDetectionStream(VideoStreamAv):
        """A `VideoStreamAv` that decodes straight to detection resolution."""

        BACKEND_NAME = "pyav-fused"

        def __init__(
            self,
            path: str,
            *,
            threading_mode: str = "AUTO",
            min_width: int = DETECTION_MIN_WIDTH,
        ) -> None:
            super().__init__(path, threading_mode=threading_mode)
            self._source_size: tuple[int, int] = super().frame_size
            self._detect_size: tuple[int, int] = detection_size(self._source_size, min_width)
            self._reformatter = VideoReformatter()

        @property
        def source_size(self) -> tuple[int, int]:
            """What the container holds, for anyone reporting on the decode."""
            return self._source_size

        @property
        def frame_size(self) -> tuple[int, int]:
            """What `read()` returns — and so what the manager must not resize."""
            return self._detect_size

        def read(self, decode: bool = True) -> Any:
            # `decode=False` is the seek path and the frame-skip path: advance
            # without paying for a conversion, exactly as the parent does. The
            # parent's own EOF recovery re-enters through here with decode=False
            # too, which is why this delegates rather than reimplementing.
            advanced = super().read(decode=False)
            if advanced is False or not decode:
                return advanced
            width, height = self._detect_size
            converted = self._reformatter.reformat(
                self._frame, width=width, height=height, format="bgr24"
            )
            return converted.to_ndarray()

    return FusedDetectionStream


def detect_shots(
    video_path: Path, *, kind: str = "screencast", max_shot_seconds: float = 25.0
) -> list[Shot]:
    """One full decode at detection resolution, PTS-backed timestamps.

    The detector never analyses more than 256 px on the long edge: 0.7.1's
    ``compute_downscale_factor`` divides the long edge down to it, so 1920x1080
    and 640x360 inputs are both analysed at 256x144. Until 2026-08-09 the
    *decode* was still full-resolution — every frame converted to a 6.2 MB BGR
    array and then resized down to 110 KB. It is fused now (``detect_spans``).
    This pass decodes the whole video and is roughly two-thirds of the keyframe
    stage (research/pipeline-perf-2026-08-09.md §2).
    """
    spans, duration = detect_spans(video_path, kind=kind)
    return subdivide(spans, duration, max_shot_seconds)


def detect_spans(
    video_path: Path, *, kind: str = "screencast", fused: bool = True
) -> tuple[list[tuple[float, float]], float]:
    """The detector's own scene list, before ``subdivide`` adds fixed cuts.

    Split out from ``detect_shots`` so a caller comparing two detection runs can
    see the *detected* cuts rather than the synthetic ones — one decode, both
    answers.

    ``fused=False`` is the pre-2026-08-09 frame path (full-resolution BGR, then
    ``cv2.resize``) and exists for exactly one caller: ``bench/keyframe_decode.py
    --fused-probe``, which is where "the boundaries barely move" gets a number
    on a real 1080p talk instead of an assertion. It is not reachable from the
    pipeline, has no env var, and records no model_key of its own — indexing is
    always fused (research/pipeline-perf-2026-08-09.md, 2026-08-09 addendum).
    """
    from scenedetect import SceneManager

    # PyAV for true PTS and 0.7.1's corrupt-frame skipping.
    #
    # `threading_mode="AUTO"` is FRAME+SLICE threading, and it is worth a third
    # of this stage. PyAV's default for an H.264 stream is SLICE alone, which on
    # this box decodes a 1080p50 file at 406 frames/s; AUTO does 1211 — measured
    # end to end at 138s -> 94s for an 18-minute talk and 187s -> 131s for a
    # 21-minute one, with **bit-identical cut lists** both times, because the
    # same frames reach the same detector in the same order. Threaded decoding
    # is the only free speedup here: everything else that makes this pass
    # cheaper (a smaller stream, frame skipping) changes what the detector sees.
    # 0.7.1 re-opens the video itself if AUTO stops short of the last frame
    # (`VideoStreamAv._handle_eof`), which was the historical reason to avoid it.
    if fused:
        video: Any = _fused_stream_class()(str(video_path), threading_mode="AUTO")
    else:
        from scenedetect import open_video

        video = open_video(str(video_path), backend="pyav", threading_mode="AUTO")
    manager = SceneManager()
    manager.add_detector(make_detector(kind))
    manager.auto_downscale = True
    manager.detect_scenes(video=video, show_progress=False)
    # start_in_scene=True: without it a video with no detected cuts returns an
    # empty list rather than one whole-video scene — the single most common way
    # this pipeline silently produces zero keyframes.
    scenes = manager.get_scene_list(start_in_scene=True)
    failures = getattr(video, "decode_failures", 0)
    if failures:
        # A nonzero count explains otherwise-inexplicable timestamp drift, which
        # for this product means wrong deep links. Say so.
        logger.warning("%s: %d frames skipped by the decoder", video_path.name, failures)

    duration = float(getattr(video.duration, "seconds", 0.0) or 0.0)
    return [(float(start.seconds), float(end.seconds)) for start, end in scenes], duration


def subdivide(
    spans: Sequence[tuple[float, float]], duration_s: float, max_shot_seconds: float
) -> list[Shot]:
    """Fixed-interval fallback for content that legitimately has no cuts.

    An unedited screen recording or a single-take talking head is one scene for
    the whole video; without this it yields exactly one keyframe.
    """
    if not spans:
        spans = [(0.0, max(duration_s, max_shot_seconds))]
    shots: list[Shot] = []
    for start, end in spans:
        span = max(0.0, end - start)
        if span <= 0:
            continue
        pieces = max(1, int(span // max_shot_seconds))
        step = span / pieces
        for piece in range(pieces):
            a = start + piece * step
            b = end if piece == pieces - 1 else start + (piece + 1) * step
            shots.append(Shot(index=len(shots), start_s=a, end_s=b))
    if not shots:
        shots.append(Shot(index=0, start_s=0.0, end_s=max(duration_s, 1.0)))
    return shots


def thin(shots: Sequence[Shot], budget: int) -> list[Shot]:
    """Uniform subsample when a video blows the keyframe budget.

    Uniform, not "the longest shots": the budget is about disk and OCR cost, and
    dropping every short shot would delete exactly the fast slide builds this
    corpus is about.
    """
    if budget <= 0 or len(shots) <= budget:
        return list(shots)
    step = len(shots) / budget
    return [shots[min(len(shots) - 1, int(i * step))] for i in range(budget)]


# -------------------------------------------------------------------- sampling


def sharpness(frame_bgr: Any, max_side: int = 720) -> float:
    """Variance of the Laplacian, downscaled first.

    Full-res is dominated by sensor and compression noise and costs ~4x more.
    """
    import cv2

    height, width = frame_bgr.shape[:2]
    scale = max_side / max(height, width)
    if scale < 1.0:
        frame_bgr = cv2.resize(frame_bgr, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA)
    grey = cv2.cvtColor(frame_bgr, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(grey, cv2.CV_64F).var())


def extract_keyframes(
    video_path: Path,
    out_dir: Path,
    relpath_for: Callable[[int, float], str],
    *,
    kind: str = "screencast",
    max_shot_seconds: float = 25.0,
    candidates_per_shot: int = 9,
    max_width: int = 1280,
    quality: int = 92,
    budget: int = 600,
    phash_threshold: int = 24,
    workers: int = 1,
    decode_threads: int = 0,
    progress: Callable[[float], None] | None = None,
) -> list[KeyframeDraft]:
    """Pass 1 decodes the video end to end; pass 2 seeks ~9 times per shot.

    For a 40-shot video that is ~360 decoded frames instead of 100,000, which is
    why pass 1 is most of this stage: 56% and 81% of it on the two 1080p talks in
    research/keyframe-decode-bench-2026-08-08.md, the rest being pass 2's ~1,100
    seeks. Both passes are worth attacking; only one of them has a fix that does
    not change the output.

    Across the 75-video corpus that split fits
    ``6.7 s x video-minutes + 1.04 s x shots`` (R^2 0.79,
    research/pipeline-perf-2026-08-09.md §2), so pass 2 is ~34% of the stage —
    and `workers` is the part of it that parallelises without moving a frame.
    """
    shots = thin(detect_shots(video_path, kind=kind, max_shot_seconds=max_shot_seconds), budget)
    return extract_from_shots(
        video_path,
        shots,
        out_dir,
        relpath_for,
        candidates_per_shot=candidates_per_shot,
        max_width=max_width,
        quality=quality,
        phash_threshold=phash_threshold,
        workers=workers,
        decode_threads=decode_threads,
        progress=progress,
    )


def extract_from_shots(
    video_path: Path,
    shots: Sequence[Shot],
    out_dir: Path,
    relpath_for: Callable[[int, float], str],
    *,
    candidates_per_shot: int = 9,
    max_width: int = 1280,
    quality: int = 92,
    phash_threshold: int = 24,
    workers: int = 1,
    decode_threads: int = 0,
    progress: Callable[[float], None] | None = None,
) -> list[KeyframeDraft]:
    """Pass 2 on its own: seek, score, write, hash.

    Split out of ``extract_keyframes`` so the two passes can be timed — and
    compared across detection runs — independently (``bench/keyframe_decode.py``).

    ``workers`` > 1 runs the *search* half — nine seeks and nine Laplacians per
    shot — on a thread pool with one ``VideoCapture`` each, and leaves the
    *commit* half (the `seen_ms` guard, the ordinal, the JPEG, the hashes)
    exactly where it was: a single thread, walking the shots in order. That
    division is the whole reason this is safe. Ordinals, filenames, the
    first-wins dedup and the collision guard all depend on the order shots are
    committed in, and none of them depend on the order they were *searched* in,
    because ``_sharpest_in`` seeks absolutely and reads nothing but the file.

    The pool is fed a chunk at a time rather than the whole list, because a
    1080p BGR frame is 6 MB and a 600-shot video handed to ``map`` at once would
    hold 3.6 GB of them waiting for the committer. At ``2 x workers`` in flight
    the ceiling is ~50 MB at four workers, and the committer (~40 ms of JPEG and
    phash against ~1 s of seeking) is never the thing anyone waits for.
    """
    import cv2

    out_dir.mkdir(parents=True, exist_ok=True)

    drafts: list[KeyframeDraft] = []
    seen_ms: set[int] = set()

    def commit(position: int, shot: Shot, best: tuple[float, Any, float] | None) -> None:
        if best is None:
            return
        score, frame, seconds = best
        # UNIQUE(video_id, t_s): two shots whose sample seeks land on the
        # same decodable frame would collide on insert.
        stamp = int(round(seconds * 1000))
        if stamp in seen_ms:
            return
        seen_ms.add(stamp)

        frame = _cap_width(frame, max_width)
        ordinal = len(drafts)
        relative = relpath_for(ordinal, seconds)
        # `relative` is what the row stores (relative to $VIDTHEQUE_DATA_DIR);
        # the bytes go under the directory we were handed, by the same name.
        absolute = out_dir / Path(relative).name
        written = cv2.imwrite(str(absolute), frame, [int(cv2.IMWRITE_JPEG_QUALITY), int(quality)])
        # `imwrite` reports failure by returning False — a full disk, a
        # read-only mount, an encoder that refused the frame. Unchecked, the
        # next line raised `FileNotFoundError` from `stat()` two hundred
        # frames in and the reason was gone.
        if not written or not absolute.exists() or absolute.stat().st_size == 0:
            raise RuntimeError(
                f"could not write keyframe {ordinal} to {absolute} "
                "(cv2.imwrite failed — out of disk?)"
            )
        height, width = frame.shape[:2]
        stored, bits = _hashes(absolute)
        drafts.append(
            KeyframeDraft(
                ordinal=ordinal,
                t_s=round(seconds, 3),
                shot=Shot(index=position, start_s=shot.start_s, end_s=shot.end_s),
                sharpness=round(score, 2),
                width=int(width),
                height=int(height),
                relpath=relative,
                jpeg_bytes=absolute.stat().st_size,
                phash=stored,
                absolute=absolute,
                _bits=bits,
            )
        )
        if progress is not None and shots:
            progress((position + 1) / len(shots))

    if workers > 1 and len(shots) > 1:
        _extract_pooled(
            video_path, shots, candidates_per_shot, workers, decode_threads, commit
        )
    else:
        capture = _open_capture(video_path, decode_threads)
        try:
            for position, shot in enumerate(shots):
                commit(position, shot, _sharpest_in(capture, shot, candidates_per_shot))
        finally:
            capture.release()

    mark_duplicates(drafts, phash_threshold)
    return drafts


def _open_capture(video_path: Path, decode_threads: int) -> Any:
    import cv2

    if decode_threads > 0:
        # Must ride the constructor: on OpenCV 5.0.0 `capture.set(N_THREADS)`
        # returns False after open and the property stays at its default
        # (bench 2026-08-09, research/pipeline-perf §9). Advisory either way:
        # nothing downstream may depend on it having been applied.
        capture = cv2.VideoCapture(
            str(video_path), cv2.CAP_FFMPEG, [cv2.CAP_PROP_N_THREADS, decode_threads]
        )
    else:
        capture = cv2.VideoCapture(str(video_path))
    if not capture.isOpened():
        raise RuntimeError(f"cannot open {video_path} for frame extraction")
    return capture


def _extract_pooled(
    video_path: Path,
    shots: Sequence[Shot],
    candidates_per_shot: int,
    workers: int,
    decode_threads: int,
    commit: Callable[[int, Shot, "tuple[float, Any, float] | None"], None],
) -> None:
    """Search on ``workers`` threads, commit on this one, in shot order.

    Every capture is opened **here, serially, before a thread exists**. OpenCV's
    FFmpeg backend guards ``open()`` with a global mutex and documents that
    concurrent opens can return an unopened capture with no error
    (``cap_ffmpeg_impl.hpp``); opening lazily inside the workers would have made
    that a rare, load-dependent "cannot open" several minutes into a job. Opened
    once and held for the life of the pool, since re-opening per shot would
    spend the win on container parsing.

    Each thread then owns exactly one capture for the whole run — the supported
    pattern is one ``VideoCapture`` per thread, never one shared between them —
    and the main thread releases them all after the pool has joined, which is
    the only moment at which no worker can still be inside ``read()``.
    """
    import queue
    import threading
    from concurrent.futures import ThreadPoolExecutor

    workers = min(workers, len(shots))
    captures = [_open_capture(video_path, decode_threads) for _ in range(workers)]
    unclaimed: queue.Queue[Any] = queue.Queue()
    for capture in captures:
        unclaimed.put(capture)
    local = threading.local()

    def search(shot: Shot) -> "tuple[float, Any, float] | None":
        capture = getattr(local, "capture", None)
        if capture is None:
            # One claim per thread, and the pool never has more threads than
            # captures, so this cannot block.
            capture = unclaimed.get_nowait()
            local.capture = capture
        return _sharpest_in(capture, shot, candidates_per_shot)

    chunk = max(1, workers * 2)
    try:
        with ThreadPoolExecutor(max_workers=workers, thread_name_prefix="keyframe") as pool:
            for start in range(0, len(shots), chunk):
                window = list(shots[start : start + chunk])
                for offset, best in enumerate(pool.map(search, window)):
                    commit(start + offset, window[offset], best)
    finally:
        for capture in captures:
            capture.release()


def _sharpest_in(capture: Any, shot: Shot, candidates: int) -> tuple[float, Any, float] | None:
    import cv2

    span = shot.span
    if span <= 0:
        return None
    low = shot.start_s + span * EDGE_TRIM
    high = shot.end_s - span * EDGE_TRIM
    if high <= low:
        low = high = (shot.start_s + shot.end_s) / 2.0
    targets = np.linspace(low, high, max(1, candidates))

    best: tuple[float, Any, float] | None = None
    for target in targets:
        capture.set(cv2.CAP_PROP_POS_MSEC, float(target) * 1000.0)
        ok, frame = capture.read()
        if not ok or frame is None:
            continue
        # Trust the decoder's reported position, not the one we asked for:
        # seeks land on the nearest decodable frame, and on VFR they land well
        # off. cv2.CAP_PROP_POS_FRAMES is advisory; POS_MSEC is not.
        actual = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
        if actual <= 0:
            actual = float(target)
        score = sharpness(frame)
        if best is None or score > best[0]:
            best = (score, frame, actual)
    return best


def _cap_width(frame: Any, max_width: int) -> Any:
    import cv2

    height, width = frame.shape[:2]
    if max_width <= 0 or width <= max_width:
        return frame
    scale = max_width / width
    return cv2.resize(
        frame, (int(max_width), max(1, int(round(height * scale)))), interpolation=cv2.INTER_AREA
    )


# ------------------------------------------------------------------- dedup


def _hashes(path: Path) -> tuple[int, np.ndarray]:
    """The 64-bit hash the column stores, and the 256-bit one dedup needs.

    `phash(hash_size=8)` is 64 bits from the top-left 8x8 of a 32x32 DCT: gross
    layout only. Two slides from one deck share template, title bar and colour
    scheme and differ only in body text, so at 64 bits their hashes are
    frequently *identical* — dedup at that width silently drops distinct
    slides. `keyframes.phash` is an INTEGER and cannot hold 256 bits, so the
    column keeps the 64-bit hash (it is for "find frames that look like this
    one" later, over an already-capped candidate set) and dedup runs on the
    wider one in memory, here, once.
    """
    import imagehash
    from PIL import Image

    with Image.open(path) as image:
        image.load()
        coarse = imagehash.phash(image, hash_size=STORED_HASH_SIZE)
        fine = imagehash.phash(image, hash_size=DEDUP_HASH_SIZE, highfreq_factor=4)
    bits = np.packbits(np.asarray(fine.hash).flatten())
    value = int("".join("1" if bit else "0" for bit in np.asarray(coarse.hash).flatten()), 2)
    return signed64(value), bits


def mark_duplicates(drafts: list[KeyframeDraft], threshold: int) -> None:
    """Greedy first-wins clustering in timeline order.

    First-wins matters for this product: it keeps the *earliest* occurrence of
    each screen, which is the timestamp a user actually wants to jump to. The
    later ones are not deleted — the row is provenance ("this shot really did
    appear again at 14:22") and `keyframes_live` is a partial index on
    `dup_of IS NULL`, so every query wanting distinct visuals gets one free.
    """
    usable = [d for d in drafts if d._bits is not None]
    if len(usable) < 2:
        return
    bits = np.vstack([d._bits for d in usable]).astype(np.uint8)
    taken = np.zeros(len(usable), dtype=bool)
    for index in range(len(usable)):
        if taken[index]:
            continue
        taken[index] = True
        # np.bitwise_count is the NumPy 2.0 popcount ufunc; the row-at-a-time
        # form keeps this O(N) in memory instead of the (N, N, nbytes) tensor.
        distances = np.bitwise_count(bits[index] ^ bits).sum(axis=1)
        duplicates = np.flatnonzero((distances <= threshold) & ~taken)
        for other in duplicates:
            taken[other] = True
            usable[int(other)].dup_of = usable[index].ordinal
