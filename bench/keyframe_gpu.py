#!/usr/bin/env python3
"""Pass 1 on the GPU: does NVDEC decode make shot detection cheaper?

`keyframe_decode.py --fused-probe` answered "where should the colour conversion
happen" and shipped the answer (research/pipeline-perf-2026-08-09.md §8). This
script answers the question that was rejected *unmeasured* in §3.4, because
`libnvcuvid.so.1` could not be loaded on this box until 2026-08-09:

    the decode itself is still on the CPU. What if it were not?

The seam is the same one §8.2 found: `SceneManager.detect_scenes(video=...)`
takes any `VideoStream`. `FFmpegPipeStream` is a `VideoStream` fed by an
**ffmpeg subprocess** writing `rawvideo bgr24` to a pipe, which lets the entire
decode + colour + downscale chain be expressed as ffmpeg arguments and swapped
per variant, with the detector — the shipped `make_detector(kind)` — held fixed.

Three things get measured, in the order they decide anything:

1. **detection variants** (`--variants`): wall + CPU for a whole 1080p60 talk,
   against today's shipped `detect_spans(fused=True)` re-measured in the same
   conditions, plus the **boundary diff** every variant owes (GPU decode is not
   bit-exact; §10's precedent is that drift is acceptable *if reported*).
2. **the feed ceiling** (`--ceiling`): the same chains with **no detector** at
   all, draining the pipe (or `-f null`). Pass-1 wall is
   `max(decode + prepare, detect)` (§8.5), so this is the number that says
   whether any further decoder work can matter, or whether the pass is already
   detector-bound.
3. **pass 2** (`--seek-probe`): `h264_cuvid` seek-decode against the cv2
   `CAP_PROP_POS_MSEC` seek `_sharpest_in` does today, on a sample of the real
   candidate timestamps.

The pipeline is imported, never modified: this is a bench feeding an
architecture decision, not a patch.

    uv run --no-sync python bench/keyframe_gpu.py \\
        --video /path/1lgFGaHoGq8.mp4 --repeats 2 \\
        --variants nvdec-scale,nvdec-resize,cpu-pipe \\
        --ceiling --kept-frames \\
        --out bench/results/raw/keyframe-gpu-2026-08-09.json
"""

from __future__ import annotations

import argparse
import json
import os
import resource
import shutil
import statistics
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from fractions import Fraction
from pathlib import Path
from typing import Any, Callable, Sequence

_BENCH = Path(__file__).resolve().parent
sys.path.insert(0, str(_BENCH))
sys.path.insert(0, str(_BENCH.parent / "mcp" / "src"))

# The boundary-diff and kept-frame machinery already exists and is what §10 was
# judged on; reuse it rather than growing a second opinion about "damage".
from keyframe_decode import (  # noqa: E402
    BUDGET,
    CANDIDATES,
    MAX_SHOT_SECONDS,
    drift,
    pair_keyframes,
    probe,
    warm_cache,
)
from vidtheque_mcp.pipeline.keyframes import (  # noqa: E402
    KeyframeDraft,
    detect_spans,
    detection_size,
    extract_from_shots,
    make_detector,
    subdivide,
    thin,
)

EDGE_TRIM = 0.1  # keyframes._sharpest_in's own constant, for the seek probe


# ------------------------------------------------------------------ plumbing


@dataclass
class Timing:
    """Wall, own CPU, and **child** CPU — half the work here is a subprocess.

    `keyframe_decode.Timing` measures `RUSAGE_SELF` only, which would report an
    ffmpeg-fed variant as using no CPU at all. Children are only accounted for
    once they are reaped, so every measured region waits for its subprocess.
    """

    wall: float
    cpu_self: float
    cpu_children: float

    @property
    def cpu(self) -> float:
        return round(self.cpu_self + self.cpu_children, 3)

    @classmethod
    def measure(cls, fn: Callable[..., Any], *args, **kwargs) -> tuple["Timing", Any]:
        before_self = resource.getrusage(resource.RUSAGE_SELF)
        before_kids = resource.getrusage(resource.RUSAGE_CHILDREN)
        start = time.perf_counter()
        value = fn(*args, **kwargs)
        wall = time.perf_counter() - start
        after_self = resource.getrusage(resource.RUSAGE_SELF)
        after_kids = resource.getrusage(resource.RUSAGE_CHILDREN)
        return (
            cls(
                wall=round(wall, 3),
                cpu_self=round(
                    (after_self.ru_utime - before_self.ru_utime)
                    + (after_self.ru_stime - before_self.ru_stime),
                    3,
                ),
                cpu_children=round(
                    (after_kids.ru_utime - before_kids.ru_utime)
                    + (after_kids.ru_stime - before_kids.ru_stime),
                    3,
                ),
            ),
            value,
        )

    def as_dict(self) -> dict[str, float]:
        return {
            "wall_s": self.wall,
            "cpu_s": self.cpu,
            "cpu_self_s": self.cpu_self,
            "cpu_children_s": self.cpu_children,
        }


class VramSampler:
    """Peak GPU memory over a run, from `nvidia-smi`, minus what was already there.

    NVDEC surfaces are small, but the card is shared with a ~12 GB llama.cpp
    lease on Tom's box: a decode path that quietly wants gigabytes is a decode
    path that cannot be deployed here. Sampled rather than queried once, because
    the interesting number is the peak, not the end.

    **One long-lived `nvidia-smi -l 1`, not a poll loop of short ones.** Child
    CPU only lands in `RUSAGE_CHILDREN` when a child is *reaped*, and the
    sampler is reaped in `__exit__` — i.e. after `Timing.measure` has taken its
    reading. A `subprocess.run` per sample would have charged every one of its
    ~50 ms to whichever decode chain was being measured at the time.
    """

    def __init__(self, interval: int = 1) -> None:
        self.interval = max(1, int(interval))
        self._proc: subprocess.Popen | None = None
        self._thread: threading.Thread | None = None
        self.samples: list[int] = []
        self.baseline: int | None = None

    @staticmethod
    def _used_mib() -> int | None:
        try:
            out = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
                capture_output=True,
                text=True,
                timeout=10,
            )
            if out.returncode != 0:
                return None
            return int(out.stdout.strip().splitlines()[0])
        except Exception:
            return None

    def __enter__(self) -> "VramSampler":
        self.baseline = self._used_mib()
        try:
            self._proc = subprocess.Popen(
                [
                    "nvidia-smi",
                    "--query-gpu=memory.used",
                    "--format=csv,noheader,nounits",
                    "-l",
                    str(self.interval),
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL,
                text=True,
            )
        except Exception:
            self._proc = None
            return self

        def loop() -> None:
            assert self._proc is not None and self._proc.stdout is not None
            for line in self._proc.stdout:
                line = line.strip()
                if line.isdigit():
                    self.samples.append(int(line))

        self._thread = threading.Thread(target=loop, daemon=True)
        self._thread.start()
        return self

    def __exit__(self, *exc: Any) -> None:
        if self._proc is not None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                self._proc.kill()
                self._proc.wait(timeout=10)
        if self._thread is not None:
            self._thread.join(timeout=5)

    def as_dict(self) -> dict[str, Any]:
        peak = max(self.samples) if self.samples else None
        return {
            "baseline_mib": self.baseline,
            "peak_mib": peak,
            "delta_mib": (peak - self.baseline)
            if (peak is not None and self.baseline is not None)
            else None,
            "samples": len(self.samples),
        }


# ------------------------------------------------------------------- variants


@dataclass(frozen=True)
class Variant:
    """One decode chain, as ffmpeg arguments.

    `emits` decides what the detector is handed and therefore who does the
    downscale: `detect` means the chain delivers 256x144 and `SceneManager`
    resizes nothing; `source` means it delivers 1920x1080 over the pipe and the
    manager's own `cv2.resize` does the work — which is the "plain hwdownload"
    arm of §3.4's rule, the one the research predicts throws the win away.
    """

    name: str
    gpu: bool
    emits: str  # "detect" | "source"
    decoder: list[str]
    filters: str
    note: str

    def input_args(self, detect_size: tuple[int, int]) -> list[str]:
        return [arg.format(w=detect_size[0], h=detect_size[1]) for arg in self.decoder]

    def filter_args(self, detect_size: tuple[int, int]) -> list[str]:
        chain = self.filters.format(w=detect_size[0], h=detect_size[1])
        return ["-vf", chain] if chain else []


# `interp_algo=bilinear` matches what the shipped fused path asks swscale for
# (PyAV's `VideoReformatter.reformat` defaults to bilinear), so the boundary
# diff reports the difference between *decoders*, not between two resamplers
# picked at random. scale_cuda's own default is nearest, which would have made
# GPU decode look worse than it is.
VARIANTS: dict[str, Variant] = {
    "nvdec-scale": Variant(
        name="nvdec-scale",
        gpu=True,
        emits="detect",
        decoder=["-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-c:v", "h264_cuvid"],
        filters="scale_cuda={w}:{h}:interp_algo=bilinear,hwdownload,format=nv12",
        note="NVDEC, downscale on the GPU before the PCIe crossing (§3.4's rule)",
    ),
    "nvdec-resize": Variant(
        name="nvdec-resize",
        gpu=True,
        emits="detect",
        decoder=[
            "-hwaccel",
            "cuda",
            "-hwaccel_output_format",
            "cuda",
            "-c:v",
            "h264_cuvid",
            "-resize",
            "{w}x{h}",
        ],
        filters="hwdownload,format=nv12",
        note="NVDEC with the resize inside the decoder, no scale filter at all",
    ),
    "nvdec-cpuscale": Variant(
        name="nvdec-cpuscale",
        gpu=True,
        emits="detect",
        decoder=["-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-c:v", "h264_cuvid"],
        filters="hwdownload,format=nv12,scale={w}:{h}",
        note="NVDEC, full-resolution PCIe crossing, downscale on the CPU after it",
    ),
    "nvdec-1080": Variant(
        name="nvdec-1080",
        gpu=True,
        emits="source",
        decoder=["-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-c:v", "h264_cuvid"],
        filters="hwdownload,format=nv12",
        note="NVDEC, 1080p BGR down the pipe, SceneManager resizes (the naive arm)",
    ),
    "cpu-pipe": Variant(
        name="cpu-pipe",
        gpu=False,
        emits="detect",
        decoder=[],
        filters="scale={w}:{h}",
        note="control: same pipe, same detector, CPU decode — isolates NVDEC "
        "from the subprocess architecture",
    ),
}


def build_command(
    video: Path,
    variant: Variant,
    detect_size: tuple[int, int],
    *,
    seconds: float | None = None,
    sink: str = "pipe",
) -> list[str]:
    limit = ["-t", str(seconds)] if seconds else []
    out = (
        ["-pix_fmt", "bgr24", "-f", "rawvideo", "-"]
        if sink == "pipe"
        else ["-f", "null", "-"]
    )
    return [
        "ffmpeg",
        "-v",
        "error",
        "-nostdin",
        *variant.input_args(detect_size),
        *limit,
        "-i",
        str(video),
        *variant.filter_args(detect_size),
        *out,
    ]


# -------------------------------------------------------------- the stream


def _pipe_stream_class() -> type:
    """A `VideoStream` whose decoder is an ffmpeg subprocess on the other end of a pipe.

    Built lazily for the same reason `keyframes._fused_stream_class()` is:
    `scenedetect` is a heavy import and this module is also imported for its
    tables. Only the four things `SceneManager.detect_scenes` actually touches
    are real — `frame_size`, `duration`, `position`, `read` — and `seek`/`reset`
    raise, because a pipe cannot do them and a silent no-op would look like a
    working seek to any future caller.

    `position` is derived from a frame counter rather than PTS, which is exact
    for the CFR H.264 this corpus is made of and is the one place this stream is
    weaker than the shipped PyAV path. `frames_read` is reported next to every
    timing so a variant that silently dropped frames cannot pass as a speedup.
    """
    import numpy as np
    from scenedetect.common import FrameTimecode
    from scenedetect.video_stream import VideoStream

    class FFmpegPipeStream(VideoStream):
        BACKEND_NAME = "ffmpeg-pipe"

        def __init__(
            self,
            path: Path,
            command: Sequence[str],
            *,
            width: int,
            height: int,
            fps: Fraction,
            duration_s: float,
        ) -> None:
            super().__init__()
            self._path = str(path)
            self._command = list(command)
            self._width = int(width)
            self._height = int(height)
            self._frame_rate = fps
            self._duration_frames = max(1, round(duration_s * float(fps)))
            self._bytes_per_frame = self._width * self._height * 3
            self._frames = 0
            self._proc: subprocess.Popen | None = None
            self._stderr = tempfile.TemporaryFile()
            self._eof = False
            self.bytes_read = 0

        # -- lifecycle

        def start(self) -> None:
            if self._proc is not None:
                return
            self._proc = subprocess.Popen(
                self._command,
                stdout=subprocess.PIPE,
                stderr=self._stderr,
                bufsize=0,
            )

        def close(self) -> dict[str, Any]:
            """Reap the child so its CPU lands in `RUSAGE_CHILDREN`, and read its complaints."""
            code: int | None = None
            if self._proc is not None:
                if self._proc.stdout is not None:
                    try:
                        self._proc.stdout.close()
                    except Exception:
                        pass
                if self._proc.poll() is None:
                    self._proc.terminate()
                try:
                    code = self._proc.wait(timeout=30)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
                    code = self._proc.wait(timeout=30)
                self._proc = None
            self._stderr.seek(0)
            message = self._stderr.read().decode("utf-8", "replace").strip()
            self._stderr.close()
            return {
                "returncode": code,
                "frames_read": self._frames,
                "bytes_read": self.bytes_read,
                "stderr": message[:500] or None,
            }

        # -- VideoStream interface

        @property
        def path(self) -> str:
            return self._path

        @property
        def name(self) -> str:
            return Path(self._path).stem

        @property
        def is_seekable(self) -> bool:
            return False

        @property
        def frame_size(self) -> tuple[int, int]:
            return (self._width, self._height)

        @property
        def aspect_ratio(self) -> float:
            return 1.0

        @property
        def frame_rate(self) -> Fraction:
            return self._frame_rate

        @property
        def duration(self) -> Any:
            return FrameTimecode(timecode=self._duration_frames, fps=self._frame_rate)

        @property
        def position(self) -> Any:
            # Same convention as `VideoStreamAv.position`: the first frame read
            # is presentation time 0, not one frame in.
            return FrameTimecode(timecode=max(0, self._frames - 1), fps=self._frame_rate)

        @property
        def position_ms(self) -> float:
            return (max(0, self._frames - 1) / float(self._frame_rate)) * 1000.0

        @property
        def frame_number(self) -> int:
            return self._frames

        def read(self, decode: bool = True) -> Any:
            if self._proc is None:
                self.start()
            assert self._proc is not None and self._proc.stdout is not None
            if self._eof:
                return False
            # bufsize=0 gives an unbuffered reader whose `read(n)` can return
            # short; `readexactly` semantics have to be built by hand or every
            # frame after the first partial read is torn.
            chunks: list[bytes] = []
            remaining = self._bytes_per_frame
            while remaining > 0:
                block = self._proc.stdout.read(remaining)
                if not block:
                    break
                chunks.append(block)
                remaining -= len(block)
            if remaining > 0:
                self._eof = True
                return False
            data = b"".join(chunks) if len(chunks) > 1 else chunks[0]
            self._frames += 1
            self.bytes_read += self._bytes_per_frame
            if not decode:
                return True
            # A view over immutable bytes: no copy, and nothing downstream
            # writes into the frame it is handed (the detector only reads).
            return np.frombuffer(data, dtype=np.uint8).reshape(
                self._height, self._width, 3
            )

        def reset(self) -> None:
            raise NotImplementedError("FFmpegPipeStream is forward-only")

        def seek(self, target: Any) -> None:
            raise NotImplementedError("FFmpegPipeStream is forward-only")

    return FFmpegPipeStream


# ------------------------------------------------------------------ detection


def detect_via_pipe(
    video: Path,
    variant: Variant,
    facts: dict[str, Any],
    kind: str,
    *,
    seconds: float | None = None,
) -> tuple[list[tuple[float, float]], float, dict[str, Any]]:
    """`detect_spans`, with the decode moved into an ffmpeg subprocess.

    Deliberately mirrors `keyframes.detect_spans` line for line — same detector,
    same `auto_downscale`, same `start_in_scene=True` — so the only difference
    between this and the baseline is where the pixels came from.
    """
    from scenedetect import SceneManager

    source = (facts["width"], facts["height"])
    detect_size = detection_size(source)
    emitted = detect_size if variant.emits == "detect" else source
    command = build_command(video, variant, detect_size, seconds=seconds)

    stream = _pipe_stream_class()(
        video,
        command,
        width=emitted[0],
        height=emitted[1],
        fps=Fraction(facts["fps_exact"]),
        duration_s=seconds or facts["duration_s"],
    )
    manager = SceneManager()
    manager.add_detector(make_detector(kind))
    manager.auto_downscale = True
    try:
        stream.start()
        manager.detect_scenes(video=stream, show_progress=False)
        scenes = manager.get_scene_list(start_in_scene=True)
    finally:
        info = stream.close()
    info["command"] = " ".join(command)
    info["emitted_size"] = list(emitted)
    spans = [(float(start.seconds), float(end.seconds)) for start, end in scenes]
    return spans, float(stream.duration.seconds), info


def compare_boundaries(
    baseline: Sequence[tuple[float, float]], candidate: Sequence[tuple[float, float]]
) -> dict[str, Any]:
    """§8.5/§10's boundary diff, in the direction that matters for provenance.

    For every cut the baseline found, where did the candidate put it? A cut that
    vanished shows up as a large delta to its nearest survivor, which is exactly
    how §10's two phantom cuts were found.
    """
    base_cuts = [round(start, 4) for start, _ in baseline]
    cand_cuts = [round(start, 4) for start, _ in candidate]
    moved: list[dict[str, float]] = []
    for cut in base_cuts:
        nearest = min(cand_cuts, key=lambda f: abs(f - cut), default=None)
        if nearest is None:
            continue
        delta = round(abs(nearest - cut), 4)
        if delta:
            moved.append({"baseline_s": cut, "variant_s": nearest, "delta_s": delta})
    moved.sort(key=lambda entry: entry["delta_s"], reverse=True)
    return {
        "baseline": len(base_cuts),
        "variant": len(cand_cuts),
        "count_delta": len(cand_cuts) - len(base_cuts),
        "unchanged": len(base_cuts) - len(moved),
        "moved": len(moved),
        "moved_over_100ms": sum(1 for m in moved if m["delta_s"] > 0.1),
        "moved_over_1s": sum(1 for m in moved if m["delta_s"] > 1.0),
        "worst": moved[:10],
        "drift": drift(base_cuts, cand_cuts),
    }


def extract_for(
    video: Path, spans: Sequence[tuple[float, float]], duration: float, work_dir: Path, label: str
) -> tuple[Timing, list[KeyframeDraft], int]:
    shots = thin(subdivide(spans, duration, MAX_SHOT_SECONDS), BUDGET)
    target = work_dir / f"gpu-{label}"
    if target.exists():
        shutil.rmtree(target)
    timing, drafts = Timing.measure(
        extract_from_shots,
        video,
        shots,
        target,
        lambda ordinal, t_s: f"{ordinal:05d}-{int(round(t_s * 1000)):09d}.jpg",
        candidates_per_shot=CANDIDATES,
    )
    return timing, drafts, len(shots)


# -------------------------------------------------------------- the ceiling


def drain(command: Sequence[str], bytes_per_frame: int) -> tuple[Timing, dict[str, Any]]:
    """Run a chain and throw every byte away — the feed with no detector on it.

    This is probe 3, and it is the number the recommendation turns on: pass-1
    wall is `max(decode + prepare, detect)` (§8.5), so a feed that is already
    faster than the detector means no further decoder work can buy anything.
    """

    def run() -> dict[str, Any]:
        stderr = tempfile.TemporaryFile()
        proc = subprocess.Popen(
            list(command), stdout=subprocess.PIPE, stderr=stderr, bufsize=0
        )
        total = 0
        assert proc.stdout is not None
        while True:
            block = proc.stdout.read(1 << 20)
            if not block:
                break
            total += len(block)
        proc.stdout.close()
        code = proc.wait()
        stderr.seek(0)
        message = stderr.read().decode("utf-8", "replace").strip()
        stderr.close()
        return {
            "returncode": code,
            "bytes": total,
            "frames": total // bytes_per_frame if bytes_per_frame else None,
            "stderr": message[:300] or None,
        }

    return Timing.measure(run)


def detector_floor(
    video: Path, facts: dict[str, Any], kind: str, seconds: float, cache: Path
) -> dict[str, Any]:
    """The detector with **no decoder at all**: frames read from a raw file.

    Probe 3's other half. A decode variant's wall is `max(feed, detect)` and the
    drain probes give `feed`; this gives `detect`, by pre-baking `seconds` of
    detection-resolution BGR to disk and replaying it with `cat`. Whatever is
    left between this and the best decode variant is all that any future
    decoder work — NVDEC, a worker endpoint, anything — could ever win.

    The file is ~110 KB/frame, so a 300 s clip is ~2 GB: kept out of `/tmp`
    (tmpfs on this box) and read twice so the second pass is served from page
    cache and measures the detector rather than the disk.
    """
    from scenedetect import SceneManager

    detect_size = detection_size((facts["width"], facts["height"]))
    bpf = detect_size[0] * detect_size[1] * 3
    if not cache.exists() or cache.stat().st_size < bpf:
        cache.parent.mkdir(parents=True, exist_ok=True)
        command = build_command(
            video, VARIANTS["nvdec-scale"], detect_size, seconds=seconds
        )
        with cache.open("wb") as handle:
            subprocess.run(command, stdout=handle, check=True)
        print(f"  [floor] baked {cache.stat().st_size / (1 << 20):.0f} MiB of raw frames")
    warm_cache(cache)

    def run() -> tuple[int, int]:
        stream = _pipe_stream_class()(
            cache,
            ["cat", str(cache)],
            width=detect_size[0],
            height=detect_size[1],
            fps=Fraction(facts["fps_exact"]),
            duration_s=seconds,
        )
        manager = SceneManager()
        manager.add_detector(make_detector(kind))
        manager.auto_downscale = True
        try:
            stream.start()
            manager.detect_scenes(video=stream, show_progress=False)
            scenes = manager.get_scene_list(start_in_scene=True)
        finally:
            info = stream.close()
        return len(scenes), info["frames_read"]

    timings = [Timing.measure(run) for _ in range(2)]
    best = min(timings, key=lambda pair: pair[0].wall)
    scenes, frames = best[1]
    entry = {
        "seconds_of_video": seconds,
        "wall_s": [t.wall for t, _ in timings],
        "best_wall_s": round(best[0].wall, 2),
        "cpu_s": [t.cpu for t, _ in timings],
        "frames": frames,
        "scenes": scenes,
        "fps": round(frames / best[0].wall, 0) if best[0].wall else None,
        "realtime_x": round(seconds / best[0].wall, 1) if best[0].wall else None,
        "note": "no decoder: detection-resolution BGR replayed from disk with cat",
    }
    print(
        f"  [floor] detector alone: {entry['best_wall_s']}s for {frames} frames "
        f"({entry['fps']} fps, {entry['realtime_x']}x realtime)"
    )
    return entry


def null_sink(command: Sequence[str]) -> tuple[Timing, dict[str, Any]]:
    """`-f null -`: decode and discard inside ffmpeg, nothing crosses a pipe."""

    def run() -> dict[str, Any]:
        completed = subprocess.run(list(command), capture_output=True, text=True)
        return {
            "returncode": completed.returncode,
            "stderr": completed.stderr.strip()[:300] or None,
        }

    return Timing.measure(run)


def ceiling_probe(
    video: Path, facts: dict[str, Any], seconds: float, names: Sequence[str]
) -> dict[str, Any]:
    source = (facts["width"], facts["height"])
    detect_size = detection_size(source)
    out: dict[str, Any] = {"seconds_of_video": seconds, "runs": {}}

    # Decode only, output discarded inside ffmpeg: the raw decoder ceilings.
    for label, variant_name in (("null-cpu", "cpu-pipe"), ("null-nvdec", "nvdec-scale")):
        variant = VARIANTS[variant_name]
        command = [
            "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            *variant.input_args(detect_size),
            "-t",
            str(seconds),
            "-i",
            str(video),
            "-f",
            "null",
            "-",
        ]
        with VramSampler() as vram:
            timing, info = null_sink(command)
        out["runs"][label] = {
            **timing.as_dict(),
            **info,
            "realtime_x": round(seconds / timing.wall, 1) if timing.wall else None,
            "fps": round(seconds * float(facts["fps"]) / timing.wall, 0) if timing.wall else None,
            "vram": vram.as_dict() if variant.gpu else None,
            "command": " ".join(command),
        }
        print(
            f"  [ceiling] {label}: {timing.wall:.1f}s wall, {timing.cpu:.1f}s cpu, "
            f"{out['runs'][label]['realtime_x']}x realtime"
        )

    # The same chains the detector is fed by, drained instead of detected on.
    for name in names:
        variant = VARIANTS[name]
        emitted = detect_size if variant.emits == "detect" else source
        bpf = emitted[0] * emitted[1] * 3
        command = build_command(video, variant, detect_size, seconds=seconds)
        with VramSampler() as vram:
            timing, info = drain(command, bpf)
        out["runs"][f"drain-{name}"] = {
            **timing.as_dict(),
            **info,
            "emitted_size": list(emitted),
            "mib_over_pipe": round(info["bytes"] / (1 << 20), 1),
            "pipe_mib_per_s": round(info["bytes"] / (1 << 20) / timing.wall, 1)
            if timing.wall
            else None,
            "realtime_x": round(seconds / timing.wall, 1) if timing.wall else None,
            "fps": round((info["frames"] or 0) / timing.wall, 0) if timing.wall else None,
            "vram": vram.as_dict() if variant.gpu else None,
            "command": " ".join(command),
        }
        entry = out["runs"][f"drain-{name}"]
        print(
            f"  [ceiling] drain-{name}: {timing.wall:.1f}s wall, {timing.cpu:.1f}s cpu, "
            f"{entry['fps']} fps, {entry['mib_over_pipe']} MiB over the pipe "
            f"({entry['pipe_mib_per_s']} MiB/s)"
        )
    return out


# ------------------------------------------------------------------- pass 2


def seek_probe(
    video: Path,
    spans: Sequence[tuple[float, float]],
    duration: float,
    facts: dict[str, Any],
    shot_sample: int,
) -> dict[str, Any]:
    """Pass 2's seek, three ways, on the *real* candidate timestamps.

    `_sharpest_in` picks `linspace(low, high, 9)` inside each shot and seeks
    absolutely to each. This times the same timestamps through (a) the shipped
    cv2 capture, (b) `ffmpeg -ss ... -frames:v 1` on the CPU and (c) the same on
    `h264_cuvid`. (b) exists to separate the decoder from the process spawn:
    an NVDEC seek that wins on decode and loses on `fork`/`exec` has not won.

    A sample of shots, not all of them, because the answer only has to be good
    enough to say whether a full NVDEC extractor is worth writing.
    """
    import cv2

    shots = thin(subdivide(spans, duration, MAX_SHOT_SECONDS), BUDGET)
    step = max(1, len(shots) // shot_sample)
    sample = shots[::step][:shot_sample]
    targets: list[float] = []
    for shot in sample:
        span = shot.span
        low = shot.start_s + span * EDGE_TRIM
        high = shot.end_s - span * EDGE_TRIM
        if high <= low:
            low = high = (shot.start_s + shot.end_s) / 2.0
        targets.extend(
            [low + (high - low) * i / (CANDIDATES - 1) for i in range(CANDIDATES)]
        )

    def cv2_seeks() -> int:
        capture = cv2.VideoCapture(str(video))
        try:
            hits = 0
            for target in targets:
                capture.set(cv2.CAP_PROP_POS_MSEC, float(target) * 1000.0)
                ok, frame = capture.read()
                if ok and frame is not None:
                    hits += 1
            return hits
        finally:
            capture.release()

    def ffmpeg_seeks(decoder: list[str], filters: str = "") -> int:
        # `hits` is a correctness gate, not decoration: the first version of this
        # probe asked for `rawvideo` from a chain still holding CUDA frames, and
        # ffmpeg refused every one of them. It looked like an 885 ms seek and was
        # really 885 ms of spawning a process to print an error.
        hits = 0
        for target in targets:
            command = [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                *decoder,
                "-ss",
                f"{target:.3f}",
                "-i",
                str(video),
                *(["-vf", filters] if filters else []),
                "-frames:v",
                "1",
                "-f",
                "rawvideo",
                "-pix_fmt",
                "bgr24",
                "-",
            ]
            completed = subprocess.run(command, capture_output=True)
            if completed.returncode == 0 and completed.stdout:
                hits += 1
        return hits

    results: dict[str, Any] = {
        "shots_total": len(shots),
        "shots_sampled": len(sample),
        "seeks": len(targets),
        "runs": {},
    }
    for label, fn in (
        ("cv2-capture", cv2_seeks),
        ("ffmpeg-cpu", lambda: ffmpeg_seeks([])),
        (
            "ffmpeg-nvdec",
            lambda: ffmpeg_seeks(
                ["-hwaccel", "cuda", "-hwaccel_output_format", "cuda", "-c:v", "h264_cuvid"],
                "hwdownload,format=nv12",
            ),
        ),
    ):
        with VramSampler() as vram:
            timing, hits = Timing.measure(fn)
        per_seek = timing.wall / len(targets) if targets else None
        results["runs"][label] = {
            **timing.as_dict(),
            "hits": hits,
            "ms_per_seek": round(per_seek * 1000, 1) if per_seek else None,
            "projected_full_pass_s": round(per_seek * len(shots) * CANDIDATES, 1)
            if per_seek
            else None,
            "vram": vram.as_dict() if label == "ffmpeg-nvdec" else None,
        }
        print(
            f"  [seek] {label}: {timing.wall:.1f}s for {len(targets)} seeks "
            f"({results['runs'][label]['ms_per_seek']} ms each), "
            f"projected full pass {results['runs'][label]['projected_full_pass_s']}s"
        )
    return results


def shot_shape(spans: Sequence[tuple[float, float]], duration: float) -> dict[str, Any]:
    """Why one shot list extracts slower than another, if the shape explains it.

    §10 left "fused extract 56.5 s vs legacy 34.5 s on 51 vs 49 shots"
    unexplained. Pass-2 cost is `seeks x (seek + decode-forward)`, and the only
    per-shot variable in that is how far apart consecutive candidates are: cv2's
    FFmpeg backend reads *forward* to a nearby target and performs a real
    keyframe seek for a distant one. So the candidate gap distribution, not the
    shot count, is what to look at.
    """
    shots = thin(subdivide(spans, duration, MAX_SHOT_SECONDS), BUDGET)
    spans_s = [shot.span for shot in shots]
    gaps = [
        (shot.span * (1 - 2 * EDGE_TRIM)) / (CANDIDATES - 1) for shot in shots if shot.span > 0
    ]
    return {
        "scenes": len(spans),
        "shots": len(shots),
        "total_shot_seconds": round(sum(spans_s), 1),
        "shot_span_mean_s": round(statistics.fmean(spans_s), 2) if spans_s else None,
        "shot_span_median_s": round(statistics.median(spans_s), 2) if spans_s else None,
        "shot_span_max_s": round(max(spans_s), 2) if spans_s else None,
        "candidate_gap_mean_s": round(statistics.fmean(gaps), 3) if gaps else None,
        "candidate_gap_median_s": round(statistics.median(gaps), 3) if gaps else None,
        # A gap under one GOP (~2 s here) is a candidate cv2 can reach by
        # reading forward; a gap over it is a guaranteed keyframe seek.
        "gaps_under_2s": sum(1 for g in gaps if g < 2.0),
        "gaps_over_2s": sum(1 for g in gaps if g >= 2.0),
        "seeks": len(shots) * CANDIDATES,
    }


# --------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--video", type=Path, required=True)
    parser.add_argument("--repeats", type=int, default=2)
    parser.add_argument("--kind", default="screencast", choices=("screencast", "talking_head"))
    parser.add_argument(
        "--variants",
        default="nvdec-scale,nvdec-resize,cpu-pipe",
        help=f"comma-separated, from: {','.join(VARIANTS)} (empty to skip detection)",
    )
    parser.add_argument(
        "--variant-repeats",
        default="",
        metavar="NAME=N,...",
        help="override --repeats for expensive variants, e.g. nvdec-1080=1",
    )
    parser.add_argument("--no-baseline", action="store_true", help="skip the CPU-fused baseline")
    parser.add_argument("--ceiling", action="store_true", help="probe 3: the feed with no detector")
    parser.add_argument("--ceiling-seconds", type=float, default=300.0)
    parser.add_argument(
        "--kept-frames",
        action="store_true",
        help="also run pass 2 per variant and diff the frames the stage keeps",
    )
    parser.add_argument("--seek-probe", type=int, default=0, metavar="SHOTS")
    parser.add_argument(
        "--legacy-shape",
        action="store_true",
        help="also detect with fused=False and compare shot-list shape (§10's "
        "unexplained extract gap)",
    )
    parser.add_argument("--work-dir", type=Path)
    parser.add_argument(
        "--raw-cache",
        type=Path,
        help="where the detector-floor probe bakes its raw frames (~110 KB/frame; "
        "keep it off tmpfs)",
    )
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    video: Path = args.video
    work_dir = args.work_dir or Path(tempfile.mkdtemp(prefix="kf-gpu-"))
    work_dir.mkdir(parents=True, exist_ok=True)
    overrides = dict(
        (part.split("=", 1)[0], int(part.split("=", 1)[1]))
        for part in args.variant_repeats.split(",")
        if "=" in part
    )

    warm_cache(video)
    facts = probe(video)
    detect_size = detection_size((facts["width"], facts["height"]))
    print(
        f"== {facts['file']}: {facts['width']}x{facts['height']} {facts['fps']:.2f} fps, "
        f"{facts['duration_s']}s, detector sees {detect_size[0]}x{detect_size[1]}"
    )

    envelope: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": {"cpus": os.cpu_count()},
        "video": facts,
        "detect_size": list(detect_size),
        "settings": {
            "detector": args.kind,
            "repeats": args.repeats,
            "candidates_per_shot": CANDIDATES,
            "max_shot_seconds": MAX_SHOT_SECONDS,
            "budget": BUDGET,
        },
        "detection": {},
    }

    baseline_spans: list[tuple[float, float]] | None = None
    baseline_duration = facts["duration_s"]
    baseline_drafts: list[KeyframeDraft] | None = None

    if not args.no_baseline:
        timings: list[Timing] = []
        spans: list[tuple[float, float]] = []
        duration = 0.0
        for repeat in range(args.repeats):
            timing, payload = Timing.measure(detect_spans, video, kind=args.kind, fused=True)
            spans, duration = payload
            timings.append(timing)
            print(
                f"  [cpu-fused] detect #{repeat + 1}: {timing.wall:.1f}s wall, "
                f"{timing.cpu:.1f}s cpu, {len(spans)} scenes"
            )
        baseline_spans, baseline_duration = spans, duration
        entry: dict[str, Any] = {
            "note": "shipped path: detect_spans(fused=True), PyAV, no subprocess",
            "wall_s": [t.wall for t in timings],
            "best_wall_s": round(min(t.wall for t in timings), 2),
            "cpu_s": [t.cpu for t in timings],
            "scenes": len(spans),
        }
        if args.kept_frames:
            timing, drafts, shots = extract_for(
                video, spans, duration, work_dir, "cpu-fused"
            )
            baseline_drafts = drafts
            entry["extract"] = {**timing.as_dict(), "shots": shots, "keyframes": len(drafts)}
            print(
                f"  [cpu-fused] extract: {timing.wall:.1f}s, {len(drafts)} keyframes "
                f"from {shots} shots"
            )
        envelope["detection"]["cpu-fused"] = entry

    names = [n.strip() for n in args.variants.split(",") if n.strip()]
    for name in names:
        if name not in VARIANTS:
            raise SystemExit(f"unknown variant {name!r}; have {','.join(VARIANTS)}")
        variant = VARIANTS[name]
        repeats = overrides.get(name, args.repeats)
        timings = []
        spans = []
        duration = 0.0
        info: dict[str, Any] = {}
        vram_summary: dict[str, Any] | None = None
        for repeat in range(repeats):
            with VramSampler() as vram:
                timing, payload = Timing.measure(
                    detect_via_pipe, video, variant, facts, args.kind
                )
            spans, duration, info = payload
            timings.append(timing)
            if variant.gpu:
                vram_summary = vram.as_dict()
            print(
                f"  [{name}] detect #{repeat + 1}: {timing.wall:.1f}s wall, "
                f"{timing.cpu:.1f}s cpu ({timing.cpu_children:.1f}s in ffmpeg), "
                f"{info['frames_read']} frames, {len(spans)} scenes"
            )
            if info.get("stderr"):
                print(f"  [{name}] ffmpeg said: {info['stderr']}")
        entry = {
            "note": variant.note,
            "emits": variant.emits,
            "gpu": variant.gpu,
            "command": info.get("command"),
            "wall_s": [t.wall for t in timings],
            "best_wall_s": round(min(t.wall for t in timings), 2),
            "cpu_s": [t.cpu for t in timings],
            "cpu_children_s": [t.cpu_children for t in timings],
            "frames_read": info.get("frames_read"),
            "scenes": len(spans),
            "vram": vram_summary,
        }
        if baseline_spans is not None:
            entry["speedup_vs_fused"] = round(
                envelope["detection"]["cpu-fused"]["best_wall_s"] / entry["best_wall_s"], 2
            )
            entry["boundaries"] = compare_boundaries(baseline_spans, spans)
        if args.kept_frames:
            timing, drafts, shots = extract_for(video, spans, duration, work_dir, name)
            entry["extract"] = {**timing.as_dict(), "shots": shots, "keyframes": len(drafts)}
            print(
                f"  [{name}] extract: {timing.wall:.1f}s, {len(drafts)} keyframes "
                f"from {shots} shots"
            )
            if baseline_drafts is not None:
                frames = pair_keyframes(baseline_drafts, drafts)
                entry["kept_frames"] = frames
                entry["identical_kept_frames"] = (
                    f"{frames['identical_frames']}/{frames['matched']}"
                )
                print(
                    f"  [{name}] kept frames identical: "
                    f"{entry['identical_kept_frames']}, phash256 mean "
                    f"{frames['phash256_mean']}"
                )
        if "boundaries" in entry:
            bounds = entry["boundaries"]
            print(
                f"  [{name}] vs fused: x{entry.get('speedup_vs_fused')}; cuts "
                f"{bounds['baseline']}->{bounds['variant']}, {bounds['moved']} moved "
                f"({bounds['moved_over_100ms']} by >100ms, {bounds['moved_over_1s']} by >1s)"
            )
        envelope["detection"][name] = entry

    if args.ceiling:
        print("== ceiling (no detector)")
        drain_names = [n for n in names] or ["nvdec-scale", "cpu-pipe"]
        for extra in ("nvdec-1080", "nvdec-cpuscale"):
            if extra not in drain_names:
                drain_names.append(extra)
        envelope["ceiling"] = ceiling_probe(
            video, facts, args.ceiling_seconds, drain_names
        )
        envelope["ceiling"]["detector_floor"] = detector_floor(
            video,
            facts,
            args.kind,
            args.ceiling_seconds,
            (args.raw_cache or (work_dir / "floor.raw")),
        )

    if args.seek_probe and baseline_spans is not None:
        print("== pass 2 seek probe")
        envelope["seek_probe"] = seek_probe(
            video, baseline_spans, baseline_duration, facts, args.seek_probe
        )

    if args.legacy_shape and baseline_spans is not None:
        print("== legacy vs fused shot-list shape")
        timing, payload = Timing.measure(detect_spans, video, kind=args.kind, fused=False)
        legacy_spans, legacy_duration = payload
        print(f"  [legacy] detect: {timing.wall:.1f}s wall, {len(legacy_spans)} scenes")
        shapes = {
            "legacy": shot_shape(legacy_spans, legacy_duration),
            "fused": shot_shape(baseline_spans, baseline_duration),
        }
        if args.kept_frames:
            legacy_timing, _, legacy_shots = extract_for(
                video, legacy_spans, legacy_duration, work_dir, "legacy"
            )
            shapes["legacy"]["extract"] = {**legacy_timing.as_dict(), "shots": legacy_shots}
            print(f"  [legacy] extract: {legacy_timing.wall:.1f}s from {legacy_shots} shots")
        envelope["shot_shape"] = shapes
        for label, shape in shapes.items():
            print(
                f"  [{label}] {shape['shots']} shots, span median "
                f"{shape['shot_span_median_s']}s, candidate gap median "
                f"{shape['candidate_gap_median_s']}s, gaps>2s {shape['gaps_over_2s']}"
                f"/{shape['gaps_under_2s'] + shape['gaps_over_2s']}"
            )

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(envelope, indent=2))
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
