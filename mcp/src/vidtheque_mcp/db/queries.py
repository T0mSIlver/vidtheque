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

# One frame's OCR lines, joined in reading order, are one document — for FTS5
# (`ocr_frames.text`, §2.5) and for every display path that prints "what is on
# screen". The separator is the same string the `group_concat(o.text, ' | '
# ORDER BY o.line_no)` expressions below produce, and the pipeline writes
# `ocr_frames` with it (`pipeline/store.py::write_ocr`) so the two agree by
# construction. It tokenizes to nothing under `unicode61`, which is the point:
# it is a reading aid, not a term, and a phrase wrapped across two lines stays
# findable.
OCR_FRAME_SEPARATOR = " | "

# FTS5 `snippet()` window, in tokens (its own hard maximum is 64). The OCR leg
# shows the matched region of a frame rather than the whole slide: at frame
# granularity the document is every line on screen, and middle-truncating that
# to `max_text_chars` is exactly as likely to hide the matched terms as to show
# them. Bounded work either way — the snippet is computed for the page's rows
# only, never for the candidate set.
OCR_SNIPPET_TOKENS = 64

# --- the vector legs' relevance floor -------------------------------------
#
# KNN always returns k rows. It has no notion of "nothing here matched": ask
# `zzzzqqqq` and the nearest 200 chunks come back, RRF ranks them, and the tool
# reports the whole corpus with confident-looking scores. Two mechanisms answer
# that, and it matters which one does the work.
#
# 1. This distance floor. sqlite-vec `distance_metric=cosine` yields
#    1 - cos(theta) in [0, 2]; hits above the ceiling are dropped BEFORE
#    fusion, never merely ranked lower.
#
#    Measured on the live corpus (6 videos, 205 chunk / 298 frame vectors,
#    2026-08-09) the real and junk distributions OVERLAP ALMOST COMPLETELY:
#
#      text  best-hit distance   real 0.504-0.576   junk 0.513-0.616
#      frame best-hit distance   real 0.877-0.919   junk 0.877-0.966
#
#    There is no threshold that separates them. Absolute cosine distance from
#    an asymmetric-prefix embedder is not calibrated across queries, so any
#    floor tight enough to reject `flurbles wibbly zonk` (best 0.513) would
#    also reject `transformer architecture explained` (best 0.504). The
#    defaults below therefore sit ABOVE the whole measured real range (real
#    20th-nearest reached 0.664 text / 0.946 frame): they trim an absurd tail
#    and nothing else, because a floor that deletes real semantic recall is
#    the worse failure. Env-tunable for corpora with different geometry.
#
# 2. `has_lexical_footing`, below — which is what actually makes the empty
#    state reachable, and does it on evidence rather than on a magic number.
VEC_MAX_DISTANCE = 0.72
FRAME_MAX_DISTANCE = 0.96

# vec0 metadata constraints must be plain comparisons to be pushed into the
# KNN, so "unbounded" is a sentinel rather than NULL. Video positions are
# REAL seconds; 1e12 s is ~31,000 years.
VEC_TIME_SENTINEL = 1e12

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

# Control characters are removed BEFORE anything else — before the leg-gating
# `bool(sanitize_fts(q))` decision and before the bind. A query of nothing but
# NUL used to sanitize to the nonempty `"\x00"`, pass the gate, and reach FTS5
# as `sqlite3.OperationalError: unterminated string`.
_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")

# Scripts that do not put spaces between words: a 2-character term there is a
# whole word, not a stray fragment, so it earns prefix expansion. (The FTS5
# tables carry `prefix='2 3'`, so a 2-char prefix query is index-served.)
_DENSE_SCRIPT_RANGES = (
    (0x2E80, 0x2FFF),  # CJK radicals, Kangxi
    (0x3040, 0x30FF),  # Hiragana, Katakana
    (0x3400, 0x4DBF),  # CJK ext A
    (0x4E00, 0x9FFF),  # CJK unified
    (0xA000, 0xA4CF),  # Yi
    (0xAC00, 0xD7AF),  # Hangul syllables
    (0xF900, 0xFAFF),  # CJK compatibility
    (0x20000, 0x2FA1F),  # CJK ext B+
)


def _dense_script(term: str) -> bool:
    return any(
        any(lo <= ord(ch) <= hi for lo, hi in _DENSE_SCRIPT_RANGES) for ch in term
    )


def _prefix_min(term: str) -> int:
    """Shortest term that earns prefix expansion, by script.

    Latin `go` expanding to `go*` is a candidate explosion; Han `缓存` is a
    complete word, and without expansion the OCR token `缓存-管理` (one token,
    thanks to the tokenchars) is unreachable from the query `缓存`.
    """
    return 2 if _dense_script(term) else 3


def _lex(query: str) -> list[tuple[str, str, bool]]:
    """(kind, value, trailing_star) triples. kind: op | ( | ) | phrase | term.

    Small hand lexer rather than one regex, because parentheses must survive as
    structure: `cache (CVE OR bug)` flattened to `cache CVE OR bug` is
    `(cache AND CVE) OR bug` under FTS5 precedence — it matches `bug`-only rows.
    """
    text = _CONTROL.sub(" ", query)
    out: list[tuple[str, str, bool]] = []
    i, n = 0, len(text)
    while i < n:
        ch = text[i]
        if ch.isspace():
            i += 1
            continue
        if ch in "()":
            out.append((ch, "", False))
            i += 1
            continue
        if ch == '"':
            close = text.find('"', i + 1)
            body = text[i + 1 :] if close == -1 else text[i + 1 : close]
            i = n if close == -1 else close + 1
            star = i < n and text[i] == "*"
            if star:
                i += 1
            out.append(("phrase", body, star))
            continue
        start = i
        while i < n and not text[i].isspace() and text[i] not in '()"':
            i += 1
        raw = text[start:i]
        star = raw.endswith("*")
        if star:
            raw = raw[:-1]
        if not raw:
            continue
        if not star and raw.upper() in _OPERATORS:
            out.append(("op", raw.upper(), False))
        else:
            out.append(("term", raw, star))
    return out


def _assemble(tokens: list[tuple[str, str, bool]], render: Any) -> str:
    """Rebuild a valid FTS5 expression, preserving the Boolean the user wrote.

    Rules that are the whole point of this function:

    * `a AND NOT b` becomes FTS5's binary `a NOT b` — never `a AND b`, which
      inverts the exclusion the user asked for.
    * unary/dangling/doubled operators are dropped (FTS5 has no unary NOT).
    * parentheses are kept, empty and unbalanced ones repaired.
    * adjacency is spelled `AND` explicitly: FTS5 allows implicit AND between
      bare phrases but NOT after a `)`, where it is "syntax error near (".
    """
    out: list[str] = []
    depth = 0
    pending: str | None = None

    def ends_expr() -> bool:
        return bool(out) and out[-1] not in _OPERATORS and out[-1] != "("

    def emit(piece: str) -> None:
        nonlocal pending
        if ends_expr():
            out.append(pending or "AND")
        pending = None
        out.append(piece)

    for kind, value, star in tokens:
        if kind == "op":
            if not ends_expr():
                continue  # unary or dangling — FTS5 cannot parse it
            if value == "NOT" and pending == "AND":
                pending = "NOT"
            elif pending is None:
                pending = value
            continue
        if kind == "(":
            emit("(")
            depth += 1
            continue
        if kind == ")":
            if depth == 0:
                continue  # unmatched close
            while out and out[-1] in _OPERATORS:
                out.pop()
            if out and out[-1] == "(":
                out.pop()  # empty group
            else:
                out.append(")")
            depth -= 1
            pending = None
            continue
        piece = render(kind, value, star)
        if piece:
            emit(piece)

    while depth > 0:
        while out and out[-1] in _OPERATORS:
            out.pop()
        if out and out[-1] == "(":
            out.pop()
        else:
            out.append(")")
        depth -= 1
    while out and out[-1] in _OPERATORS:
        out.pop()
    return " ".join(out)


def _render_plain(kind: str, value: str, star: bool) -> str:
    body = value.replace('"', "").strip()
    if not body:
        return ""
    return '"' + body + '"' + ("*" if star else "")


def _render_expanded(kind: str, value: str, star: bool) -> str:
    body = value.replace('"', "").strip()
    if not body:
        return ""
    quoted = '"' + body + '"'
    if star or kind == "phrase":
        # A phrase is exact by construction; its suffix star is the user's, and
        # dropping it (the old lexer did) turns `"foo"*` into `"foo"` and misses
        # `foobar`.
        return quoted + ("*" if star else "")
    if len(body) >= _prefix_min(body):
        return f"({quoted} OR {quoted}*)"
    return quoted


def sanitize_fts(query: str) -> str:
    """Quote-wrap user terms, keeping AND/OR/NOT, (groups), "phrases", prefix*.

    User text is not a syntax. A query that reduces to nothing (all punctuation,
    control characters, or a dangling operator) returns "" — the caller drops
    the leg rather than handing FTS5 an expression it cannot parse.
    """
    return _assemble(_lex(query), _render_plain)


def expand_prefix_fts(query: str) -> str:
    """OCR-leg matching: each plain term also matches as a prefix.

    The OCR tokenizer keeps ``-._/`` inside tokens so `nvidia-smi` and
    `torch.compile` survive as searchable units — which makes `cve-2026-22812`
    ONE token, and a search for `CVE` match nothing. Measured live: the model
    searched "opencode CVE" against a corpus whose OCR plainly contained
    `CVE-2026-22812 Detail`, got zero hits, and told the user the corpus had
    no CVE mentions (screenpipe compensates for the same tokenizer choice the
    same way — query-side expansion, never compound-splitting at index time).

    Each unquoted term at or over its script's threshold becomes
    ``("term" OR "term"*)``; phrases, operators, explicit ``term*`` and short
    terms carry `sanitize_fts` semantics unchanged.
    """
    return _assemble(_lex(query), _render_expanded)


def footing_fts(query: str) -> str:
    """OR of every term the user typed — "does ANY of this exist at all?".

    Deliberately not the expression the legs bind: those AND their terms, which
    is the right recall/precision trade for ranking and the wrong one for this
    question. Operators and grouping are discarded; only the terms survive.
    """
    pieces: list[str] = []
    for kind, value, star in _lex(query):
        if kind in {"op", "(", ")"}:
            continue
        rendered = _render_expanded(kind, value, star)
        if rendered:
            pieces.append(rendered)
    return " OR ".join(pieces)


# `LIMIT 1` inside each subquery: this asks an existence question, so it stops
# at the first posting rather than counting a corpus-wide term.
_FOOTING_SQL = """
SELECT (SELECT COUNT(*) FROM (SELECT 1 FROM cues_fts   WHERE cues_fts   MATCH :q LIMIT 1))
     + (SELECT COUNT(*) FROM (SELECT 1 FROM ocr_frames_fts
                               WHERE ocr_frames_fts MATCH :q LIMIT 1))
     + (SELECT COUNT(*) FROM (SELECT 1 FROM videos_fts WHERE videos_fts MATCH :q LIMIT 1))
"""


def has_lexical_footing(conn: sqlite3.Connection, query: str | None) -> bool:
    """True when at least one query term occurs somewhere in the corpus.

    This is the honest gate on the vector legs. KNN cannot answer "nothing
    here matched" — it returns its k nearest whatever you ask — and the
    measured distance distributions (see VEC_MAX_DISTANCE) do not separate a
    real query from gibberish. Whether the corpus contains the words at all
    does separate them, and it is a fact rather than a tuned constant:
    measured on the live corpus, every pure-nonsense query
    (`zzzzqqqq`, `asdfghjkl qwertyuiop`, `flurbles wibbly zonk`, `blorptastic`)
    has zero footing across all three FTS tables, while every real query the
    corpus can actually answer has footing in at least one.

    Fails OPEN: a query with no renderable terms is not gated, because the
    gate's job is to reject nonsense, never to invent a new empty state.
    """
    expr = footing_fts(query or "")
    if not expr:
        return True
    return int(conn.execute(_FOOTING_SQL, {"q": expr}).fetchone()[0]) > 0


def is_browse_query(query: str | None) -> bool:
    """Bare `*`/empty with filters = browse mode: skip FTS entirely, fast path."""
    return query is None or query.strip() in {"", "*"}


# ---------------------------------------------------------------------------
# §4.1 — resolve corpus filters to a video-id set


# Every `index_state` the schema permits (0001_initial.sql:70), in the order a
# video moves through them.
INDEX_STATES = ("pending", "indexing", "ready", "failed", "stale")

# What a *query* means by "in the corpus": a video that has data to answer with.
# A `pending` video has no cues; an `indexing` one has half of them and is being
# written to; a `failed` one is a row and an error string. Search and
# list-videos have always meant this and still do — it is the default rather
# than a hard-coded clause only because the dashboard's job is the opposite one
# (dashboard.md §5.2: "what state is each video in"), and a management surface
# that cannot see the failures is the one view nobody needs.
QUERYABLE_INDEX_STATES = ("ready", "stale")


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
    index_states: Sequence[str] = QUERYABLE_INDEX_STATES

    def as_params(self) -> dict[str, Any]:
        return {
            "channel": self.channel,
            "video_title": self.video_title,
            "published_after": self.published_after,
            "published_before": self.published_before,
            "indexed_after": self.indexed_after,
            "indexed_before": self.indexed_before,
            "index_states": json.dumps(list(self.index_states)),
        }


_RESOLVE_SQL = """
SELECT id, public_id FROM videos
WHERE (:channel          IS NULL OR channel_lc LIKE '%' || lower(:channel) || '%')
  AND (:video_title      IS NULL OR title_lc   LIKE '%' || lower(:video_title) || '%')
  AND (:published_after  IS NULL OR published_at >= :published_after)
  AND (:published_before IS NULL OR published_at <  :published_before)
  AND (:indexed_after    IS NULL OR indexed_at   >= :indexed_after)
  AND (:indexed_before   IS NULL OR indexed_at   <  :indexed_before)
  AND index_state IN (SELECT value FROM json_each(:index_states))
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


def resolve_speakers(conn: sqlite3.Connection, name: str) -> list[int]:
    """Partial, case-insensitive speaker match -> the cue-level speaker ids.

    Returns [] when nothing matches, which the caller must treat as "filter to
    nothing", never as "no filter" — `speaker=Alice` returning Bob's cues is the
    exact failure this resolver closes.

    Merged speakers come along: `merged_into` points at the survivor, and cues
    written before a merge still carry the old id.
    """
    rows = conn.execute(
        """
        SELECT id FROM speakers
        WHERE lower(label) LIKE '%' || lower(:name) || '%'
           OR lower(COALESCE(display_name, '')) LIKE '%' || lower(:name) || '%'
        """,
        {"name": name},
    ).fetchall()
    ids = {int(r["id"]) for r in rows}
    if not ids:
        return []
    merged = conn.execute(
        "SELECT id FROM speakers WHERE merged_into IN (SELECT value FROM json_each(?))",
        (json.dumps(sorted(ids)),),
    ).fetchall()
    return sorted(ids | {int(r["id"]) for r in merged})


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

# Every leg selects its candidates from the ALREADY-SCOPED cue set: the video,
# time, length and speaker predicates sit inside the candidate CTE, before the
# `LIMIT :candidate_cap` and before the rank is assigned. Filtering after the
# cap is the bug this shape exists to prevent — 5,000 stronger global `cache`
# cues fill the cap, the scope filter then removes all of them, and a search
# restricted to one video returns zero despite matching cues in that video.
# Assigning `r` after filtering is the other half: RRF ranks from every leg are
# then positions within the same scoped universe.
_CUE_SCOPE = """    AND c.video_id IN (SELECT value FROM json_each(:video_ids))
    AND (:t_start   IS NULL OR c.end_s   >= :t_start)
    AND (:t_end     IS NULL OR c.start_s <= :t_end)
    AND (:min_chars IS NULL OR length(c.text) >= :min_chars)
    AND (:max_chars IS NULL OR length(c.text) <= :max_chars)
    AND (:speaker_on = 0
         OR c.speaker_id IN (SELECT value FROM json_each(:speaker_ids)))"""

_LEG_FTS = """
fts_scoped AS MATERIALIZED (
  SELECT c.id AS cue_id, c.video_id, c.start_s, c.end_s, c.text, f.rank AS bm25
  FROM cues_fts f
  JOIN cues c ON c.id = f.rowid
  WHERE f.cues_fts MATCH :q
{scope}
  ORDER BY f.rank, c.id
  LIMIT :candidate_cap
),
fts_hits AS MATERIALIZED (
  SELECT cue_id, video_id, start_s, end_s, text,
         ROW_NUMBER() OVER (ORDER BY bm25, cue_id) AS r
  FROM fts_scoped
)"""

# `video_id` and `start_s` are plain vec0 metadata columns (index-schema §3.2
# deliberately avoided PARTITION KEY), and sqlite-vec 0.1.9 pushes `=`/`IN`/
# range constraints on them INTO the KNN — measured: `k=3 AND video_id IN (3,4)`
# returns 3 rows from those two videos, not 3 globally then filtered to 0.
#
# Two shapes matter. First, the constraint must be written plainly: an
# `(:x IS NULL OR col …)` guard is NOT recognised as a metadata constraint, so
# it degrades to a post-KNN filter and silently re-creates the bug. Optional
# bounds therefore bind sentinels instead of NULL.
#
# Second, only SOUND bounds are pushed. `chunks.start_s <= cue.start_s`, so
# `start_s <= :t_end` cannot drop a chunk holding an in-range cue. There is no
# `end_s` metadata column, so the lower time bound stays a cue-level predicate
# and `k` is oversampled instead.
_LEG_VEC = """
vec_hits AS MATERIALIZED (
  SELECT chunk_id, distance
  FROM vec_chunks
  WHERE embedding MATCH :qvec AND k = :k_vec
    AND video_id IN (SELECT value FROM json_each(:video_ids))
    AND start_s <= :vec_t_end
),
vec_scoped AS MATERIALIZED (
  -- ONE best distance per cue. Chunks overlap by design (45 s window, 15 s
  -- overlap), so a cue lands in two chunks and used to arrive as two rows —
  -- two RRF contributions from ONE leg, which is double-counting, not
  -- corroboration.
  SELECT c.id AS cue_id, c.video_id, c.start_s, c.end_s, c.text,
         MIN(vh.distance) AS distance
  FROM vec_hits vh
  JOIN chunks ch ON ch.id = vh.chunk_id
  JOIN cues   c  ON c.id BETWEEN ch.first_cue_id AND ch.last_cue_id
  WHERE vh.distance <= :vec_max_distance
{scope}
  GROUP BY c.id
),
vec_cues AS MATERIALIZED (
  SELECT cue_id, video_id, start_s, end_s, text,
         ROW_NUMBER() OVER (ORDER BY distance, cue_id) AS r
  FROM vec_scoped
  ORDER BY distance, cue_id
  LIMIT :candidate_cap
)"""

_LEG_BROWSE = """
browse_hits AS MATERIALIZED (
  SELECT cue_id, video_id, start_s, end_s, text,
         ROW_NUMBER() OVER (ORDER BY video_id, start_s, cue_id) AS r
  FROM (
    SELECT c.id AS cue_id, c.video_id, c.start_s, c.end_s, c.text
    FROM cues c
    WHERE 1 = 1
{scope}
    ORDER BY c.video_id, c.start_s, c.id
    LIMIT :candidate_cap
  )
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

-- The scope predicates already ran inside every leg, before its cap. This is a
-- cheap re-check over the fused set, kept so the invariant is visible at the
-- one place a new leg would be added.
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
--
-- The grid cell comes from `start_s`, so it alone bounds only start times: a
-- run whose last cue starts at 119.9 s and ends at 123 s used to produce a
-- segment longer than the 120 s this module advertises. A cue whose `end_s`
-- crosses its own cell boundary is therefore forced into an island of its own
-- (`crosses`), and so is the cue after it — which makes the invariant exact:
-- every multi-cue island lies inside one [k*max_span, (k+1)*max_span) cell, so
-- MAX(end_s) - MIN(start_s) <= max_span.
--
-- The explicit policy for a single cue longer than max_span (a 180 s cue does
-- occur in auto-captions): it is never split, because half a sentence with a
-- truncated timestamp is a worse citation than an honest overlong one. It
-- becomes a one-cue island and is the only shape that can exceed the ceiling.
marked AS (
  SELECT *,
    CASE WHEN :cluster_gap > 0
          AND start_s - LAG(end_s) OVER w <= (SELECT gap_s FROM params)
          AND CAST(start_s / (SELECT max_span FROM params) AS INTEGER)
            = CAST(LAG(start_s) OVER w / (SELECT max_span FROM params) AS INTEGER)
          AND end_s <= (CAST(start_s / (SELECT max_span FROM params) AS INTEGER) + 1)
                       * (SELECT max_span FROM params)
          AND LAG(end_s) OVER w
              <= (CAST(LAG(start_s) OVER w / (SELECT max_span FROM params) AS INTEGER) + 1)
                 * (SELECT max_span FROM params)
         THEN 0 ELSE 1 END AS is_new
  FROM filtered
  WINDOW w AS (PARTITION BY video_id ORDER BY start_s, cue_id)
),
islands AS (
  SELECT *, SUM(is_new) OVER (PARTITION BY video_id ORDER BY start_s, cue_id
                              ROWS UNBOUNDED PRECEDING) AS island
  FROM marked
),
-- Island boundaries come from the MATCHED cues; the passage does not.
bounds AS (
  SELECT video_id, island,
         MIN(start_s) AS start_s, MAX(end_s) AS end_s,
         MAX(score)   AS score,
         COUNT(*)     AS n_matched
  FROM islands
  GROUP BY video_id, island
),
-- ...and then every cue inside that interval is joined back, matched or not.
-- Concatenating only the matched cues produced keyword confetti: matches at 0 s
-- and 4 s came back as one "segment" with the cue at 2 s — which may carry the
-- negation that reverses the meaning — silently dropped.
clustered AS (
  SELECT b.video_id, b.island, b.start_s, b.end_s, b.score,
         group_concat(c.text, ' ' ORDER BY c.start_s, c.id) AS text,
         json_group_array(c.id ORDER BY c.start_s, c.id)    AS cue_ids,
         COUNT(*)                                           AS n_cues,
         b.n_matched
  FROM bounds b
  JOIN cues c ON c.video_id = b.video_id
             AND c.start_s >= b.start_s AND c.start_s <= b.end_s
  GROUP BY b.video_id, b.island
),

-- per-video diversity. This is a bounded-work overfetch, NOT the user's
-- max_per_video: one video contributing one transcript, one OCR and one frame
-- hit is three results from one video, so the cap the user asked for can only
-- be honoured once the legs are fused and cross-modal duplicates collapsed.
-- tools/search.py owns that cap; this one only stops a single video from
-- eating the candidate budget.
capped AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY video_id
                               ORDER BY score DESC, start_s, island) AS rn
  FROM clustered
)
SELECT video_id, start_s, end_s, text, cue_ids, score, n_cues, n_matched
FROM capped
WHERE rn <= (SELECT per_video FROM params)
"""


def _transcript_sql(*, do_fts: bool, do_vec: bool, do_browse: bool) -> str:
    legs: list[str] = []
    unions: list[str] = []
    if do_fts:
        legs.append(_LEG_FTS.format(scope=_CUE_SCOPE))
        unions.append(_SCORED_LEG.format(leg="fts_hits"))
    if do_vec:
        legs.append(_LEG_VEC.format(scope=_CUE_SCOPE))
        unions.append(_SCORED_LEG.format(leg="vec_cues"))
    if do_browse:
        legs.append(_LEG_BROWSE.format(scope=_CUE_SCOPE))
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
    # `None` = no speaker filter at all. An EMPTY list is not the same thing:
    # it means "a speaker was asked for and nothing in the corpus matched it",
    # which must return nothing rather than everything.
    speaker_ids: Sequence[int] | None = None
    vec_max_distance: float = VEC_MAX_DISTANCE

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
            # A vec0 metadata constraint has to be a plain comparison to be
            # pushed into the KNN, so the "no bound" case binds a sentinel
            # rather than NULL. `chunks.start_s <= cues.start_s` makes this
            # sound: no chunk holding an in-range cue can be dropped.
            "vec_t_end": VEC_TIME_SENTINEL if self.t_end is None else self.t_end,
            "vec_max_distance": self.vec_max_distance,
            "min_chars": self.min_chars,
            "max_chars": self.max_chars,
            "speaker_on": 0 if self.speaker_ids is None else 1,
            "speaker_ids": json.dumps(list(self.speaker_ids or ())),
            "limit": self.limit + 1,  # the +1 is has_more
            "offset": self.offset,
        }


def search_transcript(conn: sqlite3.Connection, params: SearchParams) -> list[sqlite3.Row]:
    legs = params.legs
    if not any(legs.values()):
        return []
    sql = (
        _transcript_sql(**legs)
        + "\nORDER BY score DESC, video_id, start_s, island"
        + "\nLIMIT :limit OFFSET :offset"
    )
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
# §4.6 — OCR leg. OCR-vs-transcript dedup is NOT here; it is one rule, and it
# lives caller-side in tools/search.py::_dedup_ocr_against_transcript.
#
# This used to carry a "cheap half": drop any OCR line a longer transcript cue
# matching the same query overlapped within +/-5s. It had no text-similarity
# test, so it was strictly more aggressive than the rule it was meant to
# prefilter for (tool-surface §3.10: similar text collapses, *different* text
# keeps both) — and it dropped instead of collapsing, so the survivor never got
# its `[transcript+ocr]` provenance either. On a screencast, where the
# presenter narrates what is on screen, searching the OCR channel for a
# narrated word returned nothing, with no `note:` to say why
# (research/e2e-smoke-2026-08-08.md §4.4). The Python half is bounded anyway —
# it runs over `limit x a small constant` rows — so there is nothing to buy
# here.

#
# The unit of this leg is the FRAME, not the OCR line (§2.5, migration 0003).
# `expand_prefix_fts` ANDs the query's terms, and a keyframe is ~12 lines, so a
# line-granular index could only match a multi-term query when every term landed
# on the same line: a slide titled "Vector databases" with "…for retrieval" in a
# bullet answered `vector retrieval` with silence. `ocr_frames` is one document
# per keyframe — its lines concatenated in reading order — so the terms only
# have to share a frame, which is also the thing the result cites.

_OCR_SQL = """
WITH ocr_cand AS MATERIALIZED (
  -- Scope, time AND length predicates inside the candidate CTE, before the cap
  -- and before the rank — same rule as the transcript legs. min_chars/max_chars
  -- used to be missing here entirely: the tool disabled the frame leg for them,
  -- printed a note, and then let OCR answer `min_chars=100` with a ten-character
  -- line. They measure the frame's whole on-screen text now, because that is
  -- what the document is.
  SELECT o.keyframe_id, o.video_id, o.t_s, o.text, f.rank AS bm25
  FROM ocr_frames_fts f
  JOIN ocr_frames o ON o.keyframe_id = f.rowid
  WHERE f.ocr_frames_fts MATCH :q
    AND o.video_id IN (SELECT value FROM json_each(:video_ids))
    AND (:t_start   IS NULL OR o.t_s >= :t_start)
    AND (:t_end     IS NULL OR o.t_s <= :t_end)
    AND (:min_chars IS NULL OR length(o.text) >= :min_chars)
    AND (:max_chars IS NULL OR length(o.text) <= :max_chars)
  ORDER BY f.rank, o.keyframe_id
  LIMIT :candidate_cap
),
scoped AS (
  SELECT o.*, ROW_NUMBER() OVER (ORDER BY o.bm25, o.keyframe_id) AS r FROM ocr_cand o
),
capped AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY video_id ORDER BY r, keyframe_id) AS rn
  FROM scoped
)
SELECT c.keyframe_id, c.video_id, c.t_s, c.text, c.r,
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

# `matched_text` is FTS5's own snippet over the frame document: the window that
# holds the query's terms, with ' | ' line separators intact so the shape of the
# slide survives. It is computed OUTSIDE the page CTE, one rowid-restricted
# lookup per returned row (<= limit+1, and `limit` is server-clamped at 50).
# Computing it in the candidate CTE instead is the obvious spelling and the
# wrong one: MATERIALIZED means it would run for every candidate up to
# `candidate_cap` — measured 1,068 ms vs 145 ms on a 200,000-frame fixture where
# every frame matched.
_OCR_PAGE = (
    "WITH page AS MATERIALIZED (\n"
    + _OCR_SQL
    + "\nORDER BY c.r LIMIT :limit OFFSET :offset\n)\n"
    "SELECT p.*, COALESCE((\n"
    "  SELECT snippet(x.ocr_frames_fts, 0, '', '', ' … ', "
    + str(OCR_SNIPPET_TOKENS)
    + ")\n"
    "  FROM ocr_frames_fts x\n"
    "  WHERE x.ocr_frames_fts MATCH :q AND x.rowid = p.keyframe_id\n"
    "), p.text) AS matched_text\n"
    "FROM page p ORDER BY p.r"
)
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
        # Prefix-expanded, not plain-sanitized: the OCR tokenizer's tokenchars
        # make `cve-2026-22812` one token, and `CVE` must still find it.
        "q": expand_prefix_fts(params.q or ""),
        "rrf_k": RRF_K,
        "candidate_cap": params.candidate_cap,
        "video_ids": json.dumps(list(params.video_ids)),
        "t_start": params.t_start,
        "t_end": params.t_end,
        "min_chars": params.min_chars,
        "max_chars": params.max_chars,
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

# `video_id` and `t_s` are vec0 metadata columns here too, and both time bounds
# are exact for a keyframe (a frame is a point, not a span), so all three
# predicates are pushed into the KNN. Filtering afterwards let `k` be exhausted
# entirely on out-of-scope videos: a frame search scoped to one video returned
# nothing while that video plainly held matching frames.
_FRAME_SQL = """
WITH frame_hits AS MATERIALIZED (
  SELECT keyframe_id, video_id, t_s, distance
  FROM vec_frames
  WHERE embedding MATCH :q_img_vec AND k = :k_frames
    AND video_id IN (SELECT value FROM json_each(:video_ids))
    AND t_s >= :frame_t_start AND t_s <= :frame_t_end
),
ranked AS (
  SELECT fh.*, ROW_NUMBER() OVER (ORDER BY fh.distance, fh.keyframe_id) AS r
  FROM frame_hits fh
  WHERE fh.distance <= :frame_max_distance
),
capped AS (
  SELECT *, ROW_NUMBER() OVER (PARTITION BY video_id
                               ORDER BY distance, keyframe_id) AS rn
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
ORDER BY c.distance, c.keyframe_id
LIMIT :limit OFFSET :offset
"""


def search_frames(
    conn: sqlite3.Connection,
    params: SearchParams,
    qimg: bytes,
    k_frames: int,
    max_distance: float = FRAME_MAX_DISTANCE,
) -> list[sqlite3.Row]:
    return conn.execute(
        _FRAME_SQL,
        {
            "q_img_vec": qimg,
            "k_frames": k_frames,
            "rrf_k": RRF_K,
            "video_ids": json.dumps(list(params.video_ids)),
            # Sentinels, not NULL: see VEC_TIME_SENTINEL.
            "frame_t_start": (
                -VEC_TIME_SENTINEL if params.t_start is None else params.t_start
            ),
            "frame_t_end": VEC_TIME_SENTINEL if params.t_end is None else params.t_end,
            "frame_max_distance": max_distance,
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
       -- Seconds are the stored fact; hours are a display rounding of them.
       -- Deriving seconds back out of a 0.1-rounded hours figure reported a
       -- 149 s corpus as 0 (research/e2e-smoke-2026-08-08.md §4.6).
       (SELECT COALESCE(SUM(duration_s), 0.0) FROM videos)                  AS duration_s,
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
# The dashboard's reads — dashboard.md §7.
#
# These answer questions the tool surface was designed *not* to answer, because
# a model does not need them: which of the seven stages ran and with what
# model, where the shot boundaries are, what the chunk boundaries are, what OCR
# read and *where on the screen* it read it. Every one is read-only, bounded,
# and over an index that already exists. No new table.


# The pipeline's order, which is not `video_stages`'s storage order (the PK is
# `(video_id, stage)`, so rows come back alphabetically). A provenance panel
# that lists `chunk` before `fetch` is a panel nobody can read.
STAGE_ORDER = ("fetch", "stt", "chunk", "text_embed", "keyframe", "ocr", "frame_embed")


def video_stages(conn: sqlite3.Connection, video_id: int) -> list[sqlite3.Row]:
    """The stage rows this video has, in pipeline order.

    Missing stages are missing on purpose: `video_stages` carries a row only
    once a stage has been planned, so "no row" and "pending" are different
    facts and the caller decides how to say so.
    """
    return conn.execute(
        """
        SELECT stage, state, model_key, stage_version, started_at, finished_at, error
        FROM video_stages WHERE video_id = ?
        ORDER BY CASE stage
                   WHEN 'fetch' THEN 0 WHEN 'stt' THEN 1 WHEN 'chunk' THEN 2
                   WHEN 'text_embed' THEN 3 WHEN 'keyframe' THEN 4
                   WHEN 'ocr' THEN 5 ELSE 6 END
        """,
        (video_id,),
    ).fetchall()


def shot_timeline(
    conn: sqlite3.Connection, video_id: int, limit: int = 2_000
) -> list[sqlite3.Row]:
    """One row per shot, from `keyframes GROUP BY shot_id`.

    There is no `scenes` table (dashboard.md §4.3) — the shot boundaries are
    denormalized onto every keyframe, and `keyframes_shot(video_id, shot_id)`
    is the index this rides. One grouped query for the whole video, never one
    per shot, and bounded: a four-hour talk with a hard cut every two seconds
    is a legal corpus entry.
    """
    return conn.execute(
        """
        SELECT shot_id,
               MIN(shot_start_s) AS start_s,
               MAX(shot_end_s)   AS end_s,
               MIN(ord)          AS first_ord,
               COUNT(*)          AS frames,
               SUM(CASE WHEN dup_of IS NULL THEN 1 ELSE 0 END) AS kept,
               SUM(CASE WHEN ocr_state = 'done'  THEN 1 ELSE 0 END) AS ocr_done,
               SUM(CASE WHEN ocr_state = 'empty' THEN 1 ELSE 0 END) AS ocr_empty
        FROM keyframes WHERE video_id = :vid
        GROUP BY shot_id ORDER BY start_s LIMIT :limit
        """,
        {"vid": video_id, "limit": limit},
    ).fetchall()


def keyframe_page(
    conn: sqlite3.Connection,
    video_id: int,
    offset: int,
    limit: int,
    shot_id: int | None = None,
) -> list[sqlite3.Row]:
    """The keyframe strip, by ordinal — duplicates **included**.

    `keyframes_in_span` and `keyframe_count` both hide `dup_of IS NOT NULL`,
    which is right for search and wrong here: "why does this shot have no OCR"
    is answered entirely by that column, so the strip shows the deduped frames
    dimmed rather than pretending they were never captured.

    Returns ``limit + 1`` rows so the caller can say `has_more` without a
    second count query.
    """
    return conn.execute(
        """
        SELECT k.id, k.ord, k.t_s, k.shot_id, k.shot_start_s, k.shot_end_s,
               k.sharpness, k.width, k.height, k.jpeg_bytes, k.dup_of, k.ocr_state,
               dup.ord AS dup_of_ord
        FROM keyframes k
        LEFT JOIN keyframes dup ON dup.id = k.dup_of
        WHERE k.video_id = :vid AND (:shot IS NULL OR k.shot_id = :shot)
        ORDER BY k.ord LIMIT :limit OFFSET :offset
        """,
        {"vid": video_id, "shot": shot_id, "limit": limit + 1, "offset": offset},
    ).fetchall()


def cue_page(
    conn: sqlite3.Connection, video_id: int, offset: int, limit: int
) -> list[sqlite3.Row]:
    """Transcript cues by time, on `cues_time(video_id, start_s)`.

    `words_json` is ~10% of the database and is never a thing a human reads
    (DECISIONS.md), so this reports whether word timings exist and does not
    carry them. `origin` and `avg_logprob` do come along: they are how a reader
    sees that a video came in through YouTube's captions rather than whisperX.

    Returns ``limit + 1`` rows — `has_more` over a total, as everywhere else.
    """
    return conn.execute(
        """
        SELECT c.id, c.seq, c.start_s, c.end_s, c.text, c.origin, c.avg_logprob,
               (c.words_json IS NOT NULL) AS has_words,
               COALESCE(s.display_name, s.label) AS speaker
        FROM cues c LEFT JOIN speakers s ON s.id = c.speaker_id
        WHERE c.video_id = :vid
        ORDER BY c.start_s, c.seq LIMIT :limit OFFSET :offset
        """,
        {"vid": video_id, "limit": limit + 1, "offset": offset},
    ).fetchall()


def chunk_spans(
    conn: sqlite3.Connection, video_id: int, first_cue_id: int, last_cue_id: int
) -> list[sqlite3.Row]:
    """The chunks overlapping a cue-id range — the embedding unit, drawn.

    Scoped to the cue page rather than to the video, because "what exactly is
    the embedding unit" is a question about the cues in front of you and a
    four-hour talk has hundreds of chunks. `chunks_span(first_cue_id,
    last_cue_id)` is the index.
    """
    return conn.execute(
        """
        SELECT id, seq, start_s, end_s, first_cue_id, last_cue_id, n_chars
        FROM chunks
        WHERE video_id = :vid AND last_cue_id >= :lo AND first_cue_id <= :hi
        ORDER BY seq
        """,
        {"vid": video_id, "lo": first_cue_id, "hi": last_cue_id},
    ).fetchall()


def cue_origins(conn: sqlite3.Connection, video_id: int) -> dict[str, int]:
    """`whisperx | yt_manual | yt_auto` → how many cues came in that way."""
    rows = conn.execute(
        "SELECT origin, COUNT(*) AS n FROM cues WHERE video_id = ? GROUP BY origin",
        (video_id,),
    ).fetchall()
    return {str(r["origin"]): int(r["n"]) for r in rows}


def ocr_for_frames(
    conn: sqlite3.Connection, keyframe_ids: Sequence[int], limit: int = 400
) -> dict[int, list[sqlite3.Row]]:
    """OCR lines and their boxes, for the frames in view — one grouped query.

    The boxes are normalized 0–1 at write time (`pipeline/store.py`), so a
    caller can draw them over the frame at any width without knowing the
    frame's pixels. Double-capped: the caller bounds the frame list, and
    ``limit`` bounds the lines, because a slide of dense small print is one
    keyframe with two hundred lines on it.
    """
    if not keyframe_ids:
        return {}
    rows = conn.execute(
        """
        SELECT keyframe_id, line_no, text, conf, x0, y0, x1, y1
        FROM ocr_lines
        WHERE keyframe_id IN (SELECT value FROM json_each(:ids))
        ORDER BY keyframe_id, line_no LIMIT :limit
        """,
        {"ids": json.dumps([int(k) for k in keyframe_ids]), "limit": limit},
    ).fetchall()
    out: dict[int, list[sqlite3.Row]] = {}
    for row in rows:
        out.setdefault(int(row["keyframe_id"]), []).append(row)
    return out


_PER_VIDEO_COUNTS = """
SELECT (SELECT COUNT(*) FROM cues       WHERE video_id = :vid) AS cues,
       (SELECT COUNT(*) FROM cues       WHERE video_id = :vid
          AND words_json IS NOT NULL)                          AS cues_with_words,
       (SELECT COUNT(*) FROM chunks     WHERE video_id = :vid) AS chunks,
       (SELECT COUNT(*) FROM chapters   WHERE video_id = :vid) AS chapters,
       (SELECT COUNT(*) FROM keyframes  WHERE video_id = :vid) AS keyframes,
       (SELECT COUNT(*) FROM keyframes  WHERE video_id = :vid
          AND dup_of IS NULL)                                  AS keyframes_kept,
       (SELECT COUNT(*) FROM ocr_frames WHERE video_id = :vid) AS ocr_frames,
       (SELECT COUNT(*) FROM ocr_lines  WHERE video_id = :vid) AS ocr_lines,
       (SELECT COALESCE(SUM(jpeg_bytes), 0) FROM keyframes
          WHERE video_id = :vid)                               AS jpeg_bytes
"""


def per_video_counts(conn: sqlite3.Connection, video_id: int) -> sqlite3.Row:
    """The counts `videos` does not denormalize — **detail page only**.

    dashboard.md §4.2: there are no per-video counters in the schema, so these
    are `COUNT(*)`s. Each rides a covering index (`cues_time`, `chunks_time`,
    `ocr_time`, `keyframes_live`) and they run one video at a time. A fifty-row
    table must never fan out into four hundred of them, which is why the videos
    table shows coverage booleans instead.
    """
    return conn.execute(_PER_VIDEO_COUNTS, {"vid": video_id}).fetchone()


def keyframe_bytes_total(conn: sqlite3.Connection) -> int:
    """Keyframe bytes on disk, from the column that already stores them.

    `SUM(jpeg_bytes)` rather than walking `keyframes/`: a directory walk grows
    with the corpus while the overview page is loading, and the schema knows
    the answer already.
    """
    return int(
        conn.execute("SELECT COALESCE(SUM(jpeg_bytes), 0) FROM keyframes").fetchone()[0]
    )


def keyframe_detail(
    conn: sqlite3.Connection, public_id: str, ordinal: int
) -> sqlite3.Row | None:
    """One keyframe by wire id (`<public_id>-<ord:05d>`), with its shot."""
    return conn.execute(
        """
        SELECT k.id, k.ord, k.t_s, k.shot_id, k.shot_start_s, k.shot_end_s,
               k.sharpness, k.width, k.height, k.jpeg_bytes, k.dup_of, k.ocr_state,
               v.public_id, v.title
        FROM keyframes k JOIN videos v ON v.id = k.video_id
        WHERE v.public_id = ? AND k.ord = ?
        """,
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
