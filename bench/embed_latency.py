#!/usr/bin/env python3
"""Qwen3-VL-Embedding-2B on this box: query latency, VRAM, frame throughput.

`research/multimodal-embedding-2026-08-09.md` §4.3 says the single-query latency
for this model is **unpublished at any size** and makes it bench item #1: over
~150 ms warm and the unified plan is in trouble. §4.5 says our 1280 px keyframes
land near the model's own token knee, which is a throughput claim nobody has
measured either. This script measures both, at Tom's chosen configuration
(native 2048 dims, bf16, no quantization — see the addendum).

It deliberately does **not** import anything from `mcp/` or `worker/`: there is
no backend for this model yet, and inventing one before the numbers exist is the
wrong order. It loads the checkpoint through the loader Qwen ships in the repo
itself (`scripts/qwen3_vl_embedding.py` inside the HF snapshot), so what is timed
is the vendor's own code path rather than a re-implementation of it.

    uv run --no-project python bench/embed_latency.py all \\
        --frames-dir /home/dev/vidtheque-data/keyframes --frames 100 \\
        --out bench/runs/embed-latency.json

Nothing here writes to `$VIDTHEQUE_DATA_DIR`; keyframes are opened read-only.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import random
import statistics
import subprocess
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

MODEL_ID = "Qwen/Qwen3-VL-Embedding-2B"

# Asymmetric instructions, the way the card recommends: the query side carries
# the task, the document side keeps the default. Both legs of a `content_type=all`
# search would pay one of these.
QUERY_INSTRUCTION = "Given a search query, retrieve the video frame that answers it."
DOC_INSTRUCTION = "Represent the user's input."

# Realistic vidtheque queries — short, the way a search actually arrives.
DEFAULT_QUERIES = [
    "how does speculative decoding work",
    "slide showing evaluation harness architecture",
    "terminal output of a failing test run",
    "the pricing table for inference providers",
    "speaker on stage answering a question about agents",
]


# ---------------------------------------------------------------- model load


def _snapshot_dir(model_id: str = MODEL_ID) -> Path:
    from huggingface_hub import snapshot_download

    return Path(snapshot_download(model_id))


def load_embedder(dtype: str = "bfloat16", attn: str = "sdpa") -> tuple[Any, Path, float]:
    """Load Qwen's own `Qwen3VLEmbedder`, timing the load.

    Returns the embedder, the snapshot path, and the load wall time. The loader
    lives in the checkpoint (`scripts/qwen3_vl_embedding.py`), not on PyPI, so it
    is imported by path — that file is the model card's documented entry point.
    """
    import torch

    snapshot = _snapshot_dir()
    loader_path = snapshot / "scripts" / "qwen3_vl_embedding.py"
    if not loader_path.exists():
        raise SystemExit(f"vendor loader missing: {loader_path}")
    spec = importlib.util.spec_from_file_location("qwen3_vl_embedding", loader_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["qwen3_vl_embedding"] = module
    spec.loader.exec_module(module)

    torch_dtype = getattr(torch, dtype)
    start = time.perf_counter()
    try:
        embedder = module.Qwen3VLEmbedder(
            model_name_or_path=str(snapshot), dtype=torch_dtype, attn_implementation=attn
        )
    except TypeError:  # transformers < 4.56 spelling
        embedder = module.Qwen3VLEmbedder(
            model_name_or_path=str(snapshot), torch_dtype=torch_dtype, attn_implementation=attn
        )
    torch.cuda.synchronize()
    return embedder, snapshot, time.perf_counter() - start


def vram() -> dict[str, Any]:
    """Both numbers, because they answer different questions.

    `torch_*` is what this process asked CUDA for — the number a VRAM budget in
    `worker/` would be written against. `nvidia_smi_process_mb` is what the card
    actually loses to it (allocator slack + the CUDA context), which is the
    number that decides whether the llama.cpp lease still fits.
    """
    import torch

    out: dict[str, Any] = {
        "torch_allocated_mb": round(torch.cuda.memory_allocated() / 2**20, 1),
        "torch_reserved_mb": round(torch.cuda.memory_reserved() / 2**20, 1),
        "torch_max_allocated_mb": round(torch.cuda.max_memory_allocated() / 2**20, 1),
    }
    free, total = torch.cuda.mem_get_info()
    out["device_free_mb"] = round(free / 2**20, 1)
    out["device_total_mb"] = round(total / 2**20, 1)
    try:
        pid = str(subprocess.os.getpid())
        raw = subprocess.run(
            [
                "nvidia-smi",
                "--query-compute-apps=pid,used_memory",
                "--format=csv,noheader,nounits",
            ],
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout
        for line in raw.splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) == 2 and parts[0] == pid:
                out["nvidia_smi_process_mb"] = float(parts[1])
        out["nvidia_smi_all"] = raw.strip()
    except Exception as exc:  # nvidia-smi is a nicety, not a dependency
        out["nvidia_smi_error"] = str(exc)
    return out


# ------------------------------------------------------------------ helpers


@dataclass
class Latencies:
    samples: list[float]

    def summary(self) -> dict[str, Any]:
        ms = sorted(round(s * 1000, 2) for s in self.samples)
        return {
            "n": len(ms),
            "min_ms": ms[0],
            "median_ms": round(statistics.median(ms), 2),
            "mean_ms": round(statistics.fmean(ms), 2),
            "p90_ms": ms[min(len(ms) - 1, int(round(0.9 * (len(ms) - 1))))],
            "max_ms": ms[-1],
        }


def _sync() -> None:
    import torch

    torch.cuda.synchronize()


def embed(embedder: Any, items: Sequence[dict[str, Any]]) -> Any:
    vectors = embedder.process(list(items))
    _sync()
    return vectors


def mrl_slice(vectors: Any, dims: int) -> Any:
    """MRL truncation is a post-hoc slice + renormalise — that is the whole trick.

    Worth stating in code because the interesting consequence is what it does
    *not* cost: no second forward pass, so query latency cannot improve at the
    model, only at the vector search.
    """
    import torch.nn.functional as F

    return F.normalize(vectors[:, :dims], p=2, dim=-1)


# ------------------------------------------------------------------ measures


def measure_latency(
    embedder: Any, queries: Sequence[str], repeats: int, mrl_dims: Sequence[int]
) -> dict[str, Any]:
    """Cold-ish, then warm — the two numbers the addendum asks for.

    "Cold-ish" is the first forward pass after load: weights resident, kernels
    not yet autotuned, allocator empty. It is not a from-disk cold start (that is
    `load_wall_s`), and the distinction matters because `EMBED_RESIDENT=1` means
    production only ever pays the warm number.
    """
    first = []
    for query in queries[:2]:
        start = time.perf_counter()
        embed(embedder, [{"text": query, "instruction": QUERY_INSTRUCTION}])
        first.append(time.perf_counter() - start)

    for _ in range(3):  # burn-in, discarded
        embed(embedder, [{"text": queries[0], "instruction": QUERY_INSTRUCTION}])

    warm: list[float] = []
    per_query: dict[str, list[float]] = {q: [] for q in queries}
    for _ in range(repeats):
        for query in queries:
            start = time.perf_counter()
            vectors = embed(embedder, [{"text": query, "instruction": QUERY_INSTRUCTION}])
            elapsed = time.perf_counter() - start
            warm.append(elapsed)
            per_query[query].append(elapsed)

    out: dict[str, Any] = {
        "dims": int(vectors.shape[-1]),
        "cold_first_call_ms": [round(s * 1000, 2) for s in first],
        "warm": Latencies(warm).summary(),
        "per_query_median_ms": {
            q: round(statistics.median(v) * 1000, 2) for q, v in per_query.items()
        },
    }

    # Both legs of a `content_type=all` search, batched into one forward pass:
    # §4.3's "they can be batched into one forward pass if we want them to be".
    both: list[float] = []
    for _ in range(repeats):
        start = time.perf_counter()
        embed(
            embedder,
            [
                {"text": queries[0], "instruction": QUERY_INSTRUCTION},
                {"text": queries[0], "instruction": DOC_INSTRUCTION},
            ],
        )
        both.append(time.perf_counter() - start)
    out["two_leg_batched"] = Latencies(both).summary()

    # MRL is a slice on the way out: same forward pass, narrower vector.
    slice_costs = {}
    vectors = embed(embedder, [{"text": queries[0], "instruction": QUERY_INSTRUCTION}])
    for dims in mrl_dims:
        samples = []
        for _ in range(max(repeats * 5, 20)):
            start = time.perf_counter()
            mrl_slice(vectors, dims)
            _sync()
            samples.append(time.perf_counter() - start)
        slice_costs[str(dims)] = Latencies(samples).summary()
    out["mrl_slice_only"] = slice_costs

    mrl_end_to_end = {}
    for dims in mrl_dims:
        samples = []
        for _ in range(repeats * len(queries)):
            start = time.perf_counter()
            vecs = embed(embedder, [{"text": queries[0], "instruction": QUERY_INSTRUCTION}])
            mrl_slice(vecs, dims)
            _sync()
            samples.append(time.perf_counter() - start)
        mrl_end_to_end[str(dims)] = Latencies(samples).summary()
    out["mrl_end_to_end"] = mrl_end_to_end
    return out


def pick_frames(frames_dir: Path, count: int, seed: int = 11) -> list[Path]:
    paths = sorted(frames_dir.glob("*/*.jpg"))
    if not paths:
        raise SystemExit(f"no keyframes under {frames_dir}")
    random.Random(seed).shuffle(paths)
    return paths[:count]


def measure_throughput(
    embedder: Any, frames: Sequence[Path], batch_sizes: Sequence[int], corpus_frames: int
) -> dict[str, Any]:
    """Images/sec per batch size, plus what a full re-embed of the corpus costs.

    Migration cost (§5.3) is the reason this number exists: a swap re-embeds
    every keyframe, and "minutes, not hours" was an estimate built on SigLIP's
    throughput, not this model's.
    """
    from PIL import Image

    sizes = []
    for path in frames[:20]:
        with Image.open(path) as im:
            sizes.append(im.size)

    embed(embedder, [{"image": str(frames[0])}])  # warm the vision tower

    runs = []
    for batch_size in batch_sizes:
        start = time.perf_counter()
        vectors_seen = 0
        for offset in range(0, len(frames), batch_size):
            chunk = frames[offset : offset + batch_size]
            vectors = embed(
                embedder, [{"image": str(p), "instruction": DOC_INSTRUCTION} for p in chunk]
            )
            vectors_seen += int(vectors.shape[0])
        elapsed = time.perf_counter() - start
        per_image = elapsed / max(vectors_seen, 1)
        runs.append(
            {
                "batch_size": batch_size,
                "images": vectors_seen,
                "wall_s": round(elapsed, 2),
                "ms_per_image": round(per_image * 1000, 1),
                "images_per_s": round(1 / per_image, 2),
                "corpus_reembed_minutes": round(corpus_frames * per_image / 60, 1),
                "vram_after": vram(),
            }
        )
        print(f"  [throughput] batch={batch_size}: {runs[-1]['images_per_s']} img/s")
    best = max(runs, key=lambda r: r["images_per_s"])
    return {
        "frame_sizes_sampled": sorted({f"{w}x{h}" for w, h in sizes}),
        "corpus_frames": corpus_frames,
        "runs": runs,
        "best": {
            "batch_size": best["batch_size"],
            "images_per_s": best["images_per_s"],
            "corpus_reembed_minutes": best["corpus_reembed_minutes"],
        },
    }


def measure_tokens(embedder: Any, frames: Sequence[Path], queries: Sequence[str]) -> dict[str, Any]:
    """How many tokens a frame and a query actually cost this processor.

    §4.5 estimates ~1,176 visual tokens per 1280x720 frame from a 28 px merged
    patch; the shipped loader uses a 32 px factor and its own `max_pixels`, so
    the estimate is worth replacing with the real count.
    """
    out: dict[str, Any] = {}
    conversation = embedder.format_model_input(image=str(frames[0]), instruction=DOC_INSTRUCTION)
    inputs = embedder._preprocess_inputs([conversation])
    out["frame_input_tokens"] = int(inputs["input_ids"].shape[-1])
    if "image_grid_thw" in inputs:
        out["image_grid_thw"] = inputs["image_grid_thw"].tolist()
    conversation = embedder.format_model_input(text=queries[0], instruction=QUERY_INSTRUCTION)
    inputs = embedder._preprocess_inputs([conversation])
    out["query_input_tokens"] = int(inputs["input_ids"].shape[-1])
    out["query"] = queries[0]
    return out


# --------------------------------------------------------------------- main


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("mode", choices=("latency", "throughput", "all"))
    parser.add_argument("--repeats", type=int, default=20)
    parser.add_argument("--frames-dir", type=Path, default=Path("/home/dev/vidtheque-data/keyframes"))
    parser.add_argument("--frames", type=int, default=100)
    parser.add_argument("--batch-sizes", default="1,4,8")
    parser.add_argument("--corpus-frames", type=int, default=3060)
    parser.add_argument("--mrl-dims", default="1024")
    parser.add_argument("--dtype", default="bfloat16")
    parser.add_argument("--attn", default="sdpa")
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()

    import torch

    envelope: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "model": MODEL_ID,
        "config": {
            "dtype": args.dtype,
            "attn_implementation": args.attn,
            "torch": torch.__version__,
            "device": torch.cuda.get_device_name(0),
        },
        "vram_before_load": vram(),
    }

    embedder, snapshot, load_wall = load_embedder(args.dtype, args.attn)
    envelope["snapshot"] = str(snapshot)
    envelope["load_wall_s"] = round(load_wall, 2)
    envelope["vram_after_load"] = vram()
    print(f"loaded in {load_wall:.1f}s; {envelope['vram_after_load']}")

    queries = list(DEFAULT_QUERIES)
    mrl_dims = [int(d) for d in args.mrl_dims.split(",") if d.strip()]

    if args.mode in ("latency", "all"):
        envelope["latency"] = measure_latency(embedder, queries, args.repeats, mrl_dims)
        print(f"  warm single-query: {envelope['latency']['warm']}")

    if args.mode in ("throughput", "all"):
        frames = pick_frames(args.frames_dir, args.frames)
        envelope["frames_used"] = [str(p) for p in frames]
        envelope["tokens"] = measure_tokens(embedder, frames, queries)
        print(f"  tokens: {envelope['tokens']}")
        envelope["throughput"] = measure_throughput(
            embedder,
            frames,
            [int(b) for b in args.batch_sizes.split(",") if b.strip()],
            args.corpus_frames,
        )

    envelope["vram_peak"] = vram()
    envelope["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")

    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(envelope, indent=2))
        print(f"wrote {args.out}")
    else:
        print(json.dumps(envelope, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
