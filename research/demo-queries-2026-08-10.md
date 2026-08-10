# Demo queries — harvested from the terra eval (2026-08-10)

Author: evaluation orchestrator, session `peppy-wibbling-moler`. Append-only
doc: add new sections, don't rewrite these findings. This is a **new dated
file**, not an edit of `research/demo-queries-2026-08-09.md`.

**What this is.** Query/result pairs harvested while running the terra-agent
eval (`research/mcp-eval-terra-2026-08-10.md`) — kept only where the payload
*itself* makes the pitch in `docs/design/positioning.md`: **the sentence, the
slide, and the second it happened.** Nine pairs kept, grouped by what they
demonstrate, best first. Near-misses — good questions whose payload presentation
undercuts them — are §6, and each one doubles as an eval finding.

**How each pair was verified.** Re-run through `scripts/mcp_call.py` against the
live server (`127.0.0.1:8100`), then checked at click level: the cited second
pulled back verbatim via `get-segment-context max_text_chars=0`, and every frame
receipt fetched over HTTP and *looked at*. A pair is not in this list unless the
receipt lands where it claims.

**Corpus at harvest time.** 153–154 videos · ~53 h · ~33.7k cues · ~6.2k
keyframes · AI Engineer, 2026-04-07 → 2026-08-09. Counts drift between pairs
because a background index batch was running; ids and timestamps do not.

---

## 1. The pitch each group makes

Positioning, §"The three pillars": *Follow the builders* · *Your agent watched
it* · *Receipts, always* — "what separates injected knowledge from a
hallucinated summary is the verbatim quote, the real slide with its OCR box, and
the `youtu.be/…?t=` link that lands on the second."

| § | Group | Pillar it proves |
|---|---|---|
| 2 | Never spoken — it was only on the slide | Receipts: *the slide* |
| 3 | Cross-video synthesis with verified quotes | Your agent watched it |
| 4 | The receipt lands on the second | Receipts: *the second* |
| 5 | It refuses rather than invents | Receipts, by negation |

---

## 2. Never spoken — it was only on the slide

### 2.1 ★ The price table nobody read out loud

**The ask.** "What does context actually cost?"

**The call.**

```json
search {"q": "context window costs money tokens", "content_type": "ocr",
        "limit": 3, "max_text_chars": 0}
```

**The payload (verbatim).**

```
Results: 1/1 (no more results)
Query: "context window costs money tokens" · content_type=ocr · order=relevance · max_per_video=3
Legs: transcript 0 · ocr 1 · frame 0 (fused, RRF k=60)
[ocr] Agentic Engineering: Working With AI, Not Just Using It — Brendan O'Leary — AI Engineer (BEKc4P87XKo)
  5:15 · https://youtu.be/BEKc4P87XKo?t=313
  Problem 1: Context is expensive | Every token you add to the context window costs money - and the cost scales fast. | 10K | 200K | 600K | 1M+ | tokens | tokens | tokens | tokens | ~$0.03 | ~$0.60 | $1.50+ | More context ≠ better results | You're paying for noise, not signal | And even if cost weren't an issue - more context actually makes the model worse.
  frame BEKc4P87XKo-00010 · score 0.0164
```

**Why it demonstrates the positioning.** `Legs: transcript 0 · ocr 1` is the
whole story in one line. **Not one of those numbers is spoken.** I pulled the
transcript window at `max_text_chars=0` and the speaker's entire treatment of
this slide is *"First, context is expensive, right? Every token you add into the
context is going to add cost … And that can add up pretty quickly."* — no
figures at all. A transcript-only product cannot answer "what does context
cost"; this is *"every line that crossed the screen"* earning its place. One
result, no noise, `1/1 (no more results)`.

**And then the image, which is the punchline.**

```json
get-frames {"frame_ids": ["BEKc4P87XKo-00010"], "max_text_chars": 0}
```

```
Frames: 1/1
BEKc4P87XKo-00010 · 5:15 · https://youtu.be/BEKc4P87XKo?t=313
  image: http://127.0.0.1:8100/frames/BEKc4P87XKo-00010.jpg?w=512&q=75
```

**Verification.** `curl` → `200 image/jpeg 15679`. I opened it: four columns,
`10K → ~$0.03`, `200K → ~$0.60`, `600K → $1.50+`, and **`1M+ → 💸💸💸`**. The
flat OCR join gives four tiers and only three prices, and the reader cannot tell
which price sits under which column — the fourth cell is an emoji OCR could not
render. This is the guide's own escalate-to-the-image rule paying off live: the
text gets you there, *the picture is the only place the layout survives.* Best
single demo in this harvest — it sells the OCR channel and `get-frames` in one
breath.

### 2.2 ★ The quotable sentence exists only on the slide

**The ask.** "Who's quoting Karpathy on context engineering, and what exactly did
he say?"

**The call.**

```json
search {"q": "context engineering", "limit": 5, "max_text_chars": 300}
```

**The payload (rank 4, verbatim).**

```
[ocr] Agentic Engineering: Working With AI, Not Just Using It — Brendan O'Leary — AI Engineer (BEKc4P87XKo)
  4:42 · https://youtu.be/BEKc4P87XKo?t=280
  Context Engineering | What you put in determines what you get out - and | it costs more than you think | "Context engineering is the delicate art and science of filling the context window | with just the right information for the next step." - Andrej Karpathy
  frame BEKc4P87XKo-00009 · score 0.0164
```

**Why it demonstrates the positioning.** The slide carries the sentence
verbatim. What the speaker actually says, at `[4:37]`, is:

> "And here Karpathy says, you know, context engineering is a delicate art and
> science of, you know, filling the context window with just what needs to
> happen for the agent to have the right context for the right iteration for the
> next step."

Two fillers, a reshaped clause, no quotation marks. **The quotable text is on
the slide; the transcript has only the paraphrase.** An agent asked for the
exact wording gets it from the OCR channel and nowhere else — "spoken once, a
slide for eight seconds, gone", recovered.

**Verification.** Re-ran the search (rank 4, same frame id, same `?t=280`), then
`get-segment-context video_id="BEKc4P87XKo" t=282.43 window=45 max_text_chars=0`
to read the spoken version. Both texts as quoted.

### 2.3 A slide term the risk audience searches for, never said aloud

**The call.**

```json
search {"q": "MCP dangerous", "limit": 5, "max_text_chars": 600}
```

**The payload (rank 1).**

```
[ocr+frame] Agentic Development Security — Ezra Tanzer, Snyk — AI Engineer (cgimkNGNjvU)
  8:54 · https://youtu.be/cgimkNGNjvU?t=532
  … | MCP server risk assessment | … | Destructive capabilities | Dangerous words | Not detected | snyk | …
  frame cgimkNGNjvU-00024 · score 0.0178
```

**Why it demonstrates the positioning.** `[ocr+frame]` provenance — two channels
agreeing on one keyframe — on a product screenshot whose category labels
("MCP server risk assessment", "Dangerous words", "Destructive capabilities")
are never spoken; the presenter talks *around* the screenshot. Good for the
security-track beat.

**Caveat before you demo it: this payload needs a crop.** The OCR line leads
with the presenter's browser chrome and a Google Slides URL, and the surrounding
tokens are mangled. Demo the *frame*, quote the three labels, and do not put the
raw OCR string on a slide. See §6.1.

**Verification.** Re-ran; same rank 1, same frame id.

---

## 3. Cross-video synthesis with verified quotes

### 3.1 ★ "Where do the speakers disagree about LLM-as-a-judge?"

**The ask** — the flagship agent-consumer question, and the one that cannot be
answered by a search box.

**The calls** (this is a *flow*, and the flow is the demo): `corpus-summary` →
`search {"q":"human annotation calibrate LLM judge","content_type":"transcript","limit":20,"max_per_video":3}`
→ three `get-segment-context` calls at the hits, `window=60..90`.

**The result — three talks, two directly contradicting, every quote verbatim.**

| Speaker | Claim | Receipt |
|---|---|---|
| Prof. David Brumley, Bugcrowd (`ZFxh7sqbUZo`) | *"It also means that there's no LLM as a judge because let's face it, **you can't trust the LLM that you're teaching to be a judge**."* | `[16:23]` → `https://youtu.be/ZFxh7sqbUZo?t=982` |
| Nick Heiner, Surge AI (`-npY6XjM8CQ`) | *"And **LLM as a judge doesn't really work either because LLMs don't have good taste in writing**."* — replaced by "a workforce of thousands of professional writers … doing blind model comparisons" | `[16:01]` → `https://youtu.be/-npY6XjM8CQ?t=960` |
| Maor Bril, Character.ai (`b_PmGocP4rc`) | Uses LLM-as-a-judge anyway: *"we started using LLM as a judge for everything … The problem with them is that, A, they're slow, B, they're only as good as your prompts"* — kept, but calibrated against human annotation | `[3:59]`/`[4:47]` → `https://youtu.be/b_PmGocP4rc?t=285` |

**Why it demonstrates the positioning.** Pillar 2, exactly: *"Agents consume the
corpus mid-task: ask for the SOTA, get what was said on stage three weeks ago."*
Three speakers, three talks, a real disagreement with a real synthesis
(objective/verifiable tasks can drop both human labels and LLM judges;
subjective quality cannot), and every claim clickable. No single video contains
this answer.

**Verification — this is the one I checked hardest**, because a fabricated quote
here would be the worst possible demo. Both flagged quotes pulled back verbatim
from `get-segment-context … max_text_chars=0`: Brumley at cue-window
`5052-5087`, the sentence sitting at `[16:23]` against a link of `?t=982`
(16:22, the 2 s lead); Heiner at cue `4860`, `start: 961.632`, against `?t=960`.
Both land on the second.

**Demo note.** Drive it with the `get-segment-context` step visible — the
verbatim window on screen next to the claim is what makes the receipt feel
earned. And quote from the anchor line: `get-segment-context` prints one link
for the whole window, so pick the moment the link points at (see §6.3).

### 3.2 The number you would only have by watching nine minutes in

**The call.**

```json
get-segment-context {"video_id": "cgimkNGNjvU", "t": 534.92, "window": 120,
                     "max_text_chars": 0}
```

**The payload (verbatim excerpt).**

```
cgimkNGNjvU · Agentic Development Security — Ezra Tanzer, Snyk — AI Engineer
Chapter: "Adoption data: who is running MCP servers and skills" (8:11-9:27)
Window: 6:54-10:54 (t=534 ±120s) · https://youtu.be/cgimkNGNjvU?t=532
…
[7:24] And in an audit that we did of nearly 4,000 skills on Claw Hub, over one in eight had a critical severity issue.
[7:30] And we actually found 76 malicious payloads in that subset.
…
[8:31] … from the average developer, we saw that more than half were using MCP servers and a fifth were leveraging skills.
[8:39] Beyond just adoption, one in 12 developers in this group had an MCP server where there was either a higher critical severity finding identified in that MCP server itself.
(cues 26162-26199 · 4,032 chars, under the unbounded budget; window bound first)
```

**Why it demonstrates the positioning.** *"You don't have time to watch
everything. Thanks to vidtheque, your agent does."* These are four hard numbers
— 4,000 skills, one in eight, 76 payloads, one in 12 — buried 7–9 minutes into a
28-minute talk, with a named enclosing chapter and a link. This is what "solid,
timestamped knowledge" looks like when the answer is a statistic rather than an
opinion.

**Verification.** The naive terra consumer (`p3-naive`) reached the same passage
unprompted and reported "one in 12 developers"; I re-pulled the window and
confirmed the figure and its wording at cue 26183 (`start: 519.701`).

---

## 4. The receipt lands on the second

### 4.1 A 22-second citation inside a 46-minute talk

**The call.**

```json
search {"q": "context engineering", "limit": 5, "max_text_chars": 300}
```

**The payload (rank 1, verbatim).**

```
[transcript] Harness Engineering: How to Build Software When Humans Steer, Agents Execute — Ryan Lopopolo, OpenAI — AI Engineer (am_oeAoUhew)
  43:26–43:48 · match at 43:28 · https://youtu.be/am_oeAoUhew?t=2606
  Does context still matter? How do people do engineering, harness engineering, context engineering? What does the future look like? …
  cues 20738-20741 · score 0.0182
```

**Why it demonstrates the positioning.** `43:26–43:48 · match at 43:28` in a
46-minute talk. The segment is the passage, the **anchor** is the second that
matched, and the link points at the anchor minus the 2 s lead — the whole
2026-08-09 anchor amendment, visible in one rendered line. Say the number out
loud in a demo: *twenty-two seconds out of forty-six minutes, and it tells you
which second.*

**Verification.** `structuredContent`: `start: 2606.57`, `match_start: 2608.15`,
`match_cue_id: 20739`, `link: …?t=2606` = `floor(2608.15 − 2)`. Consistent, and
stable across `limit=3`/`5`/`10`.

### 4.2 The frame receipt is a real, fetchable image

**The call.** `get-frames {"frame_ids": ["BEKc4P87XKo-00010"]}` (see §2.1).

**Why it demonstrates the positioning.** The footer is worth showing on its own,
because it is the honesty the dashboard's voice is built on:

```
URLs do not expire and are not signed: this server runs with auth disabled, so
the frame route is open to anyone who can reach it.
```

**Verification.** `curl -w '%{http_code} %{content_type} %{size_download}'` →
`200 image/jpeg 15679`, and the JPEG renders as the slide the OCR described.
`expires_at: null` in the structured payload — not `0` — matching the prose
exactly.

---

## 5. It refuses rather than invents

### 5.1 ★ "Find the FlashAttention-4 benchmark"

**The call.**

```json
search {"q": "FlashAttention-4", "content_type": "all", "limit": 20}
```

**The payload (verbatim).**

```
Results: 0/0
Query: "FlashAttention-4" · content_type=all
note: no word of this query occurs anywhere in the corpus, so the semantic (nearest-neighbour) legs were not queried — they would have returned their k nearest vectors regardless.

data_status: indexing (corpus has 153 videos, newest published 2026-08-09, …)
The transcript and ocr legs were queried and nothing matched; the frame leg did not run, for the reason in the note above.
next: retry with fewer filters, or list-videos to see what is indexed.
```

**Why it demonstrates the positioning.** Pillar 3 by negation. Every competing
demo in this space answers *something*; this one says **nothing, and shows its
work** — which legs ran, which did not, and why. It is the single most
trust-building payload in the product, and it is why the independent consumer
`p5-absent` answered four impossible questions with four refusals instead of
four hallucinations.

**Verification.** Reproduced verbatim by the `p6-stress` consumer on a different
nonsense token (`"xylophonically"` → identical payload shape), and by hand.

### 5.2 "What did people say about agents before 2020?"

**The call.**

```json
search {"q": "agents", "content_type": "all", "limit": 10,
        "published_before": "2020-01-01"}
```

**The payload.**

```
Results: 0/0
Query: "agents" · content_type=all
data_status: indexing (corpus has 153 videos, newest published 2026-08-09, …)
No indexed video matched the filters, so no leg was queried.
next: retry with fewer filters, or list-videos to see what is indexed.
```

**Why it demonstrates the positioning.** `agents` matches half the corpus; the
*filter* is what empties it, and the payload says which — "no indexed video
matched the filters, so no leg was queried", distinct from "nothing matched".
Pairs well with §5.1 in a demo: the same `0/0` header with two different, true
explanations under it.

**Verification.** Run by `p5-absent`; the consumer combined it with the published
span from `corpus-summary` and concluded, correctly, that no pre-2020 video
exists to quote.

---

## 6. Near-misses — good questions, undercut by the payload

Each of these is also an eval finding; the cross-reference is to
`research/mcp-eval-terra-2026-08-10.md`.

### 6.1 The right slide, wrapped in browser chrome

`search {"q":"MCP dangerous"}` (§2.3) returns the correct top hit, and its OCR
line opens with:

```
AlEngineer | n/d/1IS752ccB1da4IU2KUh70I7OtnAaloLY674TamatoVY/edit?slide=id.g3ef66d87504_0_63#slide=id.g3ef66d87504_0_63 | ②☆ | 0 | World's Fair | /users/ezrato | …
```

A presenting-mode Google Slides URL, a browser tab strip, and mangled glyphs
ahead of the three words that matched. **Demoable via the frame, not via the
text.** Worth considering: a reading-order pass that drops runs matching
browser-chrome shapes (a bare URL with `/edit?`, tab-bar glyph soup) before the
OCR text is stored — the pixels keep it, the payload does not need it.

### 6.2 "Where does anyone discuss CUDA kernel occupancy?" — a confident answer to a question the corpus cannot answer

```
$ … call search '{"q":"CUDA kernel occupancy","limit":3,"max_text_chars":200}'
Results: 3/~330+ (use offset=3 for more)
Legs: transcript 400 · ocr 0 · frame 400
  [transcript] Self-Training Agents … Hugging Face (OV56RddyFuU) 5:48–6:03 · score 0.0164
  [transcript] Self-Training Agents … Hugging Face (OV56RddyFuU) 6:04–7:59 · score 0.0164
  [frame]      Self-Training Agents … Hugging Face (OV56RddyFuU) 16:22     · score 0.0164
3 of 3 results came from OV56RddyFuU (max_per_video=3 bound).
```

The honest answer is §5.1's `0/0`. Instead: "330+ results", three of them from
one unrelated talk, at identical scores. **Do not demo any query whose terms are
not in the corpus unless every term is absent** — the `no word of this query
occurs anywhere` gate is all-or-nothing, so a query with one familiar word
("kernel") loses the refusal. Root cause and fix in the eval doc §4.1; the
positioning cost is direct — this payload is a hallucinated summary with
receipts attached.

### 6.3 A perfect quote, an approximate link

`get-segment-context` prints twenty-plus timestamped transcript lines and
exactly one `youtu.be` link (the window anchor). The `p1-research` consumer,
building the §3.1 table, therefore cited two different moments — `[16:23]` and
`[15:56]` — with the *same* `?t=982`. The first is right to the second; the
second is 27 s early. For a live demo, quote only the anchor line. Fix in eval
doc §4.4: a `?t=` per printed cue.

### 6.4 A chapter link that is two seconds off the documented contract

`video-summary` prints `6:40 … ?t=400` where every other surface would print
`?t=398` (the documented `DEEPLINK_LEAD`). Harmless to watch, but the naive
consumer read the guide, "corrected" it to `?t=398` itself, and thereby invented
a timestamp it had been told never to invent. Eval doc §4.3 — three call sites
in `tools/library.py`.

---

## 7. The shortlist, if you have three minutes

1. **§2.1** — "what does context cost?" → one OCR hit, a price table nobody said
   out loud, then the frame that shows the cell OCR could not read. *The slide.*
2. **§3.1** — "where do speakers disagree about LLM-as-a-judge?" → three talks,
   two contradicting, both quotes verbatim at the second. *Your agent watched it.*
3. **§5.1** — "find the FlashAttention-4 benchmark" → `0/0`, and it names the
   legs that ran and the one it deliberately did not. *It will not make things up.*

---

# Round 2 — post-repair pairs (2026-08-10 night)

Everything in §§1–7 was harvested while **both embedding indexes were random
projections** (`research/embedding-random-init-2026-08-10.md`). The pairs above
survived that because they lean on the *lexical* legs, on OCR, or on refusal —
none of them needed the semantic legs to work. Tonight the weights load, the
whole corpus is re-embedded, and the ceilings are calibrated on the real space
(`research/vec-floor-calibration-2026-08-10.md` §6). This section is the harvest
that was **impossible before tonight**: queries with no keyword overlap at all,
where the vector leg is the only thing carrying the answer.

**Corpus at harvest.** 182 videos · 181 queryable · 64.0 h · 41,702 cues · 7,689
keyframes · AI Engineer, 2026-01-12 → 2026-08-09.

**How each pair was verified.** Same rule as §1: re-run through
`scripts/mcp_call.py` against the live server, the cited second pulled back
verbatim with `get-segment-context max_text_chars=0`, and every frame receipt
fetched over HTTP and *looked at*. The new tell to read in each payload is
`fts 0` — the lexical sub-leg found nothing, so the hit is the semantic leg's
alone. That number did not exist this morning (eval doc §4.2).

| § | Group | Pillar it proves |
|---|---|---|
| 8 | Paraphrase — no shared word with the answer | Your agent watched it |
| 9 | The slide is the only place the layout survives | Receipts: *the slide* |
| 10 | It refuses through the floor, not through a word list | Receipts, by negation |
| 11 | Precision, and the round-1 pairs re-checked | Follow the builders |

---

## 8. Paraphrase — the query and the answer share no words

### 8.1 ★★ "What happens when the machine grades its own homework?"

**The flagship of this harvest.** One plain-English question, zero keyword
overlap, and the top two results are two named practitioners who **disagree**.

**The call.**

```json
search {"q": "what happens when the machine grades its own homework",
        "content_type": "transcript", "limit": 4, "max_per_video": 1,
        "max_text_chars": 200}
```

**The payload (verbatim, ranks 1–2).**

```
Results: 4/101 (use offset=4 for more)
Query: "what happens when the machine grades its own homework" · content_type=transcript · order=relevance · max_per_video=1
Legs: transcript 250 (fts 0 · vec 379/800) · ocr 0 · frame 0 (fused, RRF k=60)
[transcript] Claude for Long-Horizon Tasks — Lance Martin, Anthropic — AI Engineer (9QebvrrY3KY)
  6:20–7:05 · https://youtu.be/9QebvrrY3KY?t=378
  So the second theme is use verifiers. And one of the problems that we've seen with Claude
  and other models in general is that when you ask them to do a bunch of work and then say,
  OK, grade your work,…
  score 0.0164
[transcript] Teaching AI to Find Real Vulnerabilities — Prof. David Brumley, Bugcrowd — AI Engineer (ZFxh7sqbUZo)
  7:22–7:59 · https://youtu.be/ZFxh7sqbUZo?t=440
  And so you want to standardize that with a vulnerable program. You need a grading oracle.
  Now, one of the things I think the previous talk was talking about was LLM as a judge is a
  reasonable thing. W…
  score 0.0161
```

**Read `fts 0`.** Not one word of that sentence — not "machine", not "homework",
not "grades" as written — has lexical footing anywhere in 41,702 cues. The
lexical leg contributed nothing. **The entire result is the semantic leg**, and it
put the right two talks at ranks 1 and 2.

**The disagreement, both sides pulled back verbatim.**

| Speaker | Position | Receipt |
|---|---|---|
| Lance Martin, Anthropic (`9QebvrrY3KY`) | Keep the judge, **isolate its context**: *"when you ask them to do a bunch of work and then say, OK, grade your work, if that same context is being used to both do the work and grade, you can get lots of odd artifacts and confabulation"* → *"it's quite effective to separate verification into a separate context window"* | `[6:25 ?t=383]` and `[7:00 ?t=418]` |
| Prof. David Brumley, Bugcrowd (`ZFxh7sqbUZo`) | **Drop the judge**: *"one of the things I think the previous talk was talking about was LLM as a judge is a reasonable thing. What we found in cybersecurity is that is flawed. The LLMs will always say they were successful hacking."* → a deterministic grading oracle | `[7:26 ?t=444]`, `[7:31 ?t=449]`, `[7:34 ?t=452]` |

**Why it demonstrates the positioning.** All three pillars in one payload, and
the second one especially: *"ask for the SOTA, get what was said on stage three
weeks ago."* A search box needs the user to already know the words "verifier",
"grading oracle" or "LLM-as-a-judge"; this consumer asked the question the way a
person asks it. And Brumley's *"the previous talk was talking about"* is the
demo's own punchline — **one speaker rebutting another speaker at the same
conference**, and the corpus holds both sides with clickable seconds.

**Verification.** `get-segment-context video_id="9QebvrrY3KY" t=381 window=45
max_text_chars=0` → cue 17214, `start: 385.329`, against a printed `?t=383` =
`floor(385) − 2`. `get-segment-context video_id="ZFxh7sqbUZo" t=445 window=45
max_text_chars=0` → the three Brumley lines exactly as quoted, at `?t=444/449/452`.
Both land on the second.

**Demo note.** Drive it as two `get-segment-context` calls side by side. The
per-line `?t=` (eval doc §4.4) is what makes this shippable — three sentences of
Brumley, three different links, none of them invented.

### 8.2 ★ "The model gets worse the more you paste into it"

**The call.**

```json
search {"q": "the model gets worse the more you paste into it",
        "limit": 4, "max_per_video": 1, "max_text_chars": 300}
```

**The payload (rank 1, verbatim).**

```
Results: 4/130 (use offset=4 for more)
Legs: transcript 333 (fts 0 · vec 593/800) · ocr 0 · frame 70 (vec 133/800) (fused, RRF k=60)
[transcript] Agentic Engineering: Working With AI, Not Just Using It — Brendan O'Leary — AI Engineer (BEKc4P87XKo)
  4:59–5:53 · https://youtu.be/BEKc4P87XKo?t=297
  Every token you add into the context is going to add cost because all of those things, that
  whole chat history is sent back in as input tokens every time that you send it. And that can
  add up pretty quickly. And the other key is that more context doesn't always mean better
  results. And in fact, it c…
```

**Why it demonstrates the positioning.** `fts 0` again — the speaker never says
"paste", never says "worse". He says *"more context doesn't always mean better
results"*. The paraphrase lands on the sentence anyway. **This is the same video
and the same two minutes as §2.1's price-table slide**, which makes it the best
two-beat demo in the file: ask it in plain English, get the spoken sentence
(§8.2), then ask what it *costs* and get the slide nobody read out loud (§2.1),
then open the image and find the cell OCR could not render.

**Verification.** Re-run at `limit=4`; same rank 1, same link. Cue window pulled
at `max_text_chars=0`; wording as quoted.

### 8.3 "Why does my assistant forget what I told it a minute ago?"

**The call.**

```json
search {"q": "why does my assistant forget what I told it a minute ago",
        "content_type": "transcript", "limit": 4, "max_per_video": 1,
        "max_text_chars": 200}
```

Rank 1 `Agents need more than a chat — Jacob Lauritzen, CTO Legora`
(`XNtkiQJ49Ps`) `0:16–1:28 · ?t=43`, *"It's going to forget everything. It's in
the context state."*; rank 3 `Your Agent Didn't Fail. Your Harness Did.`
(`BInpv7lGp1o`) — the talk whose whole thesis is silent memory loss.

**Demo it second, not first.** The answer is right, but the payload reads
`Legs: transcript 353 (fts 0 · vec 800)` — the relevance band kept **all 800**
nearest chunks, so this query's pool was never narrowed (eval doc §9.6). It is
a good answer from a wide net, where §8.1 is a good answer from a narrow one.

---

## 9. The slide is the only place the layout survives

### 9.1 ★★ The benchmark table OCR silently mis-columns

**The ask.** "Which model is fast enough for a voice agent?"

**The calls.**

```json
search {"q": "latency p95", "limit": 5, "max_per_video": 1}
get-frames {"frame_ids": ["hMlLw1LeIK8-00038"], "return": "url",
            "include_ocr": true, "max_text_chars": 0}
```

**The payload (verbatim).**

```
Frames: 1/1
hMlLw1LeIK8-00038 · 17:13 · https://youtu.be/hMlLw1LeIK8?t=1031
  image: http://127.0.0.1:8100/frames/hMlLw1LeIK8-00038.jpg?w=512&q=75
  ocr: "aws | Chintan Agrawal | 07 | LATENCY | LLM TTFT — the bottleneck. Target: < 700 ms. |
        P50 | P95 | Multi-turn | I< 700 ms taroet | nemotron-3-ultra | 529 ms | 529 ms | 655 ms |
        98.3% | gpt-4.1 | 536 ms | 536 ms | 1771 ms | 96.3% | gpt-5.4 (low) | 782 ms | 782 ms |
        1706 ms | 97.0% | AI Claude Haiku 4.5 | 637 ms | 637 ms | 1615 ms | 98.0% |
        G gemini-3.5-flash | 960 ms | 960 ms | 1588 ms | 99.0% | AI Claude Sonnet 4.6 | 850 ms |
        850 ms | 4126 ms | 100% | NOTE | TTFT alone isn't enough. …
        Source: aiewf-eval multi-turn benchmark (kwindla, 2026) — 30-turn conversations, 10 runs each."
URLs do not expire and are not signed: this server runs with auth disabled, so
the frame route is open to anyone who can reach it.
```

**Why it demonstrates the positioning, and why it is stronger than §2.1.** The
OCR is *not garbled here — it is complete, and it is wrong to read.* The header
says `P50 | P95 | Multi-turn`, and each row then carries four numbers. A reader
takes them as P50 / P95 / multi-turn: **nemotron-3-ultra P50 529 ms, P95 529 ms,
multi-turn 655 ms.**

I opened the image. The truth is a horizontal bar chart with a *bar label* on the
left and the numeric columns on the right, so the four numbers are
`[bar label 529 ms] [P50 529 ms] [P95 655 ms] [multi-turn score 98.3%]`. **The
real P95 is 655 ms, not 529 ms**, and the flat reading-order join duplicated the
bar label into the P50 column and shifted everything one place. Every row on the
slide is off by one the same way.

This is the guide's escalate-to-the-image rule at its sharpest: §2.1's OCR was
*visibly* incomplete (three prices for four tiers), so a careful reader knows to
look. Here the OCR is *plausible and complete and wrong*, and only the picture
settles it. Say it out loud in a demo: **"the text will tell you 529. The frame
tells you 655. That is why the frame is a receipt."**

**Verification.** `curl -w '%{http_code} %{content_type} %{size_download}'
'http://127.0.0.1:8100/frames/hMlLw1LeIK8-00038.jpg?w=1280&q=85'` →
`200 image/jpeg 80389`, opened and read column by column. `structuredContent`:
`t: 1033.87`, `link: …?t=1031` = `floor(1033.87) − 2`. `expires_at: null`.
Independently reached by the `p2-frames` consumer, which put the same frame in
its `slides.md` and flagged the mangled target label without guessing at it.

### 9.2 An independent agent's deliverable, as the demo

`p2-frames` — a coding agent with no knowledge of this repo — was asked for a
`slides.md` of five architecture/numbers slides with working image URLs. It
produced five sections from five different talks, curl-checked every URL
(`200 image/jpeg`, 51,734–107,817 bytes), and wrote, unprompted:

> Some of the small OCR labels are garbled (for example, "FALED"), so they are
> not transcribed verbatim here.

**Why it demonstrates the positioning.** Pillar 2's literal claim — *"agents
consume the corpus mid-task"* — with the artifact to show for it. The file is at
`…/scratchpad/terra-eval/r2/p2-frames/ws/slides.md`; the transcript that built it
is beside it. Better demo material than a hand-written example, because nobody
here chose the five talks.

---

## 10. It refuses through the floor, not through a word list

### 10.1 ★★ "Sourdough starter hydration schedule"

**The call.**

```json
search {"q": "sourdough starter hydration schedule", "content_type": "all",
        "limit": 10, "max_text_chars": 1200}
```

**The payload (verbatim).**

```
Results: 0/0
Query: "sourdough starter hydration schedule" · content_type=all

data_status: indexing (corpus has 182 videos, newest published 2026-08-09, …)
All three legs were queried and none of them matched.
next: retry with fewer filters, or list-videos to see what is indexed.
```

**Why this is a different pair from §5.1, and a more important one.** §5.1's
`FlashAttention-4` refusal works because *no word of the query occurs in the
corpus* — the semantic legs are skipped by the `has_lexical_footing` gate, which
is all-or-nothing. §6.2 is the price of that: one familiar word ("kernel") and
the refusal is lost.

Every word of "sourdough starter hydration schedule" is ordinary English that
occurs in this corpus. **The gate does not fire. All three legs run. Nothing is
near enough, and the server says so.** That sentence — `All three legs were
queried and none of them matched.` — is the floor talking, and it could not be
produced at all before tonight: at `vec_max_distance=1.0` this query returned
401 rows across 129 videos (calibration doc §6.3).

**Pair it with §5.1 in a demo.** Same `Results: 0/0` header, three different true
explanations underneath: *no word of this exists* (§5.1), *the filter emptied it*
(§5.2), *we looked everywhere and nothing was close* (§10.1). Independently
reproduced by the `p7-migrate` consumer, which was told to run a negative control
and reported it verbatim.

---

## 11. Precision, and the round-1 pairs re-checked

### 11.1 ★ "turbopuffer" — the query that was the bug report

Round 1 used this as the reproduction for the missing relevance floor: **121 of
154 talks "matched" a proper noun spoken in three of them**, with an unrelated
Hugging Face talk at rank 2.

```
$ … call search '{"q":"turbopuffer","content_type":"transcript","limit":5,
                  "max_per_video":1,"max_text_chars":120}'

Results: 2/2 (no more results)
Legs: transcript 6 (fts 14 · vec 11/800) · ocr 0 · frame 0 (fused, RRF k=60)
[transcript] Building Turbopuffer: Gergely Orosz (@pragmaticengineer) × Simon Eskildsen (CEO) (jQDXzEVHMSE)
  28:40–29:19 · match at 29:09 · https://youtu.be/jQDXzEVHMSE?t=1747
[transcript] RAG is dead, right?? — Kuba Rogut, Turbopuffer (UM6sFg_jdlE)
  0:14–0:56 · match at 0:24 · https://youtu.be/UM6sFg_jdlE?t=22
```

**Two results. Two Turbopuffer talks. `(no more results)`.** Good demo beat
against the stated secondary enemy (YouTube's search box), and honest to show
the `vec 11/800` — 789 of the 800 nearest neighbours were "nearest", not "near".

### 11.2 §2.1 (the price table) — **holds, unchanged.** Re-run verbatim:
`Results: 1/1 (no more results)` · `Legs: transcript 0 · ocr 1 · frame 0`. And
the `Legs:` line is now cleanly readable — with the sub-leg split live, a bare
`transcript 0` really does mean the lexical *and* semantic legs both found
nothing spoken.

### 11.3 §2.2 (the Karpathy quote on the slide) — **holds, and improves.** The
`[ocr+frame]` hit on `BEKc4P87XKo-00009` was **rank 4** in round 1; it is now
**rank 1** for `q="context engineering"`, and the RRF scores differentiate
(`0.0320 · 0.0306 · 0.0302 · 0.0300`) instead of the all-tied `0.0164` that
round 1 recorded as evidence of a broken pool.

### 11.4 §4.1 (the 22-second citation) — **holds, demoted.** `am_oeAoUhew`
`43:26–43:48 · match at 43:28` is still exact, but on a 182-video corpus it now
sits at rank 5 for `q="context engineering"` rather than rank 1. Demo it by
`video_id`, not by hoping it ranks.

### 11.5 §3.1 (the LLM-as-a-judge disagreement) — **holds, and is now findable
two ways.** The round-1 query still works and is far tighter:

```
$ … call search '{"q":"human annotation calibrate LLM judge","content_type":"transcript",
                  "limit":6,"max_per_video":1,"max_text_chars":140}'
Results: 6/11 (use offset=6 for more)
Legs: transcript 24 (fts 1 · vec 32/800) · ocr 0 · frame 0
rank 1: Evaling Video Slop — Maor Bril, Character.ai (b_PmGocP4rc) 4:42–5:21 · match at 4:47
```

6 of 11, against round 1's 20 hits drawn from a claimed 200+. Brumley
(`ZFxh7sqbUZo`) and Heiner (`-npY6XjM8CQ`) both remain in the corpus with the
quotes §3.1 verified. **Prefer §8.1 as the demo now** — it reaches the same
argument from a question a person would actually type, and it surfaces the
explicit *"the previous talk was talking about"* rebuttal that §3.1's phrasing
never found.

### 11.6 §5.1 and §5.2 (the refusals) — **hold.** `flash-attention-4` → `0/0`
with the "no word of this query occurs anywhere" note, re-run by the `p5-absent`
consumer this round; `published_before="2020-01-01"` → `0/0` with the
filter explanation.

### 11.7 §6.2 (the near-miss) — **retired.** `q="CUDA kernel occupancy"` now
returns `3/16` with a frame showing `torch.randn(1024, 1024, device="cuda")` at
rank 1 and a spoken *"and then the third one is CUDA kernels"* at rank 2. Round
1's warning — *"do not demo any query whose terms are not in the corpus unless
every term is absent"* — is no longer the binding constraint; §10.1 is the
replacement rule, and it is the opposite: **junk is now safe to demo.**

### 11.8 §6.1 (browser chrome in the OCR) — **still live, still unfixed.** The
`cgimkNGNjvU` 8:54 slide still leads with a Google Slides `/edit?slide=` URL and
a tab strip. Demo the frame, not the text.

---

## 12. The shortlist, if you have three minutes — round 2

1. **§8.1** — "what happens when the machine grades its own homework?" → `fts 0`,
   and the top two hits are an Anthropic engineer and a Bugcrowd professor
   *disagreeing*, one of them explicitly rebutting the other's talk. Every
   sentence its own link. *Your agent watched it.*
2. **§9.1** — the P95 latency table: the OCR says 529 ms, the frame says 655 ms.
   *The slide, and why a receipt has to be a picture.*
3. **§10.1** — "sourdough starter hydration schedule" → `0/0`, *"All three legs
   were queried and none of them matched."* Ordinary English words, all present
   in the corpus, and it still refuses. *It will not make things up.*

Runner-up, and the best two-beat if you have four minutes: **§8.2 → §2.1** —
ask in plain English why more context hurts, get the spoken sentence; ask what
it costs, get the slide nobody read out loud; open the image, find the cell OCR
could not render.
