#!/usr/bin/env python3
"""Keyframe stage: single-stream vs dual-stream decode, on real videos.

A different question from ``run.py`` (which backend is faster) and from
``gpu_validation.py`` (does the VRAM come back): **is the keyframe stage paying
for pixels it never looks at?**

PySceneDetect analyses every frame at 256 px on the long edge (0.7.1
``compute_downscale_factor``), but it downscales *in the decode thread*, after
the full-resolution frame exists. So detection on a 1080p file and detection on
the 360p copy of the same upload run the same math on the same 256x144 pixels
while decoding ~6x fewer of them. This script measures that swap and — more
importantly — checks it changes nothing:

* **timings**: detect / extract, per path, per repeat, wall and CPU;
* **equivalence**: detected cut drift, shot count, and the phash distance
  between the frame each path actually chose.

It imports ``vidtheque_mcp.pipeline.keyframes`` on purpose: the point is to time
the shipped code, not a re-implementation of it. Nothing here touches the
database, the worker or ``$VIDTHEQUE_DATA_DIR``.

    uv run --no-sync python bench/keyframe_decode.py \\
        --pair full=/scratch/ID-1080p.mp4,detect=/scratch/ID-360p.mp4 \\
        --repeats 2 --out bench/results/raw/keyframe-decode.json

Media is not committed; point it at your own downloads. `--pts-probe` also
samples both files at fixed timestamps and hashes the frames, which is how the
"the two timelines are the same timeline" claim gets checked rather than
assumed.
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
import hashlib
import tempfile
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "mcp" / "src"))

from vidtheque_mcp.pipeline.keyframes import (  # noqa: E402
    KeyframeDraft,
    detect_spans,
    extract_from_shots,
    subdivide,
    thin,
)

MAX_SHOT_SECONDS = 25.0
BUDGET = 600
CANDIDATES = 9


# ------------------------------------------------------------------ plumbing


@dataclass
class Timing:
    wall: float
    cpu: float

    @classmethod
    def measure(cls, fn, *args, **kwargs) -> tuple["Timing", Any]:
        before = resource.getrusage(resource.RUSAGE_SELF)
        start = time.perf_counter()
        value = fn(*args, **kwargs)
        wall = time.perf_counter() - start
        after = resource.getrusage(resource.RUSAGE_SELF)
        cpu = (after.ru_utime - before.ru_utime) + (after.ru_stime - before.ru_stime)
        return cls(wall=round(wall, 3), cpu=round(cpu, 3)), value

    def as_dict(self) -> dict[str, float]:
        return {"wall_s": self.wall, "cpu_s": self.cpu}


def probe(path: Path) -> dict[str, Any]:
    """Stream facts that explain the timings: codec, size, fps, duration."""
    import av

    with av.open(str(path)) as container:
        stream = container.streams.video[0]
        rate = stream.average_rate
        return {
            "file": path.name,
            "bytes": path.stat().st_size,
            "codec": stream.codec_context.name,
            "profile": str(stream.codec_context.profile),
            "width": stream.codec_context.width,
            "height": stream.codec_context.height,
            "fps": float(rate) if rate else None,
            "fps_exact": str(rate),
            "duration_s": round(float(container.duration or 0) / 1e6, 3),
            "start_time_s": round(float(container.start_time or 0) / 1e6, 3),
        }


def warm_cache(path: Path) -> None:
    """Read the file once so no run pays for cold I/O and the others don't."""
    with path.open("rb") as handle:
        while handle.read(1 << 22):
            pass


def decode_only(path: Path) -> tuple[Timing, int]:
    """Every frame decoded and thrown away — decode cost with no detector."""
    import av

    def run() -> int:
        count = 0
        with av.open(str(path)) as container:
            stream = container.streams.video[0]
            stream.thread_type = "AUTO"
            for frame in container.decode(stream):
                count += 1
                del frame
        return count

    return Timing.measure(run)


# ---------------------------------------------------------------- equivalence


def hamming(a: bytes, b: bytes) -> int:
    return sum(bin(x ^ y).count("1") for x, y in zip(a, b))


def frame_bits(path: Path) -> tuple[int, bytes]:
    """The pipeline's own 64-bit and 256-bit hashes of a written JPEG."""
    import imagehash
    import numpy as np
    from PIL import Image

    with Image.open(path) as image:
        image.load()
        coarse = imagehash.phash(image, hash_size=8)
        fine = imagehash.phash(image, hash_size=16, highfreq_factor=4)
    coarse_bits = np.packbits(np.asarray(coarse.hash).flatten()).tobytes()
    fine_bits = np.packbits(np.asarray(fine.hash).flatten()).tobytes()
    return int.from_bytes(coarse_bits, "big"), fine_bits


def drift(a: list[float], b: list[float]) -> dict[str, Any]:
    """Nearest-neighbour drift between two boundary lists, both directions."""
    if not a or not b:
        return {"count_a": len(a), "count_b": len(b), "mean_s": None, "max_s": None}
    deltas = [min(abs(x - y) for y in b) for x in a]
    reverse = [min(abs(y - x) for x in a) for y in b]
    return {
        "count_a": len(a),
        "count_b": len(b),
        "mean_s": round(statistics.fmean(deltas), 4),
        "max_s": round(max(deltas), 4),
        "reverse_max_s": round(max(reverse), 4),
        "over_300ms": sum(1 for d in deltas if d > 0.3),
        "worst_a_s": round(a[deltas.index(max(deltas))], 3) if deltas else None,
    }


def pair_keyframes(a: list[KeyframeDraft], b: list[KeyframeDraft]) -> dict[str, Any]:
    """Match the two paths' chosen frames by timestamp, then compare pixels.

    The decision criterion lives here: a boundary that moved by 40 ms is
    irrelevant if the frame the stage *keeps* is the same frame.
    """
    pairs: list[dict[str, Any]] = []
    used: set[int] = set()
    for draft in a:
        best_index, best_delta = None, None
        for index, other in enumerate(b):
            if index in used:
                continue
            delta = abs(other.t_s - draft.t_s)
            if best_delta is None or delta < best_delta:
                best_index, best_delta = index, delta
        if best_index is None or best_delta is None or best_delta > 5.0:
            pairs.append({"a_t_s": draft.t_s, "b_t_s": None, "matched": False})
            continue
        used.add(best_index)
        other = b[best_index]
        coarse_a, fine_a = frame_bits(draft.absolute)
        coarse_b, fine_b = frame_bits(other.absolute)
        pairs.append(
            {
                "a_t_s": draft.t_s,
                "b_t_s": other.t_s,
                "matched": True,
                "dt_s": round(best_delta, 3),
                "phash64": bin(coarse_a ^ coarse_b).count("1"),
                "phash256": hamming(fine_a, fine_b),
                "sharpness_a": draft.sharpness,
                "sharpness_b": other.sharpness,
            }
        )
    matched = [p for p in pairs if p["matched"]]
    identical = [p for p in matched if p["phash256"] == 0]
    return {
        "count_a": len(a),
        "count_b": len(b),
        "matched": len(matched),
        "unmatched_a": len(pairs) - len(matched),
        "unmatched_b": len(b) - len(used),
        "identical_frames": len(identical),
        "phash256_mean": round(statistics.fmean([p["phash256"] for p in matched]), 2)
        if matched
        else None,
        "phash256_max": max((p["phash256"] for p in matched), default=None),
        "dt_max_s": max((p["dt_s"] for p in matched), default=None),
        "sharpness_ratio_mean": round(
            statistics.fmean(
                [
                    (p["sharpness_b"] / p["sharpness_a"])
                    for p in matched
                    if p["sharpness_a"]  # a zero-variance frame is a black frame
                ]
            ),
            3,
        )
        if matched
        else None,
        "pairs": pairs,
    }


def pts_probe(full: Path, small: Path, stamps: list[float]) -> list[dict[str, Any]]:
    """Do the two streams agree on what is on screen at t?

    Seek both to the same timestamp, hash both frames at a common size. If the
    timelines were offset, this is where it shows up — before any conclusion
    rests on them lining up.
    """
    import cv2
    import imagehash
    import numpy as np
    from PIL import Image

    def grab(path: Path, t: float) -> tuple[float, Any]:
        capture = cv2.VideoCapture(str(path))
        try:
            capture.set(cv2.CAP_PROP_POS_MSEC, t * 1000.0)
            ok, frame = capture.read()
            if not ok:
                return -1.0, None
            actual = capture.get(cv2.CAP_PROP_POS_MSEC) / 1000.0
            frame = cv2.resize(frame, (320, 180), interpolation=cv2.INTER_AREA)
            image = Image.fromarray(cv2.cvtColor(frame, cv2.COLOR_BGR2RGB))
            return actual, imagehash.phash(image, hash_size=16, highfreq_factor=4)
        finally:
            capture.release()

    out: list[dict[str, Any]] = []
    for stamp in stamps:
        t_full, hash_full = grab(full, stamp)
        t_small, hash_small = grab(small, stamp)
        if hash_full is None or hash_small is None:
            out.append({"t_s": stamp, "error": "no frame"})
            continue
        distance = int(np.count_nonzero(np.asarray(hash_full.hash) ^ np.asarray(hash_small.hash)))
        out.append(
            {
                "t_s": stamp,
                "landed_full_s": round(t_full, 3),
                "landed_small_s": round(t_small, 3),
                "landed_delta_s": round(abs(t_full - t_small), 3),
                "phash256": distance,
            }
        )
    return out


# --------------------------------------------------------------------- runs


@dataclass
class PathResult:
    name: str
    detect: list[Timing] = field(default_factory=list)
    extract: list[Timing] = field(default_factory=list)
    cuts: list[float] = field(default_factory=list)
    shots: int = 0
    drafts: list[KeyframeDraft] = field(default_factory=list)

    def summary(self) -> dict[str, Any]:
        detect_wall = [t.wall for t in self.detect]
        extract_wall = [t.wall for t in self.extract]
        return {
            "detect_wall_s": [round(w, 2) for w in detect_wall],
            "extract_wall_s": [round(w, 2) for w in extract_wall],
            "detect_best_s": round(min(detect_wall), 2),
            "extract_best_s": round(min(extract_wall), 2),
            "stage_best_s": round(min(detect_wall) + min(extract_wall), 2),
            "detect_cpu_s": [t.cpu for t in self.detect],
            "cuts": len(self.cuts),
            "shots": self.shots,
            "keyframes": len(self.drafts),
        }


def run_path(
    name: str,
    detect_file: Path,
    extract_file: Path,
    out_dir: Path,
    repeats: int,
    kind: str,
) -> PathResult:
    result = PathResult(name=name)
    for repeat in range(repeats):
        timing, payload = Timing.measure(detect_spans, detect_file, kind=kind)
        spans, duration = payload
        result.detect.append(timing)
        print(f"  [{name}] detect #{repeat + 1}: {timing.wall:.1f}s wall, {timing.cpu:.1f}s cpu")
        shots = thin(subdivide(spans, duration, MAX_SHOT_SECONDS), BUDGET)
        target = out_dir / f"{name}-{repeat}"
        if target.exists():
            shutil.rmtree(target)
        timing, drafts = Timing.measure(
            extract_from_shots,
            extract_file,
            shots,
            target,
            lambda ordinal, t_s: f"{ordinal:05d}-{int(round(t_s * 1000)):09d}.jpg",
            candidates_per_shot=CANDIDATES,
        )
        result.extract.append(timing)
        print(
            f"  [{name}] extract #{repeat + 1}: {timing.wall:.1f}s wall, "
            f"{len(drafts)} keyframes from {len(shots)} shots"
        )
        result.cuts = [start for start, _ in spans]
        result.shots = len(shots)
        result.drafts = drafts
    return result


def threading_probe(path: Path, kind: str, modes: Sequence[str]) -> dict[str, Any]:
    """The other lever on pass 1: how PyAV is told to thread the decode.

    `open_video(backend="pyav")` leaves the codec context on its default, which
    for H.264 is SLICE threading only. `threading_mode="AUTO"` adds frame
    threading. Both decode the same frames in the same order, so this reports
    the cut list alongside the time — a speedup that moves a cut is not this
    lever, it is a different one.
    """
    from scenedetect import SceneManager, open_video

    from vidtheque_mcp.pipeline.keyframes import make_detector

    out: dict[str, Any] = {}
    for mode in modes:
        video = open_video(str(path), backend="pyav", threading_mode=mode)
        manager = SceneManager()
        manager.add_detector(make_detector(kind))
        manager.auto_downscale = True
        start = time.perf_counter()
        manager.detect_scenes(video=video, show_progress=False)
        elapsed = time.perf_counter() - start
        scenes = manager.get_scene_list(start_in_scene=True)
        cuts = [round(float(begin.seconds), 3) for begin, _ in scenes]
        out[mode] = {
            "wall_s": round(elapsed, 2),
            "cuts": len(cuts),
            "last_frame": int(video.position.frame_num),
            "cut_list_sha": hashlib.sha256(repr(cuts).encode()).hexdigest()[:16],
        }
        print(f"  threading {mode}: {elapsed:.1f}s, {len(cuts)} cuts")
    shas = {v["cut_list_sha"] for v in out.values()}
    out["identical_cut_lists"] = len(shas) == 1
    return out


def extract_workers_probe(
    path: Path,
    kind: str,
    counts: Sequence[int],
    decode_threads: Sequence[int],
    work_dir: Path,
) -> dict[str, Any]:
    """Pass 2's lever: how many threads seek, and how many threads each decode.

    Detection runs once and every configuration extracts from the *same* shot
    list, so the only variable is the extractor. Like ``threading_probe``, this
    reports the answer next to the time: pass 2 parallelises across shots only
    because ``_sharpest_in`` seeks absolutely, and a configuration whose drafts
    differ from the serial baseline has broken that assumption rather than
    earned a speedup.

    The baseline (1 worker, OpenCV's own thread default) is always run first and
    is what everything else is compared against.
    """
    spans, duration = detect_spans(path, kind=kind)
    shots = thin(subdivide(spans, duration, MAX_SHOT_SECONDS), BUDGET)
    print(f"  [workers] {len(shots)} shots from {len(spans)} detected scenes")

    runs: list[dict[str, Any]] = []
    baseline: list[KeyframeDraft] | None = None
    for threads in decode_threads:
        for workers in counts:
            target = work_dir / f"workers-{workers}-t{threads}"
            if target.exists():
                shutil.rmtree(target)
            timing, drafts = Timing.measure(
                extract_from_shots,
                path,
                shots,
                target,
                lambda ordinal, t_s: f"{ordinal:05d}-{int(round(t_s * 1000)):09d}.jpg",
                candidates_per_shot=CANDIDATES,
                workers=workers,
                decode_threads=threads,
            )
            if baseline is None:
                baseline = drafts
            same = pair_keyframes(baseline, drafts)
            entry = {
                "workers": workers,
                "decode_threads": threads,
                **timing.as_dict(),
                "keyframes": len(drafts),
                "identical_to_baseline": (
                    len(drafts) == len(baseline)
                    and same["matched"] == len(baseline)
                    and same["identical_frames"] == len(baseline)
                ),
                "vs_baseline": same,
            }
            runs.append(entry)
            print(
                f"  [workers] w={workers} t={threads}: {timing.wall:.1f}s wall, "
                f"{timing.cpu:.1f}s cpu, {len(drafts)} keyframes, "
                f"identical={entry['identical_to_baseline']}"
            )
    best = min(runs, key=lambda r: r["wall"])
    return {
        "shots": len(shots),
        "runs": runs,
        "baseline_wall_s": runs[0]["wall"],
        "best": {"workers": best["workers"], "decode_threads": best["decode_threads"]},
        "best_speedup": round(runs[0]["wall"] / best["wall"], 2) if best["wall"] else None,
        # The only line that decides whether this ships.
        "all_identical": all(r["identical_to_baseline"] for r in runs),
    }


def nvdec_probe(path: Path, seconds: float) -> dict[str, Any]:
    """What the 3090 would do with the same decode, measured with ffmpeg alone.

    ``-c:v h264_cuvid`` + ``-f null`` : decode on the GPU, discard the output,
    never leave the process. Nothing about the worker or its lifecycle manager
    is involved — this is a ceiling, not a proposal.
    """
    out: dict[str, Any] = {}
    for label, args in (
        ("cpu", []),
        ("nvdec", ["-hwaccel", "cuda", "-c:v", "h264_cuvid"]),
    ):
        command = [
            "ffmpeg",
            "-v",
            "error",
            "-nostdin",
            *args,
            "-t",
            str(seconds),
            "-i",
            str(path),
            "-f",
            "null",
            "-",
        ]
        start = time.perf_counter()
        completed = subprocess.run(command, capture_output=True, text=True)
        elapsed = time.perf_counter() - start
        out[label] = {
            "ok": completed.returncode == 0,
            "wall_s": round(elapsed, 2),
            "realtime_x": round(seconds / elapsed, 1) if elapsed else None,
            "error": completed.stderr.strip()[:200] or None,
        }
    return out


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--pair",
        action="append",
        required=True,
        metavar="full=PATH,detect=PATH",
        help="one video: the full-resolution file and its detection stream",
    )
    parser.add_argument("--repeats", type=int, default=1)
    parser.add_argument("--kind", default="screencast", choices=("screencast", "talking_head"))
    parser.add_argument("--out", type=Path, help="write the result envelope here as JSON")
    parser.add_argument("--work-dir", type=Path, help="where keyframes go (default: a temp dir)")
    parser.add_argument("--decode-only", action="store_true", help="also time a bare decode")
    parser.add_argument("--pts-probe", action="store_true", help="check the timelines line up")
    parser.add_argument("--nvdec-probe", type=float, default=0.0, metavar="SECONDS")
    parser.add_argument(
        "--threading",
        metavar="MODES",
        help="comma-separated PyAV thread modes to compare on the full file, e.g. NONE,AUTO",
    )
    parser.add_argument(
        "--extract-workers",
        metavar="COUNTS",
        help="comma-separated pass-2 thread counts to compare, e.g. 1,2,4,8 "
        "(VIDTHEQUE_KEYFRAME_EXTRACT_WORKERS). Detection runs once; every count "
        "extracts from the same shots and is diffed against the 1-worker answer.",
    )
    parser.add_argument(
        "--decode-threads",
        metavar="COUNTS",
        default="0",
        help="comma-separated cv2.CAP_PROP_N_THREADS values to cross with "
        "--extract-workers (VIDTHEQUE_KEYFRAME_DECODE_THREADS). 0 = OpenCV's default.",
    )
    parser.add_argument(
        "--no-dual",
        action="store_true",
        help="skip the single-vs-dual comparison (for a threading-only run)",
    )
    args = parser.parse_args()

    work_dir = args.work_dir or Path(tempfile.mkdtemp(prefix="kf-bench-"))
    work_dir.mkdir(parents=True, exist_ok=True)
    envelope: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "host": {"cpus": os.cpu_count()},
        "settings": {
            "max_shot_seconds": MAX_SHOT_SECONDS,
            "candidates_per_shot": CANDIDATES,
            "budget": BUDGET,
            "detector": args.kind,
            "repeats": args.repeats,
        },
        "videos": [],
    }

    for spec in args.pair:
        parts = dict(part.split("=", 1) for part in spec.split(","))
        full, small = Path(parts["full"]), Path(parts["detect"])
        print(f"== {full.name} + {small.name}")
        warm_cache(full)
        warm_cache(small)
        entry: dict[str, Any] = {
            "full": probe(full),
            "detect": probe(small),
        }
        if args.decode_only:
            for label, path in (("full", full), ("detect", small)):
                timing, frames = decode_only(path)
                entry[label]["decode_only"] = timing.as_dict() | {"frames": frames}
                print(f"  decode-only {label}: {timing.wall:.1f}s for {frames} frames")
        if args.pts_probe:
            duration = entry["full"]["duration_s"]
            stamps = [round(duration * f, 2) for f in (0.05, 0.25, 0.5, 0.75, 0.95)]
            entry["pts_probe"] = pts_probe(full, small, stamps)
            print(f"  pts probe: {entry['pts_probe']}")

        if args.threading:
            modes = [m.strip().upper() for m in args.threading.split(",") if m.strip()]
            entry["threading"] = threading_probe(full, args.kind, modes)
            print(f"  identical cut lists: {entry['threading']['identical_cut_lists']}")
        if args.extract_workers:
            counts = [int(c) for c in args.extract_workers.split(",") if c.strip()]
            threads = [int(c) for c in args.decode_threads.split(",") if c.strip()]
            entry["extract_workers"] = extract_workers_probe(
                full, args.kind, counts, threads, work_dir
            )
            print(
                f"  best pass-2 config: {entry['extract_workers']['best']} "
                f"(x{entry['extract_workers']['best_speedup']} on the pass), "
                f"all identical: {entry['extract_workers']['all_identical']}"
            )
        if args.no_dual:
            if args.nvdec_probe:
                entry["nvdec"] = nvdec_probe(full, args.nvdec_probe)
                print(f"  nvdec: {entry['nvdec']}")
            envelope["videos"].append(entry)
            continue

        single = run_path("single", full, full, work_dir, args.repeats, args.kind)
        dual = run_path("dual", small, full, work_dir, args.repeats, args.kind)
        entry["single"] = single.summary()
        entry["dual"] = dual.summary()
        entry["speedup"] = {
            "detect": round(
                min(t.wall for t in single.detect) / min(t.wall for t in dual.detect), 2
            ),
            "stage": round(
                (min(t.wall for t in single.detect) + min(t.wall for t in single.extract))
                / (min(t.wall for t in dual.detect) + min(t.wall for t in dual.extract)),
                2,
            ),
        }
        entry["equivalence"] = {
            "cut_drift": drift(single.cuts, dual.cuts),
            "shot_delta": dual.shots - single.shots,
            "keyframes": pair_keyframes(single.drafts, dual.drafts),
        }
        summary = entry["equivalence"]["keyframes"]
        print(
            f"  speedup: detect x{entry['speedup']['detect']}, stage x{entry['speedup']['stage']}; "
            f"cuts {len(single.cuts)}->{len(dual.cuts)}, shots {single.shots}->{dual.shots}, "
            f"identical frames {summary['identical_frames']}/{summary['matched']}"
        )
        if args.nvdec_probe:
            entry["nvdec"] = nvdec_probe(full, args.nvdec_probe)
            print(f"  nvdec: {entry['nvdec']}")
        envelope["videos"].append(entry)

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(envelope, indent=2))
        print(f"wrote {args.out}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
