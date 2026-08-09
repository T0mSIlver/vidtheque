# Positioning — the contract

**Status: LOCKED (Tom, 2026-08-10).** This is the framing every public surface
builds from — the demo site, the README, the dashboard's voice, release notes,
site posts. Amendments follow the design-doc rule: change this file in the same
commit as the surface that needs the change, and say why. The evidence and the
rejected alternatives live in `research/positioning-2026-08-10.md` (append-only);
this file contains only what we ship.

---

## The position

**vidtheque empowers AI with the knowledge of the builders and creators.**

The best AI engineering knowledge is announced in videos — conference talks,
streams, deep-dives from the people actually shipping — and it is untapped:
spoken once, a slide for eight seconds, gone. Nobody has time to watch it all.

**You don't have time to watch everything. Thanks to vidtheque, your agent
does.** vidtheque turns the channels that matter into solid, timestamped
knowledge — every sentence spoken, every line that crossed the screen, every
frame — on tap for you and your agents, with receipts: the sentence, the
slide, and the second it happened.

**Follow the builders.** Point vidtheque at the builders whose experience you
trust — a conference, a channel, a creator — and their knowledge compounds
into your corpus. Today: every AI Engineer 2026 talk. Tomorrow: whichever
builders you follow.

## The three pillars

1. **Follow the builders** — the corpus is channels of practitioners, growing
   by subscription. The demo is the first shelf, not the library.
2. **Your agent watched it** — the time you don't have is the product's
   reason to exist. Agents consume the corpus mid-task: ask for the SOTA,
   get what was said on stage three weeks ago.
3. **Receipts, always** — what separates injected knowledge from a
   hallucinated summary is the verbatim quote, the real slide with its OCR
   box, and the `youtu.be/…?t=` link that lands on the second. The
   moment-with-receipt is the product's signature artifact.

## The twin line (contrast device, public-usable)

> screenpipe is AI powered by everything *you've* seen, said, or heard.
> vidtheque is AI powered by what you *didn't have time* to see — from the
> people worth listening to.

Same product-family shape, opposite knowledge flow: theirs inward-personal,
ours outward-expert. Deliberate ingestion of *published* video — no
surveillance debt; that homepage space goes to receipts.

## Personas, ranked

1. **The builder who can't watch everything** — knows the answer was in some
   talk; their agent finds it and cites it.
2. **The creator searching their own catalogue** — "find the video where I
   said X." (The inward flow; served, never the headline.)
3. **The team/community memory** — one corpus of the talks that matter,
   shared through the same tools.

## The enemy

Knowledge trapped in video: the gap between what the field just said and what
you can act on. Secondary (demo beat, not headline): YouTube's own search box.

## Vocabulary

**Blessed:** builders, creators, follow, watch/watched, knowledge, inject,
on tap, receipts, the sentence / the slide / the second, corpus, untapped,
solid/solidify, your agent.

**Banned from headlines:** MCP (it's feature #6, exactly where screenpipe
puts theirs), archive, RAG, index (as a noun in copy; fine as a verb),
second brain (the saturated inward-memory frame — ours flows outward),
"search engine".

**Word law:** never "everything *you've* watched" — the corpus is precisely
the videos you **didn't have to watch**. Ownership language attaches to the
*choice*, not the viewing: "the builders you **follow**", "the channels you
chose", "what the builders published" — and the agent is the one who watched
("your agent watched it", "it keeps watching so you don't have to"). Equally
never "everything you've *seen*" (that's surveillance-capture language —
screenpipe's flow, not ours; our ingestion is deliberate and published, no
surveillance debt). Case-sensitive identifiers are never uppercased (a
mangled `youtu.be` id is a broken promise).

## Surface implications

- **Demo site** = the proof: "the knowledge of AI Engineer 2026, on tap —
  ask it something." Real frames, real receipts, the corpus visible (the
  wall/grid), an agent visibly consuming it (activity lines, the field-test
  transcripts as the shown artifact). The corpus grid ends with the roadmap
  affordance: *+ follow a channel*.
- **Dashboard** = the instrument: sells nothing, narrates nothing (the cull
  is contract now). Its charisma is receipts rendered perfectly.
- **README** = the twin line early, pillars as sections, quickstart before
  any protocol word.
- **Roadmap line that makes the position true by construction:** follow
  channels — vidtheque keeps watching so you don't have to.
