# Demo ask-set refresh — receipt-checking the announcement questions (2026-08-13)

The 2026-08-11 ask set shipped one flagship and two judge questions. Tom's
verdict before the public announcement: the judge pair read as riddles and are
two shades of one argument. The replacement bar: **genuinely hard, contested
questions with no obvious answer** — precise, not philosophical. This file is
the receipt check for the new set, run against the LIVE demo corpus
(generation `2026-08-13-aie310`, all 310 AI Engineer 2026 talks), the same
day the corpus landed.

## Method

Every candidate ran through `GET /api/search?q=<question>&content_type=
transcript&limit=8` on vidtheque.dev. Recorded per candidate:

- `leg_counts.transcript_fts` — the mechanical form of the "shares no
  vocabulary with its answer" rule (2026-08-10 file): 0 means the question's
  words match nothing in any transcript and retrieval is pure vector.
- distinct talks in the top 8 — the "no single talk answers it" rule.
- the top hits, read by hand — leg counts prove retrieval shape, only reading
  proves the receipts are on-theme. Several candidates below pass both counts
  and still died on this step.

Four rounds; every candidate and score is kept (append-only), the shipped
five are marked. Raw JSON incl. per-hit quotes: session scratch, quotes
reproduced below for the shipped set.

### Round 1 — broad slate (theme sweep)

| candidate | `transcript_fts` | distinct talks (top 8) |
|---|---:|---:|
| What breaks first when an agent has to work for hours instead of minutes? | 0 | 5 |
| Why did everyone suddenly start sending engineers to sit with the customer? | 0 | 4 |
| Who reviews the code when nobody wrote it? | 0 | 5 |
| Why would anyone run a small model when the big ones keep getting cheaper? | 0 | 4 |
| What does it take to trust an agent with real money? | 0 | 6 |
| How do you keep a fleet of agents from stepping on each other? | 0 | 7 |
| How do you stop the demo from lying to you? | 0 | 4 |
| Why does giving the model more to read make it worse at its job? | 0 | 5 |
| What's actually left of the job when the agent does the typing? | 0 | 5 |
| How do you teach a machine what good taste is? | 0 | 5 |
| Where do you find training data no model has seen before? | 0 | 5 |
| Why do the benchmark numbers keep going up while the products stay mediocre? | 0 | 4 |
| How does an attacker look at a codebase the AI wrote? | 0 | 5 |
| What happens to quality when shipping gets twenty times faster? | 0 | 8 |
| What do teams wish they had measured before their agent met real users? | 0 | 5 |
| Why is everyone building little worlds for their models to practice in? | 0 | 7 |

### Round 2 — precise/controversial slate

| candidate | `transcript_fts` | distinct talks (top 8) |
|---|---:|---:|
| When does a smarter model make a worse agent? | 0 | 3 |
| Why not just stuff the whole codebase into the context window? | 0 | 5 |
| Does the harness make the agent, or just get in the model's way? | 0 | 3 |
| Why are the best teams making their agents less autonomous, not more? | 0 | 6 |
| When does routing to a cheaper model end up costing you more? | 0 | 4 |
| Can you do reinforcement learning when nothing about the task can be verified? | 0 | 6 |
| Is fine-tuning dead now that models can learn on the job? | 0 | 5 |
| Should the judge see the agent's full context, or does that poison the verdict? | 0 | 5 |
| Can you build a benchmark the model hasn't already memorized? | 0 | 4 |
| Does training on model-generated data compound quality or collapse it? | 5 | 7 |
| What stops an agent from writing the code that grants itself access? | 0 | 4 |
| At what point does the agent cost more than the engineer it replaces? | 0 | 6 |
| If agents can work in parallel, why does adding more of them slow teams down? | 0 | 5 |
| Do specs make agents reliable, or just move the slop upstream? | 0 | 6 |

### Round 3 — AGENTS.md and harness slates

| candidate | `transcript_fts` | distinct talks (top 8) |
|---|---:|---:|
| Why does the agent ignore your AGENTS.md? | 0 | 4 |
| Who is documentation for now, the humans or the agents? | 3896 | 8 |
| What actually belongs in an AGENTS.md file? | 0 | 4 |
| What should you write down for a coworker who forgets everything between tasks? | 0 | 5 |
| If the agent read the manual, why does it still break the rules? | 0 | 5 |
| Why do two agents built on the same model perform so differently? | 0 | 6 |
| How much of your coding agent's performance is the harness, not the model? | 0 | 3 |
| Why do software factories fail even when the harness is good? | 0 | 5 |
| Is the moat the model, or the harness wrapped around it? | 2 | 5 |

### Round 4 — Tom's final phrasings

| candidate | `transcript_fts` | distinct talks (top 8) |
|---|---:|---:|
| Is the harness or the model more important? | 193 | 7 |
| Why do agents write bad AGENTS.md? | 0 | 4 |
| What happens when the agent writes its own documentation? | 0 | 6 |
| Should the agent generate its own AGENTS.md, or should you write it by hand? | 0 | 4 |

## The shipped five, with their receipts

1. **Why does loop engineering look so much like building RLVR environments?**
   — unchanged flagship (2026-08-10 file, "Tom's ask-mode flagship").

2. **How to do reinforcement learning when the task can't be verified?**
   *(phrasing tightened by Tom after round 4; re-validated same day —
   identical profile, `fts 0`, 6 talks, same receipts)* — Receipts: the Will Brown talk itself, and —
   the reason it ships — *Agents need more than a chat* (Legora) quoting
   "verifier's rule … if a task is solvable and it's easy to verify, then
   it's go[ing to be automated]" — the law cited by a different speaker than
   the one who coined it. *Modern Post-Training: A Deep Dive* carries the
   third leg. Known overlap with the flagship's RLVR neighbourhood — accepted
   deliberately (Tom, 2026-08-13): one is about the shape of agent loops, the
   other about the training question itself.

3. **Does training on model-generated data compound quality or collapse it?**
   — `fts 5` (the word "collapse"), 7 talks. Receipts: *The Base Model Is
   Dead* [9:47] attacking the dogma head-on — "There's a lot of talk around
   synthetic data that blindly tossing it into a model can cause the model to
   collapse and performance to tank, b[ut]…" — vs MiniMax training multimodal
   from scratch [10:14] vs *Data Quality Is the Compute Multiplier* [12:05].
   A three-way, named disagreement.

4. **Is the harness or the model more important?** — `fts 193`, 7 talks. THE
   DELIBERATE EXCEPTION to the no-shared-vocabulary rule: the fight cannot be
   named without its two nouns. Receipts earn it: *What if the harness
   mattered more than the model?* [2:25] "scores range from 52.4% to 76.2% …
   and only the harness changed"; *Your Agent Didn't Fail. Your Harness Did.*
   [4:03] "A powerful engine with no brakes is not autonomy. It is a liability
   with good acceleration."; *Don't Let the LLM Drive* [1:07] "The model never
   decide[s] where we are"; and *Your Moat Is Your Data Model* [0:12] arriving
   as the surprise third camp. (An earlier softer phrasing, "How much of your
   coding agent's performance is the harness…", was single-talk-dominated —
   round 3 — the blunt phrasing is also the better-retrieving one.)

5. **Why do agents write bad AGENTS.md?** — `fts 0`, 4 talks. The weakest
   retrieval of the five, on purpose last: the corpus answers by knowing what
   a good AGENTS.md is — *Agentic Engineering: Working With AI* [19:57]
   "critical that your project has an agents.md with a **minimal** amount of
   information", [20:04] conventions/commands/requirements; *From Writing
   Code to Designing Systems* [8:01] repository intent and constraints;
   *Building pi in a World of Slop* [6:02] (pi's whole system prompt vs the
   skills standard) and *A Genius With Amnesia* as supporting voices. The
   purest synthesis test in the set.

## Validated and scrapped, with reasons

- "What happens when the machine grades its own homework?" and "Can you trust
  a model to judge how good the writing is?" — REMOVED (Tom): riddle-toned,
  same argument twice.
- Round-1 tier-1s (benchmaxx numbers-vs-products, long-horizon
  what-breaks-first, small-models, context-rot, agents-with-money) — all pass
  every bar and remain deployable; lost on interest to the round-2/4 picks.
- "Should the judge see the agent's full context…" — retrieval surfaces
  context-management talks, not the judging controversy; the Lance
  Martin/Brumley disagreement of the 08-10 file does not resurface through
  this phrasing.
- "When does a smarter model make a worse agent?" — 3 talks, soft receipts;
  the Vending-Bench Opus-4.8 surprise does not surface for this phrasing.
- "When does routing to a cheaper model end up costing you more?" — off-theme
  hits (feature-flag territory).
- "What actually belongs in an AGENTS.md file?" / "How much of your coding
  agent's performance is the harness…" — single-talk-dominated top ranks.
- "Who is documentation for now, the humans or the agents?" — `fts 3896`,
  scattered hits; a search wearing a question mark, the exact failure the
  rule exists for.

## Copy change shipped alongside

The ask input's placeholder was `ask a question about these talks…` — wrong
promise: the answers are about AI engineering, built FROM the talks. Now
`ask a question about AI engineering…` (index.html + app.js `setAskMode`).
