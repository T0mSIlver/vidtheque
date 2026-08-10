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
