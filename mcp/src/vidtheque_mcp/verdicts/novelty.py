"""Novelty: has the owner already seen what this video says? companion.md §3.2.

Before the model call, the video's chunk vectors are compared with the chunks
of videos the owner opened or asked about in the last `SEEN_DAYS`. A seen
video that holds a near copy of a large enough share of this one's passages
goes into the prompt as "already seen in <video>". The seen videos the probes
hit are then compared passage by passage, and the stretches this video repeats
are stored with the verdict, so the video page names them and its moment links
start after them (#171). `vec_chunks` as it is, no worker call; every leg is
bounded whatever the corpus size.
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
# Spans: the seen videos compared passage by passage, the chunks compared on
# each side (spread evenly past that: about two hours at the 30 s stride), and
# the matched chunks a span needs. One 45 s chunk alone may be a stock phrase.
SPAN_VIDEOS = 3
SPAN_CHUNKS = 240
MIN_SPAN_CHUNKS = 2
MAX_SPANS = 8


@dataclass(frozen=True)
class Overlap:
    video_id: int
    title: str
    channel: str | None
    share: float


@dataclass(frozen=True)
class Span:
    """`start_s`–`end_s` of this video says again what `video_id` says from `seen_s`."""

    video_id: int
    start_s: float
    end_s: float
    seen_s: float


@dataclass(frozen=True)
class Seen:
    overlaps: list[Overlap]
    spans: list[Span]


def seen(
    conn: sqlite3.Connection, video_id: int, *, owner_id: int = 1, now: int | None = None
) -> Seen:
    """The seen videos this one overlaps strongly, largest share first, and the
    stretches it repeats from the seen videos the probes hit most."""
    since = (int(time.time()) if now is None else now) - SEEN_DAYS * 86_400
    marks = ",".join("?" for _ in SEEN_KINDS)
    seen_ids = [
        int(r[0])
        for r in conn.execute(
            f"SELECT video_id FROM signals WHERE owner_id = ? AND at >= ?"
            f" AND kind IN ({marks}) AND video_id IS NOT NULL AND video_id != ?"
            " GROUP BY video_id ORDER BY MAX(at) DESC LIMIT ?",
            (owner_id, since, *SEEN_KINDS, video_id, MAX_SEEN_VIDEOS),
        )
    ]
    if not seen_ids:
        return Seen([], [])
    probes = _probes(conn, video_id)
    if not probes:
        return Seen([], [])

    hits: dict[int, int] = {}
    seen_json = json.dumps(seen_ids)
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
    compared = sorted(hits, key=lambda other: (-hits[other], other))[:SPAN_VIDEOS]
    return Seen(out, repeated_spans(conn, video_id, compared))


def repeated_spans(conn: sqlite3.Connection, video_id: int, others: list[int]) -> list[Span]:
    """The stretches of `video_id` whose chunks lie within `MAX_DISTANCE` of a
    chunk of one of `others`, in order. Runs of matched chunks merge across one
    unmatched chunk; a run shorter than `MIN_SPAN_CHUNKS` is dropped."""
    mine = _spread(chunks(conn, video_id), SPAN_CHUNKS)
    spans: list[Span] = []
    for other in others:
        theirs = _spread(chunks(conn, other), SPAN_CHUNKS)
        spans.extend(spans_between(conn, mine, theirs, other, MIN_SPAN_CHUNKS))
    spans.sort(key=lambda s: (s.start_s, s.video_id))
    return spans[:MAX_SPANS]


def spans_between(
    conn: sqlite3.Connection,
    mine: list[tuple[int, float, float]],
    theirs: list[tuple[int, float, float]],
    other: int,
    min_chunks: int,
) -> list[Span]:
    """The runs of `mine` (chunk id, start, end, in order) that `theirs` says
    too, merged across one unmatched chunk, each at least `min_chunks` long."""
    near = nearest(conn, [c[0] for c in mine], [c[0] for c in theirs])
    starts = {c[0]: c[1] for c in theirs}
    runs: list[list[int]] = []
    for i, chunk in enumerate(mine):
        if chunk[0] not in near:
            continue
        if runs and i - runs[-1][-1] <= 2:
            runs[-1].append(i)
        else:
            runs.append([i])
    return [
        Span(other, mine[run[0]][1], mine[run[-1]][2], starts[near[mine[run[0]][0]]])
        for run in runs
        if len(run) >= min_chunks
    ]


def nearest(conn: sqlite3.Connection, mine: list[int], theirs: list[int]) -> dict[int, int]:
    """Each chunk of `mine` with a chunk of `theirs` within `MAX_DISTANCE`,
    mapped to its nearest one. The vectors are read once and compared in one
    statement: a join on `vec_chunks` would rescan it per row."""
    if not mine or not theirs:
        return {}
    a = _vectors(conn, mine)
    b = _vectors(conn, theirs)
    if not a or not b:
        return {}
    rows = conn.execute(
        f"WITH a(id, e) AS MATERIALIZED (VALUES {','.join('(?,?)' for _ in a)}),"
        f" b(id, e) AS MATERIALIZED (VALUES {','.join('(?,?)' for _ in b)})"
        " SELECT a.id, b.id, vec_distance_cosine(a.e, b.e) AS d FROM a, b"
        " WHERE d <= ? ORDER BY a.id, d",
        [*(x for r in a for x in r), *(x for r in b for x in r), MAX_DISTANCE],
    )
    out: dict[int, int] = {}
    for mine_id, their_id, _ in rows:
        out.setdefault(int(mine_id), int(their_id))
    return out


def chunks(conn: sqlite3.Connection, video_id: int) -> list[tuple[int, float, float]]:
    return [
        (int(r[0]), float(r[1]), float(r[2]))
        for r in conn.execute(
            "SELECT id, start_s, end_s FROM chunks WHERE video_id = ? ORDER BY seq", (video_id,)
        )
    ]


def _vectors(conn: sqlite3.Connection, ids: list[int]) -> list[tuple[int, bytes]]:
    return [
        (int(r[0]), r[1])
        for r in conn.execute(
            "SELECT chunk_id, embedding FROM vec_chunks"
            " WHERE chunk_id IN (SELECT value FROM json_each(?))",
            (json.dumps(ids),),
        )
    ]


def _spread[T](items: list[T], cap: int) -> list[T]:
    if len(items) <= cap:
        return items
    step = len(items) / cap
    return [items[int(i * step)] for i in range(cap)]


def prompt_lines(overlaps: list[Overlap]) -> str:
    return "\n".join(
        f'already seen in "{o.title}"' + (f" ({o.channel})" if o.channel else "")
        + f": about {round(o.share * 100)}% of this video's passages say the same"
        for o in overlaps
    )


def _probes(conn: sqlite3.Connection, video_id: int) -> list[int]:
    """Up to `MAX_PROBES` chunk ids spread evenly over the video."""
    return _spread([c[0] for c in chunks(conn, video_id)], MAX_PROBES)
