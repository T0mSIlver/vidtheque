# vidtheque — MCP surface eval with independent terra consumers (2026-08-10)

Author: evaluation orchestrator, session `peppy-wibbling-moler`. Append-only
doc: add new sections, don't rewrite these findings.

**Method, in one sentence.** Six OpenAI **Codex CLI** agents on
**`gpt-5.6-terra`** were connected to the live MCP server as ordinary
tool-using consumers, given realistic user tasks and no knowledge of this
repo, and every place one of them stumbled was treated as a defect of the
surface until proved otherwise.

This is the sibling of `research/mcp-design-bench-2026-08-09.md` (four Sonnet
agents, Claude harness) with two deliberate changes: a **different vendor's
harness**, so client-side assumptions in our payload design get tested, and
**consumers who were never told they were being evaluated** — they were told to
answer a user, which is what makes the retry loops honest.

---

## 1. Methodology

### 1.1 Harness

Codex `0.144.1`, model `gpt-5.6-terra`, `model_reasoning_effort=medium`,
`--ignore-user-config` (so nothing but the vidtheque server was mounted),
`-s read-only`, and the working directory pointed at a scratch dir **outside
this repo** — a consumer that can read `docs/design/tool-surface.md` is not a
consumer. The server was wired natively, not through a shim:

```bash
codex exec -C "$RUN/ws" --ignore-user-config --skip-git-repo-check \
  -m gpt-5.6-terra \
  -c model_reasoning_effort='"medium"' \
  -c approval_policy='"never"' \
  -c mcp_servers.vidtheque.url='"http://127.0.0.1:8100/mcp"' \
  -s read-only --json -o "$RUN/final.md" \
  - < "$RUN/prompt.md" > "$RUN/events.jsonl"
```

Runner, prompts and full JSONL transcripts:
`…/scratchpad/terra-eval/{smoke,p1-research,p2-frames,p3-naive,p4-paginate,p5-absent,p6-stress}/`
(`prompt.md`, `events.jsonl`, `final.md` per run; `trace.py` renders the call
trace). Every terra line quoted below is verbatim from `events.jsonl`.

**One harness fact worth recording:** Codex renames kebab-case tools to
snake_case in its own tool list (`corpus_summary`, `get_segment_context`) while
every `next:` line we print says `corpus-summary`, `get-segment-context`. No
terra agent was confused by it — they mapped the names silently — but the
mismatch is real and will not always be free.

### 1.2 Corpus under test

153–154 videos · ~53 h · ~33.7k transcript cues · ~6.2k keyframes · one channel
(AI Engineer), published 2026-04-07 → 2026-08-09. **The corpus was growing
during the eval** (background index batches: `data_status: indexing`
throughout), which is why counts drift between quoted payloads. Where that
mattered I re-ran the call and say so.

### 1.3 Personas

| # | Run | Brief | Verdict |
|---|---|---|---|
| 1 | `p1-research` | "Where do speakers *disagree* about evals? Three named speakers, verified links, side by side." | **Succeeded.** Quotes verified verbatim. Two of five searches returned an off-topic rank 1. |
| 2 | `p2-frames` | Coding agent building `slides.md` with frame URLs + receipts. | **Not run** — see §5. |
| 3 | `p3-naive` | First contact, no guidance: "find the talk about prompt injection and MCP danger." | **Succeeded** in 12 calls. Lost its first two searches to `E_BUSY` and mis-recovered. |
| 4 | `p4-paginate` | "Complete inventory, no sampling. Exact counts. Say if the server stopped you early." | **Succeeded**, and paged to the true end — but shipped a semantic-fill artifact as a fact. |
| 5 | `p5-absent` | Four questions the corpus cannot answer (Karpathy, GPU MODE, FlashAttention-4, pre-2020). | **Succeeded — 4/4 correct refusals.** The strongest result in the eval. |
| 6 | `p6-stress` | Client-integration engineer probing clamps, junk ids, wrong axes, bad enums. | **Succeeded**, produced its own findings table; three of them match ours independently. |

### 1.4 Verification rule

No server-side claim below was written down on a terra transcript alone. Every
one was re-run by hand through `scripts/mcp_call.py` against the same live
server, and the reproduction command is printed with the finding. Where the
root cause is in code, the file:line is cited. Where I could **not** verify
something, §6 says so.

---

## 2. Per-persona summary

### 2.1 p1 — the research assistant (cross-video disagreement)

Nine MCP calls: `corpus-summary` → four `search` → three `get-segment-context`.
It produced a three-speaker disagreement table with links, and **the quotes are
real**. I pulled both windows at `max_text_chars=0` and confirmed:

- Brumley, `ZFxh7sqbUZo` `[16:23]`: *"It also means that there's no LLM as a
  judge because let's face it, you can't trust the LLM that you're teaching to
  be a judge."* — cited as `?t=982` (16:22). Correct.
- Heiner, `-npY6XjM8CQ` `[16:01]`: *"And LLM as a judge doesn't really work
  either because LLMs don't have good taste in writing."* — cited as `?t=960`
  (16:00). Correct.

That is the product working. Two things went wrong on the way:

**(a) Rank 1 was off-topic on two of five searches.** Verbatim:

```
### search {"q": "human evaluation judge", "content_type": "transcript", "limit": 10, ...}
Results: 10/~200+ (use offset=10 for more)
Legs: transcript 400 · ocr 0 · frame 0 (fused, RRF k=60)
[transcript] Everything we knew about software has changed — Theo Browne … (xUnRQ9vLXxo)
  4:38–5:21 · https://youtu.be/xUnRQ9vLXxo?t=276
  But now they look like this. And most people look at that and they're like, oh,
  that's an obvious downgrade. … This is the downfall of Apple …
```

and `{"q": "human labels evals"}` returned, at rank 1, a speaker explaining that
his son says "I'm fine". See **§4.1**.

**(b) Every quote from one video carries the same link.** `get-segment-context`
printed 24 timestamped transcript lines and exactly one `youtu.be` link, so the
agent cited `[16:23]` and `[15:56]` *both* as `?t=982`. The second is 27 s off.
See **§4.4**.

### 2.2 p3 — naive first contact

Read `vidtheque://guide` and `vidtheque://context` unprompted (good — the
resource list in `corpus-summary`'s description is doing its job; the 08-09
bench had agents guessing `vidtheque://help`). Then lost its first two searches:

```
### search {"q": "prompt injection MCP", ...}          → error: E_BUSY
    next: retry in 1s, or narrow the query so it costs less.
--- agent_message ---
The library is currently indexing and its search capacity is saturated; I'll
retry only a narrow read-only search rather than queue or add anything.
### search {"q": "prompt injection", ...}              → error: E_BUSY
```

It read "narrow the query" as the actionable half and **narrowed instead of
waiting**, twice, and then abandoned search for the corpus resource. See §4.9
— including the honest caveat about who caused the `E_BUSY`.

It recovered and answered correctly. Its factual claim — *"in Snyk's sample,
one in 12 developers had an MCP server with a high- or critical-severity
finding"* — checks out verbatim against cue 26183. **PASS for the drill-down
path.** One blemish: it wrote `?t=398` for a chapter the payload printed as
`?t=400` (§4.3).

### 2.3 p4 — the exhaustive paginator

Four `list-videos` pages and four `search` pages, ending correctly:

```
### list-videos {"limit": 50, "offset": 0}   → Videos: 50/~80+  (use offset=50 for more)
### list-videos {"limit": 50, "offset": 50}  → Videos: 50/~130+ (use offset=100 for more)
### list-videos {"limit": 50, "offset": 100} → Videos: 50/151   (use offset=150 for more)
### list-videos {"limit": 50, "offset": 150} → Videos: 1/151    (no more results)
### search {"q":"eval", …, "offset": 150}    → Results: 0/143   (past the last page)
```

`(past the last page)` and `(no more results)` both fired correctly — the §3.4
work holds up under a consumer that actually pages to the end. **PASS.**

Two failures. It was told "tell me the exact count", and got **two different
totals from two tools in the same session** (§4.7). And it shipped, as a fact in
a deliverable, *"The server returned **143 distinct talks**"* mentioning
evaluation — out of 151. That number is semantic fill, not lexical recall, and
nothing in the payload let it tell the difference (§4.1, §4.2).

### 2.4 p5 — the four unanswerable questions

**The best result in the eval.** Four questions the corpus cannot answer, four
correct refusals, zero fabrication, and it named its evidence each time:

```
1. Karpathy on tokenization: your library does not contain this. …
3. FlashAttention 4 demo benchmark: your library does not contain this.
   Search for `FlashAttention-4` returned 0/0, with the tool reporting that none
   of those words occurs in the corpus.
4. Agents before 2020: … The corpus itself reports its entire publication span
   as 2026-04-07 to 2026-08-09, so no pre-2020 video exists to quote.
```

The mechanism it leaned on is exactly the one the contract built for it:

```
Results: 0/0
Query: "FlashAttention-4" · content_type=all
note: no word of this query occurs anywhere in the corpus, so the semantic
(nearest-neighbour) legs were not queried — they would have returned their k
nearest vectors regardless.
The transcript and ocr legs were queried and nothing matched; the frame leg did
not run, for the reason in the note above.
```

`E_FEATURE_DISABLED` on `speaker="Andrej Karpathy"` also landed cleanly.

The cost: it had to *reason around* four payloads that each claimed hundreds of
results. `{"q":"CUDA kernel occupancy"}` answered `Results: 10/~330+` with a
browser screenshot at rank 1. A weaker consumer reads "330+ results" and
summarises them.

### 2.5 p6 — the stress tester

27 probes. Its own conclusions, unprompted, overlap ours on three counts:
unknown parameter names are silently ignored; a year in the in-video axis
"yields plausible but incorrect results"; and the malformed-id error hands back
"an unsafe-looking index suggestion". Its full table is in
`p6-stress/final.md`. It scored **"automatic recovery: yes"** on every typed
error — see the PASS list in §4.13.

---

## 3. Classification key

- **(a) MCP bug** — wrong or broken behaviour.
- **(b) contract–payload mismatch** — the shipped payload disagrees with
  `docs/design/tool-surface.md` or with `vidtheque://guide`.
- **(c) affordance trap** — the payload or description invites the misuse.
- **(d) token-discipline violation** — blowups, missing or misleading hints.
- **(e) PASS** — the agent erred and the surface caught it.

---

## 4. Error-mode catalog, by severity

### 4.1 HIGH · (a)(d) · The semantic legs have no effective relevance floor

**What a consumer sees.** Any query at all returns 120–145 of the ~154 videos,
and topically unrelated talks take rank 1.

Reproduction — a proper noun that is spoken in three talks:

```
$ uv run --no-sync scripts/mcp_call.py call search \
    '{"q":"turbopuffer","content_type":"transcript","limit":5,"max_per_video":1,"max_text_chars":120}'

Results: 5/121 (use offset=5 for more)
Legs: transcript 334 · ocr 0 · frame 0 (fused, RRF k=60)
[transcript] Building Turbopuffer: … (jQDXzEVHMSE)   53:18–53:59 · match at 53:36
[transcript] Self-Training Agents: Hermes Agent, HF Traces, Skills, MCP &
             Finetuning — Merve Noyan, Hugging Face (OV56RddyFuU)  12:03–13:59
[transcript] RAG is dead, right?? — Kuba Rogut, Turbopuffer (UM6sFg_jdlE)  0:24–0:33
```

**121 talks "match" `turbopuffer`.** And one video wins on everything:

```
$ … call search '{"q":"CUDA kernel occupancy","limit":3,"max_text_chars":200}'
Results: 3/~330+ · Legs: transcript 400 · ocr 0 · frame 400
  [transcript] Self-Training Agents … (OV56RddyFuU) 5:48–6:03  score 0.0164
  [transcript] Self-Training Agents … (OV56RddyFuU) 6:04–7:59  score 0.0164
  [frame]      Self-Training Agents … (OV56RddyFuU) 16:22      score 0.0164
3 of 3 results came from OV56RddyFuU (max_per_video=3 bound).

$ … call search '{"q":"voice agent interruption latency","content_type":"transcript",
                  "limit":4,"max_per_video":1,"max_text_chars":100}'
Results: 4/128 · rank 1: Self-Training Agents … (OV56RddyFuU) 18:01–18:52
```

The same Hugging Face talk is rank 1 for *CUDA kernel occupancy*, rank 1 for
*voice agent interruption latency*, rank 2 for *turbopuffer*, rank 5 for
*context engineering* — a semantic hub whose chunks sit near the corpus
centroid. It is `index_state: ready`, `coverage: tof`; this is not a
half-indexed artifact. Every score is `0.0164` = `1/(60+1)`, i.e. the whole page
is a bag of rank-1 RRF ties (§3.10's tie-break is doing the actual ordering).

**Mechanism, from the code.** `_k_for(400) = min(1000, max(50, 800)) = 800`
chunks per vector leg (`mcp/src/vidtheque_mcp/tools/search.py:501-508`) out of a
corpus of roughly four thousand 45 s chunks — a fifth of the corpus is pulled as
"nearest neighbours" before any filter. The only filter is
`WHERE distance <= :vec_max_distance` (`db/queries.py:579`), and
`vec_max_distance` / `frame_max_distance` default to **1.0**
(`config.py:110-111`, `config.py:223-224`, `deploy/.env.example:401-402`) — a
cosine distance of 1.0 admits every chunk with non-negative similarity, which
over a single-domain corpus of conference talks is nearly all of them. The
`.env.example` comment two lines above already carries calibrated figures for
the *previous* embedder pair (`0.72` text, `0.96` frame); the shipped pair got
`1.0`, which is not a floor.

This is the one finding that attacks a stated design principle head-on: §1.3
*"Relevance first … `order` is explicit, defaults to `relevance`."*

**Enhancement.** (1) Calibrate `VIDTHEQUE_VEC_MAX_DISTANCE` /
`VIDTHEQUE_FRAME_MAX_DISTANCE` for the shipped embedder the way the old pair was
calibrated, and make the calibration a bench artifact rather than a default of
`1.0`. (2) Make `k` a function of corpus size, not of the pool
(`min(k, corpus_chunks // 8)`). (3) Cheapest partial mitigation, independent of
the model: when the FTS sub-leg returned hits, require a vector hit to be within
a margin of the best FTS hit's distance before it can outrank one, and say so in
a `note:` when the cut binds.

### 4.2 HIGH · (b)(c) · `Legs:` merges the lexical and semantic sub-legs, so the guide's own diagnostic is dead

`vidtheque://guide` ships this rule:

> Read the `Legs:` counts. `transcript 0` next to on-screen hits usually means
> the phrasing differs, not that the topic is unspoken …

With §4.1 live, `transcript` never reads 0 — across every search in this eval it
read `400`, `398`, `334`, `321`, `9`, `6`. The number a caller is instructed to
read is the *fused* transcript leg (FTS ∪ vector), so it cannot distinguish "nine
talks say this" from "the KNN returned its k".

**Consequence, observed.** p4 was asked for a complete inventory and shipped:

> "**List B** … The server returned **143 distinct talks**, with one timestamped
> hit per talk. … I am confident this reaches the end of the server's current
> searchable transcript results."

143 of 151 talks, from `search q="eval" max_per_video=1`. It is not wrong about
the pagination — it is wrong about what the rows mean, and the payload gave it
no way to know.

**Enhancement.** Print the sub-leg split:
`Legs: transcript 400 (fts 9 · vec 391) · ocr 0 · frame 400`. It costs nine
characters and restores the guide's rule. Carry `leg_counts` with the same split
in `structuredContent`.

### 4.3 HIGH · (b) · `video-summary` deep links omit `DEEPLINK_LEAD`

§3.6 is unconditional: *"**Every** timestamped item in every payload carries
`https://youtu.be/<id>?t=<int>` … `t` is the clamped floor of the item's start
minus `DEEPLINK_LEAD`."* `vidtheque://guide` repeats it as a caller-facing
clause: *"The link is deliberately **2 s early** … The two-second disagreement
with the payload's own numbers is the lead, not a bug."*

`video-summary` does not apply it. Reproduction:

```
$ … call video-summary '{"video_id":"BEKc4P87XKo","max_key_texts":3, …}'
Key texts (3):
      4:37  "And here Karpathy says, you know, context engineering is a delicate
             art and science of …"                                      ?t=277
```

The cue starts at `277.95`; with the lead the link is `?t=275`. Printed: `?t=277`
— the bare floor. Three call sites build `?t=` by hand instead of going through
`deeplink()`:

- `mcp/src/vidtheque_mcp/tools/library.py:495` — chapters
- `mcp/src/vidtheque_mcp/tools/library.py:519` — key texts
- `mcp/src/vidtheque_mcp/tools/library.py:532` — OCR highlights

Everything else does it correctly: `tools/search.py:901`, `tools/segment.py:99`,
`tools/frames.py:125`, `public/ask.py:672` all pass
`deps.settings.deeplink_lead_s`.

**Consequence, observed — and it is the ugly kind.** p3 read the guide's lead
clause, saw `6:40 … ?t=400` in a chapter list, and *hand-corrected it*, shipping:

> Found it: **Ezra Tanzer — "Agentic Development Security"**. The relevant
> section starts at **6:40**: [watch from 6:40](https://youtu.be/cgimkNGNjvU?t=398).

An agent that had been told "never invent a timestamp" invented one, correctly
reasoning from a documented rule that this payload does not obey. A rule
documented in one place and honoured in four out of five is worse than no rule.

**Enhancement.** Route all three through `deeplink(public_id, t, lead)`. If
chapter boundaries are deliberately lead-free (defensible — a chapter start is a
boundary, not a spoken moment), then say so in §3.6 and in the guide, in the
same commit.

### 4.4 MEDIUM-HIGH · (c) · `get-segment-context` prints 20–40 timestamped lines and exactly one link

The whole point of the tool is "the actual words, to quote accurately". It
returns the words with per-line `[mm:ss]` stamps and one `youtu.be` link, on the
header, for the window anchor:

```
Window: 15:24-17:24 (t=984 ±60s) · https://youtu.be/ZFxh7sqbUZo?t=982
TRANSCRIPT
[15:22] Instead of trying to define one problem that's perfect, …
…
[16:23] It also means that there's no LLM as a judge because let's face it, you
        can't trust the LLM that you're teaching to be a judge.
```

An agent quoting `[15:56]` has no link for it, and the guide forbids inventing
one. p1 did the only remaining thing — reused the header link:

> | Rejects LLM-as-a-judge outright … [16:23](https://youtu.be/ZFxh7sqbUZo?t=982) |
> | … his approach uses deterministic grading … [15:56](https://youtu.be/ZFxh7sqbUZo?t=982) |

Two different claims, two different labelled minutes, one link, 27 s of error on
the second. Pillar 3 of `docs/design/positioning.md` is *"the `youtu.be/…?t=`
link that lands on the second"*.

**Enhancement.** Emit `?t=` on every printed transcript line (cheap: the
timestamp is already rendered), or at minimum carry `link` per cue in
`structuredContent` — the `cues[]` array already carries `cue_id`, `start`,
`end`, `text` and would only need one more key.

### 4.5 MEDIUM-HIGH · (c) · Unknown parameter names are accepted and silently ignored

Reproduced directly against the server (not a Codex artifact):

```
$ … call search '{"q":"context engineering","tag":"topic:test","sort_by":"recency",
                  "limit":2,"max_text_chars":120}'
Results: 2/~350+ (use offset=2 for more)
Query: "context engineering" · content_type=all · order=relevance · max_per_video=3
```

A caller who believes it filtered to `topic:test` and sorted by recency got
neither, and a 200. Meanwhile the *same tool* is strict about the two
neighbouring classes:

```
$ … search {"order":"nonesuch"}                 → E_BAD_PARAM: order must be one of …
$ … search {"fields":"video_id,definitely_not…"} → E_BAD_PARAM: unknown field(s) …
```

§3.5 argued this exact case for `fields` — *"one tool, two standards"* — and
fixed it there. The guide currently documents the silence as intended
(*"`tag=` is not a parameter and is dropped silently like any other unknown
name"*), which makes this a contract choice rather than a bug; the evidence says
the choice is wrong. p6 reached the same conclusion without seeing the contract:

> Unknown parameter names are silently ignored, unlike bad enum values and bad
> fields.

**Enhancement.** Reject unknown top-level argument names with `E_BAD_PARAM`,
naming the near miss: `tag` → *"did you mean `tags`?"*, `sort_by` → *"did you
mean `order`?"*, `t_start` on the corpus axis → point at §3.2. The guide's
"dropped silently" bullet then becomes a "rejected, and it tells you the right
name" bullet.

### 4.6 MEDIUM · (c) · `E_UNKNOWN_VIDEO` builds an `index-video` URL out of arbitrary caller input

```
$ … call video-summary '{"video_id":"not-a-video"}'
error: E_UNKNOWN_VIDEO
Video "not-a-video" is not in the corpus.
next: index-video url="https://youtu.be/not-a-video" to add it (takes ~2-6 min),
      or list-videos to browse what is indexed.
```

`not-a-video` is not an 11-character YouTube id. The remedy string-concatenates
whatever the caller sent into a `youtu.be/` URL and recommends spending 2–6
minutes of GPU on it. On a rate-limited box that is a real cost, and p6 — a
consumer with no stake in our uptime — refused it on its own:

> Partly — it suggests indexing the malformed ID as a YouTube URL, so a client
> should not follow that suggestion blindly.

The same hint on `dQw4w9WgXcQ` (well-formed, absent) is correct and useful; the
fix is a shape check, not a removal.

**Enhancement.** When `video_id` fails the id regex, say *"that is not a
video_id (11-char YouTube id, e.g. kCc8FmEb1nY)"* and drop the `index-video`
remedy — the caller almost certainly mis-copied an id, not a URL.

### 4.7 MEDIUM · (b) · `corpus-summary` and `list-videos` report different corpus sizes, with nothing reconciling them

Same minute, same server:

```
$ … call corpus-summary '{…}'                      → Corpus: 154 videos · 53.4h · …
$ … call list-videos '{"limit":100,"offset":100,…}' → Videos: 52/152 (no more results)
```

`corpus-summary` counts every row; `list-videos` counts
`QUERYABLE_INDEX_STATES` (§4.2 of the contract, deliberately). Neither payload
mentions the other. p4, asked point-blank for the exact count, had to construct
the reconciliation itself, and got it half right:

> The paginated listing ended at offset 150: **151 browsable videos**. The corpus
> summary reported **153** total records, but two were still mid-indexing …

`Gaps:` reported **one** video mid-pipeline, not two, so the agent's explanation
is a plausible guess presented as fact. This is §4.3's "two counters, one name"
lesson — fixed for jobs on 2026-08-09 — still live for videos.

**Enhancement.** A `list-videos` footer from the same `tools/corpus_state.py`
derivation: *"152 of the 154 videos in this corpus are queryable; 2 are still
being indexed (`index_state=indexing|pending`) and are not listed."* And/or a
`queryable` count beside `videos` in `corpus-summary`'s structured payload.

### 4.8 MEDIUM · (c) · The intra-video axis accepts a year and answers plausibly

```
$ p6: search {"q": "the", "t_start": 2019, "t_end": 2020, "limit": 5}
Results: 5/6 (use offset=5 for more)
Legs: transcript 6 · ocr 0 · frame 0
[transcript] Compression at the Edge … (J4_jCrTxMkk)
  33:35–33:41 · match at 33:39 · https://youtu.be/J4_jCrTxMkk?t=2017
```

The caller meant 2019 the year; the server read seconds 2019–2020 of every
video, and returned a small, tidy, entirely wrong answer with no `note:`.
§3.2's "harmless otherwise" is precisely what makes it dangerous: a *wrong*
filter that returns nothing is self-correcting, a wrong filter that returns six
plausible hits is not. p6 flagged it as the one probe where recovery was
impossible:

> A year placed in in-video time fields becomes a valid timestamp in seconds,
> yielding plausible but incorrect results.

Note the corpus axis handles the mirror-image case correctly —
`published_after="2:00"` is a hard `E_BAD_TIME_FORMAT`. Only one direction of
the confusion is caught.

**Enhancement.** Emit a `note:` whenever `t_start`/`t_end` are set without a
single-video scope, naming the other axis — the §2 "`all` means all" pattern
applied to axis confusion. A value above, say, 1900 with no `video_id` is worth
a stronger nudge still.

### 4.9 MEDIUM · (c) · `E_BUSY`'s remedy teaches a recovery that cannot work

```python
# mcp/src/vidtheque_mcp/errors.py:148-154
def busy(retry_after_s: int = 1) -> ToolError:
    return ToolError(
        "E_BUSY",
        "The server is already running its maximum number of concurrent searches.",
        f"retry in {retry_after_s}s, or narrow the query so it costs less.",
        retry_after_s=retry_after_s,
    )
```

Narrowing the query cannot help: the semaphore is acquired *before* the query
runs (`tools/search.py:351`, `db/connection.py:252-269` — `if sem.locked(): raise
busy(...)`), so a cheaper query is rejected exactly as fast. p3 followed the
clause it could act on and narrowed twice, losing both searches, then gave up on
`search` for four calls.

**Honest caveat, and it matters.** The `E_BUSY` responses in this eval were
mostly **my own doing** — the admission limit is a global `Semaphore(2)` across
`search` and `list-videos`, and I ran verification probes in parallel with the
terra runs. The *frequency* here is an artifact of my harness. The *hint text*,
and the fact that a competent consumer acted on its wrong half, are not.

**Enhancement.** Drop the second clause: *"retry in 1s — this is a concurrency
limit, not a query-cost limit; the same query will succeed."* Separately, worth
Tom's call: a global limit of 2, shared with `list-videos`, is tight for a
self-hosted server that also serves a dashboard.

### 4.10 LOW-MEDIUM · (c) · `video-summary` advertises an empty section and then points at second zero

```
$ … call video-summary '{"video_id":"BEKc4P87XKo", …}'
Chapters (0 of 0):

Key texts (3):
      4:37  "And here Karpathy says …"   ?t=277
…
next: get-segment-context video_id="BEKc4P87XKo" t=0 window=60 for the actual words.
```

Two problems in six lines. `Chapters (0 of 0):` is a bare heading over nothing —
the exact shape §3.7 forbids for tags (*"A corpus with no tags does not
advertise tags … Either ship the feature or stop advertising it"*), not applied
to chapters. And the `next:` points at **t=0** — the first second of a
27-minute talk — when the payload has just printed three timestamped key texts
it could have aimed at. Meanwhile the guide sells this tool as *"the chapter
list usually names the moment in one call"*; for this video there is no chapter
list, and the payload does not say why (another video in the same corpus,
`cgimkNGNjvU`, has 19).

**Enhancement.** Omit empty sections; aim the `next:` at the top key text
(`t=277` here); and when chapters are absent, say whether it is "no YouTube
chapters and segmentation found none" or "not computed".

### 4.11 LOW · (d) · `fields` narrows the TSV but not `structuredContent`

```
$ … call list-videos '{"limit":1,"fields":"video_id,index_state"}'
video_id  index_state
vSx5IULvBns  ready

structured: {"videos": [{"video_id": …, "title": …, "channel": …, "published": …,
  "duration": …, "coverage": …, "tags": …, "indexed_at": …, "index_state": …,
  "cues": "", "frames": "", "link": …}], …}
```

Twelve keys per row for a caller who asked for two. §3.5's premise is that
*"structured data goes in `structuredContent`, which conformant clients read
without spending prose tokens"* — but a conformant client that used `fields` to
control cost gets no control at all, and pays for `cues`/`frames` columns that
are documented-empty. Low severity because the TSV is what most models read;
worth a line in §3.5 either way.

### 4.12 LOW · (c) · Clamps are silent, and `list-videos`' approximate total scales with the page you asked for

```
list-videos {"limit": 1}   → Videos: 1/~30+
list-videos {"limit": 5}   → Videos: 5/~30+
list-videos {"limit": 50}  → Videos: 50/~80+      (true total: 152)
search {"limit": 500}      → Results: 50/~410+    (no mention of the clamp)
search {"limit": 0}        → Results: 1/~410+
search {"limit": -3}       → Results: 1/~410+
```

p6 called the clamp behaviour out three times ("silently clamped to 50; payload
did not explicitly say it changed the requested limit"). Both halves are
contract-blessed — §3.4 keeps the count probe for `list-videos` because it
really pages in SQL, and §5.2 notes that printing a clamp `note:` is *deferred*.
But `~30+` for a 152-video corpus is the same consumer-facing defect §3.4
removed from `search` on 2026-08-09, and the deferred half of the clamp fix has
now been independently re-filed by a second vendor's agent. Recording it here so
the deferral is a decision with a date on it rather than an omission.

### 4.13 PASS · (e) · What the surface got right

Worth as much as the defects, because these are the paths that used to break.

| # | Probe | Response | Why it counts |
|---|---|---|---|
| P1 | four unanswerable questions (p5) | 4/4 correct refusals with evidence | The `no word of this query occurs anywhere in the corpus` gate is what makes a genuinely empty answer reachable — and it is what a consumer needs to say "your library does not contain this" instead of answering from memory. |
| P2 | `get-frames {"frame_ids":["vSx5IULvBns-99999"]}` | `E_UNKNOWN_FRAME … Video vSx5IULvBns has keyframe ordinals 00000-00072.` | Names the valid range. p6: "supplies valid ordinal range and tells client to use returned IDs." |
| P3 | `get-frames {"frame_ids":["vSx5IULvBns:99999"]}` (malformed) | `Frames: 0/1` + `failed: … not a valid frame id (<video_id>-NNNNN)` | Collected, not fail-fast; the denominator is honest (§4.6 of the contract). |
| P4 | `search {"fields":"video_id,definitely_not_a_column"}` | `E_BAD_PARAM` listing all 13 valid fields | The §3.5 fix holds. |
| P5 | four unparseable times (`"last tuesday"`, `"2026-13-45"`, `"5 fortnights ago"`, `"2:00"`) | `E_BAD_TIME_FORMAT`, each echoing the accepted formats **including the intra-video ones** | The one hint in the eval that would let a confused caller find the *other* axis. |
| P6 | `search {"speaker":"Andrej Karpathy"}` | `E_FEATURE_DISABLED … next: omit speaker=.` | p5 dropped the filter and moved on in one turn. |
| P7 | `get-frames` × 30 ids / span 999999s / `return="image"` × 5 | `E_BAD_PARAM: frame_ids accepts at most 12 ids` · `the requested span is 999999s; the limit is 600s` · `Frames: 5/5 (4 inline, 1 as URLs — inline cap is 4 images / 6MB per call)` | The inline-budget **downgrade** rather than failure is exactly §4.6, and p6 marked it recoverable. |
| P8 | `index-video {"url":"https://youtu.be/BEKc4P87XKo"}` (already indexed) | `Already indexed: BEKc4P87XKo — … No job created.` | Idempotent, no download, no queue slot. Verified by hand — terra never got to run it (§5). |
| P9 | paging to and past the end | `Videos: 1/151 (no more results)` · `Results: 0/143 (past the last page)` | §3.4's past-the-end payload works on a consumer that actually reaches it. |
| P10 | anchored citation | `43:26–43:48 · match at 43:28 · https://youtu.be/am_oeAoUhew?t=2606` | In a 46-minute talk, a 22 s segment with the matched second named — and the truncation window keeps the matched phrase (§3.3's 2026-08-09 amendment). |
| P11 | resource discovery | p3 and p5 both read `vidtheque://guide` and `vidtheque://context` unprompted | The 08-09 fix — putting the resource list in `corpus-summary`'s description — worked on a different vendor's client. |

---

## 5. What did not run

**p2 (`p2-frames`, the coding agent building `slides.md` with frame receipts)
was designed and briefed but not executed.** Its prompt is in
`…/terra-eval/p2-frames/prompt.md`. It was cut for time and because it needed
`workspace-write` plus network egress, which would have loosened the sandbox
fence for the one persona most likely to wander. The paths it would have
exercised were covered by hand instead: `get-frames` in both modes, the signed
URL route (`curl` → `200 image/jpeg 15679`), and the OCR-vs-picture gap — see
`research/demo-queries-2026-08-10.md` §2.1, where I opened the image and
confirmed OCR had silently dropped a cell of a price table. Re-running p2 is the
obvious next increment.

---

## 6. What I could not verify

- **The `E_BUSY` frequency.** My own parallel reproductions contributed to it
  (§4.9). The hint text and p3's reaction are verified; the rate is not
  evidence about production behaviour.
- **The exact chunk count of the corpus.** §4.1's "a fifth of the corpus" is
  derived from `k=800` against ~33.7k cues at a 45 s / 15 s-overlap chunking, not
  from a direct `SELECT count(*) FROM chunks`. The direction is certain; the
  fraction is an estimate.
- **Whether `OV56RddyFuU`'s hub behaviour is embedding-model-specific or a
  property of that transcript.** Four unrelated queries put it at rank 1–2; I
  did not test a second embedder, and `make bench` is Tom's box only.
- **`t_start`/`t_end` on the OCR and frame legs.** p6's year-as-seconds probe
  returned `ocr 0 · frame 0`; with a 1-second window across 154 videos that is
  plausibly correct rather than a leg skip, and I did not isolate it.
- **Whether Codex's snake_case tool renaming ever costs a call.** It did not in
  these six runs.

---

## 7. Suggested order of work

1. §4.1 — the relevance floor. Everything else in this document is cosmetic next
   to a search tool whose rank 1 is unrelated to the query, and it is the one
   finding that contradicts a locked design principle *and* the positioning's
   third pillar.
2. §4.2 — split the `Legs:` counter. Nine characters, and it is the diagnostic a
   caller needs while §4.1 is being fixed.
3. §4.3 — three call sites in `library.py`. A documented rule honoured four
   times out of five made an agent fabricate a timestamp.
4. §4.5, §4.6, §4.8 — the three ways the surface lets a wrong call look right.
5. §4.4, §4.7, §4.10 — payload completeness.

---

# Round 2 — post-repair (2026-08-10 night)

Same orchestrator, same session, same harness. Round 1 ran against a surface
whose **text and frame embeddings were the output of a randomly initialised
network** (`research/embedding-random-init-2026-08-10.md`); this round runs
against the repaired space, the recalibrated floors
(`research/vec-floor-calibration-2026-08-10.md` §6 — text ceiling 0.55, frame
0.65, bands 0.20/0.15) and fixes for every §4 finding above.

**Seven terra runs**, all `gpt-5.6-terra`, Codex `0.144.1`,
`model_reasoning_effort=medium`, `--ignore-user-config`, working directory
outside this repo. Six are the round-1 personas re-run on the **byte-identical
prompt** — that is what makes the regression column honest — plus one new
persona built for the code paths that did not exist this morning. Run dirs:
`…/scratchpad/terra-eval/r2/{p1-research,p2-frames,p3-naive,p4-paginate,p5-absent,p6-stress,p7-migrate}/`
(`prompt.md`, `events.jsonl`, `final.md`; `run_one.sh`, `run_p2.sh`, `trace.py`,
`probe.py` at the top). Every terra line quoted below is verbatim from
`events.jsonl`; every server-side claim was re-run by hand through
`scripts/mcp_call.py` (`probe.py` wraps it with the E_BUSY retry the repaired
remedy now prescribes) and the reproduction is printed with the finding.

**Corpus under test.** 182 videos · 181 queryable · 64.0 h · 41,702 transcript
cues · 7,689 keyframes · one channel (AI Engineer), published 2026-01-12 →
2026-08-09, `data_status: indexing` throughout (tranche 6). Counts are stable
across this section because the reconciliation fix (§4.7) now prints both.

---

## 8. Regression table — round-1 finding → what a consumer sees now

| § | Round-1 defect | Now | Consumer-visible evidence |
|---|---|---|---|
| 4.1 | Semantic legs had no floor; any query "matched" 120–145 of 154 videos | **FIXED** | `q="turbopuffer"` → `Results: 2/2 (no more results)`, both Turbopuffer talks (was `5/121`, rank 1 an unrelated talk). `q="CUDA kernel occupancy"` → `3/16`, rank 1 a frame showing `torch.randn(…, device="cuda")` (was `3/~330+`, rank 1 a Hugging Face talk about self-training). `q="voice agent interruption latency"` → rank 1 *Voice Agents That Handle Interrupts* (was the same hub talk). The `OV56RddyFuU` hub does not appear in any round-2 payload I pulled. |
| 4.2 | `Legs:` merged FTS and KNN, so the guide's `transcript 0` rule was dead | **FIXED**, one residual | `Legs: transcript 6 (fts 14 · vec 11/800)`. `fts 0` is now readable and is what told p7 its top hit had no lexical footing. Residual in §9.2. |
| 4.3 | `video-summary` printed the bare floor, not `DEEPLINK_LEAD` | **FIXED** | `4:37 … ?t=275` (cue at 277.95; was `?t=277`). Chapters: `1:46 … ?t=104` (chapter at 106.0). **p3, the consumer that invented `?t=398` in round 1, invented nothing this time** — it shipped `?t=533` for a moment it labelled 8:55, which is exactly `floor(535) − 2`. |
| 4.4 | `get-segment-context` printed 20–40 stamped lines and one link | **FIXED** | `TRANSCRIPT (cite one line: https://youtu.be/b_PmGocP4rc + the ?t= printed on it)` then `[3:30 ?t=208]`, `[3:35 ?t=213]`, … and `cues[].link` in `structuredContent`. p1 shipped a four-speaker table with **eight distinct links, three of them from one video** (`?t=1067`, `?t=1085`, `?t=757`); p7 quoted three sentences of one passage at `?t=41`, `?t=69`, `?t=88`. Round 1's 27-second citation error is structurally unreachable. |
| 4.5 | Unknown parameter names accepted and silently ignored | **FIXED**, two residuals | `search {tag:"topic:foo"}` → `E_BAD_PARAM … did you mean tag= → tags=?` plus the full accepted set. p7 translated **six** old-tool parameter names off the error text alone. Residuals in §9.3, §9.4. |
| 4.6 | `E_UNKNOWN_VIDEO` string-concatenated caller input into an `index-video` URL | **PARTIAL, by design** | A URL now gets `that is a URL — the video_id is the 11 characters inside it: video_id="BEKc4P87XKo"`; a sentence gets `a video_id is an 11-char YouTube id (e.g. kCc8FmEb1nY)`. But `not-a-video` — the eval's own example — **is eleven legal YouTube-id characters**, so it still returns `index-video url="https://youtu.be/not-a-video" adds it (~2-6 min of GPU)`. `errors.py:129-143` says so explicitly and moves the honesty into the remedy ("If it came from memory, it is not an id from this corpus"). Round-2 p6 nevertheless graded it the same way round-1 p6 did: *"Partly — it treats malformed and unknown IDs identically and suggests indexing the malformed ID."* §9.5. |
| 4.7 | `corpus-summary` and `list-videos` reported different corpus sizes | **FIXED** | `Corpus: 182 videos (181 queryable · 1 queued but never built)` and, on the payload that is missing rows, `note: 181 of the 182 videos in this corpus are queryable and can appear here; 1 queued but never built (index_state=pending) cannot. corpus-summary counts all 182.` p7 was asked point-blank to resolve the disagreement and did it in one sentence off the payload. p4 shipped the reconciliation *and* the right caveat — see §10. |
| 4.8 | A year in `t_start` returned six tidy, wrong hits | **FIXED** | `t_start=2019` → `E_BAD_PARAM: t_start=2019 on the in-video axis means 2019 seconds (33:39) into every video, which is almost certainly not what you meant.` and a remedy naming **both** axes. A non-year unscoped window gets the softer `note:` instead. p6: *"explicitly detects the likely axis mix-up and gives the correct publish-date filter."* This was the one probe round-1 p6 called unrecoverable. |
| 4.9 | `E_BUSY`'s "narrow the query" taught a recovery that cannot work | **FIXED in text, half-landed in behaviour** | The remedy is now `retry the same call in 1s — the limit is on concurrent searches, not on what a query costs, so the identical call succeeds as soon as a slot frees.` p1 still did **not** repeat the identical call — it varied the query twice — but it recovered in one further call instead of abandoning `search` for four. p4 and p6 each retried the identical call and it worked. §9.7. |
| 4.10 | `Chapters (0 of 0):` over nothing, and `next:` aimed at second zero | **FIXED** | `Chapters: none — the publisher marked none in the description, and this corpus does not derive them.` and `next: get-segment-context video_id="BEKc4P87XKo" t=277 window=60 for the actual words around the first key text above.` Both halves, including the "say why there are none" clause. |
| 4.11 | `fields` narrows the TSV but not `structuredContent` | **NOT FIXED — documented instead** | Behaviour unchanged (`fields="video_id"` still returns twelve keys per row in `structuredContent`); `4bfdb2d` records in tool-surface §3.5 that `fields` shapes the text block only, and why. Recording it here so the decision has a date rather than looking like an omission. |
| 4.12 | Clamps silent; `list-videos`' approximate total scaled with the page | **FIXED**, with a new edge case | `limit=500` → `note: clamped server-side: limit=500 → 50. The caps are in vidtheque://context; page with offset instead of raising limit.` (also for `limit=0 → 1`, `limit=-3 → 1`). p6 graded all three "Yes — it clamps to 50 and explicitly says so." The total now reads `181` at `limit=1`, `limit=50` and `limit=100` alike. New edge case in §9.1. |

**Scorecard: 12 numbered round-1 findings. 9 verified fully fixed end to end,
1 partially fixed by deliberate design (§4.6), 1 fixed-with-a-new-edge-case
(§4.12 → §9.1), 1 answered with documentation rather than code (§4.11).
Nothing regressed.**

### 8.1 The near-misses in `demo-queries-2026-08-10.md` §6

- **§6.2** (a confident answer to a question the corpus cannot answer) — **gone**
  as a defect. `CUDA kernel occupancy` no longer claims 330+ results.
- **§6.3** (a perfect quote, an approximate link) — **gone**, per §4.4.
- **§6.4** (a chapter link two seconds off the contract) — **gone**, per §4.3.
- **§6.1** (the right slide wrapped in browser chrome) — **still live**. p3
  pulled the identical Google-Slides-URL-plus-tab-strip OCR string at
  `cgimkNGNjvU` 8:54 this round. Unfixed and unfiled beyond the original note.

---

## 9. New findings, by severity

### 9.1 MEDIUM · (a)(b) · Past the last page, `list-videos` prints a total equal to the offset you sent

Found by p4 while paging an exhaustive inventory; reproduced by hand.

```
$ … call list-videos '{"limit":100,"offset":200,"format":"tsv","fields":"video_id"}'
Videos: 0/200 (no more results)
…
note: 181 of the 182 videos in this corpus are queryable and can appear here; …
structured: {"videos": [], "pagination": {"limit": 100, "offset": 200,
             "has_more": false, "approx_total": 181}}
```

**`0/200` beside `approx_total: 181` in the same payload, and beside the `0/181`
and `100/181` the two neighbouring pages printed.** The mechanism is
`text.py:140-142`: when `has_more` is false the line takes `total = offset +
shown` — correct when you have *walked* to the end (`81/181` at `offset=100` is
right), wrong the moment a caller jumps past it, because `shown` is 0 and the
total collapses onto the offset. That is precisely the "the number moves with
the page you asked for" shape `4bfdb2d` removed from the *in-range* case four
hours earlier; only the out-of-range case survives.

It also loses the diagnosis `search` gives for the same situation. Compare, same
session:

```
search  {"q":"on-call tax","limit":5,"offset":5000}
  → Results: 0/4 (past the last page)
    This query has 4 results; the last page starts at offset=0.
    next: re-run with offset=0, or offset=0 for the top of the ranking.
list-videos {"limit":100,"offset":200}
  → Videos: 0/200 (no more results)
```

**Enhancement.** In `pagination_line`, when `shown == 0 and offset > 0`, print
the probe total and `(past the last page)` with the `last_offset` sentence
`search` already ships — one branch, and it makes the two paging tools say the
same thing about the same event.

### 9.2 LOW-MEDIUM · (b) · The `Legs:` sub-leg split is three different units, and the guide's own example implies they add up

`vidtheque://guide` (`tools/resources.py:150`) teaches:

> Read the `Legs:` counts, and the sub-legs in the parentheses:
> `transcript 24 (fts 9 · vec 15/800)`.

9 + 15 = 24. No live payload does that. Verbatim, from three separate runs:

```
Legs: transcript 43  (fts 0    · vec 28/800)   ← 0 + 28 ≠ 43
Legs: transcript 71  (fts 1    · vec 72/800)   ← the vec sub-leg exceeds the total
Legs: transcript 130 (fts 369  · vec 123/800)  ← the fts sub-leg exceeds it by 3×
Legs: transcript 400 (fts 5000 · vec 0/800)
```

`search.py:557-566` is explicit that this is intended — *"Units are each
sub-leg's own: cues for FTS, chunks for the vector legs"*, against a fused count
of **segments** — but nothing in the payload or the guide says so, and the
guide's illustrative numbers say the opposite. A caller who trusts the example
will read `fts 369` as "369 talks say this" and `vec 72 > 71` as a bug.

The diagnostic the split exists for is undamaged: `fts 0` still means "no
lexical footing", and both p7 and p5 used it correctly. Only the arithmetic
misleads.

**Enhancement.** Change the guide's example to real numbers and one clause —
`transcript 24 (fts 369 cues · vec 123 chunks of 800)` — or print the unit
inline. Cheapest correct fix is to stop using an example that adds up.

### 9.3 LOW-MEDIUM · (c) · The `page= → offset=` near miss is a unit mismatch

The new unknown-name error is the round's biggest single win, and this is the
one entry in its alias table that hands back a wrong answer confidently:

```
$ … call search '{"q":"evals","page":2,"limit":3}'
error: E_BAD_PARAM
Unknown parameter for search: page=. It was rejected, not applied …
next: did you mean page= → offset=? …
```

`page=2` and `offset=2` are not the same request. A client that takes the
suggestion literally on a `limit=50` listing reads rows 2–52 believing it read
page 2, and gets a 200 — the exact failure mode ("a filter you think you passed
was not") that this error was built to end, reintroduced one layer down.
`params.py:55` maps `page → offset`; `page_size → limit` on the next line is
fine because the units match.

**Enhancement.** Either drop `page` from `ALIASES` (the generic "search accepts:
…" list still gets the caller there) or give it its own sentence:
*"`offset` counts rows, not pages — page N is `offset=(N-1)×limit`."*
p7 dodged this only because it read `vidtheque://context` before it guessed.

### 9.4 LOW · (c) · The unknown-name error names the parameter but not its domain, so a rename costs two round trips

p7's own closing line, unprompted:

> The server's `kind → content_type` suggestion was incomplete: it correctly
> named the parameter, but `speech` itself is invalid and must be translated to
> `transcript`.

Reproduced:

```
$ … search {"q":"…","kind":"speech", …}
  → E_BAD_PARAM … did you mean kind= → content_type=?
$ … search {"q":"…","content_type":"speech", …}
  → E_BAD_PARAM: content_type must be one of all, transcript, ocr, frame.
```

Both errors are correct and both are recoverable; the point is that the server
knew the answer at the first call — it had the name, the value, and the enum —
and spent a round trip anyway. Low severity, and the fix is one line: when the
suggested target is an enum parameter, append its domain to the same `next:`.

### 9.5 LOW · (c) · `E_UNKNOWN_VIDEO`'s residual — see §4.6

Filed as its own line because two independent consumers, a week apart, both
graded it "partly". The shape check is right and the remedy's new precondition
clause is right; what a stress-tester sees is still an offer to spend 2–6 min of
GPU on the string it just made up. Worth Tom's call whether an id that is
plausible *and* absent should lead with `list-videos` and mention `index-video`
second — the ordering is the whole finding.

### 9.6 LOW · (d) · `vec 800` and `vec 800/800` are printed identically, so "the band did not bind" is invisible

`_legs_line` prints the `kept/considered` form only when the band actually cut
something — deliberately, so that identical numbers are not noise
(`search.py:986-993`). The result is that the one case a caller most needs to
know about reads like the tidiest:

```
q="why does my assistant forget what I told it a minute ago"
  → Legs: transcript 353 (fts 0 · vec 800) · …          ← 800 of 800 kept
q="turbopuffer"
  → Legs: transcript 6 (fts 14 · vec 11/800) · …        ← 11 of 800 kept
```

The first is a query whose semantic pool was *not* narrowed at all, and it is
the one that looks unqualified. A trailing `/800` (or a `note:` when the band
keeps everything) costs four characters and says "this pool is as wide as the
KNN".

### 9.7 LOW · (e→c) · `E_BUSY` is honest now, and consumers still do not repeat the call

Recording the behavioural half, because the text fix is verified and the
behaviour fix is not. p1's first two searches were `E_BUSY`; it read the new
remedy and issued a **third, differently-worded** query rather than the identical
one. p4 and p6 did repeat the identical call and succeeded. So: 2 of 3 consumers
now do the right thing, against 0 of 1 in round 1, and the one that did not
recovered in one call instead of five.

**The frequency is again my own doing** and is not evidence about production: the
admission limit is a global `Semaphore(2)` shared by `search` and `list-videos`,
and I ran two terra agents plus hand probes against it. Round 1's note stands —
a global limit of 2 shared with `list-videos` and the dashboard is Tom's call.

### 9.8 LOW · (c) · A phrase that exists only in a title has no lexical footing

```
$ … call search '{"q":"on-call tax","content_type":"transcript","limit":5, …}'
Results: 4/4 (no more results)
Legs: transcript 7 (fts 0 · vec 4/800) · ocr 0 · frame 0
[transcript] The Unreasonable Effectiveness of Separating the Task from the Model … (GgLQ02aO-hs)
[transcript] Always-on agents run production without the on-call tax — Justin Smith, Resolve AI (vSx5IULvBns)  19:54
```

The corpus contains a talk **named** "on-call tax"; it ranks 2, 3 and 4, and an
unrelated talk about `self.extract` takes rank 1. Titles are not in the
transcript FTS index, so `fts 0` is truthful and the vector leg is deciding
alone on four candidates. Defensible (`video_title=` is the parameter for
titles, and it works), but a demo should know it: **the one place `search` cannot
find a phrase is the title bar.** Worth a guide clause next to the `fts 0`
bullet rather than code.

### 9.9 PASS · (e) · What round 2 got right that round 1 could not test

| # | Probe | Response | Why it counts |
|---|---|---|---|
| P12 | **p2-frames ran** — the coding-agent persona §5 recorded as never executed | `slides.md` with 5 sections from 5 different talks, every frame URL curl-checked (`200 image/jpeg`, 51,734–107,817 bytes), and **garbled OCR flagged rather than guessed**: *"Some of the small OCR labels are garbled (for example, 'FALED'), so they are not transcribed verbatim here."* | 13 MCP calls, `workspace-write` + loopback egress, no indexing, no fabricated frame id. The frame-receipt path is the product's signature artifact and it now has an independent consumer's deliverable behind it. |
| P13 | A junk query made of ordinary English words | `q="sourdough starter hydration schedule"` → `Results: 0/0` · `All three legs were queried and none of them matched.` | Round 1 could only refuse through `has_lexical_footing` ("no word of this query occurs anywhere"). This refusal comes through the **floor**: the words are all in the corpus, the legs all ran, and nothing was near enough. That is the empty state §6.3 of the calibration doc predicted, reachable for the first time. |
| P14 | Four unanswerable questions (p5, identical prompt) | 4/4 correct refusals again, and this time **without having to reason around inflated denominators**: `CUDA kernel occupancy` came back `5/16`, not `10/~330+` | Round 1's cost — "it had to reason around four payloads that each claimed hundreds of results" — is gone. |
| P15 | Six old-tool parameter names, no docs (p7) | Every one translated; five off the server's own error text, the rest off `vidtheque://context` | The unknown-name error is a *migration aid*, not just a guard. This is the strongest evidence for §4.5's fix. |
| P16 | Exhaustive inventory with a queued video present (p4) | *"I cannot honestly call List B exhaustive for all 182 library items: the server says one video is still queued/unbuilt and is not queryable … The 101 is exhaustive for the 181 queryable videos"* | The §4.7 reconciliation is not decoration — a consumer used it to scope its own honesty claim. |

---

## 10. Consumer task success, round 1 vs round 2

| Persona | Round 1 | Round 2 |
|---|---|---|
| p1-research | Succeeded. **Two of five searches returned an off-topic rank 1**, and the deliverable carried two different moments on one link, 27 s off. | Succeeded. Every search on topic; the deliverable carries **four named speakers and eight distinct per-sentence links**. But: it graded the corpus *"more consensus than contradiction"* and found a softer disagreement than round 1's Brumley/Heiner pair — an honest answer, and a weaker demo. Round 1's sharper pair is still reachable (demo doc §11.5); this consumer's query wording just did not land on it. |
| p2-frames | **Not run.** | **Ran and succeeded** — see P12. |
| p3-naive | Succeeded in 12 calls; lost 2 searches to `E_BUSY` and narrowed instead of waiting; **hand-corrected a chapter link and thereby invented a timestamp**. | Succeeded in **9 calls**, 1 `E_BUSY`, no invented timestamp, and it landed on a different (also correct) talk. |
| p4-paginate | Succeeded, paged to the true end, but shipped **"143 distinct talks"** — semantic fill presented as lexical recall — out of 151. | Succeeded, paged to the true end, shipped **101 distinct talks out of 181** merged from two queries, plus *"the server did **not** stop pagination early"* and an explicit carve-out for the one unqueryable video. The count is defensible and the caveat is the payload's, not the model's invention. |
| p5-absent | **4/4 correct refusals** — the round's strongest result, at the cost of reasoning around inflated totals. | **4/4 again**, with the totals no longer inflated. |
| p6-stress | 27 probes; filed three findings that matched ours. Errors recoverable, unknown parameter names silently ignored, year-as-seconds unrecoverable. | 23 probes; **"automatic recovery: Yes"** on every row but one. Its remaining "leads a client astray" list is four items long, of which three are documented contract choices (`get-frames` partial failure inside a success, inline-image downgrade, `E_BUSY` under client concurrency) and one is §9.5. |
| p7-migrate (new) | — | Succeeded. All five jobs, six parameter renames off the error text, the 0/0 negative control, the count reconciliation, and one finding of its own (§9.4). |

**The shape of the change.** Round 1's failures were mostly *the surface lying
quietly*: a rank 1 unrelated to the query, a total that meant nothing, a link
that was right four times out of five. Every one of those is now either fixed or
named in the payload. What is left in §9 is smaller and of a different kind —
edge cases at the boundary of a page, a guide example that does not add up, an
alias that is off by one unit. Round 1 found one finding that contradicted a
locked design principle; round 2 found none.

---

## 11. What I could not verify, round 2

- **Whether the relevance gains are the floor or the embeddings.** They are
  confounded by construction: both landed tonight. The calibration doc's §6.3
  separates them on the DB side (rank 1 was already correct at ceiling 1.0 on
  the repaired space; the ceiling changes the *pool*), but no consumer-visible
  A/B exists, and I did not build one.
- **`E_BUSY` frequency**, again and for the same reason (§9.7).
- **Whether p1's softer disagreement is the corpus or the ranking.** The corpus
  grew from 154 to 182 videos between rounds, so round 2's p1 was choosing from
  a different shelf. The Brumley/Heiner pair still exists and still surfaces
  (demo doc §11.5); I did not re-run p1 with round 1's exact query wording to
  isolate it.
- **`index-video` idempotency under terra.** p6's single authorised call came
  back `user cancelled MCP tool call` from the harness both rounds; the
  behaviour is verified by hand (round-1 P8) and has never been observed
  through a consumer.
- **The `fts` sub-leg's AND semantics.** `fts 0` appears on many multi-word
  real queries (`"RAG failure modes production"`, `"human evaluation LLM
  judge"`). I did not confirm whether that is FTS5 requiring every term or a
  narrower tokenisation, which matters for how strongly §9.2's diagnostic should
  be worded.

---

## 12. Round-2 repairs — what shipped, and what was deliberately left (2026-08-11)

Added by the fix pass, append-only, one line per §9 finding. Every claim below
is pinned by a test named in the commit; the contract changed in the same commit
where behaviour did.

| § | Shipped | Where |
|---|---|---|
| 9.1 | `pagination_line` past the last page prints the probe total, `(past the last page)` and the last-page offset — the sentence `search` already had. `last_offset` joins `list-videos`' structured pagination. | `text.py`, `tools/library.py`, contract §3.4 |
| 9.2 | The `Legs:` line names its units: `transcript 130 segments (fts 369 cues · vec 123/800 chunks)`. The guide's example is now a real, non-summing payload and says the three numbers are three units. | `tools/search.py::_legs_line`, guide, contract §4.1 |
| 9.3 | `page= → offset=` carries *"offset counts ROWS, not pages — page N is offset=(N-1)×limit"*. The alias stays: dropped, the caller falls back to the accepted-names list, which is where they were already going wrong. | `tools/params.py::UNIT_HINTS`, contract §3.5 |
| 9.4 | A near miss into an enum appends the domain when the value sent is out of it (`content_type must be one of … — 'speech' is not one of them`), silent when the value already fits. Domains resolve from the tools' own tuples. | `tools/params.py::enum_domain`, contract §3.5 |
| 9.5 | `E_UNKNOWN_VIDEO` on a plausible absent id now **leads with `list-videos`** and puts `index-video` behind a precondition phrased as a test the caller can apply ("came from outside the corpus and is in front of you") rather than an act of introspection ("if it came from memory"). The clauses were right; the order was the finding. | `errors.py::unknown_video`, contract §3.8 |
| 9.6 | `kept/considered` prints always, including `vec 800/800`. Suppressing it made the un-narrowed pool — the case a caller most needs to notice — read as the tidiest. | `tools/search.py::_legs_line`, contract §4.1 |
| 9.7 | One more clause, as prescribed: *"retry the IDENTICAL call in 1s — do not reformulate the query, a different one is refused exactly as fast."* The wrong move is named as an instruction instead of implied by the right one. | `errors.py::busy`, contract §3.8 |
| 9.8 | A `note:` naming up to three matching **titles**, on the `fts 0` branch only. See below. | `tools/search.py::_note_title_footing`, `db/queries.py::title_matches`, guide, contract §4.1 |

### 12.1 §9.8 — why a note and not a leg (and what is filed for Tom)

`search` reads `cues_fts`, `ocr_frames_fts` and the two vector spaces. It never
reads `videos_fts`, so **the one place `search` cannot find a phrase is the
title bar** — `fts 0` on `"on-call tax"` is truthful, and the semantic leg then
ranks alone, which is how the eponymous talk landed at 2-4.

Shipped: when the transcript FTS sub-leg is empty and a title *in scope* matches
the same expression the legs bind, the payload says so, names up to three titles
with their `video_id`s, and points at `video_title=`. One bounded FTS lookup
(`LIMIT 3`, column-filtered to `title` so a description match is never claimed
as a title match), asked only on the branch where there is nothing else to spend
on.

Not shipped, and the reasoning, so the decision has a date rather than looking
like an omission:

1. **Title matches as results.** A `search` result is a moment with a receipt
   (§3.6). A title matches the video, not a position in it, so a title result
   would need either an invented `t=0` — the fabrication the guide's first rule
   forbids — or a result with no deep link.
2. **A title boost in the RRF fusion.** This is the honest larger fix and it is
   a *tuned constant over a scored ranking*: the same class as the vec floors,
   which is to say a bench item with a before/after per encoder
   (`vec-floor-calibration-2026-08-10.md`). The SQL exists already
   (`videos_fts … rank MATCH 'bm25(10.0, 1.0, 3.0)'`, index-schema's FTS notes);
   the measurement does not. Shipping an uncalibrated weight to fix one rank-1
   is how round 1's rank-1 problem arose in the first place, in the other
   direction.

**For Tom, one question:** should a title match contribute to the *ranking*, and
at what weight? A bench design that would settle it: the round-2 corpus, the
eval's own queries plus every query whose words appear in a title and nowhere
else, scored on whether the eponymous talk takes rank 1 — against the control
that no currently-correct rank 1 moves. Until then the note is the honest half:
it cannot mis-rank anything, and it cannot be missed.
