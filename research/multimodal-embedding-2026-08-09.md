# One model for both legs? Unified multimodal embedding, scored against this box (2026-08-09)

Tom's question: **can vidtheque replace SigLIP2-so400m-naflex (frames) + Qwen3-Embedding-0.6B
(text) with one multimodal embedding model?** He accepts slower indexing and expects
better results than SigLIP.

The answer is yes, and the interesting part is *why* — not because one space is
tidier, but because **the frame leg is the leg with an architectural deficit, and
nobody noticed** because the sibling analysis
(`research/pipeline-perf-2026-08-09.md` §6) was scoped to the text embedder and
correctly said "the frame leg stays SigLIP 2 — nothing in this analysis touches
it."

This document touches it. Short version:

- On natural images a CLIP-style dual encoder is *equal* to a 2B VLM embedder.
  On **text-in-image** content — slides, terminals, code on screen, which is
  literally this corpus — the published gap is **24.09 vs 87.84** (text→visual-document,
  UMRB) and **51.40 vs 84.11** (ViDoRe). That gap is architectural, not a scaling
  problem: SigLIP 2's own paper shows Screen2Words retrieval **saturating at ~17.8
  R@1 from patch budget 256 all the way to 1024**, so no knob we already have fixes it.
- **Qwen3-VL-Embedding-2B** (Apache-2.0, Jan 2026) is a strict upgrade on *both*
  legs over what runs today: +2.48 MMTEB retrieval on text over 0.6B, and a
  document-class image tower. MRL truncation to 1024 leaves `vec_chunks` untouched.
- It costs the perf doc's text-only plan 2.48 retrieval points versus
  Qwen3-Embedding-4B. That is the whole trade, and it buys one model instead of two.

Method: no GPU process, model download or benchmark was run for this document.
Every GPU number is cited from `research/gpu-validation-2026-08-08.md`; every
corpus number is a read-only query against `/home/dev/vidtheque-data/vidtheque.db`
(`mode=ro`); every model number carries a URL.

---

## 1. What runs today, exactly

| | text leg | frame leg |
|---|---|---|
| model | `Qwen/Qwen3-Embedding-0.6B` | `google/siglip2-so400m-patch16-naflex` |
| params | 0.6B | **1.136B** total (both towers) [[HF API]](https://huggingface.co/api/models/google/siglip2-so400m-patch16-naflex) |
| dim | 1024 (`vec_chunks FLOAT[1024]`) | 1152 (`vec_frames FLOAT[1152]`) |
| license | Apache-2.0 | Apache-2.0 |
| query path | `POST /v1/embeddings` `input_type=query` | `POST /v1/embeddings/frame-query` (text tower) |
| query context | 32K | **64 tokens**, lowercased |
| measured (3090) | 200 emb/s, **15 ms** query, 1975 MB peak / 1483 MB resident, 6.8 s load / 3.7 s reload | 103 frames/s @256 patches, **10 ms** query, 2489 MB after load / 2870 MB peak, 6.9 s load / **5.8 s reload** |

(`gpu-validation-2026-08-08.md` §2–§4; `worker/src/vidtheque_worker/backends/`.)

The corpus, read live today:

| | count |
|---|---:|
| videos | 75 |
| keyframes | 4,586 (3,060 not `dup_of`) |
| frame vectors | 3,060 |
| chunks | 2,728 (2,691 vectors) |
| keyframe JPEGs on disk | **556 MB**, max width 1280 (`pipeline/settings.py:57`) |

Two facts about the frame leg that matter for everything below:

1. **The frame leg is text→screenshot retrieval, not image similarity.** The
   query text goes through SigLIP's *text* tower into the same 1152-d space
   (`index-schema.md` §4.5; `tools/base.py:70-73`). That is the design's whole
   reason for choosing SigLIP over a captioning pass.
2. **The frame embedder sees ~7% of the pixels we stored.** `IMAGE_EMBED_MAX_PATCHES=256`
   at patch16 is 65,536 pixels — about 341×192 — against a 1280×720 stored JPEG
   (921,600 px). §3.2 shows why raising it does not help as much as you would hope.

---

## 2. The candidates

Scored against *this* deployment: single RTX 3090 (24 GB) sharing the card with a
~12 GB llama.cpp lease, worker is a stateless OpenAI-compatible HTTP API,
self-hosted only, MIT repo so the shipped default must be permissively licensed.

**Text→frame** column is the closest published proxy for "find the slide that
shows a KV-cache diagram": MMEB-V2 VisDoc / ViDoRe / UMRB T→VD, all
screenshot-and-document retrieval. **Text→text** is MMTEB Retrieval, the same
column `pipeline-perf-2026-08-09.md` §6.2 used.

| model | params | dim (MRL) | license | **text→frame** | **text→text** (MMTEB Retr.) | weights bf16 | verdict |
|---|---:|---:|---|---|---:|---:|---|
| **Qwen3-VL-Embedding-2B** | 2B | 2048 (64–2048) | **Apache-2.0** | MMEB-V2 VisDoc **79.2**, ViDoRe-v1 84.4 / v2 65.3; image overall 75.0 | **67.12** | ~4.4 GB | **recommended** |
| Qwen3-VL-Embedding-8B | 8B | 4096 | Apache-2.0 | VisDoc **82.4**, ViDoRe-v1 87.2 / v2 69.9; image 80.1 | 69.41 | ~16 GB | VRAM-disqualified (§4) |
| *SigLIP2-so400m-naflex* (today) | 1.14B | 1152 | Apache-2.0 | Screen2Words T→I **17.8 R@1 (saturated)**; no VisDoc/ViDoRe number published | **n/a — cannot do this leg** | 2.3 GB | the null hypothesis, §3.2 |
| SigLIP2 g-opt/16-384 | 1.87B | 1536 | Apache-2.0 | +1.0 COCO T→I over so400m; **no NaFlex variant** | n/a | 3.7 GB | rejected, §3.2 |
| PE-Core-G/14-448 (Meta) | 2.35B | 1280 | Apache-2.0 | **no screen/doc numbers published**; COCO T→I 58.1 (best open dual encoder) | n/a | ~4.7 GB | a gamble on the untested axis |
| GME-Qwen2-VL-2B | 2.21B | 1536 | Apache-2.0 | UMRB T→VD **87.84**; MMEB-V2 VisDoc 76.8 but image overall **51.9** | MTEB-en 65.27 (v1-era, not comparable) | ~4.4 GB | superseded by Qwen3-VL |
| RzenEmbed-8B | 8B | — | see card | MMEB-V2 VisDoc 81.3, all 72.9 | — | ~16 GB | VRAM-disqualified |
| VLM2Vec-V2 (2B) | 2B | — | Apache-2.0 | MMEB-V2 VisDoc 69.2, all 59.2 | MMEB-V3 text **24.5** | ~4.4 GB | dominated by Qwen3-VL-2B |
| BGE-VL-v1.5-mmeb | ~8B | 4096 | **MIT** | tuned for *composed image* retrieval; **absent from every ViDoRe board** | — | ~16 GB | wrong task, and VRAM |
| jina-embeddings-v4 | 3.8B | 2048 (MRL) | **Qwen Research (non-commercial)** | ViDoRe 84.11 dense / 90.17 late; ViDoRe-v3 57.52 | MTEB-en 55.97 | ~7.6 GB | **license-disqualified** |
| jina-embeddings-v5-omni-small | 1.74B | 1024 (MRL 32–1024) | **CC BY-NC-4.0** | ViDoRe-in-MIEB **79.08** | text 67.00 (own table) | ~3.5 GB | **license-disqualified**; otherwise the closest rival |
| ColQwen2.5 / colnomic / tomoro-colqwen3 / Ops-Colqwen3-4B / granite-vision-3.3-2b-embedding | 2–8B | 128–320 × **N patches** | mixed; several **Apache-2.0** | the best numbers anywhere: ViDoRe-v1&v2 up to **84.87**, ViDoRe-v3 up to **63.42** | n/a | — | **rejected on storage + SQL, §3.3** |
| voyage-multimodal-3.5, Gemini Embedding 2, Seed-1.6, Cohere v4 | — | — | closed | strong (Seed-1.6 MMEB-V2 76.9) | — | — | **API-only, no weights — violates "fully local, no third-party API"** |

Sources for the table: [Qwen3-VL-Embedding paper arXiv:2601.04720](https://arxiv.org/abs/2601.04720)
Tables 1–4 (verified from the PDF, not the card) ·
[Qwen3-VL-Embedding-2B card](https://huggingface.co/Qwen/Qwen3-VL-Embedding-2B) ·
[Qwen3-VL-Embedding-8B card](https://huggingface.co/Qwen/Qwen3-VL-Embedding-8B) ·
[QwenLM/Qwen3-VL-Embedding](https://github.com/QwenLM/Qwen3-VL-Embedding) ·
[SigLIP 2 paper arXiv:2502.14786](https://arxiv.org/html/2502.14786v1) Table 7 ·
[big_vision SigLIP2 README](https://github.com/google-research/big_vision/blob/main/big_vision/configs/proj/image_text/README_siglip2.md) ·
[GME arXiv:2412.16855](https://arxiv.org/html/2412.16855v1) Table 3 ·
[jina-embeddings-v4 arXiv:2506.18902](https://arxiv.org/html/2506.18902) Table A3 ·
[MIEB arXiv:2504.10471](https://arxiv.org/html/2504.10471v1) ·
[Perception Encoder arXiv:2504.13181](https://arxiv.org/abs/2504.13181) ·
[MMEB-V3 arXiv:2604.23321](https://arxiv.org/abs/2604.23321) ·
[jina-embeddings-v5-omni-small](https://huggingface.co/jinaai/jina-embeddings-v5-omni-small) ·
[jina-embeddings-v4](https://huggingface.co/jinaai/jina-embeddings-v4) ·
[BGE-VL-v1.5-mmeb](https://huggingface.co/BAAI/BGE-VL-v1.5-mmeb) ·
[Nemotron ColEmbed V2 arXiv:2602.03992](https://arxiv.org/html/2602.03992v2) (the ViDoRe snapshot) ·
[voyage-multimodal-3.5](https://blog.voyageai.com/2026/01/15/voyage-multimodal-3-5/).

Three negatives worth recording, because each is a question someone will ask again:
**there is no `jina-clip-v3`** (the CLIP line stops at v2 and was superseded by v4
then v5-omni — [jina.ai/models](https://jina.ai/models/)); **there is no
`voyage-multimodal-4`** (January 2026's Voyage 4 launch is text-only; the
multimodal line went to `voyage-multimodal-3.5`, still API-only, and the only
open-weights Voyage model ever shipped is text-only); and **there is no
`Nomic-Embed-Multimodal` v2** — the April 2025 family is current, and its 3B
checkpoints are **Qwen Research licensed**, only the 7B pair is Apache-2.0
([nomic blog](https://www.nomic.ai/news/nomic-embed-multimodal)).

All Qwen3-VL numbers above are taken from the **paper's** tables rather than the
model cards, because the two disagree slightly: the paper has 2B = 73.2 overall
(image 75.0 / video 61.9 / visdoc 79.2) and 8B = 77.8, while the current 8B card
lists 73.4 and 77.9 with a different video/visdoc split. Card revisions, not a
different model — but pick one source and stay on it, which this document does.

**Nothing open has clearly displaced Qwen3-VL-Embedding-8B at the top of MMEB-V2
since January 2026.** The [TIGER-Lab MMEB leaderboard](https://huggingface.co/spaces/TIGER-Lab/MMEB-Leaderboard)
has gained ~50 self-reported entries (SME-8B-V1, DME-*, Ovis-Omni, ReMatch, …) and
one of them — SME-8B-V1, 2026-08-04 — posts an *image-only* 80.38 against
Qwen3-VL-8B's 80.1, but its MMEB-V2 aggregate could not be verified and every
leaderboard row is marked `"Data Source": "Self-Reported"`. **Flagged as
unverified; do not plan around it.**

---

## 3. The frame leg is where the quality is, and SigLIP cannot get it

### 3.1 The gap is architectural, and it is huge

Three independent evaluations, three different benchmarks, same shape:

| evaluation | CLIP-style dual encoder | VLM embedder | ratio |
|---|---:|---:|---|
| UMRB **text→visual-document** ([GME Table 3](https://arxiv.org/html/2412.16855v1)) | CLIP-SF (0.4B) **24.09** | GME-2B **87.84** | 3.6× |
| ViDoRe ([jina-v4 Table A3](https://arxiv.org/html/2506.18902)) | `siglip-so400m-patch14-384` **51.40** | jina-v4 dense **84.11** | 1.6× |
| MIEB **Document Understanding** ([arXiv:2504.10471](https://arxiv.org/html/2504.10471v1)) | `siglip-so400m-patch14-384` **56.4** | voyage-multimodal-3 **71.1** | 1.26× |

And the control, on the *same* models: ordinary text→image retrieval is a tie.
UMRB T→I is CLIP-SF **59.05** vs GME-2B **57.36** — the small dual encoder *wins*.
MIEB's English Retrieval is SigLIP **40.8** vs voyage **38.8** — SigLIP wins again.
MIEB's own conclusion, quoted: *"CLIP-style models dominate traditional
classification and retrieval; MLLM-based models shine in document understanding,
OCR-heavy tasks, and multilingual settings."*

vidtheque's frame corpus is conference talks: slides, terminals, editors, diagrams
with labels. **It is the OCR-heavy column, not the natural-image column.**

**One honest caveat, and it is the biggest single unknown in this document:
every published head-to-head uses SigLIP *1* (`siglip-so400m-patch14-384`), never
SigLIP 2, and nobody has published SigLIP 2 on ViDoRe or any screenshot
benchmark at all.** SigLIP 2 improved on exactly this axis (its paper leads with
text-in-image). So the true gap for our checkpoint is smaller than 51.40 vs 84.11.
How much smaller is not published and would have to be measured. §3.2 argues it
cannot be small enough.

### 3.2 The null hypothesis: a bigger or higher-resolution SigLIP 2

Both cheap escapes are closed, and the paper closes them:

**Raising `IMAGE_EMBED_MAX_PATCHES` does not fix screenshots.** SigLIP 2's own
Table 7 ([arXiv:2502.14786](https://arxiv.org/html/2502.14786v1)), So400m/16,
Screen2Words text→image R@1:

| seq len | 64 | 144 | 256 | 576 | 676 | 784 | 900 | 1024 |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| Screen2Words T→I | 12.1 | 17.1 | **17.5** | 17.7 | 17.7 | 17.6 | 17.7 | **17.8** |

From our current 256 to the maximum trained 1024 — **4× the compute — is +0.3
points.** HierText (dense OCR) does improve (9.2 → 11.7) and SciCap actually
prefers the *square* variant at high sequence length (35.9 vs 32.6). But the
screenshot number, which is our number, is flat. `IMAGE_EMBED_MAX_PATCHES=1024`
is not a free upgrade waiting to be switched on.

**There is no bigger NaFlex.** The complete SigLIP 2 lineup is base / large /
so400m / giant-opt, and **only base and so400m have NaFlex checkpoints**
([HF listing](https://huggingface.co/api/models?author=google&search=siglip2),
[blog](https://huggingface.co/blog/siglip2)). `so400m-patch16-naflex` is already
the largest one that exists. Stepping to `giant-opt/16-384` costs +65% params
(1.87B), gives up native aspect ratio — the entire argument for NaFlex on 16:9
screencasts, per the backend's own docstring — and buys **+1.0 COCO text→image**
(56.1 vs 55.1). Reject.

**There is no SigLIP 3.** No Google announcement, no `google/siglip3*`, nothing in
`big_vision` as of today. The only open dual encoder that beats SigLIP 2 on COCO
text→image is Meta's [PE-Core-G/14-448](https://huggingface.co/facebook/PE-Core-G14-448)
(58.1 vs 56.1, Apache-2.0) — but it caps text at **72 tokens**, has no
aspect-preserving mode, and publishes **zero** screen or document numbers. Swapping
to it is a gamble on precisely the axis we care about.

Telling detail on where the field actually went: jina's 2026 omni models use
**SigLIP2 as their vision encoder**, feeding a frozen text LLM
([jina blog](https://jina.ai/news/jina-embeddings-v5-omni-multimodal-embeddings-for-text-image-audio-and-video/)).
The frontier is built *on top of* SigLIP 2's tower, not as a replacement for it —
which is the same statement as "the deficit is in the pooling-and-matching, not
the pixels."

### 3.3 Late interaction (ColPali/ColQwen/colnomic): best numbers, wrong schema

They genuinely lead the visual-document page. From the Qwen3-VL-Embedding paper's
Table 3 (all re-run by Qwen, so one methodology):

| model | size | ViDoRe-v1 | ViDoRe-v2 | ViDoRe-v3 | JinaVDR | avg |
|---|---:|---:|---:|---:|---:|---:|
| colqwen2.5-v0.2 | 3B | 89.5 | 59.3 | 52.4 | 75.6 | 72.4 |
| colnomic-embed-multimodal-3b | 3B | 89.7 | 63.5 | 56.4 | 77.6 | **74.2** |
| tomoro-colqwen3-embed-8b | 8B | 90.8 | 67.7 | 61.6 | 79.2 | **77.7** |
| **Qwen3-VL-Embedding-2B** (single vector) | 2B | 84.4 | 65.3 | 52.9 | 71.0 | 71.6 |
| **Qwen3-VL-Embedding-8B** (single vector) | 8B | 87.2 | 69.9 | 59.0 | 76.9 | 75.8 |

Qwen's own framing is fair: single-vector *"achieves performance comparable to
ColPali-style models that require significantly higher computational costs."*
2.6 points behind colnomic-3b at 2B, and the 8B single-vector already beats
colnomic-3b.

The field has also moved past ColQwen2.5, and — unlike the jina and NVIDIA lines —
several of the 2026 entrants are cleanly licensed: **`tomoro-colqwen3-embed-4b/8b`**
and **`Ops-Colqwen3-4B`** are Apache-2.0 (Qwen3-VL bases), **`colnomic-embed-multimodal-7b`**
is Apache-2.0 (the *3b* is Qwen Research), and IBM's **`granite-vision-3.3-2b-embedding`**
is Apache-2.0 at 2B. NVIDIA's `nemotron-colembed-vl-*-v2` tops ViDoRe-v3 at 63.42
but its HF cards say **CC-BY-NC-4.0** (its paper says CC-BY-4.0 — conflict flagged,
assume NC). ViDoRe-v3 nDCG@10 snapshot, **2026-02-03, possibly stale**
([Nemotron ColEmbed V2, arXiv:2602.03992](https://arxiv.org/html/2602.03992v2)):
nemotron-colembed-8b-v2 63.42 · tomoro-colqwen3-embed-8b 61.59 ·
nemotron-colembed-4b-v2 61.54 · Ops-Colqwen3-4B 61.17 · tomoro-colqwen3-embed-4b 60.20.

Reject anyway, for two reasons in increasing order of decisiveness:

1. **Storage.** ColPali emits **1,030 vectors of 128 dims per page**; ColQwen2/2.5
   **768**; granite-vision **729**; the Nemotron variants keep 773–2,304 vectors at
   **3,072–4,096 dims**
   ([Vespa](https://blog.vespa.ai/scaling-colpali-to-billions/) ·
   [colqwen2.5-v0.2](https://huggingface.co/vidore/colqwen2.5-v0.2) ·
   [granite-vision-3.3-2b-embedding](https://huggingface.co/ibm-granite/granite-vision-3.3-2b-embedding)).
   Taking ColQwen2.5 as the cheap end: our 3,060 frame vectors become
   3,060 × 768 × 128 × 4 B = **1.20 GB** against **12.5 MB** for one 1024-d f32
   vector each — **96×**. At `index-schema.md` §3.4's 500-video projection (40,000
   keyframes) that is **15.7 GB of vectors** in a database whose *entire* current
   projection is 420 MB. ColPali proper is 1.3× worse again; the Nemotron 8B is
   ~14× worse than that. The only clean lever anyone ships is Ops-Colqwen3-4B's
   **MRL over the per-token dimension** (128–2,560), which recovers ~20× — and
   still leaves us multiples above single-vector.
2. **`sqlite-vec` has no MaxSim.** The frame leg in `index-schema.md` §4.5 is one
   `vec_frames MATCH ... k = :k_frames` KNN with a `distance` column feeding RRF.
   Late interaction needs per-patch KNN plus a MaxSim aggregation plus a two-stage
   pooling/rerank. That is not a model swap; it is a new retrieval engine, and
   §3.1's "escape hatch is Vec1" does not cover it either.

The schema stores one vector per item. That is a decision, and this document is
not the place to relitigate it. **Noted for Tom as a real fork if frame quality
ever becomes the product's headline feature** — but the single-vector option is
within 2.6 points and costs 1/96th the disk.

### 3.4 The one benchmark that looks most like our corpus

If Tom wants a number from the literature that is closer to "a slide deck
screenshot" than ViDoRe's PDF pages, it is **REAL-MM-RAG-Bench**, which splits
out slide decks explicitly (nDCG@5, from the
[granite-vision card](https://huggingface.co/ibm-granite/granite-vision-3.3-2b-embedding)):

| model | FinReport | FinSlides | TechReport | TechSlides | avg |
|---|---:|---:|---:|---:|---:|
| ColNomic-3b | 78 | 81 | 88 | 92 | **85** |
| granite-vision-3.3-2b-embedding | 73 | 79 | 87 | 93 | 83 |
| ColQwen2.5-v0.2 | — | — | — | — | 81 |
| ColPali-v1.3 | — | — | — | — | 73 |

No dual encoder appears on that board at all — which is the point of §3.1, stated
one more way. And for measuring *our* frames, MIEB's **Visual STS** category is
the right instrument: it renders sentences as images to isolate text-in-image
understanding, and correlates **>99% with OCRBench and TextVQA**
([MIEB](https://huggingface.co/blog/isaacchung/introducing-mieb)).

---

## 4. This deployment: VRAM, the lease, and query latency

### 4.1 The peak envelope does not move — whisperX still owns it

Measured peaks on the 3090 (`gpu-validation-2026-08-08.md` §2, §5.1): whisperX
**7,941 MB**, SigLIP 2 **2,870 MB**, Qwen3-0.6B **1,975 MB**, plus ~340 MB of CUDA
context that never returns until the process exits. `HANDOFF-2026-08-08.md` sizes
the llama.cpp lease at ~12 GB, leaving the worker ~12 GB.

Models load **sequentially** — one `Slot` per task, LRU eviction before a load
(`worker/src/vidtheque_worker/lifecycle.py`) — so what matters is the largest
single slot, not the sum.

| config | largest embedder slot | slots | worker peak (whisperX-bound) |
|---|---:|---:|---|
| today | 3,200 MB (SigLIP estimate) | 2 | 7,941 MB |
| perf doc's 4B @ Q8 + SigLIP | ~4,000 MB | 2 | 7,941 MB |
| **unified Qwen3-VL-2B bf16** | **~6,000–7,000 MB** (est.) | **1** | 7,941 MB |
| unified Qwen3-VL-8B bf16 | ~17,000 MB | 1 | **17,000 MB — breaks the lease** |

Weights for the 2B are ~4.4 GB at bf16; the rest is activation over ~1,176 visual
tokens per frame at whatever batch size we choose, and `frame_embed_batch` is
already a knob. **The 2B fits under whisperX's existing high-water mark, so the
lease sizing does not change.** The 8B does not fit and is disqualified on this
line alone, exactly as `pipeline-perf-2026-08-09.md` §6.4 disqualified
Qwen3-Embedding-8B — and it is the same disqualification for the same reason.

### 4.2 The `EMBED_RESIDENT` trap, restated for a bigger model

`gpu-validation-2026-08-08.md` §5.3: a resident backend keeps `_any_loaded()`
true forever, so **`GPU_RELEASE_CMD` never fires while the worker is up** — on
Tom's box llama.cpp is stopped at the first embedding request and never restarted.

Today that trap costs 1,483 MB standing. **With a unified 2B it costs ~4.4 GB
standing, and the co-tenant still never comes back.** Recommendation is unchanged
from the perf doc and now firmer: **`EMBED_RESIDENT=0`.**

The consolation is real and it is the one genuine operational win of going
unified. Today a cold `content_type=all` search loads **two** models: Qwen3-0.6B
(3.7 s reload) *and* SigLIP 2 (**5.8 s reload**), possibly with an eviction
between them, because the two legs live in two spaces and two slots. A unified
model pays **one** load. The frame embedder's 5.8 s reload — which the perf doc
called "the more interesting number" — stops being a separate event.

Note also that `EMBED_RESIDENT` today covers only the *text* embedder by explicit
design (`worker/src/vidtheque_worker/config.py`: *"The frame embedder is never
resident"*). With one model there is one decision, not two, and that docstring
has to be rewritten.

### 4.3 Query latency: every search pays it

| | measured / estimated |
|---|---|
| Qwen3-Embedding-0.6B, single query | **15 ms** (measured) |
| SigLIP 2 text tower, single query | **10 ms** (measured) |
| Qwen3-VL-Embedding-2B, single query | **unpublished** — est. 15–40 ms |
| Qwen3-VL-Embedding-8B, single query | **unpublished** — est. 40–100 ms |

**There is no published single-query latency for Qwen3-VL-Embedding at any size** —
not in the paper, the cards, or the repo. The estimates above are derived from
weight-bandwidth on a 3090 and the one adjacent measurement I could find
(ColPali, ~3B, encodes a 15-token query in ~30 ms:
[OpenSearch benchmark](https://opensearch.org/blog/benchmarking-multimodal-document-search-in-opensearch-three-approaches-compared/)).
**This is bench item #1 in §7.**

Structurally: a `content_type=all` search embeds the query **twice** today (text
space + frame space) and would embed it twice with a unified model too, because
the model is instruction-aware and the two legs want different instructions. But
both calls hit one loaded model, and they can be batched into one forward pass if
we want them to be.

### 4.4 Serving and quantization

- **vLLM ≥ 0.14.0 is the supported path** ([repo](https://github.com/QwenLM/Qwen3-VL-Embedding)),
  plus `sentence-transformers` and `transformers ≥ 4.57.0`. The existing
  `Qwen3EmbedBackend` already goes through `sentence-transformers`, so the
  shortest path to a working backend is the same library.
- **transformers ≥ 4.57 is a problem worth flagging now.** The worker is pinned to
  transformers 4.x *below* 5.x because whisperX caps `huggingface-hub`
  (`siglip2_image_embed.py` module docstring). 4.57 is inside that window, but it
  is a tighter floor than today's and it needs checking against the lock before
  anyone promises a date.
- **No official GGUF, and llama.cpp support is not merged.** Draft PR #18665 is
  open; community GGUFs exist (`DevQuasar/…-2B-GGUF`, Q4_K_M and Q8_0) and users
  report `/embeddings` working only with a `--sentence-transformers-dense-modules`
  workaround for a pooling-conversion error
  ([discussion #19516](https://github.com/ggml-org/llama.cpp/discussions/19516)).
  So the perf doc's attractive idea — *serve the embedder through the llama-server
  that is already on the box, adding no second CUDA context* — **is not available
  for this model yet.** It is available for Qwen3-Embedding-4B, which is a real
  point in the two-model plan's favour.
- **Embedding-side quantization is first-class and QAT-trained.** The paper's §7.1
  reports *"int8 quantization preserves retrieval performance with negligible
  degradation, whereas binary quantization significantly impairs retrieval
  effectiveness."* That maps directly onto `config['frame_embed.storage']` and
  `vec_quantize_int8` (`index-schema.md` §3.4) — the 3.85× file-size saving
  becomes a much safer switch than it is with an embedder that was never trained
  for it.
- **MRL is measured, not assumed.** Same section: *"reducing the embedding
  dimension from 1024 to 512 results in only a 1.4% decrease in retrieval
  performance while achieving 50% storage reduction and doubling retrieval
  speed."* The perf doc had to caveat that "Qwen publishes no MRL degradation
  table" for Qwen3-Embedding. **For Qwen3-VL-Embedding they do** (Figure 6, swept
  32→1024), and 1024 — the width we need — is inside the swept range.

### 4.5 Our frames land exactly on the model's knee

Keyframes are stored at max width 1280 (`pipeline/settings.py:57`), so 1280×720 =
921,600 px, comfortably under Qwen3-VL-Embedding's default `max_pixels` of
1,843,200 ([repo](https://github.com/QwenLM/Qwen3-VL-Embedding)). At 28×28 px per
merged visual token that is **~1,176 tokens per frame** — and the paper's Figure 7
sweeps image-task performance against visual tokens from 200 to 1,200, rising
throughout with *"a pronounced diminishing return"* and a slight regression at the
very top. **We would be feeding it full-resolution frames right at the knee of its
own scaling curve, with no downsampling and no knob to tune.** Contrast today's
256-patch budget, which is a 341×192 view of the same file.

---

## 5. Migration: what a swap actually invalidates

### 5.1 Two stages, verified in the code

`_should_run` re-runs a stage exactly when its recorded `model_key` differs from
the current one (`pipeline/runner.py:1122-1139`).

- `_stage_text_embed` keys on `config['text_embed.model']` (`runner.py:652-653`).
- `_stage_frame_embed` keys on `config['frame_embed.model']` (`runner.py:866-871`).

Change both and **exactly two stages go stale**. `fetch`, `stt`, `chunk`,
`keyframe` and `ocr` all stay `done`.

**Nothing is re-downloaded.** `want_media` is gated on the *keyframe* stage being
stale (`runner.py:329-331`), which it is not — so the mp4s that
`keep_source=audio` already deleted (`runner.py:1070`) are never missed. The
inputs are the **556 MB of keyframe JPEGs already on disk** (4,586 files, verified
present) and the chunk text already in SQLite. Nothing is re-transcribed and
nothing is re-OCR'd.

### 5.2 Schema: one table rebuilt, one untouched

With MRL truncation to 1024 on both spaces:

| | today | after | change |
|---|---|---|---|
| `vec_chunks` | `FLOAT[1024]` | `FLOAT[1024]` | **none** |
| `config['text_embed.dim']` | 1024 | 1024 | **none** |
| `vec_frames` | `FLOAT[1152]` | `FLOAT[1024]` | **DROP + CREATE** |
| `config['frame_embed.dim']` | 1152 | 1024 | update |
| `config['text_embed.model']` | `Qwen/Qwen3-Embedding-0.6B` | `Qwen/Qwen3-VL-Embedding-2B` | update |
| `config['frame_embed.model']` | `google/siglip2-so400m-patch16-naflex` | `Qwen/Qwen3-VL-Embedding-2B` | update |

`index-schema.md` §1.10 rule 3 already covers the shape: *"Derived tables are
rebuilt, never migrated"*, and a dimension change enqueues a backfill while the
§1.1 boot assertion keeps the vector legs disabled until it finishes. Note the
`keyframes_ad` trigger (§3.3) has to be recreated with the table, or deleting a
video silently stops cleaning up frame vectors.

**A `docs/design/index-schema.md` edit ships in the same commit** — §1.1's config
table, §3.1's DDL, §3.4's storage arithmetic (a 1024-d frame vector is 11% smaller
than a 1152-d one), and §4.5's paragraph explaining `:q_img_vec` as "SigLIP's text
tower". That is the CLAUDE.md contract-follows-implementation rule, and §4.5 is
the paragraph that most needs rewriting.

### 5.3 Re-embed cost — minutes, not hours

| | items | today's rate | estimated new rate | estimated time |
|---|---:|---|---|---:|
| frames | 3,060 | 103 frames/s | ~4–5 frames/s | **~11 min** |
| chunks | 2,691 | 200 emb/s | ~55 emb/s | **~50 s** |

The new rates are **estimates, not measurements**: ~23× the FLOPs per frame
(2.2B params over ~1,176 tokens against SigLIP's ~429M vision tower over 256
patches) and ~3.7× per chunk. Even five times pessimistic this is about an hour of
GPU, with no downloads and no re-transcription.

Steady state, against the perf doc's stage table: `frame_embed` 2.9 s/video →
~67 s, `text_embed` 1.0 s → ~3.7 s, so a 315 s average video becomes ~381 s
(**+21%**). The `keyframe` stage at 184.5 s/video remains the dominant cost by a
wide margin, and both of the perf doc's free wins (§4 items 1–3, ~1.2× on the
whole pipeline) more than pay for this. **Tom's "would probably take more time but
still acceptable" is comfortably satisfied.**

### 5.4 The ordering trap, now doubled

`pipeline-perf-2026-08-09.md` §6.3 found it for the text leg: `note_worker_drift`
(`db/database.py:181-199`) disables the vector legs the moment the worker reports
a model that differs from `config['text_embed.model']`, and `_stage_text_embed`
skips itself when `db.vectors.enabled` is false. **Config first, worker second.**

Two additions for a unified swap:

1. **`_stage_frame_embed` gates on the same latch** (`runner.py:876-880`) — it
   checks `db.vectors.enabled`, which is set by the *text*-space drift check. So
   getting the text-side ordering wrong silently skips the frame re-embed too. One
   rule still covers it, but the blast radius is now both legs.
2. **`note_worker_drift` never sees the frame space.** `Deps.embed_query`
   deliberately calls it only for `space == "text"` (`tools/base.py:96-99`), with
   the comment *"Only the text space is checked for drift"*. That asymmetry made
   sense when the two spaces came from two checkpoints. With **one** model serving
   both, a frame-space model mismatch is a text-space model mismatch, and the
   check should cover both. Small change; worth doing in the same commit as the
   swap, not after.

### 5.5 The worker change that is not cosmetic

This is the implementation finding worth Tom's attention before anyone estimates
the work.

`build_backends` instantiates **one backend per task** — `stt`, `embed`,
`image_embed`, `ocr` (`backends/registry.py`) — and `LifecycleManager` builds
**one `Slot` per task**, each with its own `vram_estimate_mb`, its own
`load_count`, its own eviction clock (`lifecycle.py:146-148`). Registering the
same unified checkpoint under both `embed` and `image_embed` would **load it twice
and account its VRAM twice** — ~8.8 GB charged for one 4.4 GB model, and the
admission control in `gpu-validation-2026-08-08.md` §5.1 would start evicting
whisperX for a model that is already on the card.

So a unified embedder needs a **shared-slot** concept: either an alias
(`image_embed → embed`'s slot) or a `unified_embed` task that all three endpoints
submit to. The three HTTP paths — `/v1/embeddings`, `/v1/embeddings/image`,
`/v1/embeddings/frame-query` — all stay exactly as they are, which is the point of
having made them separate paths; only the routing behind them collapses.

---

## 6. Does one space actually simplify the search legs? Mostly no — and that is fine

Honest answer, from reading the query layer rather than assuming:

**The three legs are content types, not vector spaces.** `search`'s legs are
`transcript` / `ocr` / `frame` (`tools/search.py:164`), fused by RRF on *ranks*
(`tool-surface.md` §3.x), each with its own SQL and its own per-video cap. A
shared vector space changes none of that. `all` still means all; a skipped leg
still prints a `note:`; the `published_*` vs `offset_*` axes are untouched;
`frame_id` construction is untouched. **Unified is not a query-layer
simplification.** Anyone selling it as one is selling operational elegance.

What *does* change, and all of it is upside:

- **The 64-token query ceiling disappears.** Today a frame query is lowercased and
  truncated to 64 tokens, and `_warn_if_truncated` exists solely because
  truncation is otherwise mute (`siglip2_image_embed.py:276-294`). A 32K-context
  unified model retires that whole class of silent quality loss. On a corpus where
  the client model writes the queries, long descriptive frame queries are exactly
  what gets typed.
- **One model to pin, license, download, load and evict** instead of two.
- **`/v1/embeddings/frame-query` keeps earning its keep.** The sibling-path design
  (`index-schema.md` §4.5: an unknown *field* is ignored, an unknown *path* 404s)
  is still right, and it becomes the place the *image-retrieval instruction* is
  applied — the model is instruction-aware and the two legs want different
  instructions. The `frame_text_encoder` latch and its `note:` stay as they are.
- **A new option, not a recommendation: index frames as image + OCR text
  together.** Qwen3-VL-Embedding accepts arbitrary interleaved multimodal inputs,
  so a keyframe's vector could be `(JPEG, its OCR lines)` in one embedding.
  Tempting for slide content — and it couples `frame_embed` staleness to the `ocr`
  stage, which today are independent. Flagging as future work, not proposing it.

One new record the schema should carry: the **instruction string**, the way
`config['text_embed.query_prefix']` records what indexing assumed. The perf doc's
§5 already found that key drifted out of sync with reality
(`'query: '` recorded against a model that uses `Instruct: …`). An instruction-aware
model with two instructions makes that record more load-bearing, not less.

---

## 7. Verdict

**Go unified, on `Qwen/Qwen3-VL-Embedding-2B`, MRL-truncated to 1024 on both
spaces, non-resident — after one bench answers the query-latency question.**

Why this and not the alternatives, in the order Tom will ask:

1. **It is a strict upgrade on both legs over what runs today.** Text retrieval
   67.12 vs 0.6B's 64.64 on MMTEB (+2.48), and a frame tower from the model class
   that measures 1.3–3.6× better than dual encoders on document-like imagery.
   There is no leg that gets worse.
2. **It fixes the leg the perf doc left alone, which turns out to be the weak
   one.** `pipeline-perf-2026-08-09.md` §6.5 recommended Qwen3-Embedding-4B and
   concluded "the frame leg stays SigLIP 2 … nothing in this analysis touches it."
   That was correct within its scope and it is the wrong place to spend the
   upgrade: the text leg is a strong model being made stronger, the frame leg is a
   model architecturally unsuited to text-in-image retrieval and with **no bigger
   NaFlex to buy and no resolution knob that helps** (§3.2).
3. **The cost of unification is one number: 2.48 MMTEB retrieval points on the
   text leg** (VL-2B 67.12 vs Qwen3-Embedding-4B 69.60). That is the honest price
   of one model instead of two, and it is paid against a leg that is still
   improving relative to today.
4. **VRAM is not the obstacle at 2B, and is fatal at 8B.** ~4.4 GB of weights
   under whisperX's existing 7.9 GB high-water mark keeps the ~12 GB lease sized
   as it is (§4.1). The 8B — MMEB-V2 77.8, MMTEB retrieval 69.41, genuinely better
   on both legs — needs ~16 GB and is disqualified by the co-tenant, the same way
   Qwen3-Embedding-8B was.
5. **Apache-2.0.** The strongest same-space alternative, jina-embeddings-v5-omni,
   is CC BY-NC-4.0 and cannot be an MIT repo's shipped default. Everything better
   than Qwen3-VL-8B on MMEB-V2 is a closed API.
6. **The migration is small and reversible.** ~12 minutes of re-embed, no
   re-download, no re-transcription, `vec_chunks` untouched, one derived table
   rebuilt (§5).

### If the bench goes badly — the hybrid

If query latency comes back unacceptable, or the transcript leg measurably
regresses on this corpus, **the fallback is not "stay as we are". It is
best-of-breed per leg:**

> `Qwen3-Embedding-4B` (Q8, MRL@1024) for transcripts + `Qwen3-VL-Embedding-2B`
> for frames.

That is the maximum-quality configuration on paper — 69.60 text retrieval *and* a
document-class frame tower — and it costs two slots, two loads per cold search,
and the operational surface Tom asked to escape. It also keeps the llama-server
option alive for the text half, since Qwen3-Embedding-4B *does* have working GGUF
support and Qwen3-VL-Embedding does not (§4.4).

**What is not on the table any more is the perf doc's plan on its own** —
4B text + SigLIP 2 frames. It upgrades the leg that was already fine and leaves
the leg with the architectural deficit exactly where it is.

### The three numbers to measure on Tom's box first

Neither runnable here; all three are `bench/` territory and none needs a full
reindex.

1. **Single-query latency** for Qwen3-VL-Embedding-2B, warm and after an idle
   unload, against 0.6B's 15 ms and SigLIP's 10 ms. **Nothing is published for
   this model at any size.** If it comes back over ~150 ms warm, the unified plan
   is in trouble and the hybrid is the answer.
2. **Frame retrieval on this corpus**, SigLIP 2 @256 patches vs Qwen3-VL-2B, on a
   held-out set of real screenshot queries against the existing 3,060 keyframes.
   **Nobody has published SigLIP 2 on any screenshot benchmark** (§3.1) — the
   3.6× gap in the literature is measured against SigLIP *1*, and SigLIP 2 was
   specifically improved on this axis. This is the one number that decides whether
   the frame case is as strong as §3 argues. If a public instrument is wanted
   rather than a hand-built query set, MIEB's **Visual STS** is the one built for
   exactly this (§3.4).
3. **MRL@1024 vs 2048 on this corpus.** Qwen *does* publish a truncation sweep for
   this model (unlike Qwen3-Embedding) and 1024→512 costs 1.4%, so 2048→1024
   should cost less — but it is measured on MSMARCO, not conference transcripts.

`bench/scenarios/` already has `embed-resident.toml` and `frame-embed-patches.toml`;
this is a third scenario file, not a new harness.

---

## 8. Open questions for Tom

1. **Is 2.48 points of text retrieval the right price for one model instead of
   two?** That is the whole decision. Unified VL-2B gives +2.48 over today's 0.6B;
   the two-model best-of-breed gives +4.96 and keeps two slots, two loads and two
   checkpoints. Nobody can answer this from a benchmark table — it is a taste
   question about operational surface.
2. **MMTEB is multilingual; this corpus is English.** The perf doc's headline for
   the 4B was **MTEB English v2 retrieval 61.83 → 68.46**. Qwen publishes **no
   English-only table for the VL models**, so the +2.48 above is the multilingual
   proxy. If the English gap has a different shape, the trade in (1) changes. Worth
   an MTEB-eng run, or worth accepting as a known unknown?
3. **No official GGUF, and llama.cpp support unmerged.** The perf doc's neatest
   idea — serve the embedder through the llama-server already running on the box,
   no second CUDA context — works for Qwen3-Embedding-4B and does not work for
   Qwen3-VL-Embedding today. How much does that weigh?
4. **transformers ≥ 4.57 floor** against the whisperX/`huggingface-hub` pin that
   currently keeps the worker on 4.x. Needs checking against `uv.lock` before this
   gets a date.
5. **Frames as image + OCR text in one vector** (§6). Genuinely attractive for
   slides, and it couples `frame_embed` staleness to the `ocr` stage. v2 idea or
   never?
6. **Late interaction is the best number on the visual-document page and we are
   rejecting it on storage** (96× per frame, 15.7 GB at the 500-video projection)
   and on `sqlite-vec` having no MaxSim. Note the licensing objection has
   *evaporated* since ColPali — `tomoro-colqwen3-embed-4b/8b`, `Ops-Colqwen3-4B`,
   `colnomic-embed-multimodal-7b` and `granite-vision-3.3-2b-embedding` are all
   Apache-2.0 — so storage and SQL are the only things saying no now. If frame
   search ever becomes the headline feature, that is a real fork — but it is a
   retrieval-engine change, not a model swap.
7. **`Qwen3-VL-Embedding-8B` is better on both legs and does not fit.** MMEB-V2
   77.8, MMTEB retrieval 69.41 — that last number is level with the text
   specialist 4B, i.e. the 8B unified model gives up essentially *nothing* on
   text. It needs ~16 GB. Is the ~12 GB llama.cpp lease permanent, or is there a
   world where the worker gets the whole card for a night of indexing and the
   lease comes back in the morning?

---

## Addendum — Tom's decision (2026-08-09, evening)

**Adopt the unified recommendation with two amendments: native 2048 dims and
full precision, both revisitable on evidence.**

- **Dims: 2048, not the memo's MRL@1024.** Truncation is the fallback if
  query-time search proves too slow, not the starting point — MRL makes it a
  config change + ~12 min re-embed later, so the cheap experiment is to start
  from full quality. Consequence: *both* vector tables rebuild
  (`vec_chunks` 1024→2048, `vec_frames` 1152→2048), and index-schema.md's
  dims change with the implementation. Storage stays trivial
  (~5.5k vectors × 8 KB ≈ 45 MB fp32).
- **Precision: full (bf16 weights, ~4.4 GB), not quantized.** Quantization is
  the second lever held in reserve for the same "too slow / too big" trigger.
- Bench items when the GPU frees up, in order: (1) single-query embed latency
  at 2048/bf16 — the number nobody has published; (2) the frame-leg quality
  spot-check against SigLIP on our own slides/terminal frames; (3) only if
  either disappoints: MRL@1024 and/or a quantized checkpoint.

## Addendum 2 — the three numbers, measured (2026-08-09, GPU window)

Append-only; nothing above is edited. The addendum above asked for three things
in order. All three are below, plus the MRL@1024 comparison that was held in
reserve. **The unified plan survives the bench, and the reason it survives is
not quite the reason §3 predicted.**

Headline, in the order Tom asked:

| | asked | measured |
|---|---|---|
| 1. single-query latency, 2048/bf16 | "over ~150 ms warm and the unified plan is in trouble" | **11.8 ms warm** (median, n=100). Not in trouble. |
| | VRAM loaded | **4,059 MB** weights, **4,318 MB** of card, **6,504 MB** peak at batch 8 |
| 2. frame throughput | — | **5.38 img/s**; **9.5 min** to re-embed the corpus |
| 3. frame quality vs SigLIP | the number that decides §3 | **MRR 0.87 vs 0.84** overall; **0.93 vs 0.85** on text-in-image; **0.68 vs 0.79** on natural-visual |
| 4. MRL@1024 | deferred | costs **nothing** measurable here — and scored *higher* on this set |

### Method, and what it is not

`bench/embed_latency.py` and `bench/frame_retrieval_spotcheck.py`, both new,
both run on Tom's box against the live demo stack:

- **Qwen3-VL-Embedding-2B** loaded through the loader Qwen ships inside the
  checkpoint (`scripts/qwen3_vl_embedding.py`), bf16, `sdpa`, native 2048 dims,
  no quantization — Tom's configuration exactly. torch 2.8.0+cu128,
  transformers 4.57.1, RTX 3090.
- **SigLIP 2 is the deployed one**, reached over HTTP at the worker's own
  `/v1/embeddings/image` and `/v1/embeddings/frame-query`, at the production
  patch budget (`IMAGE_EMBED_MAX_PATCHES=256`; the pipeline sends no override).
  Not a fresh checkpoint configured to taste.
- Frames are real corpus keyframes, opened read-only. The corpus has grown since
  §1: **3,060 non-duplicate keyframes** (4,586 rows including `dup_of`),
  75 videos.

**The quality half is a spot check, not a benchmark.** 27 hand-written queries,
50 hand-labelled frames, one judge (me, looking at the frames), 200 unlabelled
distractor frames in the ranking pool. It can say "one model is obviously better
at X"; it cannot produce a number worth quoting next to MTEB. The per-query
table below is the evidence; the aggregate is one decimal place of false
precision on 27 samples.

### 1. Query latency: the objection evaporates

Everything measured in the same hour, on this box:

| | median | p90 | notes |
|---|---:|---:|---|
| **Qwen3-VL-Embedding-2B, one text query** | **11.78 ms** | 12.21 ms | in-process, n=100, 32 input tokens |
| Qwen3-Embedding-0.6B (deployed today) | 15.12 ms | 15.84 ms | through the worker, over HTTP |
| SigLIP 2 text tower (deployed today) | 5.75 ms | 6.05 ms | through the worker, over HTTP |
| loopback HTTP floor (`/healthz`) | 0.58 ms | 0.66 ms | so the two rows above are essentially all model |
| 2B, **both legs in one batched forward** | 15.16 ms | 15.83 ms | §4.3's "they can be batched" — 15 ms for the pair, not 2 x 11.8 |
| 2B, first call after load | **916 ms** | — | second call 12.9 ms; this is what `EMBED_RESIDENT` hides |

§4.3 estimated 15–40 ms and set the trouble line at 150 ms. The answer is
**11.8 ms**, which is *below the deployed 0.6B leg's own end-to-end number* — not
because a 2B is faster than a 0.6B in the abstract, but because at 32 input
tokens neither model is compute-bound, and the 0.6B's number includes the
`sentence-transformers` and worker path that the 2B would inherit too. The
honest statement: **swapping the text leg for this model does not cost
measurable query latency**, and §7's "if it comes back over ~150 ms the hybrid
is the answer" is settled in the unified plan's favour.

Cold start earns its own line: **916 ms for the first call after load**, then
11–13 ms forever. `EMBED_RESIDENT=1` was already the right call; with this model
it also buys away a near-second first-search stall, which is a worse thing to
show a user than a load hidden behind a spinner.

### VRAM: it fits, and it replaces more than it costs

| | |
|---|---:|
| weights, bf16 (`torch.memory_allocated`) | **4,059 MB** |
| the card's view of the process (`nvidia-smi`) | **4,318 MB** |
| peak, frame batch 8 | 5,444 MB allocated / 6,188 MB reserved / **6,504 MB** process |
| load, warm HF cache | **3.5 s** |

Against what it retires — the worker's own estimates, `qwen3-embedding` 2,000 MB
resident plus `siglip2` 3,200 MB on demand — **one model at 4.3 GB resident is
cheaper than the two it replaces**, and its 6.5 GB indexing peak still sits well
under whisperX's 8 GB slot. §4.1 holds: whisperX still owns the envelope, and
the ~12 GB llama.cpp lease is untouched by any of this.

### 2. Frame throughput, and the re-embed

| batch | img/s | ms/img | corpus (3,060 frames) |
|---:|---:|---:|---:|
| 1 | 4.02 | 249 | 12.7 min |
| 4 | 5.30 | 189 | 9.6 min |
| **8** | **5.38** | **186** | **9.5 min** |

Batching past 4 buys nothing — one 880-token frame already saturates the card.
For scale, in the same run the deployed SigLIP did the same 250 frames at
**~93 img/s including HTTP**, so **the 2B is ~17x slower per frame**. That is the
real cost of the swap, and it is affordable exactly because frame embedding was
never the expensive stage: §5.3's "minutes, not hours" survives at **~10 minutes
for a full re-embed** (~14 min if every `dup_of` row is re-embedded too), against
RapidOCR's 3.4 frames/s, which would take **15 hours** over the same frames.

**§4.5's token estimate was 34% high.** The shipped processor gives a 1280x720
keyframe a grid of `[1, 44, 80]` → **880 visual tokens, 902 with the prompt** —
not the ~1,176 estimated from a 28 px merged patch, because the loader's
`IMAGE_FACTOR` is 32. So we sit a little lower on the paper's Figure 7 curve than
§4.5 claimed: still in its top band, still no downsampling, with slightly more
headroom before the regression at the very top than we thought.

### 3. The frame leg, head to head — and a correction to §3

27 queries against a 250-frame pool (50 labelled + 200 unlabelled distractors),
`sim = q · f`, both spaces L2-normalised.

| | overall MRR | top-1 | text-in-image MRR (21 q) | natural-visual MRR (6 q) |
|---|---:|---:|---:|---:|
| SigLIP 2 @256 patches (deployed) | 0.838 | 21/27 | 0.851 | **0.792** |
| Qwen3-VL-Embedding-2B @2048 | 0.874 | 21/27 | **0.929** | 0.681 |
| Qwen3-VL-Embedding-2B @MRL 1024 | **0.901** | **22/27** | **0.952** | 0.722 |

Nominal wins: **5 Qwen / 5 SigLIP / 17 ties.** The wins are not the same size,
and three of SigLIP's five are artefacts of my labelling — I opened every one to
check:

| query (abbreviated) | SigLIP rank | 2B rank | what actually happened |
|---|---:|---:|---|
| "code editor with calc.baml open and 18 problems in the file" | **19** | **1** | the clearest result in the set: SigLIP cannot read the filename or the problem count and ranks 18 other screenshots above it |
| "terminal running ast-grep piped through jq printing orpc-unmigrated-procedure warnings" | 7 | **1** | SigLIP's top-1 was a different code screenshot; the 2B read the terminal |
| "file tree listing instruction.md, task.toml, Dockerfile, solve.sh and test.sh" | 3 | **1** | filenames are the only signal in the frame |
| "slide titled eval building describing builder, critic and tester roles" | 3 | **1** | SigLIP's top-1 was an unrelated dense slide |
| "GitHub PR to upgrade astro **to v7.0.5**" | **1** | 2 | a real SigLIP win, and a narrow one: the 2B's top-1 is the *other* astro-upgrade PR in the corpus (v5.18.0) — right in every respect but the version string |
| "speaker wearing **a baseball cap** behind a lectern" | **1** | 2 | a real SigLIP win: the 2B returned a lectern shot with no cap. Fine-grained visual attributes are a dual encoder's home ground |
| "slide that says Musical.ly was about to rebrand to TikTok" | 1 | 2 | **not a loss** — the 2B's top-1 is the *same slide* 60 s later, which my labels did not list |
| "the AI Engineer World's Fair logo card on black" | 1 | 2 | **not a loss** — the 2B's top-1 is the same bumper card in a different video |
| "three seated panelists, the middle one gesturing" | 2 | 4 | **not a loss either** — both models' top-1 is a correct unlabelled frame from the same panel |

**What this says about §3, honestly:**

- **§3's direction is confirmed on our own corpus; its magnitude is not.** The
  published gap it quotes (24.09 vs 87.84, text→visual-document) is measured
  against SigLIP **1**. Here, against SigLIP **2** at 256 patches, the frame leg
  is *already good*: 0.85 MRR on text-in-image queries, 17/21 top-1. §3.1 flagged
  exactly this risk — "nobody has published SigLIP 2 on any screenshot
  benchmark" — and flagging it was right.
- **Where SigLIP 2 fails, it fails hard, and the 2B repairs it.** Rank 19 → 1 and
  7 → 1 are the shape of the win: not a uniform lift, but a fix for the cases
  where the query turns on *small* text — a filename, a flag, a count — that a
  256-patch view of a 1280x720 screenshot has physically destroyed. Those are
  real vidtheque queries.
- **The 2B is measurably worse on natural-visual queries** (0.68 vs 0.79), which
  §3 did not predict — the literature said "equal on natural images". Six queries
  is barely a signal, but it points the same way as the two legitimate SigLIP
  wins.
- **Ties dominate: 17 of 27.** Anyone expecting a step change in *everyday*
  frame search from this swap should expect instead: the same answers, plus the
  screenshots you previously could not find at all.

### 4. MRL@1024, the deferred question

Truncating to 1024 and renormalising **cost nothing on this set and scored
slightly higher** (MRR 0.901 vs 0.874; 22 vs 21 top-1). On 27 queries that
difference is noise; the finding is the *absence of a penalty*, which is what the
question asked.

It buys nothing at query time, and it is worth saying why: MRL is a **slice on
the way out** of the same forward pass — 0.02 ms — so end-to-end at 1024 is
12.09 ms against 2048's 11.78 ms, i.e. identical. **The saving is entirely in
`sqlite-vec`**: half the vector bytes, half the distance work. Tom's decision
stands on its own terms — start at 2048 because the model costs the same either
way, and keep 1024 as the lever to pull if *search* rather than embedding turns
out to be slow.

### What is still not measured

- **The text leg's retrieval quality.** Everything above about text is latency.
  The +2.48 MMTEB claim over the 0.6B is still someone else's corpus, and open
  question 2 (MMTEB is multilingual, we are English) is untouched.
- **Anything at 8B.** Open question 7 stands.
- **int8 storage.** The paper's QAT claim (§4.4) is unverified here; with a
  re-embed costing 10 minutes it is cheap to test against the 2048-dim baseline
  that now exists.
- **transformers ≥ 4.57 against the worker's lock** (open question 4). This bench
  ran in a throwaway venv at 4.57.1 precisely so it would not touch the live
  stack — which proves the model works at 4.57 and proves nothing about whether
  `uv.lock` can get there.

### One thing found on the way, for the pipeline

**190 of the 3,060 keyframes carry no OCR text at all, and every one sampled was
a fade-to-black transition** — pure black frames, kept as keyframes, each with
its own `youtu.be/ID?t=` deep link. The sampler in
`frame_retrieval_spotcheck.py` grew a mean-luma filter to stop scoring retrieval
against them. Not this document's problem, but `_sharpest_in` choosing a black
frame as the sharpest in its shot is a keyframe-selection question somebody
should ask.
