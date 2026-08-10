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
