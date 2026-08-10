# Every vector in the corpus is a random projection (2026-08-10)

Author: root-cause session `peppy-wibbling-moler`, answering §3 of
`research/vec-floor-calibration-2026-08-10.md` ("the stored text vectors are not
in the model's space"). Append-only: add sections, don't rewrite these.

**The one sentence.** `Qwen/Qwen3-VL-Embedding-2B` has never once loaded its
weights on this stack — sentence-transformers loads it through `AutoModel`,
transformers 4.57.6 cannot match a single one of the checkpoint's 625 tensors to
that class, so it silently fills all 625 with fresh random numbers, and every
transcript and frame vector written since 2026-08-09 22:58 (6,564 chunk vectors
and 7,498 frame vectors, 100% of both indexes) is the output of a randomly
initialised 2B network, in eleven mutually orthogonal sub-spaces — one per model
load.

## 1. The symptom, reproduced three ways

All probes against the **running** worker (`http://127.0.0.1:8081`) and the live
DB read-only (`file:/home/dev/vidtheque-data/vidtheque.db?mode=ro`), 2026-08-10
~21:00, mid-batch.

**(a) The model card's own example does not reproduce.** The card publishes a
similarity matrix for four queries against one text document. Sent through
`POST /v1/embeddings` with `input_type=document` (which is what the card's bare
`model.encode(...)` does — the card's default prompt is applied by the
checkpoint's `config_sentence_transformers.json`):

| card query | worker | model card | Δ |
|---|---|---|---|
| "A woman playing with her dog on a beach at sunset." | **+0.4434** | +0.8160 | −0.373 |
| "Pet owner training dog outdoors near water." | +0.4210 | +0.5173 | −0.096 |
| "Woman surfing on waves during a sunny day." | +0.3840 | +0.3863 | −0.002 |
| "City skyline view from a high-rise building at night." | **+0.3854** | +0.1061 | +0.279 |

The matching document and an unrelated city skyline are 0.06 apart instead of
0.71. Pairwise among those four unrelated queries the worker gives 0.46–0.59:
that band is the random space's own floor, not topical similarity.

**(b) Stored vectors do not survive a worker restart.** `cos(stored vector,
re-embed of that chunk's own stored text)`, one chunk per video, sampled across
the whole `text_embed` timeline:

```
 text_embed done        video   chunk      cos
     08-09 22:58  RjfbvDXpFls    2832  -0.0166
     08-10 00:24  CgsWxRUY5Eo    1559  -0.0460
     08-10 02:44  C_GG5g38vLU    3279  +0.0163
     08-10 13:58  eBUyTS7SzV4    4803  -0.0018
     08-10 19:48  96G7FLab8xc    5895  +0.0200      <- 30 min old, still orthogonal
     08-10 20:40  WE_Gnowy3uw    6346  +0.9995      <- after the 20:19 model load
     08-10 20:58  ugUeZ8-b-u0    6482  +0.9993
```

The frame leg behaves identically (`POST /v1/embeddings/image` against the
stored JPEG): keyframe 1 → +0.0164, keyframe 5152 → +0.0476, keyframe 10480 →
+0.9998, keyframe 10785 → +0.9998.

The split is **not** "before/after the overnight re-embed". It is the model load
at **2026-08-10 20:19:38**. Anything embedded by an earlier worker *process* is
orthogonal to what this one produces, including videos indexed 30 minutes ago.
That is the tell: the mapping is re-drawn at random on every load.

**(c) Batch composition is not the cause** (ruling out the padding hypothesis —
last-token pooling with `padding_side: "left"`). Same five texts embedded solo
and in one batch: `cos(solo_i, batched_i)` = 0.9995 / 0.9995 / 0.9995 / 0.9994 /
0.9994, and putting a 400×-longer sibling in the batch changes nothing
(+0.9994). The vectors are stable and deterministic within one process. They are
just not the model's.

## 2. Root cause

`worker.log` has said so 12 times, once per embedder load, since the very first
one:

```
3909:Some weights of Qwen3VLModel were not initialized from the model checkpoint
     at Qwen/Qwen3-VL-Embedding-2B and are newly initialized:
     ['language_model.embed_tokens.weight', … 625 keys …]
3910:You should probably TRAIN this model on a down-stream task …
```

625 keys, split `{'language_model': 310, 'visual': 315}` — the entire language
tower **and** the entire vision tower. Not a head, not a projection: everything.

The mechanism, end to end:

1. The card is saved from `Qwen3VLForConditionalGeneration`, so every tensor in
   `model.safetensors` is named `model.language_model.…` / `model.visual.…`
   (verified from the safetensors header: 625 keys, all under `model.`).
2. Its `sentence_bert_config.json` says `"transformer_task": "feature-extraction"`,
   so sentence-transformers loads it with `AutoModel.from_pretrained`, which for
   `model_type: qwen3_vl` resolves to **`Qwen3VLModel`** — whose parameters are
   named `language_model.…` / `visual.…`, without the prefix.
3. transformers has a branch for exactly this ("loading a base model from a task
   model's state dict"), and it is gated on the class's `base_model_prefix`
   (`transformers/modeling_utils.py:5320-5324`):

   ```python
   prefix = model.base_model_prefix
   has_prefix_module = any(s.startswith(prefix) for s in original_checkpoint_keys) if len(prefix) > 0 else False
   expects_prefix_module = hasattr(model, prefix) if len(prefix) > 0 else False
   loading_base_model_from_task_state_dict = has_prefix_module and not expects_prefix_module
   ```

4. `Qwen3VLModel.base_model_prefix = ""` (transformers 4.57.6,
   `models/qwen3_vl/modeling_qwen3_vl.py:888`; `Qwen3VLPreTrainedModel` sets
   `"model"` at `:549`, and `Qwen3VLModel` overrides it to empty). `len(prefix) > 0`
   is false, both flags are forced false, the branch is dead — and nothing
   renames anything.
5. Zero keys match. transformers **warns** and returns a model whose every
   tensor came out of `_init_weights`. `from_pretrained` does not raise, and
   sentence-transformers does not check.

On the real checkpoint, with no weights loaded at all (meta device, so this
costs nothing to re-run):

```
AutoModel resolved to    : Qwen3VLModel
base_model_prefix        : ''
checkpoint tensors       : 625
model parameters         : 625

A. as transformers sees them today (no mapping)
   matched: 0    missing: 625
B. with key_mapping={'^model\.': ''}
   matched: 625  missing: 0   unexpected: 0
```

**Why the output still looks fine.** A randomly initialised transformer is a
random projection: deterministic for a given set of weights, unit-norm after the
`Normalize` module, exactly 2048-d, and weakly self-consistent (shared tokens →
similar vectors), which is why unrelated documents land in a tight 0.38–0.59
band instead of at zero. Every anti-drift check the stack has — the response's
`model` and `dimensions`, `config['text_embed.dim']` vs the `vec0` DDL,
`config['text_embed.model']` vs what `/status` reports, `text_embed.normalized`
— passes, because all of them describe the *shape* of the vector and none of
them describe whether the weights are real.

**Everything else was ruled out**, with receipts, before this was found: the
instruction/prefix asymmetry (both legs behave as documented, and `/status`
echoes them correctly), the dtype (`bf16` is what the card ships,
`config.json: dtype: bfloat16`), `EMBED_DIM=0` / MRL truncation (`dim=native`
in every load line, 2048 on every response, `norm≈1.0000` on every stored
vector), serialization between worker and sqlite-vec (little-endian float32
round-trips exactly — the recent videos reproduce at +0.9995), a backend
fallback (`/status` and all 12 load lines say `qwen3-vl-embedding` /
`Qwen/Qwen3-VL-Embedding-2B`), and a dependency change mid-run (the venv has not
been written since 2026-08-08 22:36).

## 3. Blast radius

`text_embed` and `frame_embed` both have `model_key = 'Qwen/Qwen3-VL-Embedding-2B'`
and `state = 'done'` for the whole corpus, and the earliest `finished_at` on
either stage is **2026-08-09 22:58** — after the first (already broken) load at
22:57:55. So the affected set is *everything*: there is no surviving good
vector to compare against, and no partial repair.

At 2026-08-10 21:05, mid-batch: **6,564 rows in `vec_chunks`** (177 videos) and
**7,498 rows in `vec_frames`** (176 videos). By the load that produced them:

| embedder load | videos | chunk vectors | frame vectors |
|---|---|---|---|
| 08-09 22:58:31 | 78 | 2,827 | 3,122 |
| 08-10 01:54:44 | 19 | 739 | 679 |
| 08-10 03:19:56 | 7 | 253 | 428 |
| 08-10 05:36:11 | 18 | 598 | 556 |
| 08-10 07:52:48 | 1 | 33 | 24 |
| 08-10 13:19:23 | 9 | 371 | 514 |
| 08-10 15:45:29 | 15 | 531 | 668 |
| 08-10 16:57:43 | 2 | 67 | 58 |
| 08-10 18:40:02 | 2 | 64 | 83 |
| 08-10 18:59:08 | 15 | 669 | 854 |
| 08-10 20:20:12 | 11 | 412 | 512 |

Each row is a different random draw, so the index is not one bad space but
**eleven mutually orthogonal ones**. That is the mechanism behind the hub video
in §3.2 of the floor doc: a query embedded by the *current* process can only
have real neighbours among the vectors that process wrote, so the newest video
wins every query, relevant or absurd. It also means the corpus grew a *new* hub
after each restart, and the "hub" the 08-09 eval saw (`OV56RddyFuU`) and the one
the 08-10 calibration saw (`CS5Cmz5FssI`) were never about those videos at all.

The pre-Qwen3-VL era (SigLIP 2 + Qwen3-Embedding-0.6B, up to 2026-08-09 22:49)
loaded clean — no such warning appears against either of those checkpoints in
`worker.log`. Nothing from that era survives; migration 0004 replaced it.

## 4. The fix, and how it was verified without touching the box

`CHECKPOINT_KEY_MAPPING = {r"^model\.": ""}` passed through
`SentenceTransformer(model_kwargs=…)`, which sentence-transformers forwards
verbatim to `AutoModel.from_pretrained`
(`sentence_transformers/base/modules/transformer.py:1836`), where transformers
applies it *before* the dead prefix branch
(`modeling_utils.py:5224-5231`).

Verified two ways, both CPU-only, neither of which loads the 2B card or
disturbs the running stack:

* **Miniature reproduction.** A hand-built 2-layer/32-hidden `Qwen3VL`, saved as
  `Qwen3VLForConditionalGeneration`, reloaded with `AutoModel`: `missing_keys:
  63, unexpected_keys: 64`, `embed_tokens identical to checkpoint: False`. With
  the key mapping: `missing_keys: 0, unexpected_keys: 1` (`lm_head.weight`,
  which the base model has no slot for, and which the real card does not ship —
  `tie_word_embeddings: true`), `embed_tokens identical to checkpoint: True`.
* **The real checkpoint's key names** against the parameter names `AutoModel`
  builds on the meta device: 0/625 today, **625/625** with the mapping, 0
  unexpected. §2's block above.

**And the load is now checked rather than assumed** — this is the more important
half. `watch_for_uninitialised_weights()` (`worker/.../backends/base.py`)
watches the `transformers` logger while the checkpoint loads, and
`Qwen3VLEmbedBackend._load` uses it as a three-step contract:

1. load with the key mapping (what transformers 4.57.6 needs);
2. if tensors are *still* newly initialised, the mapping is wrong for the
   installed transformers — log it and retry the plain load, so a future release
   that fixes `base_model_prefix` does not need a code change to be correct;
3. if that is random too, raise `BackendUnavailable` (503) quoting what
   transformers said.

A 503 costs the batch a retry. A random embedder costs the corpus, and did.
The guard also forces the logger to `WARNING` for the duration, so
`TRANSFORMERS_VERBOSITY=error` cannot switch it off by accident.

**Not verified here, and it is the one thing left:** the fix has not run on the
GPU, because the stack is mid-batch and restarting it was out of scope. The
check at restart is ~30 seconds:

```bash
grep -c "newly initialized" /home/dev/vidtheque-data/run/worker.log   # must not grow
```

and the card example must come back ≈ `0.816 / 0.517 / 0.386 / 0.106` instead of
§1(a)'s flat band. If it does not, the worker will now refuse to serve rather
than write more noise.

## 5. Repairing the data

**This is not optional and it is not automatic.** `_should_run`
(`mcp/.../pipeline/runner.py:1248-1304`) re-runs an embed stage only when
`video_stages.model_key != config['*_embed.model']`, and after the fix both
still read `Qwen/Qwen3-VL-Embedding-2B`. So a corrected worker plus a plain
re-index leaves 14,062 noise vectors in place, looking healthy, forever. The
stages have to be invalidated by hand.

The path, in order (each step verified against the code, none of it run here):

1. **Restart the stack** so the worker picks up the fix. (The live stack runs
   from this worktree — `/proc` says both processes are
   `.claude/worktrees/peppy-wibbling-moler/.venv/bin/python3` — so the fix is
   live at the next restart, and so is `_resolve_locally`.)
2. **Confirm the load is clean** — §4's two checks. Do not skip this; step 3 is
   only worth doing once.
3. **Invalidate both embed stages**, with the stack stopped:

   ```sql
   UPDATE video_stages SET state = 'pending', finished_at = NULL, error = NULL
    WHERE stage IN ('text_embed', 'frame_embed');
   ```

   This is exactly what migration 0004 did to drive last night's re-embed, and
   it is what makes `index-video` treat the videos as resumable rather than
   `already_indexed` (`tools/indexing.py:269-275`). Nothing else needs deleting:
   both writers replace rather than append — `write_chunk_vectors` and
   `write_frame_vectors` go through `_replace_vectors` (delete + insert), and
   `pending_chunks` / `all_live_keyframes` return *every* chunk and every live
   keyframe of the video, not just unvectorised ones.
4. **Re-submit the corpus with no `force_reindex`** — waves of ≤10 through
   `/home/dev/vidtheque-data/run/batch_driver.py` (which already posts
   `{"urls": […], "expand": "none"}` and polls `job-status`), or directly:

   ```
   uv run --no-sync scripts/mcp_call.py call index-video \
     '{"urls": ["https://youtu.be/<id>", …], "expand": "none"}'
   ```

   `force_reindex=true` would be **wrong**: it invalidates every intended stage
   (`runner.py:1218-1245`), re-downloading and re-transcribing 177 videos.
5. **Re-run the floor calibration.** `research/vec-floor-calibration-2026-08-10.md`
   §2's table was measured in the random space; the shipped margins
   (`VIDTHEQUE_VEC_MAX_MARGIN=0.20`, `VIDTHEQUE_FRAME_MAX_MARGIN=0.10`) are
   calibrated on corpus geometry rather than mapping quality and so are not
   *wrong*, but they have never been measured against a real one.

**Cost.** Grounded in the stage timings already in `video_stages` (which are
honest about the work even though the output was noise — the forward passes were
real):

| | videos | in-stage total | mean | p90 |
|---|---|---|---|---|
| `text_embed` | 175 | **7.3 min** | 2.5 s | 1 s |
| `frame_embed` | 174 | **20.8 min** | 7.2 s | 14 s |

So **~28 minutes of GPU work for the whole corpus**, plus ~34 s per embedder
load (the slot is non-resident and unloads after 300 s idle). Wall clock is
dominated by per-item job overhead, not by the card and not by the network:
nothing is re-downloaded and nothing is re-transcribed, because `fetch`, `stt`,
`chunk`, `keyframe` and `ocr` all keep matching `model_key`s, and
`_resolve_locally` (`runner.py:291-352`) skips the 90 s politeness sleep and the
YouTube round trip for an item whose only pending stages are the embeddings —
the fix that turned 78 videos from 2.5 h into ~20 min on the 08-10 re-embed.
Extrapolating that measured ~15 s/video: **45–75 minutes wall clock** for 177
videos driven in waves of ten, most of it the driver's 60 s poll granularity.

Last night's 250-minute overnight span is not the right anchor — that was 96
videos being *fetched and transcribed*, and only 12.5 minutes of it was
embedding.
