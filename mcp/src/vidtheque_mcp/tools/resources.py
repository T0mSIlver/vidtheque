"""The three resources: `vidtheque://corpus`, `://context`, `://guide`.

Three, deliberately — the same count screenpipe settled on. Reads stay Tools:
their proposal to migrate reads to Resources died as stale, and client support
for resources is still far behind tools.

`vidtheque://guide` also carries the **shared rules** that DECISIONS.md lifted
out of the nine tool descriptions, so this file is where "never fabricate ids"
and "two time axes" live exactly once.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

from .. import __version__
from ..db import queries
from ..text import duration_clock, iso_day, iso_z
from . import corpus_state
from .base import Deps

CORPUS_ROW_CAP = 200
# tool-surface §3.7. Reserved, not exhaustive of what a corpus uses — the
# context resource publishes the ones in use first.
RESERVED_NAMESPACES = ("topic", "person", "project", "source", "lang", "series")

# The guide is a template, not a constant, because two of its sentences name a
# write tool. A read-only deployment does not register `index-video` or
# `tag-video` (public/readonly.py), and the guide went on teaching
# `index-video → job-status` to callers whose `tools/list` did not contain it
# (demo-queries §9.1.8): the document that exists to orient a model was the one
# sending it at a tool that is not there. `guide(deps)` resolves both against
# the deployment's own mask.
ADDING = "{{ADDING}}"
MISSING = "{{MISSING}}"

_ADDING_WRITABLE = (
    "Adding to the library: index-video → job-status. Nothing is searchable until the\n"
    'job reports "done".'
)
_ADDING_READONLY = (
    "This server is **read-only**: it exposes no tool that adds, re-indexes or\n"
    "tags a video, and the library is exactly what is listed below. Nothing you\n"
    "can call will change it."
)
_MISSING_WRITABLE = (
    "- **This searches only what is indexed.** It is not the YouTube catalogue. If\n"
    "  something is missing, the answer is index-video, not a guess."
)
_MISSING_READONLY = (
    "- **This searches only what is indexed.** It is not the YouTube catalogue. If\n"
    "  something is missing, say so plainly — this read-only server cannot add it,\n"
    "  and a plausible answer from memory is not an answer from this corpus."
)

GUIDE_TEMPLATE = """# Using vidtheque

A persistent, searchable index of videos the user has chosen to keep. Work from
the top down — each step narrows what the next one has to read.

| Step | Tool | When |
|---|---|---|
| 1 | corpus-summary | "what's in the library?", "do I have anything on X?", and after any empty search |
| 2 | search | you need specific words, claims or visuals. START limit=5 |
| 3 | video-summary | you have a video_id and need its structure or a timestamp to aim at |
| 4 | get-segment-context | you have (video_id, t) and need the actual words |
| 5 | get-frames | text is not enough and you have frame ids. return="url" unless you render images |

{{ADDING}}

Step 3 is the one people skip. When the question is *where* a video discusses
something, `video-summary`'s chapter list usually names the moment in one call —
faster than walking `get-segment-context` windows outward from a search hit.

## Resources

There are exactly three, and this is the list:

- `vidtheque://guide` — this document.
- `vidtheque://corpus` — the whole library as TSV, 200 rows, newest first.
- `vidtheque://context` — JSON: current time, precomputed date boundaries,
  corpus counts, id formats, and the server-side limits below.

There are no other URIs. `vidtheque://video/<id>` and the like do not exist —
drill down with tools, not with invented resource URIs.

## Server-side limits

Values outside these are **clamped silently**, not rejected: asking for more
does not get you more, it gets you the cap with no warning.

| Parameter | Range | Default |
|---|---|---|
| `search limit` | 1–50 | 10 |
| `search max_per_video` | 1–20 | 3 |
| `list-videos limit` | 1–100 | 20 |
| `get-frames limit` (span mode) | 1–12 | 3 |
| `get-frames frame_ids` | ≤ 12 ids | — |
| `get-frames max_text_chars` | `0` or 120–2000 | 300 |
| `get-segment-context window` | 5–300 s | 45 |

To get past a cap, page with `offset` — the pagination line tells you the next
one. To check what you actually got, read the printed count, never the number
you asked for.

## Rules

- **Never fabricate ids or timestamps.** Only use video_id, frame_id, cue_id and
  t values that appeared in an actual result. A plausible-looking YouTube id that
  came from your memory is not in this corpus.
{{MISSING}}
- Two time axes: `published_after`/`published_before` choose videos by upload
  date; `t_start`/`t_end` choose seconds inside a video. They are not
  interchangeable, and neither is the pagination `offset`.
- `channel` and `video_title` are case-insensitive substrings. The tag filter is
  `tags=` — plural, comma-separated, AND semantics. `tag=` is not a parameter and
  is dropped silently like any other unknown name.
- Ordering defaults to relevance. Pass `order=recency` only if the user asked for
  "latest" or "newest".
- Start with `limit=5` and `max_text_chars=500`. Raise them when the first page
  proves the query is right.
- `max_text_chars=0` opts out of truncation entirely.
- Auto-generated captions are noisy: unusual spellings, no punctuation, wrong
  proper nouns. Prefer two or three words over an exact long phrase, and check
  `get-segment-context` before quoting anything verbatim.
- Every timestamped result carries a `https://youtu.be/<id>?t=<s>` link. Give the
  user the link, not just the timestamp. The link is deliberately **2 s early**:
  `?t=` is the result's start minus a lead, so the player has seeked by the time
  the words begin. `start:` in the payload is the true position; the two
  disagreeing by 2 s is the lead, not a bug.
- `search` never returns images. Frame ids do; `get-frames` turns them into URLs.
- Read the pagination line: `Results: 10/~40+ (use offset=10 for more)` tells you
  your next call.
- A `note:` line means a leg was skipped and why. `all` always means all: a
  missing leg is always announced, never silently dropped.
- Read the `Legs:` counts. `transcript 0` next to on-screen hits usually means
  the phrasing differs, not that the topic is unspoken — slides write
  `hasFather`, `owl:FunctionalProperty`, `CVE-2026-22812`; speech says "has
  father", "functional property". Re-search the spoken phrasing, or open
  `get-segment-context` at the top on-screen hit.
- **On-screen text is a flat reading-order join, and it is capped per frame.**
  Tables, code, bullet lists and quote/attribution pairs come back unscrambled
  from the layout that made them readable, and OCR mangles digits and bullet
  glyphs (`8.8` → `8.&`, a rank `1 ●` → `10`). When the answer depends on which
  value sits in which cell, or on a number, read the image: `get-frames`
  `return="url"` and open the URL. `get-frames max_text_chars=0` gives the
  frame's every line in reading order — but the picture is still the only place
  the layout survives.
- `get-frames limit` bounds the `video_id` span mode only. Ids you name are all
  fetched, up to 12, in the order you asked for; a bad one comes back on a
  `failed:` line rather than vanishing.
- Use only parameter names a payload printed or the tool schema lists. An
  unknown parameter is dropped silently, so a call that "worked" may have
  ignored the filter you thought you applied.
"""


def _render_guide(writable: bool) -> str:
    return GUIDE_TEMPLATE.replace(
        ADDING, _ADDING_WRITABLE if writable else _ADDING_READONLY
    ).replace(MISSING, _MISSING_WRITABLE if writable else _MISSING_READONLY)


# The full deployment's guide, resolved once. Kept as a module constant because
# it is the canonical text — `docs/design/tool-surface.md` §5.3 quotes it.
GUIDE = _render_guide(writable=True)
GUIDE_READONLY = _render_guide(writable=False)


def guide(deps: Deps) -> str:
    """`vidtheque://guide`, resolved against what this deployment registers."""
    return GUIDE if deps.offers("index-video") else GUIDE_READONLY


async def corpus_resource(deps: Deps) -> str:
    """`text/tab-separated-values`, capped at 200 rows, newest first."""
    rollup = await deps.db.read(queries.corpus_rollup)
    pool = await deps.db.read(lambda c: queries.resolve_videos(c, queries.CorpusFilter()))
    rows = await deps.db.read(
        lambda c: queries.list_videos(
            c, pool, None, "any", "recency", CORPUS_ROW_CAP, 0, deps.settings.candidate_cap
        )
    )
    rows = rows[:CORPUS_ROW_CAP]
    tag_map = await deps.db.read(lambda c: queries.video_tags(c, [int(r["id"]) for r in rows]))
    total = int(rollup["videos_ready"]) + int(rollup["videos_pending"])
    # A column of 200 empty cells is an advertisement for a feature this corpus
    # does not have (demo-queries §9.1.9: every tag surface empty across 75
    # videos). The rows are already in hand, so the probe is free — and the
    # column comes back the moment one video is tagged.
    tagged = any(tag_map.get(int(r["id"])) for r in rows)

    header = ["video_id", "title", "channel", "published", "duration", "coverage"]
    lines = [
        f"# vidtheque corpus · {total} videos · {float(rollup['hours'] or 0):.1f}h · "
        f"generated {iso_z(int(datetime.now(UTC).timestamp()))}",
        "\t".join(header + (["tags"] if tagged else [])),
    ]
    for row in rows:
        coverage = (
            ("t" if row["has_transcript"] else "-")
            + ("o" if row["has_ocr"] else "-")
            + ("f" if row["has_frames"] else "-")
        )
        cells = [
            str(row["public_id"]),
            str(row["title"]).replace("\t", " "),
            str(row["channel_name"] or ""),
            iso_day(row["published_at"]),
            duration_clock(row["duration_s"]),
            coverage,
        ]
        if tagged:
            cells.append(",".join(tag_map.get(int(row["id"]), [])))
        lines.append("\t".join(cells))
    # Say what you truncated and name the tool that narrows it.
    lines.append(
        f"# showing {len(rows)} of {total} — narrow with the list-videos tool "
        "(channel=, tags=, q=, published_after=) · read vidtheque://guide for the tool flow"
    )
    return "\n".join(lines)


async def context_resource(deps: Deps) -> str:
    """`application/json`. Exists so the model never does date arithmetic."""
    now = datetime.now(UTC)
    rollup = await deps.db.read(queries.corpus_rollup)
    gap_info = await deps.db.read(queries.gaps)
    pool = await deps.db.read(lambda c: queries.resolve_videos(c, queries.CorpusFilter()))
    channels = await deps.db.read(lambda c: queries.channel_rollup(c, pool, 4))
    tags = await deps.db.read(lambda c: queries.tag_rollup(c, pool, 6))
    total = int(rollup["videos_ready"]) + int(rollup["videos_pending"])
    # The same `data_status` word `corpus-summary` prints and `search`'s empty
    # state prints, from the same derivation — this resource is the first call
    # of most sessions, and it was the first of the three contradicting answers
    # (demo-queries §9.1.4).
    state = await corpus_state.read_corpus_state(deps, total, gap_info)

    payload = {
        "current_time": iso_z(int(now.timestamp())),
        "timezone": deps.settings.timezone,
        "timestamps": {
            "today_start": iso_z(
                int(datetime.combine(now.date(), datetime.min.time(), tzinfo=UTC).timestamp())
            ),
            "yesterday_start": iso_z(
                int(
                    datetime.combine(
                        now.date() - timedelta(days=1), datetime.min.time(), tzinfo=UTC
                    ).timestamp()
                )
            ),
            "one_week_ago": iso_z(int((now - timedelta(days=7)).timestamp())),
            "one_month_ago": iso_z(int((now - timedelta(days=30)).timestamp())),
            "one_year_ago": iso_z(int((now - timedelta(days=365)).timestamp())),
        },
        "corpus": {
            "videos": total,
            "total_duration_seconds": int(float(rollup["duration_s"] or 0)),
            "first_published": iso_day(rollup["oldest_published"]),
            "last_published": iso_day(rollup["newest_published"]),
            "last_indexed": iso_z(rollup["last_indexed"]),
            # Four numbers where there was one, because "5 queued" was read as
            # "5 indexing" by every consumer including this server's own prose.
            # `active` is queued+running; `deferred` is the subset held behind a
            # future `jobs.not_before`, which is work that will resume at
            # `deferred_until` and is not happening now.
            "active_jobs": state.queue.active,
            "running_jobs": state.queue.running,
            "deferred_jobs": state.queue.deferred,
            "deferred_until": iso_z(state.queue.deferred_until),
            "data_status": state.word,
        },
        "channels_top": [str(r["channel"]) for r in channels],
        "id_formats": {
            "video_id": "11-char YouTube id, e.g. kCc8FmEb1nY",
            "frame_id": "<video_id>-<5-digit keyframe ordinal>, e.g. kCc8FmEb1nY-00703",
            "job_id": "job_ + 12 hex",
        },
        "deep_link_format": "https://youtu.be/<video_id>?t=<seconds>",
        # The three URIs, named here because a client that cannot list
        # resources otherwise leaves the model guessing (and guessing a URI is
        # what produces `vidtheque://video/<id>`).
        "resources": ["vidtheque://corpus", "vidtheque://context", "vidtheque://guide"],
        # Clamps are silent by design (a request over the cap returns the cap),
        # so the caps have to be readable somewhere before the call.
        "limits": {
            "search.limit": [1, 50],
            "search.max_per_video": [1, 20],
            "list-videos.limit": [1, 100],
            "get-frames.limit": [1, 12],
            "get-frames.frame_ids": [1, 12],
            "get-frames.max_text_chars": [120, 2000],
            "get-segment-context.window": [5, 300],
            "out_of_range": "clamped silently — read the printed count, not the one requested",
        },
        "features": {
            "diarization": deps.db.diarization_enabled,
            "ocr": True,
            "frame_embeddings": deps.db.vectors.enabled,
        },
        "server_version": __version__,
    }
    # `tag_namespaces` advertised six namespaces over a corpus with no tag on
    # any video (demo-queries §9.1.9), which is how the tourist came to invent a
    # `tag=` parameter for a filter that could not have matched. It is published
    # when there is something to filter on — the namespaces actually in use,
    # ahead of the reserved list — or when this deployment registers `tag-video`
    # and the caller could create one.
    in_use = sorted({str(r["full"]).split(":", 1)[0] for r in tags})
    if in_use:
        payload["tag_namespaces"] = in_use + [
            ns for ns in RESERVED_NAMESPACES if ns not in in_use
        ]
    elif deps.offers("tag-video"):
        payload["tag_namespaces"] = list(RESERVED_NAMESPACES)
    return json.dumps(payload, indent=2)
