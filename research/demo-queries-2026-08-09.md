# Demo queries — the searches that make vidtheque sing (2026-08-09)

Author: exploration agent, session `peppy-wibbling-moler`. Append-only doc: add
new sections, don't rewrite these findings.

**What this is.** A verified set of example queries for the demo surface and the
public read-only dashboard — the chips a visitor clicks, the questions an agent
runs over MCP. Every query below was executed against the live demo stack
(`mcp :8100`, demo/readonly) and kept only if the top hits were *obviously,
satisfyingly right*. Cuts are in §6; everything that misbehaved on the way is in
§7 with a reproducer.

**Corpus under test.** 75 videos · 26.1 h · 16,777 transcript cues · 3,060
keyframes · one channel (AI Engineer), published 2026-04-16 → 2026-08-08.
59,817 OCR lines over 2,870 OCR'd keyframes (190 empty, 1,526 skipped as dups).

---

## 0. Read this before using the list — the stack is at migration 0002

The live demo DB (`/home/dev/vidtheque-data/vidtheque.db`) has **only migrations
0001 and 0002 applied**:

```
sqlite> select * from schema_migrations;
1|initial|fbb15d70…|1786221290
2|worker_model_ids|175f7aae…|1786221290
```

`0003_ocr_frame_fts.sql` (OCR FTS moves from lines to frames) and
`0004_unified_embedding_space.sql` are **not applied**. `ocr_fts` is still
`content='ocr_lines'` with 59,817 rows — one document per *line*.

Consequence, and it is the single biggest constraint on the OCR query set: **a
multi-term OCR query only matches when every term lands on the same physical
line.** Two demonstrations, both against a slide that plainly contains all the
terms (`ZFxh7sqbUZo-00018` has lines `int vuln(char *input) {` and
`strcpy(buf, input);/*Bug` and `Grade Oracle` and `RL Environment`):

```
search {"q":"vuln strcpy","content_type":"ocr","limit":3}                  → 0/0
search {"q":"Grade Oracle RL Environment","content_type":"ocr","limit":3}  → 0/0
```

This is exactly the recall hole 0003's header comment describes ("task #20").
Every OCR query in §2 was chosen to be *single-line-matchable* so it works today.
They will keep working after 0003; the queries in §6.2 are the ones that should
be re-tested and promoted once 0003 lands.

---

## 1. Spoken word — the transcript leg

Unless noted, run with `content_type="transcript"`, `limit=5`,
`max_text_chars` 200–400. Capability label in bold.

### 1.1 `reward hacking` — **exact term, defined out loud, four talks deep**
Top hit: *When Will The Benchmaxxing Plague End?* (`-npY6XjM8CQ`) 5:11–5:56.
Result #3 is the money shot: a **single cue** at 5:57, `cue 4774`,
`https://youtu.be/-npY6XjM8CQ?t=355` —
> "Reward hacking is basically when a model finds a lazy and creative way to
> meet the letter of the law but not the spirit."

Why it demos: single-cue clusters make the deep link land *mid-sentence on the
exact moment*, which is the whole promise. Results span 4 talks
(`-npY6XjM8CQ`, `AQv3qRCG6Gw`, `2aS7aKoXn64`, `31GUkCBD-Uc`) — corrected from
"5 talks" on 2026-08-09; the ids were always four, and the count was the slip
(§9.4). Any chip label built from this line says four.

### 1.2 `functional property disjoint` — **speech ↔ slide pairing**
Top hit: *Why Agentic Systems Need Ontologies* (`Sir59K8ZDPU`) `cue 789`, 19:01,
`?t=1139`:
> "So you have these functional properties, disjoint properties, I'll just put
> these, you can look at the slides…"

Why it demos: run it *next to* §2.1 (`owl:FunctionalProperty`, OCR leg, same
video, 18:56). The speaker says "functional properties" and tells the room to
look at the slides; the slide says `owl:FunctionalProperty`. Two legs, two
vocabularies, one moment. This is the guide's own worked example, live.

### 1.3 `145x less training compute` — **a spoken number**
Top hit: *Data Quality Is the Compute Multiplier* (`_PdK6x7PQNM`) 8:08–8:37,
`?t=486`, "…while using 145x less…". Exact, first try.
Caveat worth showing on purpose: whisperX renders "Qwen3 4B" as **"coin 3.54b"**
— a live illustration of the guide's "auto captions are noisy, check
`get-segment-context` before quoting".

### 1.4 `dirty secret of forward deployed engineering` — **title-adjacent claim**
Hits 1–3 are all *The Dirty Secret of FDE* (`Byv311hdoHE`), hit 4 is the Kepler
FDE talk. Caveat: the payoff line ("…is it doesn't exist", ~2:23) sits inside an
18-cue cluster whose link points at the cluster *start* (`?t=118`) and whose
text is middle-truncated away at `max_text_chars≤400`. Use
`max_text_chars=0` for this chip, or point the chip at hit #2 (10:10) where the
line survives in the tail. See §7.4.

### 1.5 `small towns in Bavaria` — **the memorable throwaway line**
`Building Turbopuffer` (`jQDXzEVHMSE`) `cue 3703`, 20:28, `?t=1226`:
> "It's like the entire internet runs on small towns in Bavaria, I'm convinced."

Delightful, single-cue, perfect citation. **Ships at rank #2, not #1** — see
§7.1. Keep it, but if a chip must be rank-1-true, use it with
`limit=3` so the quote is visibly on screen.

### 1.6 `supply chain attack npm package compromised` — **a real incident, told**
Top hit: *Open Source Is Dead. Long Live Open Source.* (`CoEIs6Xm8m8`) 4:01–5:35,
`?t=239` — the three-hour compromise, stolen PyPI publishing tokens, credential
harvester, "the only reason this was even caught as quickly as it was was just
pure luck". A whole story arrives in one result block.

### 1.7 `vending machine` — **a proper noun that owns one talk**
`cO8qC6HBuBg` (*Vending-Bench*) takes all three slots; result #3 is a single cue
at 6:42, "We have AI vending machines, which was kind of the first thing."
Pairs beautifully with `video-summary cO8qC6HBuBg` (§4.3).

### 1.8 `benchmark contamination training data leaked` — **cross-video, one topic**
Run with `max_per_video=1`, `limit=6`. Returns 5 *different* talks, every one
on-topic: `-npY6XjM8CQ`, `_PdK6x7PQNM`, `jWq-aZIU0kM`, `1EZdpEhwmNc`,
`Yk87oUPVaxU`. The cleanest "one question, five speakers" result in the corpus.

### 1.9 `what makes a good eval` — **a question, not a keyword**
`max_per_video=1`: YouTube Ads (`xyL2Ltkh-SA` 16:01), Morgan Stanley
(`kiqubc5b5Yo` 15:09, "you have to start with good eval"), Nubank/Snowglobe
(`KMR_RBoCa4M` 2:21). Demonstrates that natural-language questions work, not
just keywords.

### 1.10 `agents running for hours long horizon` — **conceptual, no shared vocabulary**
`max_per_video=1`: `2aS7aKoXn64` (Rethinking Environments), `Ib5t2RLtxvM`
(Snorkel), `Ib5GBkD555M` (HumanLayer, "agents really start to struggle after
maybe three to six months"). None of the three phrases the idea the same way —
good proof the leg is not just FTS.

---

## 2. On-screen text — the OCR leg

**All of these must be run with `content_type="ocr"`.** With `content_type=all`
the frame and transcript legs tie with them at rank 1 and often win the
tiebreak (§7.1) — an exact on-screen string can land at rank 3 of its own query.

### 2.1 `owl:FunctionalProperty` — **the flagship OCR query**
`1/1 result.` `Sir59K8ZDPU-00044`, 18:56, `?t=1134`. The frame is a four-column
table ("THE RULE, IN ENGLISH / THE OWL AXIOM / THE ERROR IT CATCHES") — visually
verified, the axiom is large and legible at `w=512`. Nothing in speech contains
this token. Best single demonstration of "found by reading the screen".

### 2.2 `guardrail_safety_check` — **an identifier that was never spoken**
`3/3`, all `9HbzAWnKbo4` (*From Signal to PR*, Arize) at 11:15 / 12:19 / 18:49.
At 11:15 the speaker is saying "this is a financial trading agent… there's a lot
of ways this can fail" — the token exists only as a span name inside the
screenshot of the trace tree. Verified in the image.
Caveat: it is a *product screenshot*, so the text is small; at thumbnail size a
visitor can see the trace tree but not read the span. Use §2.1 or §2.5 when the
thumbnail itself has to sell it.

### 2.3 `hillclimbness` — **a coined word, only on a slide**
`2/2`, `ZyIoTOAbRfs` (*State of Data*) at 12:13 and 13:45. Returned line:
`Demand hillclimbness, not difficulty pass@1=0 but pass@32=30%→trainable.`
Verified in the image (line 4 of "How to read the next domain").

### 2.4 `gold commit` — **a two-word phrase that fits on one slide line**
`2/2`, both `Yk87oUPVaxU-00013` (*DeepSWE*) at 5:52. Returned lines:
`The gold commit ships inside SWE-Bench Pro containers. DeepSWE v1.1 deletes future refs entirely.`
`share of reviewed SWE-Bench Pro passes flagged CHEATED. 87% of flags = gold commit read from .git`
A benchmark-integrity claim with a number, readable straight out of the payload.
(Both hits are the *same frame* — see §7.3.)

### 2.5 `annotations_to_evals.py` — **a CLI invocation**
`2/2`, `O72p-rBb2bA-00028` (*Evals-Driven Development*, SonderMind) at 12:27:
`$ annotations_to_evals.py --weeks 2 --priority-themes` and `… --write`.
The frame is "THE ANNOTATION-TO-EVAL PIPELINE" — verified, large type.

### 2.6 `CVE-2026-22812` — **a CVE number**
`2/2`, `RjfbvDXpFls` (*Building pi in a World of Slop*) at 4:32 and 4:37 — an NVD
page on screen. The full frame text (37 OCR lines) carries the whole advisory:
*"OpenCode automatically starts an unauthenticated HTTP server that allows any
local process (or any website via permissive CORS) to execute arbitrary shell
commands… fixed in 1.0.216."* Get it with `get-frames`, not with
`max_text_chars=0` (§7.2).

### 2.7 `strcpy buf input` — **C code on a slide**
`3/3`, `ZFxh7sqbUZo` (*Teaching AI to Find Real Vulnerabilities*) at 7:07 / 8:37 /
9:28: `strcpy(buf, input); /* Bug 1 */`. Works because `strcpy(buf, input);` is
one OCR line; `unicode61 tokenchars '_-./'` splits it into three tokens.

### 2.8 `csvtojson` — **one weird token, one frame in 3,060**
`1/1`, `-I5W5QVAT8E-00059` (*Notion's Token Town*) at 18:53:
`$ csvtojson data.csv I curl -X POST \` (the OCR reads the pipe as `I` — honest
about what OCR does, and the query still lands).

### 2.9 `ast-grep` — **a tool name in a CI config**
`3/3`, `xIt_mTQp6mY` (*Loop Engineering from First Principles*) at 10:49 / 11:40 /
11:56, including `- name: Install ast-grep` — a GitHub Actions step, on screen.

### 2.10 `scGPT` — **a domain model name**
`3/3`, `-561cZmir5Q` (*From Tokens to Cells*): `scGPT, Geneformer & friends`
(10:49), `scGPT: Masked Gene Modeling` (11:25, 11:55). Verified in the image —
a dark browser-rendered slide, very legible.

---

## 3. Visual — the frame-embedding leg

Run with `content_type="frame"`. Every one of these was confirmed by fetching
the top frame's thumb URL and looking at it.

### 3.1 `a terminal window with code` — **verified visually**
Top: `1EZdpEhwmNc-00040` (Snyk security talk) 15:59 — an actual iTerm2 window on
a macOS desktop, dock and menu bar included. Ranks 2–5 stay on-theme (Perception
Agents CLI output, a localhost browser, the pi leaderboard). Raise to `limit=20`
and 18 frames come back, all screens-with-text.

### 3.2 `architecture diagram with boxes and arrows` — **verified visually**
Top: `lyL5QhgIOxc-00039` (*Scaling the Hugging Face Hub*) 17:02 — a sharded
MongoDB topology, CLIENT APPLICATION → MONGOS → SHARD A/B, labelled arrows,
literally boxes and arrows. #2 `0RNNfxpdbQk-00010`, #4 `CgsWxRUY5Eo-00051`
(slide titled "ARCHITECTURE"). The single most convincing image-search chip.

### 3.3 `a line chart going up and to the right` — **verified visually**
Top: `O-CBZ3JtRvo-00027` (*Training Frontier Models to Out-Think Hackers*) 6:38 —
"We did it for coding. Now let's do it for *cyber*." with an exponential curve
rising to the right and a second flat curve below it. Exactly the requested
shape.

### 3.4 `a photo of a person on stage at a podium` — **verified visually; no text at all**
Top: `1OMHGsUZiqA-00045` 21:38 — a speaker at the World's Fair podium, blue
curtain, no slide. Ranks 2–4 are all talk-opening podium shots (`t≈11–25 s`).
The strongest proof the frame leg is not secretly reading OCR: these frames have
essentially no text.

### 3.5 `a bar chart comparing models` — **partial keeper**
Returns real comparison tables (`Yk87oUPVaxU-00010` DeepSWE results,
`2aS7aKoXn64-00043`, `hacEQHHhu2Q-00018` Gemma 4) but ranks 1–2 are browser
screenshots from `-561cZmir5Q`. Good enough as a chip; not the one to lead with.

---

## 4. Cross-video / thematic — the ask-mode and agent-workflow set

Material verified to exist; each of these has ≥2 talks' worth of citable
substance behind it.

### 4.1 "What do speakers disagree about when it comes to LLM-as-a-judge?"
Raw material confirmed. The sceptic: `-npY6XjM8CQ` 16:01 — *"LLM as a judge
doesn't really work either because LLMs don't have good taste in writing."* The
successor-argument: `q2JrUKBMf0w` 4:07 — *"Agent as a judge is about adaptive
dynamic analysis. LLM as a judge just gives you a fixed rubric."* The
practitioners who ship it anyway: `b_PmGocP4rc` (Character.ai, video),
`31GUkCBD-Uc` (Uber, multimodal), `O72p-rBb2bA` (SonderMind).
Run as: `search q="LLM as a judge" content_type=transcript max_per_video=1 limit=6`
— **and note the rank-1 problem in §7.5 before making this a chip.**

### 4.2 "Which talks tell a real production incident story?"
Confirmed: `BInpv7lGp1o` (*Your Agent Didn't Fail. Your Harness Did.*, OpenAI —
opens with one production incident and returns to it three times),
`CoEIs6Xm8m8` (the three-hour package compromise), `0RNNfxpdbQk` (Pinterest,
failing Spark jobs), `jRCpXUjz4CI` (a Typer 0.21 vs 0.26 version skew shown on
screen), `tJFjeMBKbIY` (the February 2023 launch-demo failure).
Best entry query: `search q="supply chain attack npm package compromised"` plus
`search q="one production incident" video_id="BInpv7lGp1o"`.
Do **not** use the generic phrasing — see §6.1.

### 4.3 "How do seven companies each define forward-deployed engineering?"
`list-videos q="forward deployed" limit=10` → exactly 8 videos, 8 companies
(Factory, Varick, Cognition, Ramp, Sierra, Decagon, Kepler, Anthropic), all
`tof` coverage. A perfect browse-then-compare workflow, and a strong dashboard
chip because the *list itself* is the answer.

### 4.4 "What is everyone calling 'slop', and do they mean the same thing?"
`search q="slop" limit=6 max_per_video=1` — four talks with *slop* in the title
(`AMiyLItEtLA`, `lCBf9slCanI`, `b_PmGocP4rc`, `RjfbvDXpFls`) plus an OCR hit and
a frame hit. Two results come back tagged **`[transcript+ocr]`** with a fused
score (0.0465, 0.0370) — the only query in this doc that visibly demonstrates
RRF fusing two legs onto one moment. Excellent "how it works" chip.

### 4.5 "Is benchmark contamination a solved problem?"
See §1.8 — five talks, five positions, one call.

---

## 5. Story flows — the citation chain, end to end

### 5.1 The ontology flow (best overall; use this one on the landing page)

```
1  search            {"q":"owl:FunctionalProperty","content_type":"ocr","limit":3}
     → 1/1 · Sir59K8ZDPU-00044 · 18:56 · https://youtu.be/Sir59K8ZDPU?t=1134
2  get-segment-context {"video_id":"Sir59K8ZDPU","t":1136,"window":60}
     → Chapter: "The errors an ontology catches that English cannot" (18:52–21:18)
     → [19:01] "So you have these functional properties, disjoint properties …
                you can look at the slides, but essentially, the errors it can
                catch, look over in the right-hand column."
     → FRAMES: Sir59K8ZDPU-00042, -00043, -00044
3  get-frames        {"frame_ids":["Sir59K8ZDPU-00044"]}
     → http://…/frames/Sir59K8ZDPU-00044.jpg?w=512&q=75
```

The beat that sells it: at step 2 the speaker *tells the room to look at the
slides*, and at step 3 the tool does. The frame is a table — the flat OCR join
scrambles it, the image doesn't, which is the index-schema §2.5 rule made
visible instead of asserted.

### 5.2 The never-spoken-identifier flow

```
1  search            {"q":"guardrail_safety_check","content_type":"ocr","limit":3}
     → 3/3, all 9HbzAWnKbo4 · 11:15 / 12:19 / 18:49
2  get-segment-context {"video_id":"9HbzAWnKbo4","t":675,"window":45}
     → Chapter: "Product demo: Signal, AX, and Phoenix" (11:10–13:09)
     → the speaker says "this is a financial trading agent … there's a lot of
        ways this can fail" — and never says the identifier
3  get-frames        {"frame_ids":["9HbzAWnKbo4-00040"]}
     → the Arize AX trace tree, guardrail_safety_check as a span
```

The point: a transcript-only index cannot answer this query at all. Caveat at
step 3 — the span text is small; present the image at ≥768 px.

### 5.3 The one-call flow (for the impatient visitor)

`video-summary {"video_id":"cO8qC6HBuBg"}` alone is a demo. 12 chapters, and the
titles do the selling: *"Emergent misbehavior: collusion, lying, power seeking"*,
*"Laying off Gemini, hiring GPT"*, *"The Nazi song and the reproducibility
problem"*, *"Humans as adversarial forces"*, *"Live demo: is the store in a
simulation?"* — each with a `?t=` link, plus 12 key texts and 10 on-screen
highlights. Good chip: "show me what's in a talk without watching it".

---

## 6. Cuts — and what they teach

### 6.1 Cut: `production incident postmortem what went wrong`
`content_type=transcript, max_per_video=1` → rank 1 is `BInpv7lGp1o` `cue 7947`,
**"Now let's talk about time."** A one-cue cluster with zero information, from
the right video, at rank 1. Lesson: abstract multi-concept phrasings land on the
semantic leg, and the semantic leg's cluster boundary can be a filler sentence.
Prefer a concrete noun the speaker actually said.

### 6.2 Cut *for now*: every multi-line OCR query
All return `0/0` on the current build (§0). Re-test and promote after 0003:
- `Grade Oracle RL Environment` (one slide, `ZFxh7sqbUZo-00018`)
- `vuln strcpy` (same slide)
- `SWE-Bench Pro CHEATED` (one slide, `Yk87oUPVaxU-00013`)
- `Gemini lost Andon Café` (one slide, `cO8qC6HBuBg-00021`)
- `Nemotron-Terminal-Corpus Qwen3-32B` (one table, `ewtOo0scUh0-00034`)
These are the *best* OCR queries in the corpus and none of them work today.

### 6.3 Cut: `as a judge` on the OCR leg
`0` OCR lines contain the phrase, so it looks like a corpus gap; in fact it is
the 0003 gap again plus a genuine absence. Lesson for the test phase: an empty
OCR leg is not evidence the slide doesn't say it.

### 6.4 Cut: `dirty secret it does not exist` with `cluster_gap=0`
Returns three consecutive cues from a *different* video (`zkX03APVj0M` 11:39–11:49):
"So, you know, then this begs the question, this is all cool stuff, Joseph." /
"Thank you, Sid, for speaking." / "Why are you leaking all of this alpha, right?"
Lesson: `cluster_gap=0` does not mean "give me the single best cue"; it splits a
semantically-matched chunk into its raw cues and ranks them by position, so the
displayed text can be pure filler. Don't use `cluster_gap=0` for demos. (Also
§7.6.)

### 6.5 Cut: stop-word-heavy exact quotes
`"the entire internet runs on small towns in Bavaria"` as a full sentence is
worse than the three distinctive words. Consistent with the guide's "prefer two
or three words over an exact long phrase".

### 6.6 Cut: `docker run`, `kubectl`, `git commit`, `zero day`, `vector db`
Zero OCR lines each. This is a *conference-talk* corpus, not a live-coding
corpus: there are slides of code but almost no shell transcripts. Good to know
before promising "search your terminal history" energy on the dashboard.

---

## 7. Observed issues

Recorded, not fixed. Each has an exact reproducer. All run against
`mcp :8100` demo/readonly on 2026-08-09.

### 7.1 RRF ties at rank 1, and the exact match loses the tiebreak — **highest impact**
Every leg's (and every sub-ranker's) rank-1 gets the identical RRF score
`1/(60+1) = 0.0164`. The tiebreak is not relevance-aware, so an *exact, unique*
string match can be outranked by a fuzzy neighbour.

```
uv run --no-sync scripts/mcp_call.py call search '{"q":"CVE-2026-22812","limit":5}'
```
The CVE appears in exactly 2 OCR lines in the whole corpus. Result: the two
correct OCR hits are at **ranks 3 and 4**, behind a frame hit from a Sierra FDE
talk (score 0.0164) and a transcript hit from a Cline talk (score 0.0164) —
neither contains the string.

```
uv run --no-sync scripts/mcp_call.py call search '{"q":"small towns in Bavaria","content_type":"transcript","limit":3}'
```
The literal quote (`jQDXzEVHMSE` cue 3703) is at **rank 2**, score 0.0164, tied
with a rank-1 hit from `hacEQHHhu2Q` about tiny models that contains none of the
query terms. Same tie, same loss — this time *within* one leg, so it is the
FTS/dense sub-rankers tying, not just leg-vs-leg.

Impact on the demo: any chip that promises "the exact thing you typed" must pin
`content_type`. Worth considering a lexical-exactness tiebreak at the fusion
seam (tool-surface §3.4 names that seam as the reranker slot).

### 7.2 `max_text_chars=0` does not un-truncate an OCR hit
tool-surface §3.3: *"`max_text_chars=0` still means everything: the frame's every
line, in reading order."*

```
uv run --no-sync scripts/mcp_call.py call search '{"q":"CVE-2026-22812","content_type":"ocr","limit":3,"max_text_chars":0}'
```
Returns `CVE-2026-22812 Detail` — 21 characters. The frame
(`RjfbvDXpFls-00031`) has **37 OCR lines**, including the whole NVD description
and the CVSS vector. Same with `strcpy buf input` (1 line shown of 12).
Root cause is §0 (line-level FTS ⇒ the "hit" *is* a line), but the contract text
and the printed opt-out both promise otherwise, so a user following the payload
gets nothing.

### 7.3 `get-frames` ignores `max_text_chars=0` while telling you to pass it
```
uv run --no-sync scripts/mcp_call.py call get-frames '{"frame_ids":["Sir59K8ZDPU-00044"],"max_text_chars":0}'
```
Output still contains `…[582 chars truncated — pass max_text_chars=0 for full
text]…`. The marker instructs the caller to do the exact thing the caller just
did. The guide additionally states *"There is no `max_text_chars` on
`get-frames` — the picture is the un-truncated text"*, which is a third,
different story. Three sources, three behaviours.

### 7.4 One frame can occupy several result slots
```
uv run --no-sync scripts/mcp_call.py call search '{"q":"Andon","content_type":"ocr","limit":4}'
```
Results 1 and 2 are **byte-identical**: same `frame_id` (`cO8qC6HBuBg-00004`),
same timestamp (0:21), same text (`Andon Labs`). Two OCR lines on one frame both
read "Andon Labs", and each becomes its own result. Same shape on
`annotations_to_evals.py` (2/2, both `O72p-rBb2bA-00028`) and `gold commit`
(2/2, both `Yk87oUPVaxU-00013`). It also silently burns the `max_per_video`
budget. Should disappear with 0003; until then a demo showing "3 results" may be
showing one slide three times.

### 7.5 Search links point at cluster start, not at the matched moment
```
uv run --no-sync scripts/mcp_call.py call search '{"q":"dirty secret of forward deployed engineering","content_type":"transcript","limit":4,"max_text_chars":400}'
```
Rank 1 is an 18-cue cluster spanning **2:00–3:59** with link `?t=118`. The
matching phrase is around 2:23. With `max_text_chars=400` the middle-truncation
also removes the matched phrase from the displayed text — so the result shows
neither the words that matched nor a timestamp near them. For a product whose
headline is "timestamped citations", a two-minute-wide citation is the weakest
thing in the payload.

### 7.6 `cluster_gap=0` degrades a semantic hit into arbitrary cues
```
uv run --no-sync scripts/mcp_call.py call search '{"q":"dirty secret it does not exist","content_type":"transcript","limit":3,"cluster_gap":0}'
```
Three consecutive cues (`16172`, `16173`, `16174`) from `zkX03APVj0M`, ranked
1/2/3 by position, containing none of the query terms; rank 2 is "Thank you,
Sid, for speaking." Docs describe `cluster_gap=0` as "returns raw cues", which is
literally true and practically useless: the ranking within the chunk is
positional, not relevance-based.

### 7.7 `max_per_video` truncates the page instead of backfilling — and then lies about it
```
uv run --no-sync scripts/mcp_call.py call search '{"q":"what makes a good eval","content_type":"transcript","limit":6,"max_per_video":1}'
  → Results: 3/3 (no more results)
uv run --no-sync scripts/mcp_call.py call search '{"q":"what makes a good eval","content_type":"transcript","limit":6}'
  → Results: 6/~30+ (use offset=6 for more)
```
With `max_per_video=1` the caller asked for 6 and got 3, and the payload asserts
**"no more results"** — but the second call proves ≥30 candidates exist and the
corpus certainly has more than 3 videos discussing evals. The diversity cap is
applied to the already-selected page rather than used to pull deeper into the
candidate CTE, and `has_more` is computed after the cap. Same shape on
`{"q":"MCP","limit":5,"max_per_video":1}` → 3/3 with `Legs: 6 · 6 · 6`.

### 7.8 The frame leg's `approx_total` is just `limit + 1`
```
call search '{"q":"a terminal window with code","content_type":"frame","limit":5}'
  → Results: 5/6 (use offset=5 for more)     Legs: … frame 6
call search '{"q":"a terminal window with code","content_type":"frame","limit":20}'
  → Results: 18/18 (no more results)         Legs: … frame 21
```
The first payload tells a caller there are about 6 matching frames. There are at
least 18. The bounded count probe (tool-surface §3.4) evidently doesn't cover the
vector leg, so its "total" is whatever the `limit+1` fetch returned. A dashboard
that prints "6 results" from the first call is printing a wrong number.

### 7.9 Paging past the end returns a payload with no content and no guidance
```
uv run --no-sync scripts/mcp_call.py call search '{"q":"evals","limit":3,"offset":200}'
```
Output is three lines — `Results: 0/200 (no more results)`, the query echo,
`Legs: transcript 204 · ocr 119 · frame 204` — then two blank lines and nothing
else. No `next:`, no `data_status`, no "you are past the last page, try
offset=0". A genuinely-empty search (`q="vuln strcpy"`) prints a full, helpful
empty-state block; over-paging prints strictly less. Also note the leg counts
scale with `offset+limit`: at `offset=200` each leg fetched ~204 candidates and
discarded all of them.

### 7.10 Empty-result copy claims every leg was queried when it wasn't
```
uv run --no-sync scripts/mcp_call.py call search '{"q":"vuln strcpy","content_type":"ocr","limit":3}'
  → "Every leg was queried and none of them matched."
```
Only the OCR leg was queried — the caller asked for exactly one leg. The line is
reassuring and false, and in this specific case it is actively misleading: the
terms *are* on the slide, on two different lines (§0). This is the empty state
where a `note:` about single-line matching would save the user.

### 7.11 `min_chars` drops the OCR leg silently while announcing the frame leg
```
uv run --no-sync scripts/mcp_call.py call search '{"q":"evals","min_chars":500,"limit":3}'
  → note: min_chars/max_chars are text filters — the frame leg was not queried for this call.
  → Legs: transcript 1 · ocr 0 · frame 0
```
The `note:` is correct and good. But `ocr 0` is not "nothing matched" — it is
"`min_chars` is measured against a single OCR *line*, and almost no line is 500
chars". tool-surface §4.1 says the OCR segment is the whole frame. `all` means
all, and here one leg was effectively nullified by a filter with no note.

### 7.12 Frame-route URLs are unauthenticated (expected in demo, flag for the dashboard)
`get-frames` prints, correctly and prominently: *"URLs do not expire and are not
signed: this server runs with auth disabled, so the frame route is open to
anyone who can reach it."* Correct behaviour and excellent copy — recorded only
so nobody points the public dashboard at a build in this mode by accident.

### 7.13 Minor: `get-frames` does not preserve request order
```
uv run --no-sync scripts/mcp_call.py call get-frames '{"frame_ids":["Sir59K8ZDPU-00044","9HbzAWnKbo4-00040","O72p-rBb2bA-00028"]}'
```
Returns `Sir59K8ZDPU-00044`, `O72p-rBb2bA-00028`, `9HbzAWnKbo4-00040`. Harmless
for a model, awkward for a UI that lays out a strip of frames in the order it
asked for.

### 7.14 Things that worked exactly as specified (recorded so they don't regress)
- `E_FEATURE_DISABLED` on `speaker=` — names the parameter, names the env var.
- `E_UNKNOWN_FRAME` on `Sir59K8ZDPU-09999` — states the valid ordinal range
  `00000-00050` and says "never construct one".
- Unknown parameters (`nonsense: true`, `maxtextchars`) dropped silently, exactly
  as the guide warns.
- `list-videos` TSV with the `tof` coverage column: dense, scannable, no waste.
- `corpus-summary` `data_status: indexing` surfaced correctly mid-session.
- No `429` was observed at ~1 call/1.2 s sustained over ~45 calls.

---

## 8. Suggested chip set for the dashboard (12, ordered)

| # | Chip label (what the visitor sees) | Call | Channel |
|---|---|---|---|
| 1 | reward hacking | `search q="reward hacking" content_type=transcript limit=5` | spoken |
| 2 | owl:FunctionalProperty | `search q="owl:FunctionalProperty" content_type=ocr limit=3` | on-screen |
| 3 | architecture diagram with boxes and arrows | `search q="…" content_type=frame limit=5` | visual |
| 4 | slop | `search q="slop" limit=6 max_per_video=1` | all three, fused |
| 5 | guardrail_safety_check | `search q="guardrail_safety_check" content_type=ocr limit=3` | on-screen |
| 6 | a terminal window with code | `search q="…" content_type=frame limit=5` | visual |
| 7 | benchmark contamination | `search q="benchmark contamination training data leaked" content_type=transcript max_per_video=1 limit=6` | cross-video |
| 8 | gold commit | `search q="gold commit" content_type=ocr limit=3` | on-screen |
| 9 | forward deployed engineering (8 talks) | `list-videos q="forward deployed" limit=10` | browse |
| 10 | the entire internet runs on small towns in Bavaria | `search q="small towns in Bavaria" content_type=transcript limit=3` | spoken |
| 11 | Vending-Bench, chapter by chapter | `video-summary video_id="cO8qC6HBuBg"` | summary |
| 12 | 145x less training compute | `search q="145x less training compute" content_type=transcript limit=3` | spoken |

Chips 2 + 1.2 (`functional property disjoint`) should sit next to each other:
same video, same minute, one found by reading, one by listening.

---

## 9. Field test — Sonnet agents (2026-08-09)

Author: field-test evaluator, session `peppy-wibbling-moler`. Append-only.

**Method.** Four Sonnet subagents drove the live stack (`mcp :8100`,
demo/readonly) over the real MCP protocol via `scripts/mcp_call.py`, two at a
time, ~1 call/2.5 s. None of them was told anything about §7, about the
migration-0002 gap, or which queries were expected to misbehave. Each was
briefed only as "you are testing a product; note everything confusing, broken,
or worse than it should be, with the exact call."

- **tourist** (13 calls) — tool list + `vidtheque://guide` only, free
  exploration, follow one citation chain to a frame.
- **researcher** (13 calls) — 8 of §1–§4's verified queries, unmarked, judged
  against the promise each makes, plus a drill-down.
- **synthesist** (21 calls) — answered §4.1 and §4.2 properly, with citations,
  as an ask-mode agent would.
- **adversary** (37 calls) — weird-but-legal parameters. No auth probing, no
  writes, no flooding. No `429` at ~1 call/2.5 s with two agents concurrent.

Every finding below was re-run by hand by the evaluator; the reproducers are
the evaluator's own, not the agents'.

### 9.1 CONFIRMED bugs, new to §7

#### 9.1.1 `get-frames` silently discards ids past `limit` — **highest impact**

```
call get-frames '{"frame_ids":["Sir59K8ZDPU-00044","9HbzAWnKbo4-00040","O72p-rBb2bA-00028","Yk87oUPVaxU-00013","RjfbvDXpFls-00031"]}'
  → Frames: 3/3          structured: "failed": []
call get-frames '{... same five ...,"limit":12}'
  → Frames: 5/5
```

Five valid ids, five real frames. The default `limit=3` slices `frame_ids` in
`tools/frames.py::run` (`rows, failures = await _by_ids(deps, frame_ids[:limit])`)
**before** validation, so the last two are neither fetched nor reported: they do
not appear in `frames`, they do not appear in `failed`, and the header prints
`3/3` — the denominator says "you got all of them". `frame_ids` accepts up to 12
ids while `limit` defaults to 3, so any caller passing 4+ ids loses data silently.
The same slice explains the adversary's "junk ids vanished with no error": a
malformed id in position 4+ is never parsed, so the good `failed:` message that
fires for positions 1–3 never fires for it. §7.13 missed this because its
reproducer used exactly three ids.

Severity: high. This is the last step of both flagship flows (§5.1, §5.2) and the
call a dashboard makes to lay out a strip of frames.

#### 9.1.2 The citation timestamp moves when you change `limit`

```
call search '{"q":"agents","limit":3}'  → LZuWZRze3MU 3:38–3:52 · ?t=216 · cues 1535-1537 · score 0.0310
call search '{"q":"agents","limit":5}'  → LZuWZRze3MU 3:04–3:52 · ?t=182 · cues 1531-1537 · score 0.0310
```

Same query, same rank, same hit, same score — **the deep link moves 34 seconds**.
`fetch_n = offset + limit` is passed to each leg, so a larger page pulls more raw
cues into the same cluster and the cluster's *start* (which is what the link
points at, §7.5) slides earlier. This is §7.5's real root cause, and it is worse
than §7.5 states: the citation is a function of the page size the caller happened
to ask for.

#### 9.1.3 Rank 1 changes with `limit` for the same query

```
call search '{"q":"reward hacking","limit":3}'
  → 1: [transcript] -npY6XjM8CQ 5:11–5:56 score 0.0308
call search '{"q":"reward hacking","limit":50}'
  → 1: [transcript+ocr] (different video) score 0.0457
```

`_dedup_ocr_against_transcript` awards the `[transcript+ocr]` score bonus only
when both the transcript hit and the OCR hit land inside the fetched prefix, and
the prefix is `offset+limit`. So fusion bonuses — the single largest score
differentiator in the payload, 0.0457 vs 0.0308 — appear and disappear with page
size. A visitor who clicks "show more" on chip 1 does not get more of the same
list; they get a different list with a different winner.

#### 9.1.4 `data_status: indexing` from five stale jobs, contradicted twice in one session

```
resource vidtheque://context     → "active_jobs": 5, "data_status": "indexing"
call corpus-summary '{}'         → data_status: indexing   /   0 videos currently indexing
call search '{"q":"🔥","limit":3}' → data_status: ok (… index fresh)
call job-status '{}'             → 5 queued, 0/10 items, progress 20-40%
```

Five jobs are stuck `queued` with non-zero progress and zero items; no video is
in `index_state='indexing'`. `queries.gaps` counts two different things
(`active_jobs` = rows in `jobs`, `indexing` = videos), and `corpus-summary`'s
headline `data_status` is driven by the first while its own Gaps block prints the
second. Net effect on demo day: **the first call anyone makes says the corpus is
mid-index**, the line below says nothing is indexing, and `search`'s empty state
says the index is fresh. Three answers, one session. Clearing the stale job rows
fixes the symptom; the two counters still need one name each.

#### 9.1.5 The empty state contradicts its own `note:`

```
call search '{"q":"🔥","limit":3}'
  → note: no word of this query occurs anywhere in the corpus, so the semantic
    (nearest-neighbour) legs were not queried …
  → Every leg was queried and none of them matched.
```

§7.10 recorded this line as false when the caller pins one leg. It is also false
at `content_type=all`, and here it directly contradicts a `note:` four lines
above it in the same payload. The constant `reason=` in `_empty_result` should be
derived from `legs`, not hard-coded.

#### 9.1.6 The pagination "total" is a function of `limit`, and over-paging invents one

```
call search '{"q":"reward hacking","limit":3}'   → Results: 3/~40+
call search '{"q":"reward hacking","limit":50}'  → Results: 50/~130+
call search '{"q":"evals","limit":3,"offset":200}'
  → Results: 0/200 (no more results)   structured: approx_total 556, has_more false
```

`probe_*` uses `ceiling = offset + limit + headroom(30)` and the three legs'
capped counts are summed, so the number the guide explicitly teaches callers to
read ("Read the pagination line … tells you your next call") scales with the page
you asked for. §7.8 caught the frame-leg special case (`limit + 1`); the general
case is worse, because the probe also runs *before* dedup and the per-video cap,
so it over-counts what paging can actually deliver. At `offset=200` the text
prints `0/200` — a "total" equal to the offset — while structured content says
556 and `has_more: false`. Three numbers, one payload, none of them the answer.

#### 9.1.7 `expires_at: 0` reads as "expired in 1970"

`get-frames` prints the correct prose ("URLs do not expire and are not signed…")
and returns `"expires_at": 0` in structured content for every frame. Any
programmatic consumer doing `now > expires_at` treats every frame URL as expired.
`null` is the honest encoding of "never". Flagged by the tourist without reading
any source.

#### 9.1.8 Demo mode hides the write tools; the copy still recommends them

`tools/list` in demo/readonly returns 7 tools — `index-video` and `tag-video` are
correctly absent (`public/readonly.py`). But `vidtheque://guide` still says
"Adding to the library: index-video → job-status", `errors.unknown_video`'s
`next:` is `index-video url="https://youtu.be/<id>"`, and `job-status` with no
arguments ends on `next: index-video url="…" force_reindex=true to retry a failed
job.` Every dead end in the demo points at a tool the demo does not expose.

#### 9.1.9 Smaller, all first-hand

- **`pagination.limit: 0`** when a *corpus filter* matches nothing
  (`{"q":"agents","channel":"nonexistent","limit":3}`) — the early return in
  `_empty_result` defaults `limit`/`offset` to 0. The same zero-result shape from
  an unmatched query correctly echoes `limit: 3`. The empty-state `Query:` line
  also never echoes the filters that caused the emptiness, while the body says
  "No indexed video matched the filters".
- **`format="tsv"` accepts unknown `fields`**:
  `{"format":"tsv","fields":"nonexistent_field,video_id"}` prints a column headed
  `nonexistent_field` with every cell blank, no error, no note — while
  `order="bogus"` in the same tool returns a clean `E_BAD_PARAM` listing the valid
  values.
- **`Tags (top 0 of 1)`** — `tag_count` returns 1, `tag_rollup` over the video pool
  returns 0. There is one tag in the DB attached to nothing. Every tag surface
  (corpus-summary, list-videos column, video-summary, `vidtheque://corpus`) is
  empty across all 75 videos while `vidtheque://context` advertises six
  namespaces; the tourist invented `tag=` (the real parameter is `tags=`) and had
  it silently dropped.
- **The `Query:` echo is never truncated** — a 512-char query is reprinted in full
  in the header of every page, against the token-discipline invariant.

### 9.2 CONFIRMED, already in §7

| §7 | Reproduced by | Note |
|---|---|---|
| 7.1 RRF ties, exact match loses | researcher, `CVE-2026-22812` → correct OCR hit at **rank 3**, behind a frame hit with no textual relation (verified by drill-down: the frame is an FDE-headlines collage) | Mechanism added: the tiebreak is `_sort_key`, whose first element is `public_id` — ties are broken **alphabetically by video id**, so ids starting `-`/digits win systematically |
| 7.2 `max_text_chars=0` on OCR | not re-run; code confirms intent (`row["text"] if max_text_chars == 0`) | Blocked by 0002, not a code bug |
| 7.3 `get-frames` truncation marker | evaluator, every frame in 9.1.1 | Now **four** sources: the marker says pass `max_text_chars=0`; the tool description says "capped at 300 chars/frame with no opt-out"; the guide says "There is no `max_text_chars` on `get-frames`"; tool-surface §4.7 documents the mismatch |
| 7.4 one frame, several slots | researcher (`gold commit`, `annotations_to_evals.py`) | 0002 |
| 7.5 link at cluster start | synthesist, unprompted: `tJFjeMBKbIY` link `?t=1137`, the quoted airline story starts at **1164 s** — 27 s of scrubbing | See 9.1.2 for the root cause |
| 7.7 `max_per_video` truncates + lies | researcher Q4: `limit=6, max_per_video=1` → `Results: 3/3 (no more results)` with `Legs: transcript 7` and `approx_total: 36` | |
| 7.8 frame `approx_total` = `limit+1` | subsumed by 9.1.6 | |
| 7.9 over-paging prints nothing useful | evaluator, verbatim | |
| 7.10 "Every leg was queried" | widened, see 9.1.5 | |
| 7.12 unsigned frame URLs | tourist + researcher both quoted the warning back approvingly | Working as intended |
| 7.13 `get-frames` order | evaluator: request `Sir…,9Hbz…,O72p…,Yk87…,Rjfb…` → returned `Rjfb…,Sir…,Yk87…,O72p…,9Hbz…` | |
| §0 multi-line OCR | **neither** unbriefed agent hit it | The single-line-matchable query set in §2 is doing its job |

### 9.3 UX friction — not bugs, but they cost demo quality

1. **The 2 s deep-link lead is undocumented and reads as a bug.** Two agents
   independently flagged that `start: 311.28` sits next to `?t=309`. It is
   `deeplink_lead_s=2`, deliberate and good. Nothing in the guide or the payload
   says so, so a careful agent treats the server as inconsistent with itself.
   One clause in the guide fixes it.
2. **The frame leg is noisy on descriptive queries.** The researcher scored
   `a terminal window with code` at **1 of 5** — ranks 2–5 were a markdown test
   report, a browser on localhost, a leaderboard slide. §3.1 called the same set
   "on-theme"; both readings are defensible, which is the problem: the chip label
   promises "terminal with code" and the result is "dense monospace-ish screen".
   `frame_max_distance=1.0` is permissive enough that the leg never declines to
   answer. Either tighten it or label the chip for what it does.
3. **No query-quality signal.** The synthesist's `postmortem on call paged 3am`
   returned five OCR hits on video-player chrome ("PAUSE/REPLAY/REC", "Ask
   Gemini") inside a normal-looking `5/~40+` frame. *"A bad query looks
   structurally identical to a good one."*
4. **`video-summary` can return `Chapters (0 of 0)`** with no explanation, on a
   video the guide's step 3 sends you to. The synthesist had to fall back to
   `get-segment-context` walking, which is exactly what step 3 exists to prevent.
5. **`list-videos`' `channel` column is the conference, not the speaker's
   company** — §4.3's "8 companies" answer is only recoverable by parsing titles.
6. **Silent clamps are inconsistent with the server's own best behaviour.**
   `limit=0/-5 → 1`, `limit=9999 → 50`, `max_per_video=999 → 20` (visible only in
   the echoed `Query:` line), `max_text_chars=1 → 120`: none carries a `note:`.
   `get-segment-context t=99999` does it right — *"note: t was past the end of the
   video and was clamped to 1274s."* That pattern already exists; it just is not
   applied.

### 9.4 False alarms — recorded so they are not re-filed

- *"The link lands 2 s before the sentence, not mid-sentence"* — that is
  `deeplink_lead_s`, by design (see 9.3.1). Filed by two agents.
- *"Negative `offset` produces an unexplained slice"* — `offset=-3` clamps to 0
  correctly. The slice looked wrong because of 9.1.2, not because of the offset.
- *"`window=99999` clamps with no warning"* — the effective window is printed:
  `Window: 13:56-23:56 (t=1136 ±300s)`. Not a `note:`, but not silent.
- *"Unsigned, non-expiring frame URLs"* — correct and prominently disclosed in
  demo mode (§7.12).
- *"`reward hacking` spans 4 videos, not the 5 talks §1.1 claims"* — the
  researcher is right about the count, but §1.1 itself lists only four ids while
  saying five. A doc arithmetic slip, not a product regression.
- *"`limit=0` returns 1 result"* — documented silent-clamp behaviour.

### 9.5 Ranked for demo day, and the three fixes worth making first

Ranked by what a visitor or an evaluating agent actually hits:

1. 9.1.4 stale `data_status: indexing` — the *first* call in any session.
2. 9.1.1 `get-frames` silent drop — the last call in both flagship flows.
3. 9.1.2 / 9.1.3 citation and rank-1 instability under `limit`.
4. 9.1.6 pagination totals that move with the page.
5. §7.1 exact match losing the RRF tiebreak (alphabetical by video id).
6. 9.1.5 empty state that contradicts its own note.
7. 9.1.8 hints pointing at tools demo mode does not expose.

**Fix 1 — `get-frames`: validate first, slice second, and say what you dropped.**
Move the `limit` cut after `_by_ids`, and either report over-limit ids in
`failed:` or raise `E_BAD_PARAM` naming the cap. Silent data loss on the money
shot is the one failure a demo cannot absorb.

**Fix 2 — make the citation independent of the page.** Anchor the deep link to
the *matched* cue rather than the cluster start, and stop deriving cluster
membership from `offset+limit` (cluster over a fixed candidate window, then page).
One change closes 9.1.2, 9.1.3 and §7.5, and it is the difference between
"timestamped citations" being a claim and being a guarantee.

**Fix 3 — one honest number per payload.** Clear the five stale job rows before
the demo; derive `_empty_result`'s reason line from `legs`; encode "never
expires" as `null`; and either make the probe total page-independent or stop
printing it as a total. These are small, and together they are the difference
between a server an agent trusts and one it double-checks.
