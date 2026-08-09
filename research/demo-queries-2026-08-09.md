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

### 1.1 `reward hacking` — **exact term, defined out loud, five talks deep**
Top hit: *When Will The Benchmaxxing Plague End?* (`-npY6XjM8CQ`) 5:11–5:56.
Result #3 is the money shot: a **single cue** at 5:57, `cue 4774`,
`https://youtu.be/-npY6XjM8CQ?t=355` —
> "Reward hacking is basically when a model finds a lazy and creative way to
> meet the letter of the law but not the spirit."

Why it demos: single-cue clusters make the deep link land *mid-sentence on the
exact moment*, which is the whole promise. Results span 5 talks
(`-npY6XjM8CQ`, `AQv3qRCG6Gw`, `2aS7aKoXn64`, `31GUkCBD-Uc`).

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
