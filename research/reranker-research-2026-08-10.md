# Qwen3-VL reranker — adoption research (2026-08-10)

Produced by codex gpt-5.6-sol (read-only over this repo, web search on),
commissioned by the orchestrator session on Tom's question "do we need the
Qwen3-VL reranker?". Local links are worktree-absolute as written.

## Decision: **later — do not ship it now**

Vidtheque should not add a Qwen3-VL cross-encoder reranker to the default search path yet. Keep `Qwen/Qwen3-VL-Reranker-2B` as the only viable future candidate; reject the 8B model for this box.

The reason is evidence, not model quality:

- All previous local relevance impressions are invalid because every stored vector was produced by randomly initialized weights. Only the repaired 6,701-chunk/11,197-frame space is meaningful, and it has only been validated with a small floor-calibration set, not an ordering evaluation ([incident](</home/dev/work/vidtheque/.claude/worktrees/peppy-wibbling-moler/research/embedding-random-init-2026-08-10.md:7>), [recalibration](</home/dev/work/vidtheque/.claude/worktrees/peppy-wibbling-moler/research/vec-floor-calibration-2026-08-10.md:168>)).
- Qwen’s published gains are against its own dense top-100, not vidtheque’s stronger four-ranker BM25+dense+OCR+frame RRF baseline.
- The 2B model’s visual-document gains are relevant, but its broad image and video averages actually regress slightly.
- Query→frame cross-encoding is slow enough to damage the public demo, especially when ask mode performs several searches per answer.
- A second 2B checkpoint turns retrieval into query-time GPU lease work, reversing the original “query-time is SQLite/JPEG only” operational property.

## The actual models

| Model | Capability | Usable footprint on the 3090 | License | Call |
|---|---|---:|---|---|
| `Qwen/Qwen3-VL-Reranker-2B` | Text, image, screenshot, video, or mixed-modal query/document pairs; therefore **yes, it can score text query→frame** | 4.26 GB BF16 weights; **estimated** 5–10 GB standalone working set, depending on pair length and batch; **estimated** 9–15 GB while the 4.3 GB embedder is also loaded | Apache-2.0 | Only candidate |
| `Qwen/Qwen3-VL-Reranker-8B` | Same modalities, stronger published ranking | 17.6 GB BF16 weights; **estimated** 18–23+ GB working set before a useful image batch | Apache-2.0 | No |

The exact IDs, 32K context, modalities, pairwise scoring, and licenses are on the official [2B](https://huggingface.co/Qwen/Qwen3-VL-Reranker-2B) and [8B](https://huggingface.co/Qwen/Qwen3-VL-Reranker-8B) cards. The repositories contain [4.26 GB](https://huggingface.co/Qwen/Qwen3-VL-Reranker-2B/blob/main/model.safetensors) and [17.6 GB](https://huggingface.co/Qwen/Qwen3-VL-Reranker-8B/tree/main) of BF16 weights. Qwen does not publish a reranker quantization result; community conversions therefore should not be the decision baseline.

The 8B checkpoint is operationally disqualified: it cannot coexist safely with the ~12 GB llama.cpp lease and the embedder on a 24 GB card, while unloading and reloading models around every search would be worse.

## Published benefit—and the missing evidence

Qwen retrieves the top 100 with `Qwen3-VL-Embedding-2B`, then reranks that dense-only list. The technical report does not evaluate BM25, hybrid fusion, RRF, small-corpus behavior, or vidtheque-like clustered video passages ([evaluation protocol](https://arxiv.org/pdf/2601.04720)).

| Benchmark | Embedding-2B | Reranker-2B | Change |
|---|---:|---:|---:|
| MMEB-v2 retrieval average | 73.4 | 75.1 | +1.7 |
| MMEB image | 74.8 | 73.8 | **−1.0** |
| MMEB video | 53.6 | 52.1 | **−1.5** |
| MMEB visual documents | 79.2 | 83.4 | +4.2 |
| MMTEB retrieval | 68.1 | 70.0 | +1.9 |
| JinaVDR | 71.0 | 80.9 | +9.9 |
| ViDoRe v3 | 52.9 | 60.8 | +7.9 |

These are Qwen’s current [model-card results](https://huggingface.co/Qwen/Qwen3-VL-Reranker-2B). They suggest a real benefit for slides, screenshots, and visually structured documents—not a universal multimodal lift.

There is one useful small-corpus analogy, but it is not Qwen or video: a 2026 preprint over 7,318 financial text/table documents reports hybrid BM25+dense RRF improving from Recall@5 0.695/MRR@3 0.433 to 0.816/0.605 after Cohere reranking. It also found top-20 candidates insufficient and top-50 effective ([study and ablations](https://arxiv.org/abs/2604.01733)). That proves reranking can help a corpus of this order of magnitude, but the paper itself warns that its whole-document financial results may not generalize to chunked or other-domain corpora.

Therefore, the gain over vidtheque’s actual hybrid baseline is **unmeasurable from the literature**. It is reasonable to infer that it will be smaller than Qwen’s dense-only gains because RRF, exact-phrase/term-coverage tie-breaks, leg agreement, and the repaired distance floors already resolve part of the same ordering problem—but that is an inference, not a result.

## Latency and operating cost

No official Qwen source publishes 3090 reranking latency. The closest measured multimodal result I found ran 50 batches of 25 query/image pairs on an A100 40 GB: the 2B reranker took 417 seconds, or 8.34 seconds per 25-pair batch/roughly 3 pairs per second effective throughput ([measurement](https://huggingface.co/blog/UlrickBL/qwen3reranker-comparison)).

| Path, 2B BF16 | Warm latency | Cold/operational cost |
|---|---:|---|
| Top-20 text chunks | **Estimate:** 0.8–2 s | **Estimate:** additional 3–5 s reranker load after idle |
| Top-50 text chunks | **Estimate:** 2–5 s | Same |
| Top-20 frames | **Estimate:** 7–15 s | Same, with higher activation VRAM |
| Top-50 frames | **Estimate:** 17–40 s | Likely requires small batches |
| Mixed `content_type=all` | **Estimate:** 2–15 s depending on frame count | Ask mode multiplies this across searches |

The estimates scale the A100 image measurement using vidtheque’s much shorter mean text chunks and the local 3090 measurement of the same 2B backbone: 4.3 GB process VRAM, 6.5 GB frame-batch peak, and a 3.5-second warm-cache load ([local benchmark](</home/dev/work/vidtheque/.claude/worktrees/peppy-wibbling-moler/research/multimodal-embedding-2026-08-09.md:694>)). They must be replaced by a direct 3090 benchmark before any adoption decision.

The worker can accommodate the model structurally, but not for free. The lifecycle manager serializes all GPU work and only releases the llama.cpp lease after every non-resident slot unloads ([lifecycle](</home/dev/work/vidtheque/.claude/worktrees/peppy-wibbling-moler/worker/src/vidtheque_worker/lifecycle.py:537>)). A separate reranker checkpoint cannot share the embedding checkpoint’s loaded weights. It must remain non-resident, and every reranked query refreshes the interval during which the GPU lease remains held ([policy](</home/dev/work/vidtheque/.claude/worktrees/peppy-wibbling-moler/worker/src/vidtheque_worker/config.py:137>)).

## Where it would fit

The documented FTS seam is currently occupied by a bi-encoder operation: cap FTS at 5,000, compute stored-vector cosine distance over that known set, and order by distance ([schema](</home/dev/work/vidtheque/.claude/worktrees/peppy-wibbling-moler/docs/design/index-schema.md:1474>)). Cross-encoding 5,000 pairs is not viable.

A Qwen cross-encoder should instead consume a fixed top-20 or top-50 after the four sub-rankers have been fused and OCR/frame/transcript duplicates collapsed, but before final relevance ordering, the global per-video cap, and pagination. That preserves provenance and lets a collapsed hit be scored as text, image, or text+image. The current ordering location is explicit in [search.py](</home/dev/work/vidtheque/.claude/worktrees/peppy-wibbling-moler/mcp/src/vidtheque_mcp/tools/search.py:404>).

The worker-side addition is conceptually one custom `POST /v1/rerank`: query plus bounded documents—text and optional frame payloads—in, aligned IDs and scalar scores out. Internally it still requires a new backend protocol/registry task, settings and caps, OpenAPI schema, HTTP client, model lifecycle slot, graceful fallback, and CPU-only fake tests. **Estimated implementation cost: 2–4 engineering days plus the relevance evaluation.** The hard part is maintaining stable pagination, diversity backfill, and fallback ordering, not the endpoint itself.

## Cheaper precision moves first

**Evaluate and tune the repaired baseline.** Sweep the current margins/ceilings only on labeled post-repair queries, then test `RRF k` values such as 10/30/60 and modest per-leg weights. This costs no VRAM and almost no latency. A comparable 7,318-document study found fusion choice itself moved Recall@5 materially, including a score-combination result above its default RRF k=60, so treating 60 as an unevaluated constant is not justified for this corpus ([ablation](https://arxiv.org/abs/2604.01733)).

**Use the existing evidence features.** Exact phrase, term coverage, leg agreement, the 0.55/0.65 ceilings, and the relative bands already address false positives and RRF ties ([contract](</home/dev/work/vidtheque/.claude/worktrees/peppy-wibbling-moler/docs/design/tool-surface.md:595>)). Their post-repair top-five quality has not been measured yet; adding a model before measuring them would obscure whether the cheaper machinery is sufficient.

**If errors prove transcript-only, test a smaller text reranker.** `Qwen/Qwen3-Reranker-0.6B` is Apache-2.0 and improves Qwen’s English MTEB-R dense top-100 score from 61.82 to 65.80 in its official evaluation ([model card](https://huggingface.co/Qwen/Qwen3-Reranker-0.6B)). It cannot score frames, but would be substantially cheaper if the observed problem is transcript ordering rather than multimodal ranking.

Late interaction is not a cheaper substitute here: the existing research estimates roughly 96× frame-vector storage at its cheap end and requires MaxSim plus a new retrieval engine, not a backend swap ([analysis](</home/dev/work/vidtheque/.claude/worktrees/peppy-wibbling-moler/research/multimodal-embedding-2026-08-09.md:248>)).

## Trigger conditions

Revisit the 2B reranker when either the corpus reaches roughly **500 videos** or real search telemetry produces a stable hard-query set—but corpus size alone must not trigger adoption.

Ship it only if all of these hold:

1. At least 10% of representative queries exhibit an **ordering error**: a clearly relevant result is already in fused top-20 but misses top-5. Missing candidates are recall failures a reranker cannot fix.
2. The 2B reranker improves post-repair nDCG@5 or MRR@5 by at least **0.05 absolute**, without regression on exact identifiers, natural-visual queries, or per-video diversity.
3. Added warm p95 latency is at most **500 ms per default search**, and an ask-mode run adds at most **2 seconds total**. Otherwise it may only qualify as an explicit high-precision path, not the default.
4. A 3090 test demonstrates safe peak VRAM, 20 repeated acquire/release cycles, and reliable restoration of the llama.cpp lease.

## Two-hour experiment that settles it

1. Select 30 real queries: 10 transcript paraphrases, 10 slides/terminal/OCR, 10 natural-visual.
2. Freeze the repaired corpus and capture the post-fusion, post-dedup top-50 for each.
3. Blind-label the union of current top-10 and reranked top-10; record whether gold was already in top-20/50.
4. Run `Qwen3-VL-Reranker-2B` BF16 through native Transformers at depths 20 and 50.
5. Compare MRR@5, nDCG@5, Recall@5, and regressions by modality.
6. Record warm/cold p50/p95, peak VRAM, and one representative multi-search ask loop.
7. Adopt only if the four trigger conditions above pass; otherwise leave it out.

**Final call: later.** The 2B reranker is technically relevant, especially for slides and screenshots, but there is presently no evidence that its incremental gain over repaired hybrid RRF is worth its user-facing latency and query-time GPU lease cost.