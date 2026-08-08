"""The query shapes from index-schema §4. Every one of them ran on the fixture.

Rules that hold everywhere in this module:

* ``:name`` bound parameters — no string interpolation anywhere near SQL.
* Filters resolve to a video-id set **first** (§4.1), then the FTS and vector
  legs get a bounded id list.
* Every expensive CTE is ``MATERIALIZED``: without it SQLite may flatten the CTE
  into the join, re-evaluate the FTS scan per outer row, and — worse — the
  ``LIMIT`` stops meaning "cap the candidates".
* ``has_more`` comes from ``LIMIT n+1``; the count probe runs over the *same*
  expression, capped at ``offset + limit + 30``.
"""

from __future__ import annotations

import json
import re
import sqlite3
import struct
from dataclasses import dataclass, field
from typing import Any, Iterable, Sequence

RRF_K = 60.0
CLUSTER_MAX_SECONDS = 120.0

# ---------------------------------------------------------------------------
# FTS5 query sanitising
#
# Verified: bare `nvidia-smi` and `torch.compile` are OperationalError at parse
# time (`-` is NOT, `.` is a column qualifier). We quote-wrap user terms while
# preserving the operators we document.

# `NEAR` is deliberately NOT an operator here: FTS5 spells it `NEAR(a b, 5)`,
# so a bare `a NEAR b` is a parse error either way. Quoting it as a term is the
# behaviour a user typing the word "near" expects.
_OPERATORS = {"AND", "OR", "NOT"}
_TOKEN = re.compile(r'"[^"]*"|\S+')


def sanitize_fts(query: str) -> str:
    """Quote-wrap user terms, keeping AND/OR/NOT, "phrases" and prefix*.

    User text is not a syntax. A query that reduces to nothing (all punctuation,
    or a dangling operator) returns "" — the caller drops the leg rather than
    handing FTS5 an expression it cannot parse.
    """
    parts: list[str] = []
    for raw in _TOKEN.findall(query.strip()):
        if raw.startswith('"'):
            phrase = raw if raw.endswith('"') and len(raw) > 1 else raw + '"'
            if phrase.strip('"').strip():
                parts.append(phrase)
            continue
        if raw.upper() in _OPERATORS:
            parts.append(raw.upper())
            continue
        trailing_star = raw.endswith("*")
        term = (raw[:-1] if trailing_star else raw).strip("()")
        if not term:
            continue
        quoted = '"' + term.replace('"', "") + '"'
        parts.append(quoted + "*" if trailing_star else quoted)

    # An operator needs a term on both sides, so drop dangling and doubled ones.
    cleaned: list[str] = []
    for token in parts:
        if token in _OPERATORS and (not cleaned or cleaned[-1] in _OPERATORS):
            continue
        cleaned.append(token)
    while cleaned and cleaned[-1] in _OPERATORS:
        cleaned.pop()
    return " ".join(cleaned)


def is_browse_query(query: str | None) -> bool:
    """Bare `*`/empty with filters = browse mode: skip FTS entirely, fast path."""
    return query is None or query.strip() in {"", "*"}


# ---------------------------------------------------------------------------
# §4.1 — resolve corpus filters to a video-id set


@dataclass
class CorpusFilter:
    channel: str | None = None
    video_title: str | None = None
    published_after: int | None = None
    published_before: int | None = None
    indexed_after: int | None = None
    indexed_before: int | None = None
    video_ids: Sequence[str] = field(default_factory=tuple)
    tags: Sequence[str] = field(default_factory=tuple)

    def as_params(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "video_title": self.video_title,
            "published_after": self.published_after,
            "published_before": self.published_before,
            "indexed_after": self.indexed_after,
            "indexed_before": self.indexed_before,
        }


_RESOLVE_SQL = """
SELECT id, public_id FROM videos
WHERE (:channel          IS NULL OR channel_lc LIKE '%' || lower(:channel) || '%')
  AND (:video_title      IS NULL OR title_lc   LIKE '%' || lower(:video_title) || '%')
  AND (:published_after  IS NULL OR published_at >= :published_after)
  AND (:published_before IS NULL OR published_at <  :published_before)
  AND (:indexed_after    IS NULL OR indexed_at   >= :indexed_after)
  AND (:indexed_before   IS NULL OR indexed_at   <  :indexed_before)
  AND index_state IN ('ready','stale')
"""

# The tag join is the shape that bit screenpipe hardest. Cap first, join second;
# HAVING COUNT(DISTINCT t.id) = :n_tags is AND semantics without N self-joins.
_TAGGED_SQL = """
WITH tagged AS MATERIALIZED (
  SELECT vt.video_id
  FROM video_tags vt
  JOIN tags t ON t.id = vt.tag_id
  WHERE t.full IN (SELECT value FROM json_each(:tags))
  GROUP BY vt.video_id
  HAVING COUNT(DISTINCT t.id) = :n_tags
)
SELECT video_id FROM tagged
"""


def resolve_videos(conn: sqlite3.Connection, flt: CorpusFilter) -> list[int]:
    """Collapse the corpus-axis filters into a small video-id list."""
    rows = conn.execute(_RESOLVE_SQL, flt.as_params()).fetchall()
    ids = [int(r["id"]) for r in rows]

    if flt.video_ids:
        wanted = set(flt.video_ids)
        by_public = {r["public_id"]: int(r["id"]) for r in rows}
        ids = [by_public[p] for p in by_public if p in wanted]

    if flt.tags:
        tagged = {
            int(r["video_id"])
            for r in conn.execute(
                _TAGGED_SQL, {"tags": json.dumps(list(flt.tags)), "n_tags": len(flt.tags)}
            )
        }
        ids = [i for i in ids if i in tagged]

    return ids


def lookup_video(conn: sqlite3.Connection, public_id: str) -> sqlite3.Row | None:
    return conn.execute("SELECT * FROM videos WHERE public_id = ?", (public_id,)).fetchone()


def lookup_video_ids(conn: sqlite3.Connection, public_ids: Iterable[str]) -> dict[str, int]:
    ids = list(public_ids)
    if not ids:
        return {}
    rows = conn.execute(
        "SELECT id, public_id FROM videos WHERE public_id IN "
        "(SELECT value FROM json_each(?))",
        (json.dumps(ids),),
    ).fetchall()
    return {r["public_id"]: int(r["id"]) for r in rows}


# ---------------------------------------------------------------------------
# §4.3 — full hybrid: two legs, RRF fusion, clustering, diversity, has_more

# The three legs are composed rather than switched on a bound flag: `MATCH` is
# evaluated whether or not a guard is true, and an empty FTS5 query string (or
# an empty vec0 blob) is a parse error, not an empty result.

_LEG_FTS = """
fts_hits AS MATERIALIZED (
  SELECT c.id AS cue_id, c.video_id, c.start_s, c.end_s, c.text,
         ROW_NUMBER() OVER (ORDER BY f.rank) AS r
  FROM cues_fts f
  JOIN cues c ON c.id = f.rowid
  WHERE f.cues_fts MATCH :q
  ORDER BY f.rank
  LIMIT :candidate_cap
)"""

_LEG_VEC = """
vec_hits AS MATERIALIZED (
  SELECT chunk_id, distance
  FROM vec_chunks
  WHERE embedding MATCH :qvec AND k = :k_vec
),
vec_cues AS MATERIALIZED (
  SELECT c.id AS cue_id, c.video_id, c.start_s, c.end_s, c.text,
         ROW_NUMBER() OVER (ORDER BY vh.distance) AS r
  FROM vec_hits vh
  JOIN chunks ch ON ch.id = vh.chunk_id
  JOIN cues   c  ON c.id BETWEEN ch.first_cue_id AND ch.last_cue_id
)"""

_LEG_BROWSE = """
browse_hits AS MATERIALIZED (
  SELECT c.id AS cue_id, c.video_id, c.start_s, c.end_s, c.text,
         ROW_NUMBER() OVER (ORDER BY c.video_id, c.start_s) AS r
  FROM cues c
  WHERE c.video_id IN (SELECT value FROM json_each(:video_ids))
  LIMIT :candidate_cap
)"""

_SCORED_LEG = """    SELECT cue_id, video_id, start_s, end_s, text,
           1.0 / ((SELECT rrf_k FROM params) + r) AS s FROM {leg}"""

_TRANSCRIPT_HEAD = """
WITH
params AS (SELECT :rrf_k AS rrf_k, :cluster_gap AS gap_s,
                  :cluster_max AS max_span, :max_per_video AS per_video),
{legs},

scored AS (
  SELECT cue_id, video_id, start_s, end_s, text, SUM(s) AS score FROM (
{unions}
  ) GROUP BY cue_id
),

filtered AS (
  SELECT s.* FROM scored s
  WHERE s.video_id IN (SELECT value FROM json_each(:video_ids))
    AND (:t_start   IS NULL OR s.end_s   >= :t_start)
    AND (:t_end     IS NULL OR s.start_s <= :t_end)
    AND (:min_chars IS NULL OR length(s.text) >= :min_chars)
    AND (:max_chars IS NULL OR length(s.text) <= :max_chars)
),

-- adjacent-cue clustering: gaps-and-islands, bounded on BOTH axes. Gap-only
-- clustering collapsed an entire video into one 1199.8 s "result" on the
-- fixture; the fixed grid guarantees a hard ceiling.
marked AS (
  SELECT *,
    CASE WHEN :cluster_gap > 0
          AND start_s - LAG(end_s) OVER w <= (SELECT gap_s FROM params)
          AND CAST(start_s / (SELECT max_span FROM params) AS INTEGER)
            = CAST(LAG(start_s) OVER w / (SELECT max_span FROM params) AS INTEGER)
         THEN 0 ELSE 1 END AS is_new
  FROM filtered
  WINDOW w AS (PARTITION BY video_id ORDER BY start_s)
),
islands AS (
  SELECT *, SUM(is_new) OVER (PARTITION BY video_id ORDER BY start_s
                              ROWS UNBOUNDED PRECEDING) AS island
  FROM marked
),
clustered AS (
  SELECT video_id, island,
         MIN(start_s) AS start_s, MAX(end_s) AS end_s,
         group_concat(text, ' ' ORDER BY start_s) AS text,
         json_group_array(cue_id ORDER BY start_s) AS cue_ids,
         MAX(score)  AS score,
         COUNT(*)    AS n_cues
  FROM islands
  GROUP BY video_id, island
),

-- per-video diversity, applied BEFORE the page slice: clustering first means a
-- ten-cue sentence counts once against max_per_video.
capped AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY video_id
                               ORDER BY score DESC, start_s) AS rn
  FROM clustered
)
SELECT video_id, start_s, end_s, text, cue_ids, score, n_cues
FROM capped
WHERE rn <= (SELECT per_video FROM params)
"""


def _transcript_sql(*, do_fts: bool, do_vec: bool, do_browse: bool) -> str:
    legs: list[str] = []
    unions: list[str] = []
    if do_fts:
        legs.append(_LEG_FTS)
        unions.append(_SCORED_LEG.format(leg="fts_hits"))
    if do_vec:
        legs.append(_LEG_VEC)
        unions.append(_SCORED_LEG.format(leg="vec_cues"))
    if do_browse:
        legs.append(_LEG_BROWSE)
        unions.append(_SCORED_LEG.format(leg="browse_hits"))
    return _TRANSCRIPT_HEAD.format(
        legs=",".join(legs), unions="\n    UNION ALL\n".join(unions)
    )


@dataclass
class SearchParams:
    q: str | None
    video_ids: Sequence[int]
    qvec: bytes | None = None
    limit: int = 10
    offset: int = 0
    max_per_video: int = 3
    cluster_gap: float = 8.0
    candidate_cap: int = 5000
    t_start: float | None = None
    t_end: float | None = None
    min_chars: int | None = None
    max_chars: int | None = None
    k_vec: int = 200

    @property
    def browse(self) -> bool:
        return is_browse_query(self.q)

    @property
    def legs(self) -> dict[str, bool]:
        # A query made entirely of punctuation sanitises to nothing. That is not
        # an error and not a browse — it is a lexical leg with no terms, so the
        # leg is dropped rather than handed an empty FTS5 expression (which is a
        # parse error, not an empty result).
        return {
            "do_fts": not self.browse and bool(sanitize_fts(self.q or "")),
            "do_vec": self.qvec is not None and not self.browse,
            "do_browse": self.browse,
        }

    def bind(self) -> dict[str, Any]:
        return {
            "rrf_k": RRF_K,
            "cluster_gap": self.cluster_gap,
            "cluster_max": CLUSTER_MAX_SECONDS,
            "max_per_video": self.max_per_video,
            "q": sanitize_fts(self.q or ""),
            "qvec": self.qvec if self.qvec is not None else b"",
            "k_vec": self.k_vec,
            "candidate_cap": self.candidate_cap,
            "video_ids": json.dumps(list(self.video_ids)),
            "t_start": self.t_start,
            "t_end": self.t_end,
            "min_chars": self.min_chars,
            "max_chars": self.max_chars,
            "limit": self.limit + 1,  # the +1 is has_more
            "offset": self.offset,
        }


def search_transcript(conn: sqlite3.Connection, params: SearchParams) -> list[sqlite3.Row]:
    legs = params.legs
    if not any(legs.values()):
        return []
    sql = _transcript_sql(**legs) + "\nORDER BY score DESC, video_id, start_s\nLIMIT :limit OFFSET :offset"
    return conn.execute(sql, params.bind()).fetchall()


def probe_transcript(conn: sqlite3.Connection, params: SearchParams, headroom: int = 30) -> tuple[int, bool]:
    """Bounded count probe over the SAME expression as the page query.

    It cannot disagree with the page because it *is* the page's filter; a
    duplicated count query is screenpipe's standing correctness liability.
    `~40+` reads as "at least 40, we stopped counting".
    """
    legs = params.legs
    if not any(legs.values()):
        return 0, False
    ceiling = params.offset + params.limit + headroom
    sql = (
        "WITH probe AS ("
        + _transcript_sql(**legs)
        + " LIMIT :ceiling) SELECT COUNT(*) FROM probe"
    )
    bound = params.bind()
    bound["ceiling"] = ceiling
    total = int(conn.execute(sql, bound).fetchone()[0])
    return total, total >= ceiling


# ---------------------------------------------------------------------------
# §4.6 — OCR leg, with the cheap half of OCR-vs-transcript dedup in SQL

_OCR_SQL = """
WITH ocr_cand AS MATERIALIZED (
  SELECT o.id, o.video_id, o.t_s, o.text, o.keyframe_id,
         ROW_NUMBER() OVER (ORDER BY f.rank) AS r
  FROM ocr_fts f
  JOIN ocr_lines o ON o.id = f.rowid
  WHERE f.ocr_fts MATCH :q
  ORDER BY f.rank
  LIMIT :candidate_cap
),
txt_cand AS MATERIALIZED (
  SELECT c.id, c.video_id, c.start_s, c.end_s, c.text
  FROM cues_fts f
  JOIN cues c ON c.id = f.rowid
  WHERE f.cues_fts MATCH :q
  ORDER BY f.rank
  LIMIT :candidate_cap
),
scoped AS (
  SELECT o.* FROM ocr_cand o
  WHERE o.video_id IN (SELECT value FROM json_each(:video_ids))
    AND (:t_start IS NULL OR o.t_s >= :t_start)
    AND (:t_end   IS NULL OR o.t_s <= :t_end)
    AND NOT EXISTS (
      SELECT 1 FROM txt_cand t
      WHERE t.video_id = o.video_id
        AND o.t_s BETWEEN t.start_s - 5.0 AND t.end_s + 5.0
        AND length(t.text) >= length(o.text)   -- the longer text wins
    )
),
capped AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY video_id ORDER BY r) AS rn
  FROM scoped
)
SELECT c.id, c.video_id, c.t_s, c.text, c.keyframe_id, c.r,
       1.0 / (:rrf_k + c.r) AS score,
       k.ord AS ord,
       -- The public id, built exactly as the frame leg builds it (§3.1). Built
       -- from `videos.id` it comes out as `1-00033`, which `get-frames` cannot
       -- resolve — and the guide tells the model to use only ids it has seen.
       v.public_id || '-' || printf('%05d', k.ord) AS frame_id
FROM capped c
JOIN keyframes k ON k.id = c.keyframe_id
JOIN videos    v ON v.id = c.video_id
WHERE c.rn <= :max_per_video
"""

_OCR_PAGE = _OCR_SQL + "\nORDER BY c.r LIMIT :limit OFFSET :offset"
_OCR_PROBE = "WITH probe AS (" + _OCR_SQL + " LIMIT :ceiling) SELECT COUNT(*) FROM probe"


def search_ocr(conn: sqlite3.Connection, params: SearchParams) -> list[sqlite3.Row]:
    if is_browse_query(params.q) or not sanitize_fts(params.q or ""):
        return []
    return conn.execute(_OCR_PAGE, _ocr_bind(params)).fetchall()


def probe_ocr(conn: sqlite3.Connection, params: SearchParams, headroom: int = 30) -> tuple[int, bool]:
    if is_browse_query(params.q) or not sanitize_fts(params.q or ""):
        return 0, False
    bound = _ocr_bind(params)
    bound["ceiling"] = params.offset + params.limit + headroom
    total = int(conn.execute(_OCR_PROBE, bound).fetchone()[0])
    return total, total >= bound["ceiling"]


def _ocr_bind(params: SearchParams) -> dict[str, Any]:
    return {
        "q": sanitize_fts(params.q or ""),
        "rrf_k": RRF_K,
        "candidate_cap": params.candidate_cap,
        "video_ids": json.dumps(list(params.video_ids)),
        "t_start": params.t_start,
        "t_end": params.t_end,
        "max_per_video": params.max_per_video,
        "limit": params.limit + 1,
        "offset": params.offset,
    }


# ---------------------------------------------------------------------------
# §4.5 — the frame leg
#
# `k` is inflated to feed the diversity cap and bounded independently of
# `limit`: with max_per_video=3, asking for k = limit returns too few distinct
# videos.

_FRAME_SQL = """
WITH frame_hits AS MATERIALIZED (
  SELECT keyframe_id, video_id, t_s, distance
  FROM vec_frames
  WHERE embedding MATCH :q_img_vec AND k = :k_frames
),
ranked AS (
  SELECT fh.*, ROW_NUMBER() OVER (ORDER BY fh.distance) AS r
  FROM frame_hits fh
  WHERE fh.video_id IN (SELECT value FROM json_each(:video_ids))
    AND (:t_start IS NULL OR fh.t_s >= :t_start)
    AND (:t_end   IS NULL OR fh.t_s <= :t_end)
),
capped AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY video_id ORDER BY distance) AS rn
  FROM ranked
)
SELECT v.public_id || '-' || printf('%05d', k.ord) AS frame_id,
       v.public_id AS video_id, v.id AS video_row_id,
       v.title AS title, v.channel_name AS channel,
       c.t_s, c.distance,
       1.0 / (:rrf_k + c.r) AS score,
       (SELECT group_concat(o.text, ' | ' ORDER BY o.line_no)
          FROM ocr_lines o WHERE o.keyframe_id = c.keyframe_id) AS ocr_text
FROM capped c
JOIN keyframes k ON k.id = c.keyframe_id
JOIN videos    v ON v.id = c.video_id
WHERE c.rn <= :max_per_video
ORDER BY c.distance
LIMIT :limit OFFSET :offset
"""


def search_frames(
    conn: sqlite3.Connection, params: SearchParams, qimg: bytes, k_frames: int
) -> list[sqlite3.Row]:
    return conn.execute(
        _FRAME_SQL,
        {
            "q_img_vec": qimg,
            "k_frames": k_frames,
            "rrf_k": RRF_K,
            "video_ids": json.dumps(list(params.video_ids)),
            "t_start": params.t_start,
            "t_end": params.t_end,
            "max_per_video": params.max_per_video,
            "limit": params.limit + 1,
            "offset": params.offset,
        },
    ).fetchall()


# ---------------------------------------------------------------------------
# §4.4 — related tags (bounded, degrades to omission)

_RELATED_TAGS_SQL = """
SELECT t.full, COUNT(*) AS n
FROM video_tags vt
JOIN tags t ON t.id = vt.tag_id
WHERE vt.video_id IN (SELECT value FROM json_each(:result_video_ids))
  AND t.full NOT IN (SELECT value FROM json_each(:query_tags))
GROUP BY t.full
ORDER BY n DESC
LIMIT 30
"""


def related_tags(
    conn: sqlite3.Connection, result_video_ids: Sequence[int], query_tags: Sequence[str]
) -> list[sqlite3.Row]:
    return conn.execute(
        _RELATED_TAGS_SQL,
        {
            "result_video_ids": json.dumps(list(result_video_ids)),
            "query_tags": json.dumps(list(query_tags)),
        },
    ).fetchall()


# ---------------------------------------------------------------------------
# list-videos

# Ordered over the `base` CTE's output columns, not over `videos` — the outer
# SELECT has no `v` alias in scope.
_LIST_ORDER = {
    "recency": "published_at DESC, id DESC",
    "title": "lower(title) ASC",
    "duration": "duration_s DESC",
    "indexed_at": "indexed_at DESC",
    "relevance": "rank_score ASC, published_at DESC",
}

_LIST_MATCHED = """
matched AS MATERIALIZED (
  SELECT f.rowid AS id, f.rank AS rank_score
  FROM videos_fts f
  WHERE f.videos_fts MATCH :q
  ORDER BY f.rank
  LIMIT :candidate_cap
),
"""

_LIST_SQL = """
WITH {matched}
base AS (
  SELECT v.id, v.public_id, v.title, v.channel_name, v.published_at, v.duration_s,
         v.indexed_at, v.index_state,
         {rank_expr} AS rank_score,
         MAX(CASE WHEN s.stage = 'stt'         AND s.state = 'done' THEN 1 ELSE 0 END) AS has_transcript,
         MAX(CASE WHEN s.stage = 'ocr'         AND s.state = 'done' THEN 1 ELSE 0 END) AS has_ocr,
         MAX(CASE WHEN s.stage = 'frame_embed' AND s.state = 'done' THEN 1 ELSE 0 END) AS has_frames
  FROM videos v
  LEFT JOIN video_stages s ON s.video_id = v.id
  WHERE v.id IN (SELECT value FROM json_each(:video_ids))
    {fts_clause}
  GROUP BY v.id
)
SELECT * FROM base
WHERE (:has_filter = 'any'
    OR (:has_filter = 'transcript' AND has_transcript = 1)
    OR (:has_filter = 'ocr'        AND has_ocr = 1)
    OR (:has_filter = 'frames'     AND has_frames = 1)
    OR (:has_filter = 'all'        AND has_transcript = 1 AND has_ocr = 1 AND has_frames = 1))
"""


def _list_sql(q: str | None) -> str:
    if is_browse_query(q):
        return _LIST_SQL.format(matched="", rank_expr="0.0", fts_clause="")
    return _LIST_SQL.format(
        matched=_LIST_MATCHED,
        rank_expr="COALESCE((SELECT rank_score FROM matched m WHERE m.id = v.id), 0.0)",
        fts_clause="AND v.id IN (SELECT id FROM matched)",
    )


def _list_bind(video_ids: Sequence[int], q: str | None, has_filter: str, candidate_cap: int) -> dict[str, Any]:
    return {
        "video_ids": json.dumps(list(video_ids)),
        "q": sanitize_fts(q or ""),
        "candidate_cap": candidate_cap,
        "has_filter": has_filter,
    }


def list_videos(
    conn: sqlite3.Connection,
    video_ids: Sequence[int],
    q: str | None,
    has_filter: str,
    order: str,
    limit: int,
    offset: int,
    candidate_cap: int,
) -> list[sqlite3.Row]:
    order_by = _LIST_ORDER.get(order, _LIST_ORDER["recency"])
    sql = _list_sql(q) + f"\nORDER BY {order_by}\nLIMIT :limit OFFSET :offset"
    bound = _list_bind(video_ids, q, has_filter, candidate_cap)
    bound["limit"] = limit + 1
    bound["offset"] = offset
    return conn.execute(sql, bound).fetchall()


def probe_videos(
    conn: sqlite3.Connection,
    video_ids: Sequence[int],
    q: str | None,
    has_filter: str,
    limit: int,
    offset: int,
    candidate_cap: int,
    headroom: int = 30,
) -> tuple[int, bool]:
    ceiling = offset + limit + headroom
    sql = "WITH probe AS (" + _list_sql(q) + " LIMIT :ceiling) SELECT COUNT(*) FROM probe"
    bound = _list_bind(video_ids, q, has_filter, candidate_cap)
    bound["ceiling"] = ceiling
    total = int(conn.execute(sql, bound).fetchone()[0])
    return total, total >= ceiling


def video_tags(conn: sqlite3.Connection, video_ids: Sequence[int]) -> dict[int, list[str]]:
    if not video_ids:
        return {}
    rows = conn.execute(
        "SELECT vt.video_id, t.full FROM video_tags vt JOIN tags t ON t.id = vt.tag_id "
        "WHERE vt.video_id IN (SELECT value FROM json_each(?)) ORDER BY t.full",
        (json.dumps(list(video_ids)),),
    ).fetchall()
    out: dict[int, list[str]] = {}
    for row in rows:
        out.setdefault(int(row["video_id"]), []).append(row["full"])
    return out


# ---------------------------------------------------------------------------
# §4.8 — rollups

_CORPUS_SQL = """
SELECT (SELECT COUNT(*) FROM videos WHERE index_state = 'ready')      AS videos_ready,
       (SELECT COUNT(*) FROM videos WHERE index_state <> 'ready')     AS videos_pending,
       (SELECT COALESCE(ROUND(SUM(duration_s)/3600.0, 1), 0.0) FROM videos) AS hours,
       (SELECT COUNT(*) FROM cues)                                    AS cues,
       (SELECT COUNT(*) FROM keyframes WHERE dup_of IS NULL)          AS keyframes,
       (SELECT COUNT(*) FROM ocr_lines)                               AS ocr_lines,
       (SELECT MIN(published_at) FROM videos)                         AS oldest_published,
       (SELECT MAX(published_at) FROM videos)                         AS newest_published,
       (SELECT MAX(indexed_at)   FROM videos)                         AS last_indexed
"""


def corpus_rollup(conn: sqlite3.Connection) -> sqlite3.Row:
    return conn.execute(_CORPUS_SQL).fetchone()


def coverage(conn: sqlite3.Connection, video_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        SELECT v.public_id,
               MAX(CASE WHEN s.stage='stt'         AND s.state='done' THEN 1 ELSE 0 END) AS has_transcript,
               MAX(CASE WHEN s.stage='ocr'         AND s.state='done' THEN 1 ELSE 0 END) AS has_ocr,
               MAX(CASE WHEN s.stage='frame_embed' AND s.state='done' THEN 1 ELSE 0 END) AS has_frames
        FROM videos v LEFT JOIN video_stages s ON s.video_id = v.id
        WHERE v.id = ? GROUP BY v.id
        """,
        (video_id,),
    ).fetchone()


def channel_rollup(conn: sqlite3.Connection, video_ids: Sequence[int], limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT COALESCE(channel_name, '(unknown)') AS channel,
               COUNT(*) AS n, COALESCE(SUM(duration_s), 0) AS seconds
        FROM videos
        WHERE id IN (SELECT value FROM json_each(?))
        GROUP BY channel ORDER BY n DESC, channel LIMIT ?
        """,
        (json.dumps(list(video_ids)), limit),
    ).fetchall()


def channel_count(conn: sqlite3.Connection, video_ids: Sequence[int]) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(DISTINCT COALESCE(channel_name,'(unknown)')) FROM videos "
            "WHERE id IN (SELECT value FROM json_each(?))",
            (json.dumps(list(video_ids)),),
        ).fetchone()[0]
    )


def tag_rollup(conn: sqlite3.Connection, video_ids: Sequence[int], limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT t.full, COUNT(*) AS n
        FROM video_tags vt JOIN tags t ON t.id = vt.tag_id
        WHERE vt.video_id IN (SELECT value FROM json_each(?))
        GROUP BY t.full ORDER BY n DESC, t.full LIMIT ?
        """,
        (json.dumps(list(video_ids)), limit),
    ).fetchall()


def tag_count(conn: sqlite3.Connection) -> tuple[int, int]:
    row = conn.execute("SELECT COUNT(*) AS n, COUNT(DISTINCT ns) AS ns FROM tags").fetchone()
    return int(row["n"]), int(row["ns"])


def recent_indexed(conn: sqlite3.Connection, video_ids: Sequence[int], limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT public_id, title, channel_name, duration_s, indexed_at
        FROM videos
        WHERE id IN (SELECT value FROM json_each(?)) AND indexed_at IS NOT NULL
        ORDER BY indexed_at DESC LIMIT ?
        """,
        (json.dumps(list(video_ids)), limit),
    ).fetchall()


def gaps(conn: sqlite3.Connection) -> dict[str, Any]:
    transcript_no_ocr = int(
        conn.execute(
            """
            SELECT COUNT(*) FROM videos v
            WHERE EXISTS (SELECT 1 FROM video_stages s WHERE s.video_id=v.id
                            AND s.stage='stt' AND s.state='done')
              AND NOT EXISTS (SELECT 1 FROM video_stages s WHERE s.video_id=v.id
                            AND s.stage='ocr' AND s.state='done')
            """
        ).fetchone()[0]
    )
    failed = conn.execute(
        """
        SELECT v.public_id, v.title, s.error, s.finished_at
        FROM videos v JOIN video_stages s ON s.video_id = v.id
        WHERE v.index_state = 'failed' AND s.state = 'failed'
        ORDER BY s.finished_at DESC LIMIT 5
        """
    ).fetchall()
    indexing = int(
        conn.execute("SELECT COUNT(*) FROM videos WHERE index_state = 'indexing'").fetchone()[0]
    )
    active_jobs = int(
        conn.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]
    )
    recent_failed_jobs = int(
        conn.execute(
            "SELECT COUNT(*) FROM jobs WHERE state = 'failed' "
            "AND finished_at >= unixepoch() - 86400"
        ).fetchone()[0]
    )
    return {
        "transcript_no_ocr": transcript_no_ocr,
        "failed": failed,
        "indexing": indexing,
        "active_jobs": active_jobs,
        "recent_failed_jobs": recent_failed_jobs,
    }


# ---------------------------------------------------------------------------
# video-summary

def chapters(conn: sqlite3.Connection, video_id: int, limit: int) -> list[sqlite3.Row]:
    return conn.execute(
        "SELECT seq, start_s, end_s, title FROM chapters WHERE video_id = ? "
        "ORDER BY start_s LIMIT ?",
        (video_id, limit),
    ).fetchall()


def chapter_count(conn: sqlite3.Connection, video_id: int) -> int:
    return int(
        conn.execute("SELECT COUNT(*) FROM chapters WHERE video_id = ?", (video_id,)).fetchone()[0]
    )


def speakers_for(conn: sqlite3.Connection, video_id: int) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT COALESCE(sp.display_name, sp.label) AS speaker,
               SUM(c.end_s - c.start_s) AS seconds,
               MIN(c.start_s) AS first_s
        FROM cues c JOIN speakers sp ON sp.id = c.speaker_id
        WHERE c.video_id = ? AND c.speaker_id IS NOT NULL
        GROUP BY sp.id ORDER BY seconds DESC LIMIT 8
        """,
        (video_id,),
    ).fetchall()


def key_texts(conn: sqlite3.Connection, video_id: int, limit: int, t_start: float | None, t_end: float | None) -> list[sqlite3.Row]:
    """Sampled transcript lines, spread across the video and bounded by `limit`.

    The contract calls for a precomputed salience table; the schema does not
    carry one yet (see the README's deviations), so this samples the longest
    cues per equal-width bucket — O(caps) rows out, one index scan in.
    """
    return conn.execute(
        """
        WITH scoped AS (
          SELECT id, start_s, end_s, text, length(text) AS n
          FROM cues
          WHERE video_id = :vid
            AND (:t_start IS NULL OR end_s   >= :t_start)
            AND (:t_end   IS NULL OR start_s <= :t_end)
        ),
        bucketed AS (
          SELECT *, NTILE(:limit) OVER (ORDER BY start_s) AS bucket FROM scoped
        ),
        best AS (
          SELECT *, ROW_NUMBER() OVER (PARTITION BY bucket
                                       ORDER BY n DESC, start_s) AS rn
          FROM bucketed
        )
        SELECT id, start_s, end_s, text FROM best WHERE rn = 1
        ORDER BY start_s LIMIT :limit
        """,
        {"vid": video_id, "limit": limit, "t_start": t_start, "t_end": t_end},
    ).fetchall()


def ocr_highlights(conn: sqlite3.Connection, video_id: int, limit: int, t_start: float | None, t_end: float | None) -> list[sqlite3.Row]:
    """Distinct on-screen texts, with near-identical runs collapsed by phash.

    `keyframes_live` (dup_of IS NULL) is the partial index that gives every
    "distinct visuals" query path a cheap plan.
    """
    return conn.execute(
        """
        SELECT k.ord, k.t_s, k.phash,
               group_concat(o.text, ' | ' ORDER BY o.line_no) AS screen_text,
               COUNT(*) AS n_lines
        FROM keyframes k JOIN ocr_lines o ON o.keyframe_id = k.id
        WHERE k.video_id = :vid AND k.dup_of IS NULL
          AND (:t_start IS NULL OR k.t_s >= :t_start)
          AND (:t_end   IS NULL OR k.t_s <= :t_end)
        GROUP BY k.id
        ORDER BY length(group_concat(o.text, ' ')) DESC, k.t_s
        LIMIT :limit
        """,
        {"vid": video_id, "limit": limit, "t_start": t_start, "t_end": t_end},
    ).fetchall()


def video_links(conn: sqlite3.Connection, video_id: int, limit: int, t_start: float | None = None, t_end: float | None = None) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT url, title, t_s FROM video_links
        WHERE video_id = :vid
          AND (:t_start IS NULL OR (t_s IS NOT NULL AND t_s >= :t_start))
          AND (:t_end   IS NULL OR (t_s IS NOT NULL AND t_s <= :t_end))
        ORDER BY COALESCE(t_s, 1e18), seq LIMIT :limit
        """,
        {"vid": video_id, "limit": limit, "t_start": t_start, "t_end": t_end},
    ).fetchall()


def keyframe_count(conn: sqlite3.Connection, video_id: int) -> int:
    return int(
        conn.execute(
            "SELECT COUNT(*) FROM keyframes WHERE video_id = ? AND dup_of IS NULL",
            (video_id,),
        ).fetchone()[0]
    )


# ---------------------------------------------------------------------------
# §4.7 — get-segment-context: four small indexed queries, each independently
# bounded, each mapping to one include_* toggle. Switching a toggle off does
# not just discard work — it never issues the query.


def context_transcript(conn: sqlite3.Connection, video_id: int, t: float, window: float) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT c.id AS cue_id, c.start_s, c.end_s, c.text,
               COALESCE(s.display_name, s.label) AS speaker
        FROM cues c
        LEFT JOIN speakers s ON s.id = c.speaker_id
        WHERE c.video_id = :vid AND c.end_s >= :lo AND c.start_s <= :hi
        ORDER BY c.start_s
        """,
        {"vid": video_id, "lo": t - window, "hi": t + window},
    ).fetchall()


def context_chapter(conn: sqlite3.Connection, video_id: int, t: float) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT title, start_s, end_s FROM chapters WHERE video_id = ? AND ? >= start_s AND ? < end_s",
        (video_id, t, t),
    ).fetchone()


def context_ocr(conn: sqlite3.Connection, video_id: int, t: float, window: float, limit: int = 8) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT k.ord, k.t_s,
               group_concat(o.text, ' | ' ORDER BY o.line_no) AS screen_text
        FROM keyframes k
        LEFT JOIN ocr_lines o ON o.keyframe_id = k.id
        WHERE k.video_id = :vid AND k.t_s BETWEEN :lo AND :hi AND k.dup_of IS NULL
        GROUP BY k.id ORDER BY k.t_s LIMIT :limit
        """,
        {"vid": video_id, "lo": t - window, "hi": t + window, "limit": limit},
    ).fetchall()


def cue_by_id(conn: sqlite3.Connection, cue_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT c.id, c.video_id, c.start_s, c.end_s, v.public_id "
        "FROM cues c JOIN videos v ON v.id = c.video_id WHERE c.id = ?",
        (cue_id,),
    ).fetchone()


# ---------------------------------------------------------------------------
# get-frames


def keyframes_by_ord(conn: sqlite3.Connection, pairs: Sequence[tuple[int, int]]) -> list[sqlite3.Row]:
    """Look up (video_id, ord) pairs in one indexed query."""
    if not pairs:
        return []
    payload = json.dumps([{"v": v, "o": o} for v, o in pairs])
    return conn.execute(
        """
        SELECT k.id, k.video_id, k.ord, k.t_s, k.jpeg_path, k.jpeg_bytes,
               v.public_id, v.title,
               (SELECT group_concat(o.text, ' | ' ORDER BY o.line_no)
                  FROM ocr_lines o WHERE o.keyframe_id = k.id) AS ocr_text
        FROM json_each(?) j
        JOIN keyframes k ON k.video_id = json_extract(j.value, '$.v')
                        AND k.ord      = json_extract(j.value, '$.o')
        JOIN videos v ON v.id = k.video_id
        ORDER BY k.video_id, k.ord
        """,
        (payload,),
    ).fetchall()


def keyframes_in_span(
    conn: sqlite3.Connection, video_id: int, t_start: float | None, t_end: float | None, limit: int
) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT k.id, k.video_id, k.ord, k.t_s, k.jpeg_path, k.jpeg_bytes,
               v.public_id, v.title,
               (SELECT group_concat(o.text, ' | ' ORDER BY o.line_no)
                  FROM ocr_lines o WHERE o.keyframe_id = k.id) AS ocr_text
        FROM keyframes k JOIN videos v ON v.id = k.video_id
        WHERE k.video_id = :vid AND k.dup_of IS NULL
          AND (:lo IS NULL OR k.t_s >= :lo)
          AND (:hi IS NULL OR k.t_s <= :hi)
        ORDER BY k.t_s LIMIT :limit
        """,
        {"vid": video_id, "lo": t_start, "hi": t_end, "limit": limit},
    ).fetchall()


def max_ord(conn: sqlite3.Connection, video_id: int) -> int | None:
    row = conn.execute("SELECT MAX(ord) FROM keyframes WHERE video_id = ?", (video_id,)).fetchone()
    return None if row is None or row[0] is None else int(row[0])


def keyframe_path(conn: sqlite3.Connection, public_id: str, ordinal: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT k.jpeg_path, k.jpeg_bytes FROM keyframes k JOIN videos v ON v.id = k.video_id "
        "WHERE v.public_id = ? AND k.ord = ?",
        (public_id, ordinal),
    ).fetchone()


# ---------------------------------------------------------------------------
# tag-video (writes)


def apply_tags(
    conn: sqlite3.Connection,
    video_ids: Sequence[int],
    add: Sequence[tuple[str, str]],
    remove: Sequence[tuple[str, str]],
    dry_run: bool,
) -> dict[str, Any]:
    """Whole call is one transaction — partial batches do not apply."""
    added: dict[str, tuple[int, int]] = {}
    removed: dict[str, int] = {}

    for ns, name in add:
        full = f"{ns}:{name}"
        if not dry_run:
            conn.execute(
                "INSERT OR IGNORE INTO tags (owner_id, ns, name) VALUES (1, ?, ?)", (ns, name)
            )
        row = conn.execute("SELECT id FROM tags WHERE ns = ? AND name = ?", (ns, name)).fetchone()
        tag_id = int(row["id"]) if row else None
        present = 0
        if tag_id is not None:
            present = int(
                conn.execute(
                    "SELECT COUNT(*) FROM video_tags WHERE tag_id = ? AND video_id IN "
                    "(SELECT value FROM json_each(?))",
                    (tag_id, json.dumps(list(video_ids))),
                ).fetchone()[0]
            )
            if not dry_run:
                conn.executemany(
                    "INSERT OR IGNORE INTO video_tags (video_id, tag_id) VALUES (?, ?)",
                    [(v, tag_id) for v in video_ids],
                )
        added[full] = (len(video_ids) - present, present)

    for ns, name in remove:
        full = f"{ns}:{name}"
        row = conn.execute("SELECT id FROM tags WHERE ns = ? AND name = ?", (ns, name)).fetchone()
        if row is None:
            removed[full] = 0
            continue
        tag_id = int(row["id"])
        present = int(
            conn.execute(
                "SELECT COUNT(*) FROM video_tags WHERE tag_id = ? AND video_id IN "
                "(SELECT value FROM json_each(?))",
                (tag_id, json.dumps(list(video_ids))),
            ).fetchone()[0]
        )
        if not dry_run:
            conn.execute(
                "DELETE FROM video_tags WHERE tag_id = ? AND video_id IN "
                "(SELECT value FROM json_each(?))",
                (tag_id, json.dumps(list(video_ids))),
            )
        removed[full] = present

    total, namespaces = tag_count(conn)
    return {"added": added, "removed": removed, "total_tags": total, "namespaces": namespaces}


# ---------------------------------------------------------------------------
# vectors


def pack_f32(vector: Sequence[float]) -> bytes:
    """sqlite-vec wants a packed float32 blob."""
    return struct.pack(f"{len(vector)}f", *vector)
