# Calibrating the vector legs' relevance floor (2026-08-10)

Author: fix session `peppy-wibbling-moler`, answering
`research/mcp-eval-terra-2026-08-10.md` §4.1 ("the semantic legs have no
effective relevance floor"). Append-only: add sections, don't rewrite these.

**What this doc is for.** `db/queries.py` says the replacement procedure for a
distance ceiling is: *embed a set of real queries and a set of junk queries, look
at the best-hit distributions, and put the ceiling above the whole real range.*
This is that measurement, run against the shipped
`Qwen/Qwen3-VL-Embedding-2B` (2048-d) space on the live corpus — plus the reason
the answer is **not** a new absolute ceiling.

> **§§1-5 are the RANDOM-SPACE measurement.** Everything below this line up to
> §6 was measured against an embedding index that turned out to be the output of
> a randomly initialised network — §3 is where that was first seen, and
> `research/embedding-random-init-2026-08-10.md` is the root cause and the
> repair. The numbers are kept because they are what a broken space looks like
> from the search side, and because §3's probes are the ones that caught it.
> **The shipped defaults come from §6.**

## 1. Method

Live DB opened read-only (`file:/home/dev/vidtheque-data/vidtheque.db?mode=ro`,
6,200 chunks / 10,337 keyframes / 169 videos at the time of the run), queries
embedded through the **running worker** on the same endpoints search uses
(`POST /v1/embeddings` with `input_type=query`, `POST /v1/embeddings/frame-query`
— model `Qwen/Qwen3-VL-Embedding-2B`, dims 2048 on both), then the same KNN the
tool runs: `SELECT distance FROM vec_chunks WHERE embedding MATCH ? AND k = 800`.
12 real queries (topics the corpus demonstrably covers, including the eval's own
`turbopuffer` / `CUDA kernel occupancy` / `voice agent interruption latency`) and
10 junk queries (sourdough, Napoleon, feline hyperthyroidism, the offside rule…).

## 2. What the distances look like

Text leg, distance at rank *n*, ranges across queries:

| | best | 20th | 50th | 400th | 800th |
|---|---|---|---|---|---|
| real (12 q) | 0.715–0.767 | 0.789–0.814 | 0.921–0.935 | 0.961–0.973 | 0.970–0.982 |
| junk (10 q) | 0.739–0.767 | 0.795–0.813 | 0.920–0.941 | 0.966–0.975 | 0.974–0.983 |

Frame leg: real best 0.940–0.955, junk best 0.932–0.957; both flat to ~0.97 by
rank 400.

**Conclusion 1 — an absolute ceiling is still unsettable.** Real and junk best-hit
ranges overlap *completely* (real 0.715–0.767 vs junk 0.739–0.767): every value
that would reject "how to prune apple trees in winter" also rejects "CUDA kernel
occupancy". This is the same result the SigLIP-era measurement got (real
0.504–0.576 vs junk 0.513–0.616) in a different space, which is the point: the
overlap is a property of asymmetric-prefix embedders over a single-domain corpus,
not of one model. The absolute ceilings therefore stay at 1.0.

**Conclusion 2 — there is a knee, and it is per query.** Every query, real or
junk, has ~11–25 chunks under 0.80, ~45 under 0.85, ~48 under 0.92, and then the
tail: 800 of 6,200 chunks pulled in as "nearest neighbours". A cut relative to
the query's own best hit (`best + 0.20`) keeps ~48 candidates instead of 800 —
a 16× smaller semantic pool, and it needs no knowledge of the radius at which
this particular model packs its corpus. That is what shipped
(`VIDTHEQUE_VEC_MAX_MARGIN=0.20`, `VIDTHEQUE_FRAME_MAX_MARGIN=0.10`).

**Where the two margins come from.** The only pair we have ever calibrated
(`research/multimodal-embedding-2026-08-09.md`, SigLIP 2 + Qwen3-Embedding-0.6B)
put the real 20th-nearest at 0.664 text / 0.946 frame against real best hits of
0.504–0.576 / 0.877–0.919 — i.e. the whole real range sat within **0.16** (text)
and **0.069** (frame) of that query's own best hit. 0.20 and 0.10 sit above both,
exactly as the absolute ceilings did, and reproduce them (0.576 + 0.20 ≈ 0.72;
0.877 + 0.10 ≈ 0.96) without hard-coding that pair's radius.

## 3. The finding this measurement tripped over — the stored text vectors are
not in the model's space

Not the finding I went looking for, and it is bigger than the floor. Three
probes, all against the live DB and the running worker:

1. **Re-embedding a chunk's own text does not reproduce its stored vector.**
   `cos(stored, re-embedded-as-document)` for chunks 536 / 1500 / 3000 / 6100 =
   **0.014 / 0.029 / 0.007 / −0.017**. For chunk 6200 — the most recently
   indexed video — it is **0.9997**. Two documents that *are* both in this
   model's space score ≥ 0.46 against each other however unrelated their topic,
   so ~0.01 is not "a bit stale": those vectors are from another mapping
   entirely. `video_stages.text_embed` reads `done` for all 167 videos.
2. **Every query's nearest neighbours are the same one video.** Top-8 for
   `turbopuffer`, `CUDA kernel occupancy`, `LLM as a judge evaluation`,
   `sourdough bread hydration schedule` and `symptoms of feline hyperthyroidism`
   are *all* chunks of `CS5Cmz5FssI` ("How AI is changing Software
   Engineering") — the newest video, i.e. the one whose vectors do reproduce.
   This is the eval's §4.1 hub (`OV56RddyFuU` on 08-09, a different video
   because the corpus grew), and it is a hub because it is the only correctly
   embedded neighbourhood in the index.
3. **The worker's own text retrieval is degenerate right now.** Five unrelated
   documents (Eiffel Tower, sourdough, photosynthesis, transformers, feline
   hyperthyroidism) and their five obvious queries, embedded fresh through
   `/v1/embeddings`, score **2/5 top-1**, with one document winning three of the
   five queries and all similarities inside 0.21–0.32.

So the semantic legs on the live corpus are currently carrying close to zero
query-relevant signal, and no ranking change can fix that. **This is a
worker/pipeline matter, not a search-path one — flagged for Tom, untouched
here.** It does not weaken the floor work: the band is what stops a noise leg
from flooding the fused ranking, and it is calibrated on the *geometry* of the
corpus rather than on the quality of the mapping. It does mean the shipped
margins deserve a re-run once the embedding side is healthy — the numbers to
regenerate are §2's table.

## 4. Reproducing

The three scripts (`calibrate.py`, `hubs.py`, `roundtrip.py`, `sanity.py`) are
scratch, not committed; each is ~60 lines of "open the DB read-only, POST to the
worker, run the KNN, print percentiles" and the queries are listed in §1. Nothing
here needs the repo — a read-only DB handle and a running worker is the whole
apparatus.

## 5. Before/after on the live corpus (added with the fix)

`queries.search_transcript` called directly against the live DB (read-only, 171
videos — the index batches added ~17 videos between §2's run and this one, which
is why the band keeps ~200 chunks here rather than §2's ~48: the new videos are
the correctly-embedded ones, so they land inside the band), query vectors from
the running worker, `k=800`, absolute ceiling 1.0 in both columns — the only
difference is the band:

| query | | rows | videos | vec kept | latency | rank 1 |
|---|---|---|---|---|---|---|
| `turbopuffer` | before | 401 (pool full) | 119 | 800/800 | 3737 ms | `UM6sFg_jdlE` "RAG is dead, right?? — Turbopuffer" |
| | after | 109 | **36** | 242/800 | **850 ms** | same |
| `CUDA kernel occupancy` | before | 401 (pool full) | 132 | 800/800 | 3088 ms | `FB-MLPhL9Ms` (unrelated) |
| | after | 25 | **5** | 189/800 | **663 ms** | same |
| `voice agent interruption latency` | before | 401 (pool full) | 123 | 800/800 | 3021 ms | `CS5Cmz5FssI` (the hub) |
| | after | 25 | **5** | 189/800 | **709 ms** | same |

Three things to read here (see also §6). The **counts stop lying**: `turbopuffer` no longer
"matches" 119 of 171 talks, and the candidate pool stops filling, which also
retires the misleading "deeper matches exist" note on these queries. The band is
**4× cheaper**, because the fused ranking is built over hundreds of candidates
instead of the whole `k`. And **rank 1 does not move** — the band drops the tail,
it does not re-rank; two of these three rank 1s are still topically wrong, and
they will stay wrong until §3's embedding problem is fixed. That is the honest
split between what a floor can fix and what it cannot.

## 6. §3 was right, and it is worse — the answer is in a sibling doc

Added by the root-cause session that §3 was flagged to (2026-08-10, evening).
`research/embedding-random-init-2026-08-10.md` has the full write-up; the short
version, because it changes how §2 and §5 should be read:

`Qwen/Qwen3-VL-Embedding-2B` **has never loaded its weights on this stack.**
sentence-transformers loads it through `AutoModel`, transformers 4.57.6 cannot
match any of the checkpoint's 625 tensors to `Qwen3VLModel` (its
`base_model_prefix` is `""`, which kills the rename branch), so all 625 are
randomly initialised — with a warning, not an error. Every vector in both
indexes is a random projection, and because the draw is redone on every model
load, the corpus is **eleven mutually orthogonal random spaces**.

So: §3's ~0.01 round-trips were not stale vectors, they were vectors from a
different random draw. §3.2's hub is simply the sub-space the *current* worker
process happens to share. And §2's distributions — including the complete
overlap between real and junk best hits that "an absolute ceiling is still
unsettable" rests on — are properties of a random 2048-d projection over 6,200
chunks, not of this model over this corpus. The band that shipped is safe
(it is calibrated on geometry, and geometry is all a random space has), but
§2's table and §5's before/after must both be re-measured once the corpus is
re-embedded on real weights. The conclusion that survives untested is the SigLIP-era
one; this run cannot corroborate it.

---

## 6. Recalibration on the repaired space (2026-08-10, ~22:30)

Same method as §1 — same 12 real and 10 junk queries, same read-only DB handle,
same `k=800` KNN, same worker endpoints — re-run after the weight-loading repair
(`4bb20ec`) and the full re-embed. Corpus: 6,701 chunks / 11,197 keyframes,
tranche still running. **This is where the shipped defaults come from.**

Sanity first, the probe that failed at 2/5 in §3: five unrelated documents and
their five obvious queries now score **5/5 top-1**, correct document 0.61-0.77,
unrelated 0.11-0.33. The model is doing retrieval.

### 6.1 Real and junk finally separate

Best-hit cosine distance, by query (full table: 12 real, 10 junk):

| leg | real best-hit | junk best-hit | corridor |
|---|---|---|---|
| text | **0.220 - 0.459** | **0.579 - 0.665** | 0.12 wide, empty |
| frame | 0.382 - 0.623 | 0.550 - 0.749 | overlapping |

Tightest real queries on the text leg were `context engineering` (0.220),
`LLM as a judge evaluation` (0.237) and `agent memory and skills` (0.242); the
loosest were `turbopuffer` (0.459 — a proper noun the FTS leg owns anyway) and
`fine-tuning with LoRA adapters` (0.419). Junk ran from `tax deductions for
rental property depreciation` (0.579) to `Napoleon's retreat from Moscow` (0.665).

Compare §2, where the same 22 queries gave real 0.715-0.767 against junk
0.739-0.767 — no corridor, in either direction. The overlap in §2 was not a
property of asymmetric-prefix embedders after all; it was the random space.
(The SigLIP-era overlap in `db/queries.py`'s comment was measured on a
6-video corpus and is left standing as its own datum.)

### 6.2 What ships, and why the band stays

**`VIDTHEQUE_VEC_MAX_DISTANCE = 0.55`** (was 1.0) — inside the corridor, 0.09
above the worst real best-hit, 0.03 below the best junk one.
**`VIDTHEQUE_FRAME_MAX_DISTANCE = 0.65`** (was 1.0) — above the whole real range
(worst real best-hit 0.623), which is as far as the frame leg's partial
separation allows; three junk queries keep a handful of frames each, and that is
the deliberate direction to err in.

**`VIDTHEQUE_VEC_MAX_MARGIN` stays 0.20**, **`VIDTHEQUE_FRAME_MAX_MARGIN` goes
0.10 → 0.15.** The band is not redundant with the ceiling, and the repaired
space is what makes that visible: a junk query's `k` nearest are **flat** — best
0.579, 800th 0.771, a spread of 0.19 — so a 0.20 band around its own best hit
keeps all 800. Measured, with the ceiling open, every junk query still fused 800
chunks over ~120 videos. The ceiling separates real from junk; the band bounds a
real query's fan-out. Real 50th-nearest sits 0.18-0.22 (text) and 0.09-0.18
(frame) from its own best hit, so 0.20/0.15 are "about the top 50 chunks / top
20-50 frames". Frame moved because 0.10 was leaving the leg with 3-12 candidates
— under one page.

### 6.3 End to end, live (`queries.search_transcript`, 171 videos)

| query | ceiling 1.0 (was) | ceiling 0.55 (ships) | rank 1 |
|---|---|---|---|
| `turbopuffer` | 136 rows / 70 videos | **10 rows / 2 videos** | "Building Turbopuffer" ✓ |
| `voice agent interruption latency` | 126 / 50 | 126 / 50 | "Voice Agents That Handle Interrupts" ✓ |
| `LLM as a judge evaluation` | 73 / 34 | 73 / 34 | "Build Evals That Actually Matter" ✓ |
| `CUDA kernel occupancy` | 32 / 14 | 17 / 7 | "First Steps Toward Automated AI Research" (nearest real neighbour; the corpus has no CUDA talk) |
| `how to prune apple trees in winter` | 401 / 117 | **0 / 0** | — |
| `symptoms of feline hyperthyroidism` | 401 / 125 | **0 / 0** | — |
| `sourdough bread hydration schedule` | 401 / 129 | **0 / 0** | — |

Two things to read. **Rank 1 is now topically right on every real query** — that
is the embedding repair, not the floor; §5's before/after had the same floor and
the wrong rank 1s. And the ceiling costs real recall nowhere in this sample
while taking junk from "the whole corpus, confidently ranked" to nothing: the
`Results: 0/0` empty state is now reachable through the *floor*, not only
through `has_lexical_footing`, which matters because a junk query made of common
English words ("how to prune apple trees in winter") has lexical footing.

Frame leg, same shape: `symptoms of feline hyperthyroidism` 169 rows → 0;
`CUDA kernel occupancy` 51 → 6; `voice agent interruption latency` unchanged at
the top and wider at 0.15 (9 rows → 38, 15 videos).

### 6.4 When to re-run this

On any encoder change, and on any corpus whose domain is much broader than "one
conference's talks" — a general-purpose library will have a wider real range and
may need a looser ceiling. The procedure is §1; the two numbers to reproduce are
the best-hit ranges in §6.1. If they overlap, do not set an absolute ceiling:
raise both to 1.0 and let the band and `has_lexical_footing` carry it, which is
exactly the configuration this project ran until today.
