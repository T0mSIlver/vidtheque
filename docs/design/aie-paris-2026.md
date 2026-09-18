# AI Engineer Paris 2026 edition

Status: contract written 2026-09-15. Implementation follows this document only
after the phase 1 review. `DECISIONS.md` wins if the two disagree.

This edition is a bounded use of the product that already exists. It uses tags,
one follow, one STT backend, and one public page. It does not add an MCP tool or
a deployable.

Sources it must not contradict: `positioning.md`, `demo-site.md`,
`tool-surface.md`, `following.md`, `frontend-migration.md`, `DESIGN.md`, and
`PRODUCT.md`.

---

## 1. The edition and its tags

The edition slug is `aie-paris-2026`. Its public page is `GET /paris` in the
existing Next.js app. Python continues to own `/api/*`, `/frames/*`, and `/mcp`.
This keeps one origin, one public read policy, and one deployment.

The brief's tag names use the existing required `series:` namespace on the
wire. Changing tag validation to admit bare names would split one edition from
the namespaced tag system used by every tool.

| Meaning | Wire tag | Applied to |
|---|---|---|
| edition | `series:aie-paris-2026` | every 2026 video in this edition |
| day stream | `series:aie-paris-2026-stream` | each day VOD |
| individual talk | `series:aie-paris-2026-talk` | each later per-talk upload |

A day VOD and a later talk upload remain separate indexed videos. The page
prefers an explicitly mapped talk upload for a schedule row, then falls back to
the mapped day VOD span. This avoids duplicate rows without deleting either
source.

The 2025 pair is not edition content. `d6dp_dwgpYQ` and `wyUdpmj9-64` may be
indexed only in an isolated private data directory for the dress rehearsal.
They never enter the public database, the `/paris` payload, screenshots, or a
post. A visibility flag is not added because isolation already gives a stronger
guarantee.

## 2. One Python-owned edition file

The committed file is
`mcp/src/vidtheque_mcp/editions/aie-paris-2026.json`. Python loads it with
`importlib.resources`; the browser never imports or bundles it. The facade is
the only reader outside `mcp/`, so validation and derived states have one owner.

JSON is used because both the stored fixture and the facade payload are typed
records, Python can read it without a dependency, and the wheel already owns
the package directory. A scraper is not part of the repository.

The file has `schema_version: 1`. Loading fails at boot in development and in
tests if a required field is missing, an id repeats, a time is invalid, an end
does not follow its start, a stage is unknown, or a main-stage row lacks an
alignment object. A bad committed fixture must not become a partially rendered
public schedule.

The field lists in §2.1 and §2.2 are exact. An edition, session, speaker,
`tags`, `context_bias`, or `alignment` object carrying a key those tables do not
name fails to load, because the facade answers out of this file and a field no
review saw is a field a reader could be shown. *(Amended 2026-09-16: the
unknown-key rule was implied by "the records in §2.2" and was not enforced.)*

### 2.1 Edition fields

| Field | Type | Rule |
|---|---|---|
| `schema_version` | integer | exactly `1` |
| `slug` | string | `aie-paris-2026` |
| `title` | string | public event title, at most 120 characters |
| `timezone` | IANA name | `Europe/Paris` |
| `starts_on`, `ends_on` | ISO date | inclusive event dates |
| `source_url` | HTTPS URL | saved schedule's canonical source |
| `source_captured_on` | ISO date | date of the checked fixture |
| `organizer` | string | `Mistral`, as printed by the source page |
| `streamed_stage` | stage | `main` |
| `tags` | object | the three namespaced tags in §1 |
| `context_bias.fixed` | string array | the fixed AI lexicon in §7 |
| `sessions` | array | the records in §2.2, capped at 100 at load time |

### 2.2 Session fields

`day`, `start`, and `end` are schedule time in `Europe/Paris`. They are not VOD
offsets. Converting a wall clock into a video offset would overload the two time
axes and would be wrong whenever a stream starts early or a break runs long.

| Field | Type | Rule |
|---|---|---|
| `id` | string | stable id copied from the source page when present |
| `day` | ISO date | within the edition dates |
| `start`, `end` | `HH:MM` | local to `timezone`, with `end > start` |
| `stage` | enum | `main`, `discovery-1`, `discovery-2`, or `workshop` |
| `title` | string | source spelling, at most 256 characters |
| `speakers` | array | at least one `{name, company}` pair |
| `category` | string | source spelling, at most 120 characters |
| `alignment` | object or `null` | object for `main`; `null` for an unstreamed stage |

The fixture contains 34 sessions, 2 on September 23 and 32 on September 24.
Eleven are on the main stage. The remaining 23 stay in the fixture because the
page must state what was not streamed and because their later individual
uploads may be mapped without rewriting the schedule.

### 2.3 Alignment data and the talk table

Each main-stage session carries four nullable alignment fields. They begin
`null` because an offset cannot be known before the VOD exists.

| Field | Meaning |
|---|---|
| `talk_video_id` | explicitly matched individual upload |
| `stream_video_id` | day VOD used as the fallback |
| `start_s` | inclusive offset in the day VOD |
| `end_s` | exclusive offset in the day VOD |

The facade joins these values to queryable videos and produces one talk row per
main-stage session. An individual upload is usable only when it carries both the
edition and talk tags. A stream fallback is usable only when it carries both
the edition and stream tags. Explicit ids prevent title heuristics from pairing
the wrong upload with a speaker.

| `alignment_state` | Condition | Offset fields |
|---|---|---|
| `not_yet_indexed` | neither eligible tagged video exists | `video_id`, `start_s`, and `end_s` are `null` |
| `indexed_not_aligned` | an eligible video exists, but no complete mapping does | all three remain `null` |
| `aligned` | a queryable talk upload is mapped, or the stream id and both offsets are valid | all three are present |

For an individual upload, the selected span is `0` through the indexed video
duration. For a stream fallback, it is the committed `start_s` and `end_s`.
The talk upload wins when both mappings are valid because it removes hours of
unrelated context from playback and retrieval.

YouTube chapters already land in `videos.chapters_json` and the `chapters`
table. MCP exposes them through `video-summary` and segment context, but the
public facade does not expose them today. Chapters may help an operator align a
row, but they do not become alignment automatically. The schedule remains the
label source and the committed mapping remains the choice a reviewer can check.

## 3. The additive facade contract

`GET /api/editions/{slug}` is added to the existing read-only facade. It calls
an edition service that reads the committed file and the existing SQLite
tables. It does not add a query layer or a write path.

Every success and refusal carries `Cache-Control: no-store`. The payload
describes a corpus that changes while videos index, so a cached alignment state
would lie.

### 3.1 Parameters

| Parameter | Default | Server bound | Meaning |
|---|---:|---:|---|
| `limit` | `50` | `1..100` | schedule sessions returned |
| `offset` | `0` | `0..1000` | schedule session offset |
| `video_limit` | `20` | `1..50` | tagged videos returned |
| `video_offset` | `0` | `0..1000` | tagged video offset |

The response also has the facade's 60,000-character ceiling. If the character
ceiling drops a session or video from the tail, the matching `has_more` is true
and `notes` names the bound. Item and character caps are independent.

Neither page is trimmed to nothing. `next_offset` is the offset plus what the
page kept, so an emptied page hands back the offset it was asked for and a
client following the hint re-requests the same trimmed page forever. The first
row of each page stays, the response exceeds the ceiling by that row, and
`notes` says so. A payload still over the ceiling with no rows at all — the
edition metadata by itself — is a fixture nobody can serve at any offset, and
answers as the §3.2 `E_INTERNAL` rather than as an oversized response.
*(Amended 2026-09-16: the trim could empty a page and leave `next_offset` on
the offset it had been given, which is a loop rather than a hint.)*

### 3.2 Payload fields

| Field | Type | Meaning |
|---|---|---|
| `edition` | object | `schema_version`, `slug`, `title`, `timezone`, `starts_on`, `ends_on`, `source_url`, `source_captured_on`, `organizer`, `streamed_stage`, and `tags` — never `sessions` or `context_bias` |
| `sessions` | array | the paged schedule records from §2.2 |
| `pagination` | object | `{limit, offset, has_more, next_offset}` for `sessions` |
| `talks` | array | main-stage talk rows for the returned session page |
| `videos` | array | tagged queryable videos with `video_id`, `title`, `channel`, `duration`, `tags`, `index_state`, `kind`, and `link` |
| `video_pagination` | object | `{limit, offset, has_more, next_offset}` for `videos` |
| `notes` | string array | clamps, dropped tails, or tagged videos that cannot be classified |

Each talk row contains the schedule id, day, scheduled start and end, title,
speakers, category, `alignment_state`, selected `video_id`, `source_kind`,
`start_s`, `end_s`, and a timestamped source link when aligned. `source_kind`
is `talk`, `stream`, or `null`.

Every object here is built field by field from the lists above, so a fixture key
outside §2.1 and §2.2 has no route to a reader even if one is committed.

A video row's `channel` is the uploader as indexed and `link` is the plain
`https://youtu.be/<video_id>` the search facade already returns, so the corpus
listing names its source without the page composing a URL of its own.
*(Amended 2026-09-16: both were emitted and accepted by the typed client from
the start and only the table was short.)*

An unknown slug returns HTTP 404 with `{error: "E_UNKNOWN_EDITION", message,
next}`. A malformed committed fixture is an HTTP 500 `E_INTERNAL`, is logged
with its file and field, and never returns a partial edition.

### 3.3 Tag-scoped search and Ask

`GET /api/search` gains the existing tool parameter `tags`. It accepts at most
10 comma-separated namespaced tags with AND semantics and passes them unchanged
to `tools.search.run`. The edition page always sends
`tags=series:aie-paris-2026`.

`POST /api/ask` gains `tags` beside `q` in its JSON body. The endpoint validates
the same bound and applies it to every internal search. The model cannot remove
or replace this scope in a tool call. Segment context is limited to video ids
returned by that scoped loop, so a fabricated id cannot escape the edition.

Both additions are optional. Existing callers that omit `tags` receive the
same corpus-wide behavior they have now.

## 4. The `/paris` page

`GET /paris` is a fourth route in the existing front end, not a fourth
deployable. It reaches Next the way `/` and `/demo` do, and for the same
reason: the edge names the paths that are **Python's** and everything else
falls through to the app that serves pages, and `web/src/proxy.ts`'s document
matcher is an exclusion list of what is not a document. So the page receives a
fresh nonce, the production CSP and the three companion document headers with
no new row in either file. *(Amended 2026-09-16: the contract asked for an
explicit row in each. Both were written, and both were wrong — a Caddy matcher
for `/paris` restates the fall-through, and naming `/paris` in `proxy.ts` means
excluding the prefix from the catch-all, which takes the policy off the 404
document at `/paris/anything`. `frontend-migration.md` §1a and §1b carry the
route and the policy; a row is added there when a path becomes Python's, which
this one never does.)*

The page reads `GET /api/editions/aie-paris-2026` through the typed server-only
client. Search and Ask continue to call Python directly through same-origin
`/api/search` and `/api/ask` with the edition tag. No browser bundle reads the
fixture.

*Amended 2026-09-16 (Tom): the page order and the URL.* The hero comes
first, then the search-or-ask console, then the programme. The programme sits
in its own server `<Suspense>` below the console, so its height never moves
the box. The page reads the edition once per request, through React `cache`,
and that one read serves both the console's talk labels and the programme.
Search and ask are the same client console as `/demo` (`demo-site.md` §6.1),
with the edition tag fixed and no corpus count. Interactions call
`/api/search` and `/api/ask` directly and never re-render the page. The URL is
`/paris?q=…&type=…` for a search and `/paris?ask=…` for a loaded, unfired
question (`demo-site.md` §6.2).

*Amended 2026-09-18 (Tom): place the connect panel before the programme.* The
page order is hero, console, connect panel, programme. The programme keeps its
own server `<Suspense>` below the connect panel and never moves the console as
it streams in.

The visual system is `DESIGN.md`'s demo register. The page uses the existing
tokens, zero radius, gold only for the selected moment, lime only for OCR
evidence, visible state words, reduced motion, fixed image boxes, and no new
font or breakpoint. The timeline and receipts are the content. No image exists
only as decoration.

### 4.1 Copy

The headline from the brief is:

> The main stage was eight and a half hours. Ask it a question.

The second line is:

> Every main-stage talk from AI Engineer Paris, cited to the second, slides
> included. Point your own agent at it.

"Main-stage" admits the 23 fixture sessions that were not streamed, as
`positioning.md` requires.

The page carries no organizer credit — Tom asked for the line out on
2026-09-16. The `organizer` field stays on the edition read and in the
fixture. The page does not imply sponsorship, use a logo, or claim
endorsement.

### 4.2 Timeline and alignment states

The main stage renders as a day-grouped timeline. There is one row for each of
the 11 main-stage sessions in the fixture.

A `not_yet_indexed` row prints "not yet indexed" and no fabricated clock or
disabled timestamp. An `indexed_not_aligned` row prints "indexed, not yet
aligned" and no offset. An `aligned` row prints the selected video link and
offset, speaker, title, category, and source kind.

Mixed states are valid. One talk upload may be ready while the day VOD still
has unaligned rows. The page renders each row from its own state and never
promotes a day to ready because one row is ready.

Discovery Track 1, Discovery Track 2, and Workshop sessions render in muted,
greyed groups with this exact label:

> not streamed; may arrive later as individual uploads

Those rows have schedule times and speaker details but no VOD offset. A later
individual upload appears only after its explicit fixture mapping and tags are
present.

### 4.3 Page and query states

Before a VOD is indexed, the schedule remains useful and every main-stage row
shows `not yet indexed`. The page does not replace the timeline with an empty
screen.

While the edition read is pending, the page reserves the timeline's layout and
prints `loading`. A typed refusal prints its word, message, and `next` value.
An unreachable facade prints `unavailable` and offers one retry.

Search reuses `/demo`'s query states and result receipts. *(Amended
2026-09-16: the states are the shared console's own, so a refusal, an
unreachable server and a rate limit print exactly as on `/demo`, and a retry
repeats the request rather than reloading the page.)* An empty query does
not run. No hits, an empty corpus, a rate limit, a refusal, and an unavailable
server remain distinct states. Results are labelled client-side by the talk row
whose selected `video_id` and `[start_s, end_s)` contain the hit.

Ask reuses `/demo`'s streamed activity, cited answer, sources, rate-limit, and
degradation states. Its scoped tag is fixed by the page and is not a control the
visitor can turn off.

### 4.4 What the page never renders

The page never says "five tracks" or "real time". It never presents a
discovery or workshop session as streamed. It never renders Paris 2025 content,
an uncommitted offset, an exact corpus total, an unpublished price, or a result
outside `series:aie-paris-2026`.

The page never claims the day stream was indexed while it was live. The follow
waits through `is_live`, `is_upcoming`, and `post_live`; the page can only show
data after the stable VOD is indexed.

### 4.5 MCP endpoint and footer

The endpoint block is copied from `/demo`: the heading "Add this corpus to your
own agent", `mcp_url` from `/api/meta`, and one copy button for each row:

```
mcp endpoint  <mcp_url>
claude code   claude mcp add --transport http vidtheque <mcp_url>
codex         codex mcp add vidtheque --url <mcp_url>
mistral vibe  vibe mcp add vidtheque --url <mcp_url>
```

Under the rows, the panel says: "Claude, ChatGPT and Le Chat: add a custom
connector and paste the endpoint. No sign-in."

*Amended 2026-09-18 (Tom): add Codex, Mistral Vibe, and custom connectors.* The
four rows above replace the endpoint and Claude-only pair. All commands derive
from the same `mcp_url`.

The footer keeps `/demo`'s line, "The videos belong to the people who made
them.", with the `Removal on request` link to `docs/takedown.md`.

## 5. The follow and dedup operation

The follow watches `https://www.youtube.com/@aiDotEngineer` on the `streams`
and `videos` tabs. Streams are needed for the day VODs. Videos are needed for
the later uploads, whose titles carry no conference marker.

| Rule | Value | Why |
|---|---|---|
| `title_include` | `Paris`, then every unique fixture speaker name | `Paris` catches day VODs; full names catch per-talk uploads with fewer false positives than surnames |
| `tags` | `series:aie-paris-2026` | common scope can be applied safely to every accepted candidate |
| `channels` | `all` | transcript, OCR, and frames are all part of the edition claim |
| `backfill` | `0` | the follow starts with 2026 and cannot pull older channel uploads while unattended |
| `max_per_check` | `2` | both day streams can land without a burst, while a later upload batch drains across checks |
| `mode` | `auto` | the follow is meant to run unattended |
| `check_interval_s` | `21600` | the existing six-hour default is enough for same-day VODs without hammering the channel |

`title_include` must hold `Paris` plus the fixture's 34 unique speaker names, a
35-term list. `MAX_TITLE_TERMS` is 50. Plain case-insensitive substring
matching, the 80-character per-term limit, and the existing validation path
stay unchanged. This is a limit adjustment, not a regex feature or a new tool.
The orchestrator set this global bound and flagged it to Tom.

The follow applies only the common edition tag because its tags are static for
the whole rule. After an accepted candidate is identified, the operator uses
the existing `tag-video` tool to add the stream or talk tag. Conditional tag
rules are not added. The endpoint ignores a video for row selection until its
specific tag is present, so a missed classification is visible rather than
silently treated as a talk.

The 16-hour rolling daily follow budget remains global. A day pair near ten
hours fits under it, while a later upload burst is held and reconsidered. No
edition-specific budget is invented.

## 6. The Voxtral STT backend

`voxtral` is a worker STT backend selected with the existing `STT_BACKEND` and
registered beside `whisperx`. It serves `voxtral-mini-latest`, makes external
API calls, reports `vram_estimate_mb=0`, and has no resident model. `load()` and
`unload()` manage the client only.

The existing corpus is not re-transcribed. Only jobs deliberately run with the
edition's Voxtral worker configuration use the backend. `mcp/` never reads the
backend name.

The worker's transcription endpoint gains one optional multipart field,
`context_bias`, encoded as a JSON string array. It is the only change to the
request half of the HTTP seam; the response gains one optional field, below.
`mcp/` sends it per request because `mcp/` owns the edition file and
the worker must remain stateless.

| Field rule | Bound |
|---|---:|
| number of terms | 0..100 |
| one term | 1..100 Unicode characters after trimming |
| encoded field | at most 12,000 bytes |

Invalid input returns the worker's existing 400 `invalid_input` envelope.
Duplicates are removed by NFKC plus case-fold comparison, with the first source
spelling kept. The worker forwards the validated list unchanged to Mistral.

Word timestamps win over the language pin. The upstream request sends
`timestamp_granularities=["segment", "word"]` and omits `language`, because
Mistral documents those parameters as incompatible. `align=True` is accepted
by the backend and is a documented no-op because the words already carry the
model's alignment.

Diarization stays off. The shared `Transcription` type has no speaker field,
the corpus setting already defaults to disabled, and the committed talk table
provides the public attribution. Adding a speaker field to the worker response
would widen the HTTP contract for data this edition does not need.

One upstream request covers at most 3,600 seconds. Adjacent requests overlap by
30 seconds and run sequentially. Sequential calls bound temporary disk, API
concurrency, and the number of paid requests in flight.

Every request sends an extracted chunk, including a recording short enough for
one request. The extraction is ffmpeg to mono 16 kHz FLAC: speech recognition
reads one channel at 16 kHz, so anything above that is bandwidth and megabytes
rather than accuracy, and forwarding the pipeline's own file would put a body
on the wire that nothing here had bounded.

The seam merge keeps the earlier copy of a repeated word sequence. It compares
normalized words inside the overlap, chooses the longest suffix and prefix
match of at least three words, shifts the later timestamps by the chunk start,
and drops only the matched prefix. A match counts only when the clock agrees
with the words: the two copies start within two seconds of each other, and the
first word kept from the later chunk does not start before the last word
already kept. A filler phrase repeated elsewhere in the overlap passes the word
test and would append speech that runs backwards, so a match failing the time
test is treated as no match. If no safe match exists, it keeps both sides
— interleaved by timestamp, so the same seconds appear twice but never out of
order — and records a degraded seam instead of deleting speech by guess. Word
timestamps are therefore monotonic on both paths. Segments are rebuilt from the
merged words, span every word they hold, start in order, and never overlap
after a safe merge.

A degraded seam is reported, not only logged. `verbose_json` gains one optional
field, `degraded_seams`, a list of seam seconds, absent unless a seam degraded.
It is the second and last change to the HTTP seam, and it is small on purpose:
a transcript carrying a duplicated half-minute is honest but not clean, and
`mcp/` can only record which video carries one if the worker's answer says so.
A backend that transcribes in one pass never sets it, and `mcp/` ignores the
field when reading cues.

The official overview currently says recordings up to three hours, while the
known-limitations page says 60 minutes and 500 MB. The backend follows the
smaller limit, and it follows both halves of it: chunking answers the minutes,
and the encoded chunk's size is checked against 500,000,000 bytes before the
paid call. A chunk over that limit fails as the retryable 503, not as the 400
that the upstream 413 would become — a rejection settles the item, and an hour
of a conference VOD should not be dropped on a bound a differently configured
worker would clear. A change to the upstream limit may reduce the number of
calls later, but it does not change the worker response shape.

### 6.1 Environment

| Variable | Default | Scope |
|---|---|---|
| `STT_BACKEND` | `whisperx` | existing selector; set `voxtral` only for the edition worker |
| `STT_MODEL` | `large-v3` | existing model id; set `voxtral-mini-latest` with `voxtral` |
| `MISTRAL_API_KEY` | empty | new, worker only; required by `voxtral` |
| `MISTRAL_BASE_URL` | `https://api.mistral.ai/v1` | new, worker only |
| `VIDTHEQUE_INDEX_MAX_DURATION_S` | `14400` | existing guard; set `39600` on the edition indexing box |

`39600` is an 11-hour ceiling. It admits the observed 8 to 10-hour event VOD
shape without disabling the existing duration guard. The public default stays
four hours.

The API key never enters `mcp/`, `web/`, a job payload, a log, the eval
process, or an eval result. The Mistral account must have its own spend cap before a batch runs.

## 7. The context-bias builder

The builder reads the same validated edition object as the facade. There is no
second speaker list in worker code, bench code, or an environment value.

Terms are deduplicated by NFKC plus case-fold comparison and keep the first
fixture spelling. The builder stops at 100 terms in this order:

1. Full speaker names, in session order.
2. Companies, in first appearance order.
3. The fixed lexicon from the fixture: `vLLM`, `SGLang`, `pyannote`, `RRF`,
   `LoRA`, `KV cache`, `MCP`, `FlashAttention`, `Voxtral`, and `Qwen`.
4. Distinctive title terms, in session order. A term must be at least four
   characters, occur in only one title after normalization, and not be an
   English stop word.

Names and companies come first because the eval measures proper nouns. The
fixed lexicon precedes title terms because these spellings are known ASR risks
and remain useful across talks. Rarity gives the remaining slots to title words
that identify one session rather than words such as "agents" or "model".

The same list is sent to each chunk of a day VOD. A later per-talk upload may
use the same list; creating a second per-talk algorithm would make the two eval
arms incomparable.

## 8. The evaluation

The eval lives under `bench/` and runs only on Tom's box. CPU tests use recorded
responses and never call Mistral or download a model.

The paid arms go through an already configured worker over HTTP, so the eval
command holds no credential of its own: §6.1 keeps `MISTRAL_API_KEY` in the
worker process, and the worker's boot check is what refuses `voxtral` without
it. The eval never reads the variable and never asks the operator to export it.

About ten Paris 2025 talks form one fixed manifest of source ids and audio
spans. All three arms use the same audio:

| Arm | Transcript |
|---|---|
| 1 | the already indexed whisperX transcript |
| 2 | Voxtral with an empty bias list |
| 3 | Voxtral with the schedule-derived bias list |

The metric is proper-noun term accuracy. For each talk, the denominator is the
deduplicated speaker, company, and distinctive-title term list derived for that
talk. A term is correct when NFKC normalization, case folding, and whitespace
collapse produce an exact phrase in the transcript. The report prints matched
terms, missed terms, numerator, denominator, and accuracy for each talk and
arm, then a macro average across talks.

Every output starts with this limitation:

> This measures schedule-derived proper-noun coverage, not general word error.
> A missing term may not have been spoken, and a present term may occur outside
> the intended mention.

The command is a dry run until cost is known. Before submission it requires an
explicit `--price-per-minute` read from Mistral's official pricing page, and
the figure must be finite and above zero: zero, a negative, `nan` or `inf` is
refused at the argument, because a projection nobody can read is not a price
preflight. The
repository does not scrape that page: a scraper is one more thing to break on a
page the operator is already looking at, and a silently stale number is worse
than no number. The command prints the price source, the time the operator's
figure was entered, audio minutes, request count, two Voxtral arms, and
projected cost, then requires the explicit execute flag. No price is committed
as fact.

The 2025 dress rehearsal, its audio, transcripts, and eval output remain
private. Its per-talk and aggregate results are never published.

## 9. Bounds for a long VOD

The duration guard is the first bound. The edition box uses the existing
`VIDTHEQUE_INDEX_MAX_DURATION_S=39600`; it does not set the documented `0`
opt-out.

`VIDTHEQUE_MAX_KEYFRAMES=600` remains the hard per-video frame cap. When it
binds, the keyframe stage thins shots uniformly. It does not keep every frame
from the first hours and discard the end of the day.

Transcript chunks use the existing 45-second target and 15-second overlap, a
30-second stride. A continuous 8-hour VOD therefore creates about 960 text
chunks and about 960 `vec_chunks` rows, although cue boundaries can change the
exact count and there is no separate chunk-count cap. Frame embeddings add at
most 600 `vec_frames` rows, and fewer when keyframes deduplicate. At 2,048
float32 dimensions, the expected raw vector payload is about 7.5 MiB for text
and at most 4.7 MiB for frames before SQLite and vec0 overhead.

The private 2025 rehearsal records the database size and the counts of cues,
chunks, keyframes, OCR lines, `vec_chunks`, and `vec_frames` before and after
the 8h27 VOD. The two readings must name the same data directory and may only
grow; a baseline from another database, or a count that shrank, fails the
comparison rather than clearing the gate with a delta that measures nothing. More than 600 frame vectors blocks arming the follow because the
hard cap failed. A text count unexpectedly above about 960 also blocks arming
until duplicate writes and abnormal cue spacing are ruled out; the rehearsal
does not invent a second text-row cap.

`VIDTHEQUE_OCR_BATCH=8`, `VIDTHEQUE_EMBED_BATCH=32`, and
`VIDTHEQUE_FRAME_EMBED_BATCH=8` bound each worker request independently of the
video length. The edition does not raise them.

If a process dies at hour six, the existing queue is the recovery mechanism.
The runner heartbeats every 30 seconds. After
`VIDTHEQUE_STALE_CLAIM_S=300`, another pass requeues the item, resets only the
interrupted stage to `pending`, and leaves completed stages done. The default
three item attempts stop a repeatable crash from looping forever. A controlled
shutdown releases the claim immediately.

The retained audio policy stays `VIDTHEQUE_KEEP_SOURCE=audio`. A failed later
stage can resume without downloading or transcribing again, and original video
is deleted only after the final stage succeeds.

## 10. Security, consent, and publication gate

Transcript and slide OCR text are untrusted evidence, not instructions. The
page renders them as text and never as HTML. MCP clients must make the same
trust distinction. The edition does not rewrite or suppress suspected prompt
injection because doing so would alter the receipt a citation is meant to show.

Before publication, the existing public-deployment audit gets a delta review
for the money-bearing Mistral credential, edition facade, and untrusted content
handoff. The review does not reopen the established public read-only baseline;
it checks only the new exposure introduced by this edition.

The organizer gate is satisfied once the request has been sent and no refusal
has arrived before the announcement. The request goes to the conference or
organizer contact, not to an interviewer, and states what is indexed, that
every result links to the original YouTube source, and how removal works. This
task writes no email.

An explicit refusal stops publication and removes the edition from the public
corpus. Development and the private dress rehearsal may continue, but the
route is not announced. The private eval remains unpublished under either
outcome.

The page carries the existing "Removal on request" footer link and no organizer
credit (§4.1). Every search result, Ask citation, screenshot, and post keeps its
source link. No paywalled, private, or unlisted video enters the public edition.

## 11. Open, for Tom

4. The 2026 stream ids and every VOD offset are unknowable on 2026-09-15. They
   stay `null` until the indexed videos and their receipts can be checked.
5. Recheck the saved schedule before arming the follow. The committed fixture
   is a dated capture, and organizer edits must land as a reviewed fixture
   change with a new reported count.
