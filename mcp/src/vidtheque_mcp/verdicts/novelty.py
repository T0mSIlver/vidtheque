"""Novelty: has the owner already seen what this video says? companion.md §3.2.

Before the model call, the video's chunk vectors are compared with the chunks
of videos the owner opened or asked about in the last `SEEN_DAYS`. A seen
video that holds a near copy of a large enough share of this one's passages
goes into the prompt as "already seen in <video>". `vec_chunks` as it is, no
worker call; every leg is bounded whatever the corpus size.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass

SEEN_DAYS = 90
# What "opened or asked about" means in signals (§2.3): the app's open, watch
# and Ask Claude, and the owner's agent reading the video.
SEEN_KINDS = ("open", "watch", "ask_claude", "mcp_read")
# Bounds: the newest seen videos, a spread of this video's chunks, a few lines.
MAX_SEEN_VIDEOS = 200
MAX_PROBES = 24
K_PER_PROBE = 3
MAX_LINES = 3
# Passage-to-passage cosine distance for "says the same thing". Tighter than
# search's query-to-passage 0.55; not yet calibrated on the live corpus.
MAX_DISTANCE = 0.25
# The share of this video's probed chunks a seen video must cover.
MIN_SHARE = 0.25


@dataclass(frozen=True)
class Overlap:
    video_id: int
    title: str
    channel: str | None
    share: float


def seen_overlap(
    conn: sqlite3.Connection, video_id: int, *, owner_id: int = 1, now: int | None = None
) -> list[Overlap]:
    """The seen videos this one overlaps strongly, largest share first."""
    since = (int(time.time()) if now is None else now) - SEEN_DAYS * 86_400
    marks = ",".join("?" for _ in SEEN_KINDS)
    seen = [
        int(r[0])
        for r in conn.execute(
            f"SELECT video_id FROM signals WHERE owner_id = ? AND at >= ?"
            f" AND kind IN ({marks}) AND video_id IS NOT NULL AND video_id != ?"
            " GROUP BY video_id ORDER BY MAX(at) DESC LIMIT ?",
            (owner_id, since, *SEEN_KINDS, video_id, MAX_SEEN_VIDEOS),
        )
    ]
    if not seen:
        return []
    probes = _probes(conn, video_id)
    if not probes:
        return []

    hits: dict[int, int] = {}
    seen_json = json.dumps(seen)
    for chunk_id in probes:
        row = conn.execute(
            "SELECT embedding FROM vec_chunks WHERE chunk_id = ?", (chunk_id,)
        ).fetchone()
        if row is None:
            continue
        near = {
            int(r["video_id"])
            for r in conn.execute(
                "SELECT video_id, distance FROM vec_chunks"
                " WHERE embedding MATCH ? AND k = ?"
                " AND video_id IN (SELECT value FROM json_each(?))",
                (row["embedding"], K_PER_PROBE, seen_json),
            )
            if float(r["distance"]) <= MAX_DISTANCE
        }
        for other in near:
            hits[other] = hits.get(other, 0) + 1

    strong = sorted(
        ((n / len(probes), other) for other, n in hits.items() if n / len(probes) >= MIN_SHARE),
        key=lambda t: (-t[0], t[1]),
    )[:MAX_LINES]
    out: list[Overlap] = []
    for share, other in strong:
        meta = conn.execute(
            "SELECT title, channel_name AS channel FROM videos WHERE id = ?",
            (other,),
        ).fetchone()
        if meta is not None:
            out.append(Overlap(other, str(meta["title"]), meta["channel"], share))
    return out


def prompt_lines(overlaps: list[Overlap]) -> str:
    return "\n".join(
        f'already seen in "{o.title}"' + (f" ({o.channel})" if o.channel else "")
        + f": about {round(o.share * 100)}% of this video's passages say the same"
        for o in overlaps
    )


def _probes(conn: sqlite3.Connection, video_id: int) -> list[int]:
    """Up to `MAX_PROBES` chunk ids spread evenly over the video."""
    ids = [
        int(r[0])
        for r in conn.execute("SELECT id FROM chunks WHERE video_id = ? ORDER BY seq", (video_id,))
    ]
    if len(ids) <= MAX_PROBES:
        return ids
    step = len(ids) / MAX_PROBES
    return [ids[int(i * step)] for i in range(MAX_PROBES)]
