# Landscape survey: YouTube MCP servers, video RAG, and adjacent infra

Data gathered 2026-08-08 by an Opus research subagent. Star counts and `last push` dates come from the GitHub GraphQL API (accurate as of that date), not from README claims.

---

## 1. Existing YouTube / video MCP servers

The `youtube-mcp` namespace is **extremely** crowded — 30+ repos on GitHub, 14+ npm packages. Almost all are thin transcript/metadata wrappers. Only three do anything visual.

### Tier A — the well-known transcript/metadata servers

| Project | Stars | Last push | License | What it exposes |
|---|---|---|---|---|
| [ZubeidHendricks/youtube-mcp-server](https://github.com/ZubeidHendricks/youtube-mcp-server) | 561 | 2026-07-16 | MIT | YouTube **Data API v3** wrapper (needs a Google API key): video mgmt, Shorts, channel analytics. Not yt-dlp based. |
| [anaisbetts/mcp-youtube](https://github.com/anaisbetts/mcp-youtube) | 538 | 2026-06-19 | MIT | The original. Single tool: `download_youtube_url` → subtitles via yt-dlp. No API key. Minimal by design. |
| [jkawamoto/mcp-youtube-transcript](https://github.com/jkawamoto/mcp-youtube-transcript) | 463 | **2026-08-08** | MIT | `get_transcript`, `get_timed_transcript`, `get_video_info`, `get_available_languages`. Uses `youtube-transcript-api`, not yt-dlp. **Most actively maintained of the pure-transcript servers.** |
| [kevinwatt/yt-dlp-mcp](https://github.com/kevinwatt/yt-dlp-mcp) | 266 | 2026-05-20 | MIT | The most complete *yt-dlp wrapper*: 10 tools — `ytdlp_search_videos`, `ytdlp_download_video/audio` (with trimming), `ytdlp_download_transcript`, `ytdlp_list_subtitle_languages`, `ytdlp_get_video_metadata(_summary)`, `ytdlp_get_video_comments(_summary)`. TypeScript, Zod validation, MCP tool annotations (readOnly/idempotent hints), automatic output truncation. |
| [mourad-ghafiri/youtube-mcp-server](https://github.com/mourad-ghafiri/youtube-mcp-server) | 56 | 2026-01-03 | MIT | Transcription + metadata. |
| [kirbah/mcp-youtube](https://github.com/kirbah/mcp-youtube) (npm `@kirbah/mcp-youtube`) | 27 | 2026-08-05 | MIT | Explicitly optimises for *token-efficient structured output* from the Data API. |
| [coyaSONG/youtube-mcp-server](https://github.com/coyaSONG/youtube-mcp-server) | 15 | 2026-08-05 | MIT | "Citation-ready" angle: transcript search + exact timestamp deep-links + cross-video evidence. Closest in *spirit* to a research-oriented search tool. |

**Worth stealing from `yt-dlp-mcp`:** the tool-annotation hints and the hard character-limit truncation on every tool result. Both are exactly the sort of thing that bites you once an agent starts calling tools in a loop.

### Tier B — the ones that actually look at pixels (the real competition)

**[jordanrendric/claude-video-vision](https://github.com/jordanrendric/claude-video-vision)** — 1,154 stars, pushed 2026-08-07, MIT. The most popular thing in this space and the closest analogue to the `get_frames` design.
- Tools: `video_watch`, `video_analyze`, `video_detail`, `video_info`, `video_configure`, `video_setup`.
- ffmpeg frame extraction → **base64 images delivered straight into Claude's multimodal input**. Explicitly framed as "a perception layer, not an interpretation layer" — the client LLM does the reasoning. Same thesis as our `get_frames`.
- Audio: three pluggable backends (Gemini API / local whisper.cpp / OpenAI). YouTube via yt-dlp with subtitle-priority ordering (manual → auto → STT fallback).
- **Critically: no index, no search, no persistence.** Session cache with a 7-day TTL, then gone. Every question re-watches the video.
- Adaptive FPS driven by the user's query; default 512px frames.

**[guimatheus92/mcp-video-analyzer](https://github.com/guimatheus92/mcp-video-analyzer)** — 42 stars, pushed 2026-08-04, MIT. Feature-wise the nearest miss to our ingest pipeline.
- 8 tools including `get_frames` (scene detection *or* dense 1fps), `get_frame_at`, `get_frame_burst`, `analyze_moment`, `analyze_video` (transcript + keyframes + OCR + timeline).
- Does **OCR** (Tesseract.js with grayscale/upscale/contrast preprocessing) and perceptual-hash frame dedup.
- Whisper via a fallback chain (HF transformers → CLI → OpenAI API). Node.js, no Docker, no GPU story.
- **No embeddings, no vector index, no search across videos.** It is per-video analysis, not a corpus.

**[0xchamin/mcptube](https://github.com/0xchamin/mcptube)** — 146 stars, pushed 2026-04-13, no license (!). Python 3.12.
- 25+ tools: `add_video`, `wiki_list/show/search/ask`, `get_frame`, `classify_video`, `synthesize`, `discover_videos`.
- Ingest: transcript → **ffmpeg scene-change filter** for frames → vision LLM (GPT-4o/Claude/Gemini, BYOK) writes descriptions → knowledge extraction into a compounding "wiki" (Karpathy LLM-wiki pattern, CRDT-like merge semantics across videos).
- Storage: JSON wiki files + **SQLite FTS5**. **Keyword search only — no embeddings, no OCR.**
- Explicit "passthrough pattern": most tools return structured data for client-side reasoning rather than doing server-side LLM synthesis. Same philosophy as ours.

**[woosal1337/media-mcp](https://github.com/woosal1337/media-mcp)** — 4 stars, 2026-06-11, MIT. Tiny, but one design decision matters: for frames it **returns absolute file paths, not base64**, and lets the client read them with its own vision. Local whisper-cli with per-token confidence. Docker image published. See the pitfall in §4 for why the path-vs-base64 choice is load-bearing.

**Also-rans:** [minbang930/Youtube-Vision-MCP](https://github.com/minbang930/Youtube-Vision-MCP) (7★, dead since 2025-04) just proxies YouTube URLs to Gemini's native video understanding. [tan-yong-sheng/ai-vision-mcp](https://github.com/tan-yong-sheng/ai-vision-mcp) (72★, 2026-04). [OAMaestro/video-vision-mcp](https://github.com/OAMaestro/video-vision-mcp) (1★) — the repo behind the marketing site `videovisionmcp.com` (site returns HTTP 402, likely a dead/paywalled hosted product). [burningion/video-editing-mcp](https://github.com/burningion/video-editing-mcp) (284★, 2025-10) — multimodal audio+visual `search-videos`, but it's a client for the hosted **Video Jungle** service, not self-hostable. [video-creator/ffmpeg-mcp](https://github.com/video-creator/ffmpeg-mcp) (141★, 2026-05) — ffmpeg CLI as MCP; useful reference for clip/concat tool shapes.

---

## 2. Video RAG / video search (open source, non-MCP)

| Project | Stars | Last push | Notes |
|---|---|---|---|
| [HKUDS/VideoRAG](https://github.com/HKUDS/VideoRAG) | **3,261** | 2026-03-18 | KDD'26. Dual-channel: multimodal **knowledge graph** (LightRAG/nano-graphrag lineage) + hierarchical context encoding. **ImageBind** for visual embeddings, ASR for audio. Claims hundreds of hours indexed on a single RTX 3090 (24 GB). Research code, `NOASSERTION` license, no MCP interface, no OCR channel. **The single most relevant prior art for the index design.** |
| [Leon1207/Video-RAG-master](https://github.com/Leon1207/Video-RAG-master) | 450 | 2026-06-26 | NeurIPS 2025. **Our exact modality stack**: OCR + ASR + object detection as "visually-aligned auxiliary texts", retrieved via RAG and injected into any LVLM. Training-free, all open-source tools, no commercial APIs. Their ablation is directly useful: **ASR helps broadly, OCR helps text-centric questions, DET helps spatial questions.** No license file — check before borrowing code. |
| [opea-project/GenAIExamples › VideoQnA](https://github.com/opea-project/GenAIExamples/tree/main/VideoQnA) | 735 (monorepo) | 2026-08-06 | Apache-2.0, Intel. Docker-compose microservices: embedding / retrieval / reranking / large-vision-model behind a gateway. CLIP ViT-B/32 + VDMS vector DB + Video-LLaMA. **Mean-aggregates clip embeddings — indexes whole videos, not frames or transcripts separately.** Good reference for the *service decomposition*, weak on retrieval granularity. |
| [starsuzi/VideoRAG](https://github.com/starsuzi/VideoRAG) | 84 | 2025-03-17 | Different paper, same name. RAG *over a video corpus* with a frame-selection mechanism (model-agnostic, CLIP-compatible). Effectively abandoned. |
| [johanmodin/clifs](https://github.com/johanmodin/clifs) | 485 | 2022-03-15 | "Contrastive Language-Image Forensic Search" — the canonical free-text-search-through-video-frames-with-CLIP demo. Dead, but the cleanest minimal reference. |
| [di37/video-rag-bot](https://github.com/di37/video-rag-bot) | 18 | 2025-07-21 | CLIP 512-d frame vectors → Qdrant, ffmpeg extraction. Small but a complete working shape. |
| [danielgural/semantic_video_search](https://github.com/danielgural/semantic_video_search) | 29 | 2025-05-27 | FiftyOne plugin; embeds visual/audio/OCR/conversation modalities into a clip-level dataset — but via the **Twelve Labs API** (closed). GPL-3.0. |
| [memvid/memvid](https://github.com/memvid/memvid) | 16,189 | 2026-07-14 | Apache-2.0. **Name collision trap, not a competitor** — it encodes *text chunks as QR codes inside MP4 frames*. It is not video understanding at all. |

Commercial/managed for context (not self-hostable): **Mixpeek** ([mixpeek.com](https://mixpeek.com/), has an MCP server; publishes a genuinely useful [2026 video embedding benchmark](https://mixpeek.com/blog/video-embedding-benchmark-2026)), **VideoDB** ([videodb-python](https://github.com/video-db/videodb-python), 96★), **Twelve Labs** (Marengo/Pegasus).

**The dozens of `youtube-rag` LangChain+ChromaDB student projects** ([balmasi](https://github.com/balmasi/youtube-rag), [XynaxDev](https://github.com/XynaxDev/youtube-rag-system), [lokeshwarlakhi](https://github.com/lokeshwarlakhi/RAG-Chat-with-YouTube), [cloud-ray](https://github.com/cloud-ray/youtube-rag), etc.) are all transcript-only, Streamlit-shaped, and not worth tracking individually.

---

## 3. Adjacent infrastructure

### Screen-capture RAG — the architectural precedent
**[screenpipe/screenpipe](https://github.com/screenpipe/screenpipe)** — 20,825 stars, YC S26, Rust.
- Captures screenshots on OS events, pairs each with the accessibility tree, **falls back to OCR** when a11y data is unavailable. Audio via local Whisper or Deepgram.
- **SQLite + FTS5**, HTTP API on `localhost:3030` exposing `search`, `frames`, `audio`, `elements`, `health`.
- Ships an MCP server (`npx screenpipe-mcp`) with **`search_screen`, `search_audio`, `get_recent_context`, `get_frame`** — our `search_video` / `get_frames` tool surface, one domain over. The reference for *what tool shapes actually work in practice at 20k-star scale*.
- Also worth copying: their permission model (content-type gating to `ocr`/`audio`/`input`/`accessibility`, time-of-day restrictions enforced at three layers including server middleware).
- Design lesson: **event-driven capture beats fixed-interval capture** — same argument as shot detection over 1fps sampling.

### STT serving
- **[speaches-ai/speaches](https://github.com/speaches-ai/speaches)** — 3,576★, MIT. OpenAI-compatible server over faster-whisper + Kokoro/Piper TTS. **Dynamic model load-on-request and unload-after-idle**, Docker Compose for CPU and CUDA, SSE streaming. Most of a GPU inference worker already built. (Formerly `fedirz/faster-whisper-server`.)
- **[hwdsl2/docker-whisper](https://github.com/hwdsl2/docker-whisper)** — 89★, active. faster-whisper + diarization, OpenAI-compatible, multi-arch, offline mode. Simpler than speaches if you only need STT.
- **[SYSTRAN/faster-whisper](https://github.com/SYSTRAN/faster-whisper)** — 24,812★ but **last push 2025-11-19**; effectively in maintenance. Still the right library.
- **[m-bain/whisperX](https://github.com/m-bain/whisperX)** — 23,480★, active, BSD-2. faster-whisper + wav2vec2 forced alignment for word-level timestamps + pyannote diarization. **The upgrade over raw faster-whisper for frame-accurate transcript↔timestamp alignment / deep-links.**
- **[ggml-org/whisper.cpp](https://github.com/ggml-org/whisper.cpp)** — 52,691★, active.

### Shot detection / keyframes
- **[Breakthrough/PySceneDetect](https://github.com/Breakthrough/PySceneDetect)** — 5,080★, active, BSD-3. CPU-only, `ContentDetector`/`ThresholdDetector`, YCbCr-Y histogram deltas. The default choice; good Python API.
- **[soCzech/TransNetV2](https://github.com/soCzech/TransNetV2)** — 1,014★, unmaintained (2023), MIT. SOTA accuracy, handles *gradual* transitions (dissolves/fades) that PySceneDetect misses. Weights still work.
- Common production pipeline per the literature: **PySceneDetect for cheap CPU segmentation, TransNetV2 only where gradual transitions matter.** Reference: [Scene Detection Policies and Keyframe Extraction Strategies for Large-Scale Video Analysis](https://arxiv.org/pdf/2506.00667).
- **[keplerlab/katna](https://github.com/keplerlab/katna)** — 398★, stale. K-means over frame histograms, then picks the sharpest frame per cluster via Laplacian variance. **The blur/sharpness filter is worth reimplementing even if you skip the library** — scene-cut frames are frequently mid-transition and blurry.
- `ffmpeg -vf "select='gt(scene,0.4)'"` is what mcptube and mcp-video-analyzer both use — zero dependencies, decent enough.

### OCR & visual embeddings
- OCR: [PaddleOCR](https://github.com/PaddlePaddle/PaddleOCR) (87,263★), [RapidOCR](https://github.com/RapidAI/RapidOCR) (7,415★, active — ONNX runtime, easiest to containerise), [docTR](https://github.com/mindee/doctr) (6,203★). Tesseract is the weakest option for video frames.
- Embeddings: [open_clip](https://github.com/mlfoundations/open_clip) (14,047★, active). **On CLIP-vs-SigLIP**: 2026 benchmarks put **SigLIP 2 SO400M as the strongest open image-text model**, and specifically the **NaFlex variant on OCR/document retrieval** because it preserves aspect ratio — exactly the on-screen-text case. Sources: [Spheron](https://www.spheron.network/blog/multimodal-embedding-models-gpu-cloud-siglip2-jinaclip-cohere/), [Mixpeek](https://mixpeek.com/curated-lists/best-multimodal-embedding-models).

### Model load/unload on a single GPU — already solved
**[mostlygeek/llama-swap](https://github.com/mostlygeek/llama-swap)** — 5,295★, active, MIT, Go, zero dependencies, one binary + one YAML. Transparent OpenAI/Anthropic-compatible proxy that loads the right upstream on demand and **unloads it on a per-model TTL to free VRAM**. Routes across llama.cpp, vLLM, tabbyAPI, stable-diffusion.cpp. This is "custom GPU inference worker with pluggable backends and on-demand load/unload", already built and battle-tested.

### Vector storage — a real caveat
**[asg017/sqlite-vec](https://github.com/asg017/sqlite-vec)** is 7,989★ but **still `0.1.8-alpha.1` (Mar 2026), last pushed 2026-05-18**. Brute-force KNN, no ANN index. Alternatives: **[sqliteai/sqlite-vector](https://github.com/sqliteai/sqlite-vector)** (1,077★, active, SIMD kernels + 2/3/4-bit TurboQuant scans, markets itself as production-grade) and **[Vec1](https://sqlite.org/vec1)**, an actual ANN extension from the SQLite project itself. For a single-owner corpus sqlite-vec's brute force is almost certainly fine — but it's alpha software with a slowing pulse; Vec1 is the escape hatch.

---

## 4. MCP servers returning images — and the pitfall to design around

**The spec is fine.** MCP defines `ImageContent` as `{"type":"image","data":"<base64>","mimeType":"image/png"}`, and a conformant client converts that into a native image block. **Client support is not fine.**

- [anthropics/claude-code#31208](https://github.com/anthropics/claude-code/issues/31208) — Claude Code passes MCP `ImageContent` through as **raw base64 text** rather than a native image block. Cost: **~15,000–25,000 tokens per image instead of ~1,600 — a 10–20× blowup** — and the model can't actually see it. Users hit `Error: result (62,162 characters) exceeds maximum allowed tokens`. **Closed as not planned.** Duplicates #14150, #9152, #4002 also closed unfixed. Affects Jupyter, Playwright, Figma MCP servers alike.
- Same class of bug elsewhere: [cline/cline#1865](https://github.com/cline/cline/issues/1865), [microsoft/agent-framework#2900](https://github.com/microsoft/agent-framework/issues/2900), [rmcp-openapi#79](https://gitlab.com/lx-industries/rmcp-openapi/-/issues/79). Broader writeup: ["MCP Has an Image Problem"](https://patent.dev/model-context-protocol-has-an-image-problem/).

**Implication for `get_frames`:** returning frames as base64 `ImageContent` — what `claude-video-vision` does — is the *correct* implementation and will be badly broken on some major clients. The two shipped mitigations in the wild:
1. **`media-mcp`'s approach**: return **absolute local file paths**, let the client's own Read/vision tool load the JPEG. Works perfectly in Claude Code, useless for a remote server behind Cloudflare Tunnel (no shared filesystem).
2. **Return a URL** to a frame served by the MCP server itself (an authenticated `/frames/<id>.jpg` behind the same OAuth). Nobody in the survey does this, and it's the only option that works for the remote-server topology.

Suggestion: make the return mode a per-tool parameter (`return: "image" | "url" | "path"`) with capability sniffing, rather than picking one.

Working reference implementations that do return images: **screenpipe MCP** (`get_frame`), **claude-video-vision** (base64), **Playwright MCP** (screenshots), **[mcptube](https://github.com/0xchamin/mcptube)** (`get_frame`).

---

## Synthesis

### (a) Already exists well enough — don't rebuild

| Component in the design | Use instead |
|---|---|
| GPU worker: OpenAI-compatible, pluggable backends, load/unload on demand | **[llama-swap](https://github.com/mostlygeek/llama-swap)** as the router, or **[speaches](https://github.com/speaches-ai/speaches)** if the workload is STT-dominated. (Note: we consciously chose to own the worker anyway — inference is the trademark — but these are the benchmarks to beat/learn from.) |
| Transcription | faster-whisper, or **whisperX** for word-level alignment / timestamp deep-links |
| Shot detection | PySceneDetect (+ TransNetV2 only for gradual transitions); add Katna's Laplacian-variance sharpness filter |
| OCR | RapidOCR or PaddleOCR — not Tesseract |
| Visual embeddings | open_clip with **SigLIP 2 NaFlex** (better than CLIP specifically on text-in-image) |
| yt-dlp tool surface | [kevinwatt/yt-dlp-mcp](https://github.com/kevinwatt/yt-dlp-mcp) is a complete, well-annotated reference for the download/metadata/comment tools |
| Multimodal index design | [HKUDS/VideoRAG](https://github.com/HKUDS/VideoRAG) for the graph/retrieval structure; [Leon1207/Video-RAG-master](https://github.com/Leon1207/Video-RAG-master) for the OCR+ASR+DET fusion (and its ablations, which tell you which channel earns its cost) |

### (b) The gap — nothing does this

Across everything surveyed, **no project combines all four of these**:

1. **Persistent, cross-video, multimodal index behind an MCP interface.** `claude-video-vision` (1.1k★) has frames-to-model but *zero persistence*. `mcptube` has persistence but FTS5 keyword only, *no embeddings, no OCR*. `mcp-video-analyzer` has OCR + keyframes but *no index at all*. HKUDS/VideoRAG has the real index but *is research code with no MCP interface, no yt-dlp ingest, and no server*.
2. **Visual embeddings (CLIP/SigLIP) in an MCP server.** Literally nobody. Every MCP server in this space is either transcript-text search or "ship the frame to a vision LLM and store the caption." Nobody embeds frames and does vector retrieval over pixels.
3. **Fully self-hosted, GPU-local, no third-party API.** The visual MCP servers all offload the hard part to GPT-4o/Claude/Gemini/Twelve Labs/Video Jungle. `media-mcp` is local-only but is 4 stars and does no indexing.
4. **Remote/multi-client deployment with OAuth.** Every one of these is a stdio server on the user's laptop. A self-hostable *remote* MCP server (OAuth, Cloudflare Tunnel, separate CPU and GPU services) is unoccupied territory — and it's precisely the topology that forces solving the frame-delivery problem properly (§4), which nobody has published a good answer to.

Secondary: **screenpipe proves the tool surface works** (`search_screen`/`search_audio`/`get_frame` at 20k stars) — but only for your own screen. Nobody has built the same thing for a YouTube corpus.

### (c) Naming collisions to avoid

**Hard collisions — do not use:** `VideoRAG` (3 projects, one 3.3k★), `Video-RAG` (NeurIPS 2025), `mcptube` (GitHub + PyPI), `youtube-mcp-server` (10+ repos + npm), `youtube-mcp` / `yt-mcp` / `video-vision-mcp` (npm + GitHub), `mcp-youtube` (two holders), `memvid` (16k★, unrelated meaning), `screenpipe` / `*pipe` constructions, `VideoQnA` / `VideoRAGQnA` (Intel OPEA).

**Also avoid:** `video-mcp`, `mcp-video-analyzer`, `media-mcp`, `video-rag-bot`, `mcp-rag-server`, `clifs`.

Recommendation: pick something that signals *index/corpus* rather than *youtube* or *rag* — the discriminating feature is the persistent multimodal index, and both `youtube-*` and `*-rag` namespaces are saturated to the point where a new entrant is invisible in search.
