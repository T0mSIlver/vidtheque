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
from .base import Deps

CORPUS_ROW_CAP = 200

GUIDE = """# Using vidtheque

A persistent, searchable index of videos the user has chosen to keep. Work from
the top down — each step narrows what the next one has to read.

| Step | Tool | When |
|---|---|---|
| 1 | corpus-summary | "what's in the library?", "do I have anything on X?", and after any empty search |
| 2 | search | you need specific words, claims or visuals. START limit=5 |
| 3 | video-summary | you have a video_id and need its structure or a timestamp to aim at |
| 4 | get-segment-context | you have (video_id, t) and need the actual words |
| 5 | get-frames | text is not enough and you have frame ids. return="url" unless you render images |

Adding to the library: index-video → job-status. Nothing is searchable until the
job reports "done".

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
| `get-frames limit` | 1–12 | 3 |
| `get-segment-context window` | 5–300 s | 45 |

To get past a cap, page with `offset` — the pagination line tells you the next
one. To check what you actually got, read the printed count, never the number
you asked for.

## Rules

- **Never fabricate ids or timestamps.** Only use video_id, frame_id, cue_id and
  t values that appeared in an actual result. A plausible-looking YouTube id that
  came from your memory is not in this corpus.
- **This searches only what is indexed.** It is not the YouTube catalogue. If
  something is missing, the answer is index-video, not a guess.
- Two time axes: `published_after`/`published_before` choose videos by upload
  date; `t_start`/`t_end` choose seconds inside a video. They are not
  interchangeable, and neither is the pagination `offset`.
- `channel` and `video_title` are case-insensitive substrings.
- Ordering defaults to relevance. Pass `order=recency` only if the user asked for
  "latest" or "newest".
- Start with `limit=5` and `max_text_chars=500`. Raise them when the first page
  proves the query is right.
- `max_text_chars=0` opts out of truncation entirely.
- Auto-generated captions are noisy: unusual spellings, no punctuation, wrong
  proper nouns. Prefer two or three words over an exact long phrase, and check
  `get-segment-context` before quoting anything verbatim.
- Every timestamped result carries a `https://youtu.be/<id>?t=<s>` link. Give the
  user the link, not just the timestamp.
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
  `return="url"` and open the URL. There is no `max_text_chars` on `get-frames`
  — the picture is the un-truncated text.
- Use only parameter names a payload printed or the tool schema lists. An
  unknown parameter is dropped silently, so a call that "worked" may have
  ignored the filter you thought you applied.
"""


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

    lines = [
        f"# vidtheque corpus · {total} videos · {float(rollup['hours'] or 0):.1f}h · "
        f"generated {iso_z(int(datetime.now(UTC).timestamp()))}",
        "video_id\ttitle\tchannel\tpublished\tduration\tcoverage\ttags",
    ]
    for row in rows:
        coverage = (
            ("t" if row["has_transcript"] else "-")
            + ("o" if row["has_ocr"] else "-")
            + ("f" if row["has_frames"] else "-")
        )
        lines.append(
            "\t".join(
                [
                    str(row["public_id"]),
                    str(row["title"]).replace("\t", " "),
                    str(row["channel_name"] or ""),
                    iso_day(row["published_at"]),
                    duration_clock(row["duration_s"]),
                    coverage,
                    ",".join(tag_map.get(int(row["id"]), [])),
                ]
            )
        )
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
    total = int(rollup["videos_ready"]) + int(rollup["videos_pending"])

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
            "active_jobs": gap_info["active_jobs"],
            "data_status": "indexing" if gap_info["active_jobs"] else ("empty" if not total else "ok"),
        },
        "channels_top": [str(r["channel"]) for r in channels],
        "tag_namespaces": ["topic", "person", "project", "source", "lang", "series"],
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
    return json.dumps(payload, indent=2)
