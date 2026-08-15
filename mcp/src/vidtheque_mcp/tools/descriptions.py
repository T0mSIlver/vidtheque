"""Tool descriptions and annotations.

These are the tool-surface §4 texts with the **shared rules lifted out**, per
DECISIONS.md: "Tool description budget: <= ~120 words each; shared rules live in
the `guide` resource, not repeated per tool." The doc's verbatim blocks run
120-190 words each because every one of them restates the two time axes, the
case-insensitivity rule and the never-fabricate-ids rule. Nine copies of that is
roughly 4k tokens of permanent context in every session; one copy in
`vidtheque://guide` is the same information for a ninth of the budget.

What stays per tool: purpose, USE WHEN, DO NOT USE, and the starting parameters.
What moves to the guide: the two time axes, case-insensitive substrings,
relevance-vs-recency ordering, never fabricating ids, and reading the pagination
line.
"""

from __future__ import annotations

from mcp_types import ToolAnnotations

SEARCH = """
Search the indexed video corpus: transcripts, on-screen text, frame imagery.
Every result carries a timestamped youtu.be deep link.

USE WHEN: you need specific words, claims, numbers, code or visuals from videos
the user has indexed — "where does he explain KV caching".

DO NOT USE: to learn what is in the corpus (corpus-summary); to understand one
video end to end (video-summary); to read the transcript around a hit
(get-segment-context). Indexed videos only, never the public YouTube catalogue.

START WITH limit=5; limit clamps to 50, so page with offset.
content_type=all means all three channels, always. Follow a hit with
video-summary — its chapter list names the moment faster than probing
get-segment-context. See vidtheque://guide.
""".strip()

LIST_VIDEOS = """
List videos in the corpus, with optional filters — the browsable library.
Title, channel, publish date, duration, tags, and which channels of data each
video has (transcript / OCR / frame embeddings).

USE WHEN: the user asks what is indexed, wants everything from one channel or
tag, or you need a video_id before calling video-summary or a scoped search.
Also use it after an empty search to check whether the video is even in the
corpus.

DO NOT USE: to find content inside videos (search); to get a picture of the
whole corpus at once (corpus-summary — one call instead of paging).

START WITH limit=20, format="tsv". See vidtheque://guide for the shared rules.
""".strip()

CORPUS_SUMMARY = """
Pre-aggregated overview of the whole video corpus — one call, not paged.
How many videos, which channels, which topics/tags, date span, coverage gaps,
and what was indexed most recently.

USE WHEN: this is your FIRST call in a session, the user asks what is in the
library, or a search came back empty — it says whether the corpus is empty,
still indexing, or simply lacks that topic.

DO NOT USE: to find content (search); for detail on one video (video-summary).

Turn off what you do not need: include_channels, include_tags, include_recent,
include_gaps, include_guidance. Every section is capped.

Three resources back this up: vidtheque://guide (tool flow and shared rules),
vidtheque://context (limits, id formats, time), vidtheque://corpus (the whole
library as TSV).
""".strip()

VIDEO_SUMMARY = """
Structured overview of one indexed video, instead of its whole transcript.
Chapters with timestamps and deep links, speakers, the most informative
on-screen texts, tags, and links from the description.

USE WHEN: the user asks what a video covers, wants its structure, or you need to
pick a timestamp to drill into. A good second call after search returns a video
you have not seen before.

DO NOT USE: to search across videos (search); to read the actual words around a
moment (get-segment-context — this tool samples, it does not transcribe).

Everything heavy has an off switch: include_chapters, include_speakers,
include_key_texts, include_ocr_highlights, include_links, include_tags. Full
transcripts are never returned at any setting.
""".strip()

GET_SEGMENT_CONTEXT = """
Everything around one moment in one video, given a video_id and a timestamp.
The verbatim transcript window, the on-screen text of nearby keyframes, the
enclosing chapter, and frame ids for get-frames.

USE WHEN: search or video-summary gave you a hit and you need the actual words —
to quote accurately, or to judge whether it is relevant.

DO NOT USE: as a transcript dump (capped at 300s and 4000 chars — use
video-summary); to find the moment in the first place (search).

START WITH window=45 — seconds each side of t, clamped 5-300. If the line you
want is cut off, raise window rather than guessing new t. Pass video_id and t
exactly as a result gave them.
""".strip()

GET_FRAMES = """
Fetch keyframe images from indexed videos, as URLs (default) or inline base64.

USE WHEN: a result mentions a slide, diagram, chart or UI and text is not
enough — you have frame ids from search, video-summary or get-segment-context.
Also when OCR reads garbled or clipped: dense slides (tables, code) are pixels,
not text.

DO NOT USE: to browse a video (frames are keyframes, not a filmstrip).

START WITH return="url" and open the URL. Every id you pass is fetched (max 12);
limit bounds only the video_id span mode. The ocr: line is capped at 300
chars/frame — max_text_chars=0 gives every line. return="image" inlines base64
JPEG, max 4 per call — 10-20x the nominal token cost on some clients.
""".strip()

INDEX_VIDEO = """
Add a video, playlist or channel to the corpus. Async — returns a job id.
Indexing runs in the background and takes roughly 1-3 minutes per hour of
video, longer if the GPU is busy.

USE WHEN: the user gives you a video URL that search says is not indexed, or
asks to add something to the library.

DO NOT USE: to fetch a transcript for immediate reading (nothing is queryable
until the job reaches "done"); on a URL the user did not ask for; repeatedly for
the same URL — a video already in the corpus returns its existing video_id
unless force_reindex=true.

Tell the user it is queued and poll job-status at most every 15 seconds.
""".strip()

JOB_STATUS = """
Check the status of an indexing job started by index-video.
Call with no arguments to list recent jobs.

USE WHEN: you started an index-video job and need to know whether the video is
searchable yet, or a tool told you a video is still indexing.

DO NOT USE: in a tight loop. Indexing takes minutes; poll at most every 15
seconds, and prefer telling the user "it is running, ask me again in a minute"
over polling repeatedly inside one turn.

A job is only searchable at state "done". The response says exactly what is
available before then. A job still running needs another poll, not a re-index —
force_reindex is for a job that actually reported "failed".
""".strip()

FOLLOW_CHANNEL = """
Follow a YouTube channel or playlist: new uploads that match your rule are
indexed on their own, on a schedule. One tool, five verbs —
action="follow|unfollow|pause|resume|check_now".

USE WHEN: the user wants to keep up with a source rather than paste its videos
one at a time, or asks to stop, pause, resume or re-check something they
already follow.

DO NOT USE: for one video or a one-off playlist (index-video); to see what is
already followed (corpus-summary include_follows=true).

Nothing is fetched here — the first check runs on the next tick. Bound what it
takes with tabs, min_duration, max_per_check and title_include; mode="review"
holds candidates instead of queueing them. Unfollowing keeps every video it
brought in.
""".strip()

TAG_VIDEO = """
Add or remove namespaced tags on an indexed video.
Namespaces are topic:, person:, project:, source:, lang:, series:, and the tags
are used as filters by search, list-videos and corpus-summary.

USE WHEN: the user asks to organise, label or collect videos, or explicitly
approves a tag you proposed.

DO NOT USE: to tag speculatively. Never invent tags the user did not ask for — a
corpus with 400 machine-generated topic tags is worse than one with none. Check
existing tags first (corpus-summary include_tags=true) and reuse them rather
than coining near-duplicates.

Both add and remove are idempotent: adding an existing tag or removing an absent
one succeeds and reports no change.
""".strip()


# openWorldHint is false for every query tool (the corpus is a closed local
# index) and true for the two that reach the internet — index-video directly,
# follow-channel through the checks it schedules.
#
# idempotentHint is used as a cache-safety signal: true when the same arguments
# return the same answer until the corpus changes. That makes job-status the one
# read tool with idempotentHint: false.
ANNOTATIONS: dict[str, ToolAnnotations] = {
    "search": ToolAnnotations(
        title="Search video corpus", readOnlyHint=True, idempotentHint=True, openWorldHint=False
    ),
    "list-videos": ToolAnnotations(
        title="List indexed videos", readOnlyHint=True, idempotentHint=True, openWorldHint=False
    ),
    "corpus-summary": ToolAnnotations(
        title="Summarize the corpus", readOnlyHint=True, idempotentHint=True, openWorldHint=False
    ),
    "video-summary": ToolAnnotations(
        title="Summarize one video", readOnlyHint=True, idempotentHint=True, openWorldHint=False
    ),
    "get-segment-context": ToolAnnotations(
        title="Get context around a moment",
        readOnlyHint=True,
        idempotentHint=True,
        openWorldHint=False,
    ),
    "get-frames": ToolAnnotations(
        title="Get keyframe images", readOnlyHint=True, idempotentHint=True, openWorldHint=False
    ),
    "index-video": ToolAnnotations(
        title="Index a video", readOnlyHint=False, idempotentHint=True, openWorldHint=True
    ),
    "job-status": ToolAnnotations(
        title="Check indexing job", readOnlyHint=True, idempotentHint=False, openWorldHint=False
    ),
    "tag-video": ToolAnnotations(
        title="Tag a video", readOnlyHint=False, idempotentHint=True, openWorldHint=False
    ),
    # openWorldHint, even though the tool itself never leaves the box: what it
    # creates is a standing instruction to fetch from the internet, and a
    # client reasoning about the annotation is reasoning about the effect.
    # idempotentHint because following the same URL twice returns the first
    # follow and creates no second row.
    "follow-channel": ToolAnnotations(
        title="Follow a channel", readOnlyHint=False, idempotentHint=True, openWorldHint=True
    ),
}

DESCRIPTIONS: dict[str, str] = {
    "search": SEARCH,
    "list-videos": LIST_VIDEOS,
    "corpus-summary": CORPUS_SUMMARY,
    "video-summary": VIDEO_SUMMARY,
    "get-segment-context": GET_SEGMENT_CONTEXT,
    "get-frames": GET_FRAMES,
    "index-video": INDEX_VIDEO,
    "job-status": JOB_STATUS,
    "tag-video": TAG_VIDEO,
    "follow-channel": FOLLOW_CHANNEL,
}
