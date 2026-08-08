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
        "(channel=, tags=, q=, published_after=)"
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
            "total_duration_seconds": int(float(rollup["hours"] or 0) * 3600),
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
        "features": {
            "diarization": deps.db.diarization_enabled,
            "ocr": True,
            "frame_embeddings": deps.db.vectors.enabled,
        },
        "server_version": __version__,
    }
    return json.dumps(payload, indent=2)
