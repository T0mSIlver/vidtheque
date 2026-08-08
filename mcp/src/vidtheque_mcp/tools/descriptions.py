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
Search the indexed video corpus: spoken content (transcripts), on-screen text
(OCR of keyframes) and frame imagery, with a timestamped youtu.be deep link on
every result.

USE WHEN: you need specific words, claims, numbers, code or visuals from videos
the user has indexed — "where does he explain KV caching", "which video shows
the nvidia-smi output".

DO NOT USE: to learn what is in the corpus at all (corpus-summary); to
understand one video end to end (video-summary); to read the full transcript
around a moment you already found (get-segment-context). This searches only
indexed videos, never the public YouTube catalogue.

START WITH limit=5 and add filters before raising it. content_type=all means
all three channels, always. See vidtheque://guide for the shared rules.
""".strip()

LIST_VIDEOS = """
List videos in the corpus, with optional filters. The browsable library: title,
channel, publish date, duration, tags, and which channels of data each video has
(transcript / OCR / frame embeddings).

USE WHEN: the user asks what is indexed, wants everything from one channel or
tag, or you need a video_id before calling video-summary or a scoped search.
Also use it after an empty search to check whether the video is even in the
corpus.

DO NOT USE: to find content inside videos (search); to get a picture of the
whole corpus at once (corpus-summary — one call instead of paging).

START WITH limit=20, format="tsv". See vidtheque://guide for the shared rules.
""".strip()

CORPUS_SUMMARY = """
Pre-aggregated overview of the whole video corpus: how many videos, which
channels, which topics/tags, date span, coverage gaps, and what was indexed most
recently. One call instead of paging list-videos.

USE WHEN: this is your FIRST call in a session, or the user asks what is in the
library, what topics are covered, or whether something is indexed yet. Also call
it after an empty search — it says whether the corpus is empty, still indexing,
or simply does not contain that topic.

DO NOT USE: to find content (search); for detail on one video (video-summary).

Turn off what you do not need: include_channels, include_tags, include_recent,
include_gaps, include_guidance. Every section is capped.
""".strip()

VIDEO_SUMMARY = """
Structured overview of one indexed video: chapters with timestamps and deep
links, speakers, the most informative on-screen texts, tags, and links from the
description. Use it instead of reading the whole transcript.

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
Everything around one moment in one video: the verbatim transcript window, the
on-screen text of nearby keyframes, the enclosing chapter, and frame ids you can
pass to get-frames.

USE WHEN: search or video-summary gave you a video_id and a timestamp and you
need the actual words — to quote accurately, to check context before and after,
or to decide whether a hit is relevant.

DO NOT USE: as a transcript dump (the window is capped at 300s and 4000 chars —
for broad coverage call it two or three times at different t, or use
video-summary); to find the moment in the first place (search).

START WITH window=45. Pass video_id and t exactly as a previous result gave
them.
""".strip()

GET_FRAMES = """
Fetch keyframe images from indexed videos. Returns image URLs by default; pass
return="image" only if you can render inline images.

USE WHEN: a result mentions a slide, diagram, chart, terminal or UI and the text
alone is not enough — you have frame ids from search, video-summary or
get-segment-context.

DO NOT USE: to browse a video visually (frames are keyframes, not a filmstrip);
to read text already in the payload (OCR text comes with the search result).

START WITH limit=3 and return="url". URLs are signed and expire; fetching one
costs no context. return="image" inlines base64 JPEG, capped at 4 images per
call regardless of limit — on some clients inline images cost 10-20x their
nominal token price.
""".strip()

INDEX_VIDEO = """
Add a video, playlist or channel to the corpus. Returns immediately with a job
id — indexing runs in the background and takes roughly 1-3 minutes per hour of
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
Check the status of an indexing job started by index-video. Call with no
arguments to list recent jobs.

USE WHEN: you started an index-video job and need to know whether the video is
searchable yet, or a tool told you a video is still indexing.

DO NOT USE: in a tight loop. Indexing takes minutes; poll at most every 15
seconds, and prefer telling the user "it is running, ask me again in a minute"
over polling repeatedly inside one turn.

A job is only searchable at state "done". The response says exactly what is
available before then.
""".strip()

TAG_VIDEO = """
Add or remove tags on an indexed video. Tags are namespaced — topic:, person:,
project:, source:, lang:, series: — and are used as filters by search,
list-videos and corpus-summary.

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
# index) and true for index-video (it fetches from the internet).
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
}
