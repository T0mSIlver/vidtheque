#!/usr/bin/env python3
"""Frame leg, head to head: Qwen3-VL-Embedding-2B vs the SigLIP 2 in production.

`research/multimodal-embedding-2026-08-09.md` §3 argues the frame leg has an
architectural deficit on text-in-image content, and §7 makes measuring it on
*this* corpus bench item #2 — because the 3.6x gap in the literature is against
SigLIP **1**, and nobody has published SigLIP 2 on any screenshot benchmark.

This is a **spot check, not a benchmark**: a few dozen real keyframes, a couple
of dozen hand-written queries, one judge (the author, reading the frames). It
can tell you a model is obviously better or that the two are close; it cannot
produce a number anyone should quote as MTEB.

What keeps it honest:

* frames are sampled from the real corpus, stratified by how much OCR text they
  carry, so slides/terminals/code and face/stage shots are both represented;
* queries are written against what the frames actually show, and each is labelled
  ``text_in_image`` or ``visual`` so the two cases can be scored separately —
  §3's claim is specifically about the first;
* the ranking pool is padded with **distractor frames the queries were not
  written against**, because rank-1-of-50 flatters everything;
* the SigLIP side is the *deployed* one — the live worker's own endpoints at the
  production patch budget — not a fresh checkpoint configured to taste.

    uv run --no-project python bench/frame_retrieval_spotcheck.py select \\
        --db /home/dev/vidtheque-data/vidtheque.db --count 56 \\
        --out bench/runs/spotcheck-frames.json
    uv run --no-project python bench/frame_retrieval_spotcheck.py run \\
        --frames bench/runs/spotcheck-frames.json \\
        --queries bench/scenarios/frame-spotcheck-queries.json \\
        --distractors 200 --out bench/runs/spotcheck-results.json

The database is opened `mode=ro`; keyframe JPEGs are read, never written.
"""

from __future__ import annotations

import argparse
import collections
import json
import random
import re
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

sys.path.insert(0, str(Path(__file__).resolve().parent))

DATA_DIR = Path("/home/dev/vidtheque-data")
WORKER = "http://127.0.0.1:8081"

# Buckets exist so the sample cannot accidentally be all slides. `code_term` is
# the case §3 says SigLIP structurally cannot do; `visual` is the case where the
# literature says a dual encoder is already equal, and it is in here precisely so
# a win for the 2B is not assumed.
CODE_RE = re.compile(r"(\$ |def |import |npm |pip |curl |sudo |=> |\{|\}|error|Error|\.py|\.ts|\.json)")
BUCKET_PLAN = {"visual": 10, "code_term": 14, "dense_slide": 12, "slide": 12, "sparse": 8}


# ------------------------------------------------------------------ sampling


def bucket_for(nlines: int, head: str) -> str:
    if nlines == 0:
        return "visual"
    if nlines >= 8 and CODE_RE.search(head):
        return "code_term"
    if nlines >= 15:
        return "dense_slide"
    if nlines <= 5:
        return "sparse"
    return "slide"


def mean_luma(path: Path) -> float:
    """Cheap mean brightness, used to keep black frames out of the sample.

    Not a nicety: the zero-OCR end of this corpus is mostly *fade-to-black
    transitions*, not stage shots — 4 of the first 10 sampled came back pure
    black. A black frame is an unanswerable retrieval target for either model,
    so scoring against one measures nothing. (It is also a keyframe-selection
    smell worth a look one day, but not by this script.)
    """
    from PIL import Image

    with Image.open(path) as im:
        im.draft("L", (64, 64))  # JPEG DCT-scaled decode: no full-size pass
        return sum(im.convert("L").resize((16, 16)).tobytes()) / 256


def load_frames(db: Path) -> list[dict[str, Any]]:
    conn = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    rows = conn.execute(
        """
        SELECT k.id, v.source_id, v.title, k.t_s, k.jpeg_path, k.width, k.height,
               (SELECT count(*) FROM ocr_lines o WHERE o.keyframe_id = k.id) AS nlines,
               (SELECT group_concat(text, ' ~ ')
                  FROM (SELECT text FROM ocr_lines
                         WHERE keyframe_id = k.id ORDER BY line_no LIMIT 10)) AS head
          FROM keyframes k JOIN videos v ON v.id = k.video_id
         WHERE k.dup_of IS NULL
        """
    ).fetchall()
    cols = ["id", "source_id", "title", "t_s", "jpeg_path", "width", "height", "nlines", "head"]
    frames = [dict(zip(cols, row)) for row in rows]
    for frame in frames:
        frame["head"] = frame["head"] or ""
        frame["bucket"] = bucket_for(frame["nlines"], frame["head"])
    return frames


def select(db: Path, count: int, seed: int, out: Path, min_luma: float) -> int:
    frames = load_frames(db)
    buckets: dict[str, list[dict[str, Any]]] = collections.defaultdict(list)
    for frame in frames:
        buckets[frame["bucket"]].append(frame)
    print({k: len(v) for k, v in buckets.items()})

    scale = count / sum(BUCKET_PLAN.values())
    rng = random.Random(seed)
    per_video: collections.Counter[str] = collections.Counter()
    chosen: list[dict[str, Any]] = []
    for bucket, want in BUCKET_PLAN.items():
        pool = buckets[bucket][:]
        rng.shuffle(pool)
        taken = 0
        for frame in pool:
            if taken >= round(want * scale):
                break
            if per_video[frame["source_id"]] >= 2:  # spread across the corpus
                continue
            luma = mean_luma(DATA_DIR / frame["jpeg_path"])
            if luma < min_luma:
                continue
            frame["mean_luma"] = round(luma, 1)
            chosen.append(frame)
            per_video[frame["source_id"]] += 1
            taken += 1
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(chosen, indent=1))
    print(f"selected {len(chosen)} frames from {len(set(f['source_id'] for f in chosen))} videos")
    print(f"wrote {out}")
    return 0


def select_distractors(
    db: Path, exclude: set[int], count: int, seed: int
) -> list[dict[str, Any]]:
    frames = [f for f in load_frames(db) if f["id"] not in exclude]
    random.Random(seed + 1).shuffle(frames)
    return frames[:count]


# ------------------------------------------------------------------ backends


def siglip_image_vectors(paths: Sequence[Path], batch: int = 8) -> list[list[float]]:
    """The deployed frame leg, through the deployed worker.

    No `max_num_patches` is sent on purpose: the pipeline does not send one
    either (`pipeline/runner.py`), so the worker's own `IMAGE_EMBED_MAX_PATCHES`
    applies and the baseline is the budget the corpus was actually indexed at.
    """
    import requests

    vectors: list[list[float]] = []
    for offset in range(0, len(paths), batch):
        chunk = paths[offset : offset + batch]
        files = [("file", (p.name, p.read_bytes(), "image/jpeg")) for p in chunk]
        response = requests.post(f"{WORKER}/v1/embeddings/image", files=files, timeout=300)
        response.raise_for_status()
        payload = response.json()
        vectors.extend(item["embedding"] for item in payload["data"])
        print(f"  [siglip] {len(vectors)}/{len(paths)}")
    return vectors


def siglip_query_vectors(queries: Sequence[str]) -> list[list[float]]:
    import requests

    response = requests.post(
        f"{WORKER}/v1/embeddings/frame-query", json={"input": list(queries)}, timeout=120
    )
    response.raise_for_status()
    return [item["embedding"] for item in response.json()["data"]]


def qwen_vectors(
    embedder: Any, items: Sequence[dict[str, Any]], batch: int, label: str
) -> Any:
    import torch

    chunks = []
    for offset in range(0, len(items), batch):
        chunks.append(embedder.process(list(items[offset : offset + batch])).float().cpu())
        torch.cuda.synchronize()
        print(f"  [qwen:{label}] {sum(c.shape[0] for c in chunks)}/{len(items)}")
    return torch.cat(chunks)


# ------------------------------------------------------------------- scoring


def rank_table(
    sims: Any, query_ids: Sequence[str], frame_ids: Sequence[int], relevant: dict[str, list[int]]
) -> dict[str, Any]:
    """Rank of the best relevant frame, and what came first instead.

    Reciprocal rank rather than a hit rate: with a hand-labelled set this small,
    *how far down* the right frame sits is the only signal with any resolution.
    """
    out = {}
    for row, query_id in enumerate(query_ids):
        order = list(reversed(sims[row].argsort().tolist()))
        ranked = [frame_ids[i] for i in order]
        gold = set(relevant[query_id])
        best_rank = next((i + 1 for i, fid in enumerate(ranked) if fid in gold), None)
        out[query_id] = {
            "top1": ranked[0],
            "top3": ranked[:3],
            "top1_relevant": ranked[0] in gold,
            "best_relevant_rank": best_rank,
            "reciprocal_rank": round(1 / best_rank, 4) if best_rank else 0.0,
            "top1_score": round(float(sims[row][order[0]]), 4),
        }
    return out


def run(
    frames_path: Path,
    queries_path: Path,
    db: Path,
    distractors: int,
    batch: int,
    seed: int,
    out: Path | None,
) -> int:
    import torch

    from embed_latency import DOC_INSTRUCTION, QUERY_INSTRUCTION, load_embedder, vram  # noqa: E402

    labelled = json.loads(frames_path.read_text())
    queries = json.loads(queries_path.read_text())
    labelled_ids = {f["id"] for f in labelled}
    pool = labelled + select_distractors(db, labelled_ids, distractors, seed)
    paths = [DATA_DIR / f["jpeg_path"] for f in pool]
    frame_ids = [f["id"] for f in pool]
    missing = [p for p in paths if not p.exists()]
    if missing:
        raise SystemExit(f"{len(missing)} keyframes missing on disk, first: {missing[0]}")

    query_ids = [q["id"] for q in queries]
    relevant = {q["id"]: q["relevant"] for q in queries}
    texts = [q["query"] for q in queries]
    for query in queries:
        unknown = set(query["relevant"]) - labelled_ids
        if unknown:
            raise SystemExit(f"query {query['id']} cites unlabelled frames: {sorted(unknown)}")

    envelope: dict[str, Any] = {
        "started_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "pool": {"labelled": len(labelled), "distractors": len(pool) - len(labelled)},
        "queries": queries,
        "worker": WORKER,
    }

    print("== siglip baseline (live worker)")
    start = time.perf_counter()
    siglip_frames = torch.tensor(siglip_image_vectors(paths))
    siglip_queries = torch.tensor(siglip_query_vectors(texts))
    envelope["siglip_wall_s"] = round(time.perf_counter() - start, 1)
    envelope["siglip_dims"] = int(siglip_frames.shape[-1])

    print("== qwen3-vl-embedding-2b")
    embedder, snapshot, load_wall = load_embedder()
    envelope["qwen_snapshot"] = str(snapshot)
    envelope["qwen_load_wall_s"] = round(load_wall, 2)
    start = time.perf_counter()
    qwen_frames = qwen_vectors(
        embedder, [{"image": str(p), "instruction": DOC_INSTRUCTION} for p in paths], batch, "frames"
    )
    qwen_queries = qwen_vectors(
        embedder,
        [{"text": q["query"], "instruction": q.get("instruction") or QUERY_INSTRUCTION} for q in queries],
        batch,
        "queries",
    )
    envelope["qwen_wall_s"] = round(time.perf_counter() - start, 1)
    envelope["qwen_dims"] = int(qwen_frames.shape[-1])
    envelope["qwen_vram"] = vram()

    results = {
        "siglip": rank_table(siglip_queries @ siglip_frames.T, query_ids, frame_ids, relevant),
        "qwen2b": rank_table(qwen_queries @ qwen_frames.T, query_ids, frame_ids, relevant),
    }
    # MRL@1024 on the same forward pass — free to check while everything is loaded.
    sliced = torch.nn.functional.normalize(qwen_frames[:, :1024], p=2, dim=-1)
    sliced_q = torch.nn.functional.normalize(qwen_queries[:, :1024], p=2, dim=-1)
    results["qwen2b_mrl1024"] = rank_table(sliced_q @ sliced.T, query_ids, frame_ids, relevant)
    envelope["results"] = results

    summary: dict[str, Any] = {}
    kinds = sorted({q.get("kind", "unlabelled") for q in queries})
    for model, table in results.items():
        rows = list(table.values())
        summary[model] = {
            "mrr": round(sum(r["reciprocal_rank"] for r in rows) / len(rows), 4),
            "top1_hits": sum(1 for r in rows if r["top1_relevant"]),
            "n": len(rows),
        }
        for kind in kinds:
            subset = [table[q["id"]] for q in queries if q.get("kind", "unlabelled") == kind]
            if subset:
                summary[model][f"mrr_{kind}"] = round(
                    sum(r["reciprocal_rank"] for r in subset) / len(subset), 4
                )
                summary[model][f"top1_hits_{kind}"] = sum(1 for r in subset if r["top1_relevant"])
    wins = collections.Counter()
    for query in queries:
        q_rank = results["qwen2b"][query["id"]]["best_relevant_rank"] or 10**6
        s_rank = results["siglip"][query["id"]]["best_relevant_rank"] or 10**6
        wins["qwen2b" if q_rank < s_rank else "siglip" if s_rank < q_rank else "tie"] += 1
    summary["wins"] = dict(wins)
    envelope["summary"] = summary
    print(json.dumps(summary, indent=2))

    envelope["frame_index"] = {
        str(f["id"]): {
            "video": f["source_id"],
            "t_s": f["t_s"],
            "bucket": f["bucket"],
            "labelled": f["id"] in labelled_ids,
        }
        for f in pool
    }
    envelope["finished_at"] = datetime.now(timezone.utc).isoformat(timespec="seconds")
    if out:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(envelope, indent=2))
        print(f"wrote {out}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="mode", required=True)

    pick = sub.add_parser("select")
    pick.add_argument("--db", type=Path, default=DATA_DIR / "vidtheque.db")
    pick.add_argument("--count", type=int, default=56)
    pick.add_argument("--seed", type=int, default=7)
    pick.add_argument("--min-luma", type=float, default=12.0)
    pick.add_argument("--out", type=Path, required=True)

    go = sub.add_parser("run")
    go.add_argument("--frames", type=Path, required=True)
    go.add_argument("--queries", type=Path, required=True)
    go.add_argument("--db", type=Path, default=DATA_DIR / "vidtheque.db")
    go.add_argument("--distractors", type=int, default=200)
    go.add_argument("--batch", type=int, default=4)
    go.add_argument("--seed", type=int, default=7)
    go.add_argument("--out", type=Path)

    args = parser.parse_args()
    if args.mode == "select":
        return select(args.db, args.count, args.seed, args.out, args.min_luma)
    return run(
        args.frames, args.queries, args.db, args.distractors, args.batch, args.seed, args.out
    )


if __name__ == "__main__":
    raise SystemExit(main())
