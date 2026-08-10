"""Regression tests for the query-layer correctness review (2026-08-09).

Every test here is one of the review's *concrete* failure cases, built as the
smallest corpus that reproduces it. The shared fixture in `conftest.py` cannot
express most of them — it has one chunk per video (so overlapping-chunk
double-counting is invisible), no speakers, and too few cues for a candidate cap
to bite — so these build their own.

The seams they cover, in the review's numbering:

  F1  non-relevance orderings sorted a relevance-truncated prefix
  F2  corpus/time filters applied AFTER the FTS/KNN caps
  F3  `max_per_video` enforced per modality instead of on the fused list
  F4  an enabled `speaker=` filter never reaching the SQL
  F6  RRF double-counting overlapping chunks, and dropping OCR agreement
  F8  `min_chars`/`max_chars` silently not applying to OCR
  F9  clustered text that was not the contiguous run, and a soft 120 s bound
  F10 rank ties with no deterministic key
  plus the vector legs' relevance floor and lexical-footing gate.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import replace
from pathlib import Path

import pytest

from vidtheque_mcp.config import Settings
from vidtheque_mcp.db import migrations, queries
from vidtheque_mcp.db.connection import open_read_connection, open_write_connection
from vidtheque_mcp.tools import search

from .conftest import FRAME_DIM, TEXT_DIM, Assembled, vector_for

# --------------------------------------------------------------------- helpers


class Corpus:
    """A tiny hand-built corpus. `write` mutates, `read` is a fresh ro handle."""

    def __init__(self, root: Path) -> None:
        self.path = root / "vidtheque.db"
        self.path.parent.mkdir(parents=True, exist_ok=True)
        conn = open_write_connection(self.path)
        migrations.migrate(conn)
        conn.close()
        self._read: sqlite3.Connection | None = None

    def write(self, fn) -> None:
        conn = open_write_connection(self.path)
        try:
            conn.execute("BEGIN IMMEDIATE")
            fn(conn)
            conn.execute("COMMIT")
        finally:
            conn.close()

    @property
    def read(self) -> sqlite3.Connection:
        if self._read is None:
            self._read = open_read_connection(self.path)
        return self._read

    def close(self) -> None:
        if self._read is not None:
            self._read.close()
            self._read = None


@pytest.fixture
def corpus(tmp_path: Path):
    built = Corpus(tmp_path / "corpus")
    yield built
    built.close()


def add_video(
    conn: sqlite3.Connection,
    source_id: str,
    *,
    published_at: int = 1_700_000_000,
    duration_s: float = 600.0,
    title: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO videos (owner_id, source_id, url, title, channel_name, published_at, "
        "duration_s, index_state, indexed_at) VALUES (1, ?, ?, ?, 'ch', ?, ?, 'ready', 1)",
        (
            source_id,
            f"https://youtu.be/{source_id}",
            title or f"title {source_id}",
            published_at,
            duration_s,
        ),
    )
    return int(cur.lastrowid or 0)


def add_cue(
    conn: sqlite3.Connection,
    video_id: int,
    seq: int,
    start_s: float,
    end_s: float,
    text: str,
    speaker_id: int | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO cues (video_id, seq, start_s, end_s, text, speaker_id) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (video_id, seq, start_s, end_s, text, speaker_id),
    )
    return int(cur.lastrowid or 0)


def add_ocr(
    conn: sqlite3.Connection,
    video_id: int,
    ordinal: int,
    t_s: float,
    *lines: str,
    phash: int | None = None,
) -> int:
    """One keyframe and its OCR — `*lines` because a slide is several of them.

    Writes `ocr_lines` (the truth) and `ocr_frames` (the FTS content table),
    exactly as `pipeline/store.py::write_ocr` does.
    """
    cur = conn.execute(
        "INSERT INTO keyframes (video_id, ord, t_s, shot_id, shot_start_s, shot_end_s, phash, "
        "sharpness, width, height, jpeg_path, jpeg_bytes, ocr_state) "
        "VALUES (?, ?, ?, 1, ?, ?, ?, 1.0, 16, 16, ?, 1, 'done')",
        (
            video_id,
            ordinal,
            t_s,
            t_s,
            t_s + 1,
            # The index-time picture identity. Two keyframes of a held slide
            # share it, which is how the legs collapse them (§7.4).
            1000 + ordinal if phash is None else phash,
            f"k/{video_id}-{ordinal}.jpg",
        ),
    )
    kf = int(cur.lastrowid or 0)
    for line_no, line in enumerate(lines):
        conn.execute(
            "INSERT INTO ocr_lines (keyframe_id, video_id, t_s, line_no, text, conf, "
            "x0, y0, x1, y1) VALUES (?, ?, ?, ?, ?, 0.9, 0, 0, 1, 1)",
            (kf, video_id, t_s, line_no, line),
        )
    conn.execute(
        "INSERT INTO ocr_frames (keyframe_id, video_id, t_s, text) VALUES (?, ?, ?, ?)",
        (kf, video_id, t_s, queries.OCR_FRAME_SEPARATOR.join(lines)),
    )
    return kf


def add_chunk(
    conn: sqlite3.Connection,
    video_id: int,
    seq: int,
    first_cue: int,
    last_cue: int,
    text: str,
    start_s: float,
    end_s: float,
    vector_text: str | None = None,
) -> int:
    cur = conn.execute(
        "INSERT INTO chunks (video_id, seq, start_s, end_s, first_cue_id, last_cue_id, text, "
        "n_chars) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (video_id, seq, start_s, end_s, first_cue, last_cue, text, len(text)),
    )
    chunk_id = int(cur.lastrowid or 0)
    conn.execute(
        "INSERT INTO vec_chunks (chunk_id, video_id, start_s, embedding) VALUES (?, ?, ?, ?)",
        (
            chunk_id,
            video_id,
            start_s,
            queries.pack_f32(vector_for(vector_text or text, TEXT_DIM)),
        ),
    )
    return chunk_id


def pool(conn: sqlite3.Connection) -> list[int]:
    return queries.resolve_videos(conn, queries.CorpusFilter())


# ------------------------------------------------- F2: filters before the caps


def test_scoped_search_survives_a_candidate_cap_filled_by_other_videos(
    corpus: Corpus,
) -> None:
    """The review's headline case, and the reason a single-video search returned
    zero: hundreds of stronger global `cache` cues fill the FTS candidate CTE,
    the video filter then removes every one of them, and the scoped video's real
    match is never reached because it was never a candidate.
    """

    def build(conn: sqlite3.Connection) -> None:
        noisy = add_video(conn, "noisyvideo0")
        target = add_video(conn, "targetvideo")
        for i in range(300):
            add_cue(conn, noisy, i, i * 3.0, i * 3.0 + 2.0, "cache cache cache cache")
        add_cue(
            conn,
            target,
            0,
            0.0,
            3.0,
            "a long weak line that mentions cache once among many unrelated words",
        )

    corpus.write(build)
    conn = corpus.read
    target = queries.lookup_video_ids(conn, ["targetvideo"])["targetvideo"]

    rows = queries.search_transcript(
        conn,
        queries.SearchParams(q="cache", video_ids=[target], limit=10, candidate_cap=50),
    )
    assert rows, "the scoped video's match must survive a cap the noisy video fills"
    assert {int(r["video_id"]) for r in rows} == {target}


def test_scoped_ocr_survives_a_candidate_cap_filled_by_other_videos(
    corpus: Corpus,
) -> None:
    def build(conn: sqlite3.Connection) -> None:
        noisy = add_video(conn, "noisyvideo0")
        target = add_video(conn, "targetvideo")
        for i in range(300):
            add_ocr(conn, noisy, i, i * 3.0, "cache cache cache")
        add_ocr(conn, target, 0, 1.0, "one quiet mention of cache on a busy slide")

    corpus.write(build)
    conn = corpus.read
    target = queries.lookup_video_ids(conn, ["targetvideo"])["targetvideo"]

    rows = queries.search_ocr(
        conn, queries.SearchParams(q="cache", video_ids=[target], limit=10, candidate_cap=50)
    )
    assert rows
    assert {int(r["video_id"]) for r in rows} == {target}


def test_scoped_vector_leg_does_not_exhaust_k_on_other_videos(corpus: Corpus) -> None:
    """`k` used to be spent globally and filtered afterwards, so a vector search
    scoped to one video could come back empty while that video held chunks."""

    def build(conn: sqlite3.Connection) -> None:
        noisy = add_video(conn, "noisyvideo0")
        target = add_video(conn, "targetvideo")
        for i in range(40):
            cue = add_cue(conn, noisy, i, i * 10.0, i * 10.0 + 5.0, f"near miss number {i}")
            add_chunk(
                conn,
                noisy,
                i,
                cue,
                cue,
                f"near miss number {i}",
                i * 10.0,
                i * 10.0 + 5.0,
                vector_text="the query text itself",  # every noisy chunk is the nearest
            )
        cue = add_cue(conn, target, 0, 0.0, 5.0, "the answer lives here")
        add_chunk(conn, target, 0, cue, cue, "the answer lives here", 0.0, 5.0)

    corpus.write(build)
    conn = corpus.read
    target = queries.lookup_video_ids(conn, ["targetvideo"])["targetvideo"]

    qvec = queries.pack_f32(vector_for("the query text itself", TEXT_DIM))
    # `k_vec=5` is far smaller than the 40 out-of-scope chunks that are all
    # nearer to the query vector than the target's one chunk.
    rows = queries.search_transcript(
        conn,
        queries.SearchParams(
            q="answer",
            video_ids=[target],
            qvec=qvec,
            limit=10,
            k_vec=5,
            vec_max_distance=2.0,
        ),
    )
    assert rows, "k must be spent inside the scope, not on the 40 out-of-scope chunks"
    assert {int(r["video_id"]) for r in rows} == {target}


def test_scoped_frame_leg_does_not_exhaust_k_on_other_videos(corpus: Corpus) -> None:
    def build(conn: sqlite3.Connection) -> None:
        noisy = add_video(conn, "noisyvideo0")
        target = add_video(conn, "targetvideo")
        for i in range(40):
            kf = add_ocr(conn, noisy, i, i * 10.0, f"slide {i}")
            conn.execute(
                "INSERT INTO vec_frames (keyframe_id, video_id, t_s, embedding) VALUES (?,?,?,?)",
                (kf, noisy, i * 10.0, queries.pack_f32(vector_for("the query", FRAME_DIM))),
            )
        kf = add_ocr(conn, target, 0, 1.0, "the wanted slide")
        conn.execute(
            "INSERT INTO vec_frames (keyframe_id, video_id, t_s, embedding) VALUES (?,?,?,?)",
            (kf, target, 1.0, queries.pack_f32(vector_for("the wanted slide", FRAME_DIM))),
        )

    corpus.write(build)
    conn = corpus.read
    target = queries.lookup_video_ids(conn, ["targetvideo"])["targetvideo"]

    qimg = queries.pack_f32(vector_for("the query", FRAME_DIM))
    rows = queries.search_frames(
        conn,
        queries.SearchParams(q="anything", video_ids=[target], limit=10),
        qimg,
        k_frames=5,
        max_distance=2.0,
    )
    assert rows
    assert {str(r["video_id"]) for r in rows} == {"targetvideo"}


def test_time_window_is_part_of_candidate_selection(corpus: Corpus) -> None:
    """Same shape on the time axis: an out-of-window run fills the cap first."""

    def build(conn: sqlite3.Connection) -> None:
        vid = add_video(conn, "onevideo000")
        for i in range(300):
            add_cue(conn, vid, i, 1000.0 + i * 3.0, 1002.0 + i * 3.0, "cache cache cache")
        add_cue(conn, vid, 999, 10.0, 13.0, "an early quiet cache mention")

    corpus.write(build)
    conn = corpus.read
    rows = queries.search_transcript(
        conn,
        queries.SearchParams(
            q="cache", video_ids=pool(conn), limit=10, candidate_cap=50, t_start=0.0, t_end=60.0
        ),
    )
    assert rows, "the in-window cue must be a candidate, not a post-cap survivor"
    assert all(float(r["start_s"]) <= 60.0 for r in rows)


def test_ranks_are_recomputed_over_the_filtered_set(corpus: Corpus) -> None:
    """Every leg's RRF rank must be a position within the SAME scoped universe.

    Global ranks on one leg and filtered ranks on another biased cross-modal
    ordering: the scoped leg's first hit got rank 1 while the unscoped leg's
    equally-good first hit got rank 4,000.
    """

    def build(conn: sqlite3.Connection) -> None:
        other = add_video(conn, "othervideo0")
        target = add_video(conn, "targetvideo")
        for i in range(50):
            add_cue(conn, other, i, i * 3.0, i * 3.0 + 2.0, "cache cache cache")
        add_cue(conn, target, 0, 0.0, 3.0, "a single cache mention")

    corpus.write(build)
    conn = corpus.read
    target = queries.lookup_video_ids(conn, ["targetvideo"])["targetvideo"]
    rows = queries.search_transcript(
        conn,
        queries.SearchParams(q="cache", video_ids=[target], limit=10, cluster_gap=0.0),
    )
    assert len(rows) == 1
    # rank 1 within the scope => the top RRF score the leg can award.
    assert float(rows[0]["score"]) == pytest.approx(1.0 / (queries.RRF_K + 1))


# --------------------------------------------------- F8: OCR length predicates


def test_ocr_honours_min_and_max_chars(corpus: Corpus) -> None:
    """`content_type=ocr q=cache min_chars=100` returned a ten-character line."""

    def build(conn: sqlite3.Connection) -> None:
        vid = add_video(conn, "onevideo000")
        add_ocr(conn, vid, 0, 1.0, "cache")
        add_ocr(conn, vid, 1, 2.0, "cache " + "x" * 200)

    corpus.write(build)
    conn = corpus.read
    ids = pool(conn)

    short_only = queries.search_ocr(
        conn, queries.SearchParams(q="cache", video_ids=ids, limit=10, max_chars=20)
    )
    assert [r["text"] for r in short_only] == ["cache"]

    long_only = queries.search_ocr(
        conn, queries.SearchParams(q="cache", video_ids=ids, limit=10, min_chars=100)
    )
    assert len(long_only) == 1
    assert len(str(long_only[0]["text"])) >= 100


async def test_ocr_length_filter_end_to_end(assembled: Assembled) -> None:
    """The tool disabled the frame leg for min_chars and printed a note about
    it, which made the silence on the OCR leg look deliberate."""
    result = await search.run(
        assembled.deps, q="nvidia", content_type="ocr", min_chars=500, limit=5
    )
    hits = (result.structured_content or {})["results"]
    assert hits == [], hits


# ------------------------- #20: OCR search is frame-granular, never line-granular

# The slide the old index could not find. Three lines, and no single one of them
# holds both `vector` and `retrieval`.
SLIDE = [
    "Vector databases",
    "for retrieval augmented generation",
    "pgvector · qdrant · faiss",
]


def _line_level_hits(lines: list[str], q: str) -> int:
    """What a line-granular OCR index answers — the shape #20 replaced.

    Same tokenizer, same query expression the leg binds; one document per line
    instead of one per frame. It is built here rather than left in the schema
    because the point of the fix is that there is only one OCR index now.
    """
    probe = sqlite3.connect(":memory:")
    try:
        probe.execute(
            "CREATE VIRTUAL TABLE line_fts USING fts5(text, "
            "tokenize=\"unicode61 remove_diacritics 2 tokenchars '_-./'\", prefix='2 3')"
        )
        probe.executemany("INSERT INTO line_fts(text) VALUES (?)", [(line,) for line in lines])
        expression = queries.expand_prefix_fts(q)
        return int(
            probe.execute(
                "SELECT COUNT(*) FROM line_fts WHERE line_fts MATCH ?", (expression,)
            ).fetchone()[0]
        )
    finally:
        probe.close()


def test_two_terms_on_different_lines_of_one_slide_match(corpus: Corpus) -> None:
    """The headline case: the legs AND their terms, so a line-granular index
    answered `vector retrieval` with silence on the slide that says both."""

    def build(conn: sqlite3.Connection) -> None:
        vid = add_video(conn, "slidevideo0")
        add_ocr(conn, vid, 0, 30.0, *SLIDE)

    corpus.write(build)
    conn = corpus.read

    assert _line_level_hits(SLIDE, "vector retrieval") == 0, "the recall hole, reproduced"

    rows = queries.search_ocr(
        conn, queries.SearchParams(q="vector retrieval", video_ids=pool(conn), limit=10)
    )
    assert len(rows) == 1
    assert str(rows[0]["frame_id"]) == "slidevideo0-00000"
    assert float(rows[0]["t_s"]) == 30.0
    matched = str(rows[0]["matched_text"]).lower()
    assert "vector" in matched and "retrieval" in matched, matched


def test_one_term_on_one_line_still_matches_and_still_cites_the_frame(
    corpus: Corpus,
) -> None:
    """The single-line case the line index got right, unchanged — including a
    `tokenchars` identifier, which is why OCR does not get `porter`."""

    def build(conn: sqlite3.Connection) -> None:
        vid = add_video(conn, "slidevideo0")
        add_ocr(conn, vid, 0, 30.0, *SLIDE)
        add_ocr(conn, vid, 1, 90.0, "$ nvidia-smi", "18304MiB / 24564MiB")

    corpus.write(build)
    conn = corpus.read
    ids = pool(conn)

    rows = queries.search_ocr(conn, queries.SearchParams(q="nvidia-smi", video_ids=ids, limit=10))
    assert [str(r["frame_id"]) for r in rows] == ["slidevideo0-00001"]
    assert "nvidia-smi" in str(rows[0]["matched_text"])

    rows = queries.search_ocr(conn, queries.SearchParams(q="qdrant", video_ids=ids, limit=10))
    assert [str(r["frame_id"]) for r in rows] == ["slidevideo0-00000"]


def test_a_term_repeated_down_a_slide_is_one_result_not_five(corpus: Corpus) -> None:
    """A frame is one document, so a word in five bullets is one hit at one
    timestamp — the line index spent five of `max_per_video` on the same slide."""

    def build(conn: sqlite3.Connection) -> None:
        vid = add_video(conn, "slidevideo0")
        add_ocr(conn, vid, 0, 30.0, *[f"bullet {i} about the kv cache" for i in range(5)])

    corpus.write(build)
    conn = corpus.read

    rows = queries.search_ocr(
        conn, queries.SearchParams(q="cache", video_ids=pool(conn), limit=10)
    )
    assert len(rows) == 1
    assert float(rows[0]["t_s"]) == 30.0


async def test_a_multi_line_slide_answers_a_multi_term_query_end_to_end(
    assembled: Assembled,
) -> None:
    """Through the tool: the hit cites the frame and the timestamped link, and
    the text it shows carries both terms."""
    result = await search.run(
        assembled.deps, q="paged fragmentation", content_type="ocr", limit=5
    )
    hits = (result.structured_content or {})["results"]
    assert len(hits) == 1, hits
    hit = hits[0]
    assert hit["source"] == "ocr"
    assert hit["frame_id"] == "zduSFxRajkE-00000"
    assert hit["link"].startswith("https://youtu.be/zduSFxRajkE?t=")
    assert "paged" in hit["text"] and "fragmentation" in hit["text"]
    # And the whole frame, in reading order, is what `max_text_chars=0` means.
    assert "block table" in hit["text"]


async def test_max_text_chars_zero_still_means_the_whole_frame(
    assembled: Assembled,
) -> None:
    """The documented opt-out is "no truncation, give me everything" — for OCR
    that is the frame's every line, not the snippet window."""
    result = await search.run(
        assembled.deps, q="paged", content_type="ocr", max_text_chars=0, limit=5
    )
    hits = (result.structured_content or {})["results"]
    assert [h["text"] for h in hits] == ["paged kv cache | block table | 4% fragmentation"]


# ------------------------------------------------------ F4: the speaker filter


def _diarized(corpus: Corpus) -> tuple[int, int]:
    def build(conn: sqlite3.Connection) -> None:
        conn.execute(
            "INSERT INTO speakers (id, label, display_name) VALUES (1, 'SPEAKER_00', 'Alice')"
        )
        conn.execute(
            "INSERT INTO speakers (id, label, display_name) VALUES (2, 'SPEAKER_01', 'Bob')"
        )
        conn.execute("UPDATE config SET value = '1' WHERE key = 'diarization.enabled'")
        vid = add_video(conn, "onevideo000")
        add_cue(conn, vid, 0, 0.0, 3.0, "alice talks about the cache", speaker_id=1)
        add_cue(conn, vid, 1, 10.0, 13.0, "bob talks about the cache", speaker_id=2)

    corpus.write(build)
    return 1, 2


def test_speaker_resolves_partially_and_case_insensitively(corpus: Corpus) -> None:
    _diarized(corpus)
    conn = corpus.read
    assert queries.resolve_speakers(conn, "ali") == [1]
    assert queries.resolve_speakers(conn, "ALICE") == [1]
    assert queries.resolve_speakers(conn, "SPEAKER_01") == [2]
    assert queries.resolve_speakers(conn, "nobody") == []


def test_speaker_predicate_reaches_the_transcript_sql(corpus: Corpus) -> None:
    """Neither SearchParams nor the transcript SQL carried a speaker predicate,
    so `speaker=Alice q=cache` returned Bob too."""
    _diarized(corpus)
    conn = corpus.read
    ids = pool(conn)

    both = queries.search_transcript(
        conn, queries.SearchParams(q="cache", video_ids=ids, limit=10, cluster_gap=0.0)
    )
    assert len(both) == 2

    alice = queries.search_transcript(
        conn,
        queries.SearchParams(
            q="cache", video_ids=ids, limit=10, cluster_gap=0.0, speaker_ids=[1]
        ),
    )
    assert [str(r["text"]) for r in alice] == ["alice talks about the cache"]


def test_speaker_filter_binds_on_the_browse_leg_too(corpus: Corpus) -> None:
    _diarized(corpus)
    conn = corpus.read
    rows = queries.search_transcript(
        conn,
        queries.SearchParams(
            q="*", video_ids=pool(conn), limit=10, cluster_gap=0.0, speaker_ids=[2]
        ),
    )
    assert [str(r["text"]) for r in rows] == ["bob talks about the cache"]


def test_unmatched_speaker_filters_to_nothing_not_to_everything(corpus: Corpus) -> None:
    """`speaker_ids=[]` is "nothing matched that name", which must return
    nothing. Only `None` means "no speaker filter"."""
    _diarized(corpus)
    conn = corpus.read
    ids = pool(conn)
    assert (
        queries.search_transcript(
            conn,
            queries.SearchParams(q="cache", video_ids=ids, limit=10, speaker_ids=[]),
        )
        == []
    )
    assert queries.search_transcript(
        conn, queries.SearchParams(q="cache", video_ids=ids, limit=10, speaker_ids=None)
    )


# ------------------------------------------------------------ F6: RRF hygiene


def test_overlapping_chunks_contribute_one_vector_score_per_cue(
    corpus: Corpus,
) -> None:
    """Chunks overlap by design (45 s window, 15 s overlap), so a cue lands in
    two of them and used to arrive from the vector leg TWICE — two RRF
    contributions from one leg. The shared fixture has one chunk per video, so
    it could never show this.
    """

    def build(conn: sqlite3.Connection) -> None:
        vid = add_video(conn, "onevideo000")
        cues = [
            add_cue(conn, vid, i, i * 10.0, i * 10.0 + 8.0, f"line number {i} about cache")
            for i in range(6)
        ]
        # Two overlapping chunks, both covering cue index 2 and 3.
        add_chunk(conn, vid, 0, cues[0], cues[3], "first window", 0.0, 38.0)
        add_chunk(conn, vid, 1, cues[2], cues[5], "second window", 20.0, 58.0)

    corpus.write(build)
    conn = corpus.read
    qvec = queries.pack_f32(vector_for("first window", TEXT_DIM))

    params = queries.SearchParams(
        q="cache",
        video_ids=pool(conn),
        qvec=qvec,
        limit=50,
        cluster_gap=0.0,
        max_per_video=50,
        vec_max_distance=2.0,
    )
    rows = queries.search_transcript(conn, params)
    # One row per cue, and no cue may carry more than (fts 1/(k+1) + vec
    # 1/(k+1)) — the ceiling for two legs each contributing once.
    ceiling = 2.0 / (queries.RRF_K + 1)
    assert rows
    assert len(rows) == len({int(json.loads(r["cue_ids"])[0]) for r in rows})
    for row in rows:
        assert float(row["score"]) <= ceiling + 1e-9, dict(row)


async def test_collapsed_ocr_adds_its_leg_contribution(assembled: Assembled) -> None:
    """When a rank-1 OCR hit collapses into a rank-1 transcript hit, the
    survivor used to keep only the transcript score — the corroboration was
    thrown away instead of summed."""
    transcript_only = search.Hit(
        source="transcript",
        video_id=1,
        public_id="v",
        title="t",
        channel=None,
        published_at=None,
        start_s=0.0,
        end_s=5.0,
        text="paged kv cache block table",
        score=0.01,
        cue_ids=[1],
    )
    ocr = search.Hit(
        source="ocr",
        video_id=1,
        public_id="v",
        title="t",
        channel=None,
        published_at=None,
        start_s=1.0,
        end_s=None,
        text="paged kv cache block table",
        score=0.005,
        cue_ids=[],
        frame_id="v-00001",
    )
    survivors = search._dedup_ocr_against_transcript([transcript_only, ocr])
    assert len(survivors) == 1
    assert survivors[0].source == "transcript+ocr"
    assert survivors[0].score == pytest.approx(0.015)
    assert survivors[0].frame_id == "v-00001"


async def test_several_collapsing_ocr_lines_contribute_only_once(
    assembled: Assembled,
) -> None:
    """A repeated slide is still ONE leg agreeing, not three."""
    transcript = search.Hit(
        source="transcript",
        video_id=1,
        public_id="v",
        title="t",
        channel=None,
        published_at=None,
        start_s=0.0,
        end_s=5.0,
        text="paged kv cache block table",
        score=0.01,
        cue_ids=[1],
    )
    ocrs = [
        search.Hit(
            source="ocr",
            video_id=1,
            public_id="v",
            title="t",
            channel=None,
            published_at=None,
            start_s=1.0 + i,
            end_s=None,
            text="paged kv cache block table",
            score=0.005 - i * 0.001,
            cue_ids=[],
            frame_id=f"v-0000{i}",
        )
        for i in range(3)
    ]
    survivors = search._dedup_ocr_against_transcript([transcript, *ocrs])
    assert len(survivors) == 1
    # The BEST ocr contribution, exactly once — not 0.01 + 0.005 + 0.004 + 0.003.
    assert survivors[0].score == pytest.approx(0.015)


# ----------------------------------------------- F9: contiguity and hard bound


def test_cluster_text_is_the_contiguous_run_not_only_the_matches(
    corpus: Corpus,
) -> None:
    """Matches at 0 s and 4 s clustered, and the unmatched cue at 2 s — which
    may carry the negation that reverses the meaning — was dropped from the
    concatenation. That is keyword confetti, not a passage."""

    def build(conn: sqlite3.Connection) -> None:
        vid = add_video(conn, "onevideo000")
        add_cue(conn, vid, 0, 0.0, 1.8, "the cache is useful")
        add_cue(conn, vid, 1, 2.0, 3.8, "but never in this situation")
        add_cue(conn, vid, 2, 4.0, 5.8, "so the cache is wrong here")

    corpus.write(build)
    conn = corpus.read
    rows = queries.search_transcript(
        conn, queries.SearchParams(q="cache", video_ids=pool(conn), limit=10, cluster_gap=8.0)
    )
    assert len(rows) == 1
    text = str(rows[0]["text"])
    assert "but never in this situation" in text, text
    assert int(rows[0]["n_cues"]) == 3
    assert int(rows[0]["n_matched"]) == 2
    assert json.loads(rows[0]["cue_ids"]) == sorted(json.loads(rows[0]["cue_ids"]))
    assert len(json.loads(rows[0]["cue_ids"])) == 3


def test_cluster_span_is_bounded_on_end_times(corpus: Corpus) -> None:
    """The grid bounded START times only: a dense run whose last cue starts at
    119.9 s and ends at 123 s produced a segment longer than the advertised
    120 s ceiling."""

    def build(conn: sqlite3.Connection) -> None:
        vid = add_video(conn, "onevideo000")
        seq = 0
        t = 0.0
        while t < 119.0:
            add_cue(conn, vid, seq, t, t + 1.8, "cache line")
            seq += 1
            t += 2.0
        add_cue(conn, vid, seq, 119.9, 123.0, "cache line crossing the boundary")

    corpus.write(build)
    conn = corpus.read
    rows = queries.search_transcript(
        conn,
        queries.SearchParams(
            q="cache", video_ids=pool(conn), limit=200, cluster_gap=8.0, max_per_video=200
        ),
    )
    assert rows
    for row in rows:
        span = float(row["end_s"]) - float(row["start_s"])
        assert span <= queries.CLUSTER_MAX_SECONDS + 1e-6, dict(row)


def test_an_individually_overlong_cue_is_its_own_segment(corpus: Corpus) -> None:
    """The explicit policy: a single 180 s cue is never split — half a sentence
    with a truncated timestamp is a worse citation than an honest overlong one —
    so it is the ONLY shape that may exceed the ceiling, and it stands alone."""

    def build(conn: sqlite3.Connection) -> None:
        vid = add_video(conn, "onevideo000", duration_s=1000.0)
        add_cue(conn, vid, 0, 0.0, 180.0, "one very long cache cue")
        add_cue(conn, vid, 1, 181.0, 183.0, "a normal cache cue after it")

    corpus.write(build)
    conn = corpus.read
    rows = queries.search_transcript(
        conn,
        queries.SearchParams(
            q="cache", video_ids=pool(conn), limit=50, cluster_gap=8.0, max_per_video=50
        ),
    )
    long_rows = [r for r in rows if float(r["end_s"]) - float(r["start_s"]) > 120.0]
    assert len(long_rows) == 1
    assert int(long_rows[0]["n_matched"]) == 1, "the overlong cue must stand alone"
    # ...and it did not swallow the cue after it.
    assert len(rows) == 2


# ------------------------------------------------------- F10: deterministic ties


def test_tied_ranks_produce_a_stable_order(corpus: Corpus) -> None:
    """Identical text gives identical BM25 ranks, and identical chunk vectors
    give identical distances. Without a stable id in every ORDER BY, the rows at
    the candidate and page boundaries can change between two identical calls."""

    def build(conn: sqlite3.Connection) -> None:
        vid = add_video(conn, "onevideo000")
        for i in range(30):
            add_cue(conn, vid, i, i * 10.0, i * 10.0 + 2.0, "identical cache line")

    corpus.write(build)
    conn = corpus.read
    params = queries.SearchParams(
        q="cache", video_ids=pool(conn), limit=50, cluster_gap=0.0, max_per_video=50
    )
    first = [
        (float(r["start_s"]), json.loads(r["cue_ids"]))
        for r in queries.search_transcript(conn, params)
    ]
    for _ in range(5):
        again = [
            (float(r["start_s"]), json.loads(r["cue_ids"]))
            for r in queries.search_transcript(conn, params)
        ]
        assert again == first


def test_tied_pages_do_not_overlap_or_skip(corpus: Corpus) -> None:
    def build(conn: sqlite3.Connection) -> None:
        vid = add_video(conn, "onevideo000")
        for i in range(30):
            add_cue(conn, vid, i, i * 10.0, i * 10.0 + 2.0, "identical cache line")

    corpus.write(build)
    conn = corpus.read

    def page(offset: int, limit: int) -> list[float]:
        rows = queries.search_transcript(
            conn,
            queries.SearchParams(
                q="cache",
                video_ids=pool(conn),
                limit=limit,
                offset=offset,
                cluster_gap=0.0,
                max_per_video=50,
            ),
        )
        return [float(r["start_s"]) for r in rows[:limit]]

    walked: list[float] = []
    for offset in range(0, 30, 5):
        walked.extend(page(offset, 5))
    assert len(walked) == len(set(walked)), "paging must not repeat a row"
    assert sorted(walked) == sorted(float(i * 10.0) for i in range(30))


# ------------------------------------- the vector legs' floor and footing gate


async def test_a_nonsense_query_returns_nothing(assembled: Assembled) -> None:
    """The demo's zero-results state was unreachable.

    Nearest-neighbour search returns its k nearest whatever it is asked, so
    `zzzzqqqq` came back with the whole corpus wearing confident RRF scores.

    Two independent mechanisms produce the empty answer, and they fire in
    different regimes — the lexical-footing gate (asserted here, by its note)
    and the distance floor (asserted by the two floor tests below). On a corpus
    of real embeddings the gate does the work, because measured real and junk
    distances overlap; on this fixture's pseudo-random vectors the floor does.
    Both are tested because production sees both regimes.
    """
    for junk in ("zzzzqqqq", "asdfghjkl qwertyuiop", "blorptastic", "flurbles wibbly zonk"):
        result = await search.run(assembled.deps, q=junk, limit=10)
        payload = result.structured_content or {}
        assert not result.is_error, junk
        assert payload["results"] == [], (junk, payload["results"])
        assert payload["pagination"]["has_more"] is False
        assert "data_status" in payload, junk
        assert any("nearest-neighbour" in n for n in payload["notes"]), (junk, payload["notes"])


async def test_a_real_query_still_reaches_the_semantic_legs(
    assembled: Assembled,
) -> None:
    """The gate must not be a general recall cut: a query whose terms exist
    still gets its vector leg, including the paraphrase case the leg is for."""
    result = await search.run(assembled.deps, q="cache", limit=10)
    assert (result.structured_content or {})["results"]
    assert not any("nearest-neighbour" in n for n in (result.structured_content or {})["notes"])


def test_footing_is_an_or_over_terms_not_the_and_the_legs_bind(
    corpus: Corpus,
) -> None:
    """The legs AND their terms, which is right for ranking and wrong for
    "does the corpus know any of these words". A real multi-word question whose
    AND-form matches nothing must still be footed."""

    def build(conn: sqlite3.Connection) -> None:
        vid = add_video(conn, "onevideo000")
        add_cue(conn, vid, 0, 0.0, 3.0, "the cache is the interesting part")

    corpus.write(build)
    conn = corpus.read

    assert queries.has_lexical_footing(conn, "cache")
    # No cue contains all of these, so the AND-form the leg binds finds nothing...
    assert queries.search_transcript(
        conn, queries.SearchParams(q="how does the cache behave under pressure", video_ids=pool(conn))
    ) == []
    # ...but the corpus does know these words, so the semantic leg still runs.
    assert queries.has_lexical_footing(conn, "how does the cache behave under pressure")
    assert not queries.has_lexical_footing(conn, "zzzzqqqq blorptastic")


def test_footing_fails_open_on_a_query_with_no_terms(corpus: Corpus) -> None:
    """The gate rejects nonsense; it must never invent a new empty state."""

    def build(conn: sqlite3.Connection) -> None:
        add_video(conn, "onevideo000")

    corpus.write(build)
    conn = corpus.read
    for empty in ("", "   ", "()", "\x00", "*"):
        assert queries.has_lexical_footing(conn, empty) is True, repr(empty)


def test_vector_floor_drops_hits_beyond_the_ceiling(corpus: Corpus) -> None:
    """Below-floor vector hits are dropped BEFORE fusion, not ranked lower."""

    def build(conn: sqlite3.Connection) -> None:
        vid = add_video(conn, "onevideo000")
        cue = add_cue(conn, vid, 0, 0.0, 5.0, "an unrelated line about gardening")
        add_chunk(conn, vid, 0, cue, cue, "an unrelated line about gardening", 0.0, 5.0)

    corpus.write(build)
    conn = corpus.read
    qvec = queries.pack_f32(vector_for("something else entirely", TEXT_DIM))

    def run(ceiling: float):
        return queries.search_transcript(
            conn,
            queries.SearchParams(
                q="gardening",
                video_ids=pool(conn),
                qvec=qvec,
                limit=10,
                cluster_gap=0.0,
                vec_max_distance=ceiling,
            ),
        )

    # The FTS leg matches either way; what changes is whether the vector leg
    # also contributed, which shows up as a strictly larger fused score.
    wide = run(2.0)
    tight = run(0.0)
    assert wide and tight
    assert float(wide[0]["score"]) > float(tight[0]["score"])


def test_frame_floor_drops_hits_beyond_the_ceiling(corpus: Corpus) -> None:
    def build(conn: sqlite3.Connection) -> None:
        vid = add_video(conn, "onevideo000")
        kf = add_ocr(conn, vid, 0, 1.0, "a slide")
        conn.execute(
            "INSERT INTO vec_frames (keyframe_id, video_id, t_s, embedding) VALUES (?,?,?,?)",
            (kf, vid, 1.0, queries.pack_f32(vector_for("a slide", FRAME_DIM))),
        )

    corpus.write(build)
    conn = corpus.read
    qimg = queries.pack_f32(vector_for("nothing like that slide at all", FRAME_DIM))
    params = queries.SearchParams(q="slide", video_ids=pool(conn), limit=10)

    assert queries.search_frames(conn, params, qimg, 20, max_distance=2.0)
    assert queries.search_frames(conn, params, qimg, 20, max_distance=0.0) == []


def test_the_configured_floors_are_the_defaults() -> None:
    """The knobs are wired, and `deploy/.env.example` documents both.

    Against the class defaults, not the test fixture: the fixture's vectors are
    `sin()` stand-ins with no model's geometry, so it pins its own numbers,
    while these are measurements — 2026-08-10, on the repaired
    Qwen3-VL-Embedding-2B space, real best-hit 0.220-0.459 against junk
    0.579-0.665 (research/vec-floor-calibration-2026-08-10.md §6). They shipped
    open at 1.0 for as long as the encoder was randomly initialised and no
    corridor existed to put them in; moving them again means re-running that
    measurement, not editing this line."""
    shipped = Settings(
        data_dir=Path("/data"), public_url="http://x", worker_url="http://y"
    )
    assert shipped.vec_max_distance == queries.VEC_MAX_DISTANCE == 0.55
    assert shipped.frame_max_distance == queries.FRAME_MAX_DISTANCE == 0.65
    env = (Path(__file__).resolve().parents[2] / "deploy" / ".env.example").read_text()
    assert "VIDTHEQUE_VEC_MAX_DISTANCE" in env
    assert "VIDTHEQUE_FRAME_MAX_DISTANCE" in env


# ===========================================================================
# The field test, 2026-08-09 (research/demo-queries-2026-08-09.md §7 and §9).
#
# Nine observed failures in the search / ranking / citation / pagination
# cluster, each with the reproducer the field test recorded. They share one
# root: the ranking used to be computed over `offset + limit` rows, so it was a
# function of the page the caller asked for rather than of the query. The tests
# below are written against the *behaviour* — same query, same answer, at any
# limit — so the guarantee survives a rewrite of how it is achieved.
# ===========================================================================


@pytest.fixture
async def tool_corpus(settings: Settings, fake_embeddings):
    """`assembled`, but over a corpus the test builds itself.

    The shared fixture is three videos and ten cues; several of these failures
    only appear with enough candidates for a page to be a *prefix* of something.
    """
    from vidtheque_mcp.app import assemble
    from vidtheque_mcp.jobs.runner import NotImplementedPipeline

    opened: list = []

    async def build(fn):
        conn = open_write_connection(settings.db_path)
        try:
            migrations.migrate(conn)
            conn.execute("BEGIN IMMEDIATE")
            fn(conn)
            conn.execute("COMMIT")
        finally:
            conn.close()
        parts = assemble(
            settings,
            embeddings=fake_embeddings,
            run_pipeline=False,
            pipeline=NotImplementedPipeline(),
        )
        await parts.db.open()
        opened.append(parts)
        return parts

    yield build
    for parts in opened:
        await parts.db.close()
        parts.auth.close()


def rows_of(result) -> list[dict]:
    assert result.structured_content is not None
    return result.structured_content["results"]


def page_of(result) -> dict:
    assert result.structured_content is not None
    return result.structured_content["pagination"]


def text_of(result) -> str:
    return "\n".join(b.text for b in result.content if getattr(b, "type", "") == "text")


def add_frame_vector(
    conn: sqlite3.Connection, keyframe_id: int, video_id: int, t_s: float, vector_text: str
) -> None:
    conn.execute(
        "INSERT INTO vec_frames (keyframe_id, video_id, t_s, embedding) VALUES (?, ?, ?, ?)",
        (keyframe_id, video_id, t_s, queries.pack_f32(vector_for(vector_text, FRAME_DIM))),
    )


# ------------------------------------------------ §9.1.2 / §7.5: the citation


async def test_the_citation_is_the_matched_cue_and_does_not_move_with_limit(
    tool_corpus,
) -> None:
    """§7.5 and §9.1.2, together, because they are one bug.

    A semantic leg expands its chunk to every cue inside it, so the island grows
    to the width of the chunk — and the deep link pointed at the island's first
    second. Worse, how much of the chunk arrived depended on `k`, and `k` was
    `offset + limit` times a constant, so the citation for the *same hit at the
    same rank* moved 34 seconds between `limit=3` and `limit=5`.

    The corpus below is that shape at its smallest: 110 decoy chunks sit exactly
    on the query vector, so under the old `k` the target's own chunk fell
    outside the KNN at `limit=3` and inside it at `limit=5`.
    """
    phrase = "the phrase we are looking for"

    def make(conn: sqlite3.Connection) -> None:
        decoy = add_video(conn, "decoyvideo0")
        for i in range(110):
            cue = add_cue(conn, decoy, i, i * 10.0, i * 10.0 + 4.0, f"decoy line {i}")
            add_chunk(
                conn, decoy, i, cue, cue, f"decoy chunk {i}",
                i * 10.0, i * 10.0 + 4.0, vector_text=phrase,
            )
        target = add_video(conn, "targetvide0")
        cues = [
            add_cue(
                conn, target, i, i * 5.0, i * 5.0 + 4.0,
                f"and here is {phrase}" if i == 20 else f"unrelated preamble number {i}",
            )
            for i in range(21)
        ]
        add_chunk(
            conn, target, 0, cues[0], cues[-1], "the whole talk", 0.0, 104.0,
            vector_text=phrase,
        )

    parts = await tool_corpus(make)
    small = rows_of(await search.run(parts.deps, q=phrase, content_type="transcript", limit=3))
    large = rows_of(await search.run(parts.deps, q=phrase, content_type="transcript", limit=5))

    def target_hit(rows: list[dict]) -> dict:
        return next(r for r in rows if r["video_id"] == "targetvide0")

    a, b = target_hit(small), target_hit(large)
    # 1. The link is the same at both page sizes — the whole point.
    assert a["link"] == b["link"], (a, b)
    assert a["match_start"] == b["match_start"] == 100.0
    assert a["score"] == b["score"]
    # 2. And it points at the matched cue, not at the island's first second.
    assert a["start"] < 100.0, "the island really is wider than the match"
    assert a["link"].endswith("?t=98")


# ----------------------------------------------------- §9.1.3: rank stability


def _fusion_bonus_corpus(conn: sqlite3.Connection) -> None:
    """One video whose transcript hit is corroborated by an OCR hit that used to
    fall outside a small page's fetched prefix, and one rival that outranks it
    without the corroboration."""
    rival = add_video(conn, "rivalvideo0")
    add_cue(conn, rival, 0, 0.0, 4.0, "the kv cache the kv cache the kv cache")
    both = add_video(conn, "bothlegsvid")
    add_cue(conn, both, 0, 30.0, 34.0, "the kv cache is the whole trick")
    add_ocr(conn, both, 0, 31.0, "the kv cache is the whole trick")
    # Four slides that outrank `both`'s on BM25, so the OCR leg's copy of the
    # corroborating hit sits at rank 5 — outside `limit + 1` for a small page.
    for i in range(4):
        noise = add_video(conn, f"ocrnoisevi{i}")
        add_ocr(conn, noise, 0, 5.0, "kv cache")


async def test_rank_one_is_the_same_at_limit_1_and_limit_50(tool_corpus) -> None:
    """§9.1.3. The `[transcript+ocr]` bonus is the largest score differentiator
    in the payload, and it used to fire only when both legs' copies of the hit
    landed inside `offset + limit` — so "show more" did not show more of the
    same list, it showed a different list with a different winner."""
    parts = await tool_corpus(_fusion_bonus_corpus)
    small = rows_of(await search.run(parts.deps, q="kv cache", limit=1))
    large = rows_of(await search.run(parts.deps, q="kv cache", limit=50))

    assert small[0]["video_id"] == large[0]["video_id"] == "bothlegsvid"
    assert small[0]["source"] == "transcript+ocr"
    assert small[0]["score"] == large[0]["score"]
    # And the corroboration is real, not an artefact of the page: two legs.
    assert large[0]["score"] > large[1]["score"]


# -------------------------------------------- §7.1 / §9.2: the RRF tie-break


async def test_an_exact_match_wins_the_rrf_tie(tool_corpus) -> None:
    """§7.1, the highest-impact finding. Every leg's rank 1 scores exactly
    1/(60+1), so ties at the top are the rule, not the exception; the tie-break
    was `_sort_key`, whose first element is `public_id` — alphabetical by video
    id. An exact, unique string match came back behind a fuzzy neighbour that
    did not contain it at all."""

    def make(conn: sqlite3.Connection) -> None:
        # Alphabetically first, and a pure vector neighbour: it contains not one
        # word of the query.
        aaa = add_video(conn, "aaaavideo00")
        cue = add_cue(conn, aaa, 0, 10.0, 14.0, "tiny models on a laptop, mostly")
        add_chunk(
            conn, aaa, 0, cue, cue, "tiny models on a laptop, mostly", 10.0, 14.0,
            vector_text="small towns in bavaria",
        )
        # Alphabetically last, and it is the answer, word for word.
        zzz = add_video(conn, "zzzzvideo00")
        add_ocr(conn, zzz, 0, 20.0, "the internet runs on small towns in Bavaria")

    parts = await tool_corpus(make)
    rows = rows_of(await search.run(parts.deps, q="small towns in bavaria", limit=5))

    assert len(rows) == 2, rows
    assert rows[0]["score"] == rows[1]["score"], "the tie is the premise of the test"
    assert rows[0]["video_id"] == "zzzzvideo00", rows
    # The old order, spelled out, so the regression is unmistakable.
    assert sorted(r["video_id"] for r in rows)[0] == "aaaavideo00"


def test_the_tiebreak_puts_relevance_before_identity() -> None:
    """The unit of the rule: identity is the LAST key, never the first."""
    weak = search.Hit(
        source="transcript", video_id=1, public_id="aaa", title="", channel=None,
        published_at=None, start_s=0.0, end_s=1.0, text="something else",
        score=0.0164, cue_ids=[1], coverage=0.0,
    )
    strong = search.Hit(
        source="ocr", video_id=2, public_id="zzz", title="", channel=None,
        published_at=None, start_s=0.0, end_s=None, text="the exact phrase",
        score=0.0164, cue_ids=[], coverage=1.0, exact=1,
    )
    assert search._sort([weak, strong], "relevance") == [strong, weak]
    # Identity still decides when nothing else can, so the order stays total.
    strong.exact, strong.coverage = 0, 0.0
    assert search._sort([strong, weak], "relevance") == [weak, strong]


# ------------------------------------------------------- §7.7: max_per_video


def _six_videos(conn: sqlite3.Connection) -> None:
    for i in range(6):
        vid = add_video(conn, f"evalvideo{i:02d}")
        add_cue(conn, vid, 0, 10.0, 14.0, "what makes a good eval is coverage")
        add_cue(conn, vid, 1, 100.0, 104.0, "a good eval has to be reproducible")


async def test_max_per_video_backfills_the_page_instead_of_truncating_it(
    tool_corpus,
) -> None:
    """§7.7. The caller asked for 6 and got 3, and the payload asserted "no more
    results" while the same query without the cap proved ≥30 candidates existed:
    the cap was applied to a page of six already-fetched rows, and `has_more`
    was computed after it."""
    parts = await tool_corpus(_six_videos)
    result = await search.run(
        parts.deps, q="good eval", content_type="transcript", limit=6, max_per_video=1
    )
    rows = rows_of(result)
    assert len(rows) == 6, rows
    assert len({r["video_id"] for r in rows}) == 6
    assert "(no more results)" in text_of(result)
    assert page_of(result)["approx_total"] == 6


# ----------------------------------------- §7.8 / §9.1.6: pagination honesty


async def test_the_total_does_not_scale_with_the_page(tool_corpus) -> None:
    """§9.1.6. `probe_*` ceilinged at `offset + limit + 30`, so the number the
    guide explicitly teaches callers to read went `3/~40+` at `limit=3` and
    `50/~130+` at `limit=50` — same query, same corpus. It needs more
    candidates than the headroom to show, which is the point: the number only
    lied once there was something to lie about."""

    def make(conn: sqlite3.Connection) -> None:
        for i in range(40):
            vid = add_video(conn, f"evalvideo{i:02d}")
            add_cue(conn, vid, 0, 10.0, 14.0, "what makes a good eval is coverage")
            add_cue(conn, vid, 1, 100.0, 104.0, "a good eval has to be reproducible")

    parts = await tool_corpus(make)
    totals = {
        page_of(
            await search.run(
                parts.deps, q="good eval", content_type="transcript", limit=limit
            )
        )["approx_total"]
        for limit in (1, 3, 12, 50)
    }
    assert totals == {80}, totals


async def test_the_frame_legs_total_is_not_limit_plus_one(tool_corpus) -> None:
    """§7.8. The count probe never covered the vector leg, so its "total" was
    whatever the `limit + 1` fetch returned: `5/6` for a query with 18 matching
    frames."""

    def make(conn: sqlite3.Connection) -> None:
        for i in range(8):
            vid = add_video(conn, f"framevide{i:02d}")
            kf = add_ocr(conn, vid, 0, 10.0, "terminal window with code")
            add_frame_vector(conn, kf, vid, 10.0, "a terminal window with code")

    parts = await tool_corpus(make)
    result = await search.run(
        parts.deps, q="a terminal window with code", content_type="frame", limit=3
    )
    assert len(rows_of(result)) == 3
    assert page_of(result)["approx_total"] == 8, page_of(result)


async def test_paging_past_the_end_says_where_the_end_is(tool_corpus) -> None:
    """§7.9. Over-paging printed `Results: 0/200` — a "total" equal to the
    offset — the query echo, the leg counts, and then two blank lines: strictly
    less help than a genuinely empty search gets."""
    parts = await tool_corpus(_six_videos)
    result = await search.run(
        parts.deps, q="good eval", content_type="transcript", limit=3, offset=200
    )
    body = text_of(result)
    assert "0/200" not in body
    assert "past the last page" in body
    pagination = page_of(result)
    assert pagination["offset"] == 200
    assert pagination["has_more"] is False
    assert pagination["approx_total"] == 12
    assert pagination["last_offset"] == 9
    assert "offset=9" in body


async def test_the_end_of_the_pool_is_not_the_end_of_the_corpus(
    tool_corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A bounded pool is honest only if the payload says which end it reached.

    `(no more results)` at the end of a pool that filled would be §7.7's lie in
    a new place: the ranking ran out, the corpus did not.
    """
    parts = await tool_corpus(_six_videos)
    monkeypatch.setattr(search, "CANDIDATE_POOL", 2)
    result = await search.run(
        parts.deps, q="good eval", content_type="transcript", limit=50
    )
    body = text_of(result)
    assert "(no more results)" not in body
    assert "end of the ranked pool" in body
    assert "deeper matches exist" in body
    assert page_of(result)["pool_exhausted"] is True


async def test_paging_past_a_full_pool_still_says_the_pool_was_full(
    tool_corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The 2026-08-09 review's MEDIUM: the two honesty rules have to compose.

    An offset past the end took the early return, which dropped `pool_full` —
    so the one response that most invites "there is nothing past here" printed
    a bounded count as a complete one, with no `pool_exhausted` and no note.
    """
    parts = await tool_corpus(_six_videos)
    monkeypatch.setattr(search, "CANDIDATE_POOL", 2)
    result = await search.run(
        parts.deps, q="good eval", content_type="transcript", limit=3, offset=50
    )
    body = text_of(result)
    pagination = page_of(result)
    assert rows_of(result) == []
    assert pagination["pool_exhausted"] is True
    assert "ranked pool" in body, "the count describes the pool, not the corpus"
    assert "deeper matches exist" in body
    assert pagination["approx_total"] == 2, "the pool, and only the pool"
    assert f"offset={pagination['last_offset']}" in body


async def test_the_pool_is_the_pool_and_not_the_pool_plus_one(
    tool_corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The review's LOW: the `+1` row is a sentinel, not a result.

    Each leg fetches `pool + 1` to learn whether it had more to give. Keeping
    that row made the pool one deeper than every sentence in the payload said
    it was — the leg counts, the total and "the first N candidates per leg" all
    disagreed with each other by one.
    """
    parts = await tool_corpus(_six_videos)
    monkeypatch.setattr(search, "CANDIDATE_POOL", 2)
    result = await search.run(
        parts.deps, q="good eval", content_type="transcript", limit=50
    )
    # `transcript 2 (…)` — the fused count is still counted after the sentinel
    # goes; what follows it is the sub-leg split (§4.2 of the terra eval).
    assert "Legs: transcript 2 segments (" in text_of(result), "counted after the sentinel goes"
    assert page_of(result)["approx_total"] == 2
    assert len(rows_of(result)) == 2, "and the page cannot show a 3rd of 2"
    assert page_of(result)["pool_exhausted"] is True, "still known to be bounded"


async def test_a_filter_that_matches_nothing_still_echoes_the_page(
    tool_corpus,
) -> None:
    """§9.1.9. The early return defaulted `limit`/`offset` to 0, so the same
    empty result described itself differently depending on which emptiness it
    was."""
    parts = await tool_corpus(_six_videos)
    result = await search.run(
        parts.deps, q="good eval", channel="nonexistent", limit=3, offset=6
    )
    assert page_of(result) == {
        "limit": 3,
        "offset": 6,
        "has_more": False,
        "approx_total": 0,
    }


# ------------------------------------------------------- §7.6: cluster_gap=0


async def test_cluster_gap_zero_does_not_decay_into_positional_filler(
    tool_corpus,
) -> None:
    """§7.6. Three consecutive cues, ranked 1/2/3 by position, containing none
    of the query terms — rank 2 was "Thank you, Sid, for speaking."

    The mechanism: the vector leg ranked the *cues* it expanded out of one
    chunk, so they arrived as ranks 1, 2, 3 and took the whole page. A
    chunk-level match has no per-cue evidence to spread over forty cues, so at
    `cluster_gap=0` the semantic leg cites its chunk once, at the chunk's anchor.
    """

    def make(conn: sqlite3.Connection) -> None:
        vague = add_video(conn, "vaguevideo0")
        cues = [
            add_cue(conn, vague, i, i * 5.0, i * 5.0 + 4.0, f"thank you for speaking {i}")
            for i in range(20)
        ]
        add_chunk(
            conn, vague, 0, cues[0], cues[-1], "the whole talk", 0.0, 99.0,
            vector_text="the dirty secret",
        )
        lexical = add_video(conn, "lexicalvid0")
        add_cue(conn, lexical, 0, 12.0, 16.0, "the dirty secret of this industry")

    parts = await tool_corpus(make)
    rows = rows_of(
        await search.run(
            parts.deps,
            q="the dirty secret",
            content_type="transcript",
            cluster_gap=0,
            limit=3,
        )
    )
    from_vague = [r for r in rows if r["video_id"] == "vaguevideo0"]
    assert len(from_vague) == 1, "one chunk, one citation"
    assert rows[0]["video_id"] == "lexicalvid0", rows


# ---------------------------------------------------- §7.4: duplicate frames


async def test_a_held_slide_is_one_result_not_two(tool_corpus) -> None:
    """§7.4 ("Andon"). Two keyframes, one slide: the talk held it across a shot
    boundary, both frames OCR'd to the same text, and both became results —
    identical down to the timestamp string, and each spending a slot of
    `max_per_video`. `keyframes.phash` already knows they are one picture."""

    def make(conn: sqlite3.Connection) -> None:
        vid = add_video(conn, "andonvideo0")
        add_ocr(conn, vid, 0, 21.0, "Andon Labs", phash=777)
        add_ocr(conn, vid, 1, 24.0, "Andon Labs", phash=777)
        add_ocr(conn, vid, 2, 90.0, "Andon Labs, and now a different slide", phash=778)

    parts = await tool_corpus(make)
    rows = rows_of(await search.run(parts.deps, q="Andon", content_type="ocr", limit=4))
    assert sorted(r["frame_id"] for r in rows) == [
        "andonvideo0-00000",
        "andonvideo0-00002",
    ], rows


async def test_one_frame_found_by_two_legs_is_one_result(tool_corpus) -> None:
    """The cross-leg half of the same rule: the OCR leg and the frame leg index
    the same keyframes, so a slide that matches on both arrived twice."""

    def make(conn: sqlite3.Connection) -> None:
        vid = add_video(conn, "bothframevi")
        kf = add_ocr(conn, vid, 0, 40.0, "architecture diagram with boxes and arrows")
        add_frame_vector(conn, kf, vid, 40.0, "architecture diagram with boxes and arrows")

    parts = await tool_corpus(make)
    rows = rows_of(
        await search.run(parts.deps, q="architecture diagram with boxes and arrows", limit=5)
    )
    assert len(rows) == 1, rows
    assert rows[0]["source"] == "ocr+frame"
    # Two channels agreeing is corroboration: RRF sums the legs, it does not
    # pick one and throw the other away.
    assert rows[0]["score"] == pytest.approx(2 / 61, abs=5e-5)


async def test_a_narrated_slide_does_not_come_back_as_a_frame_as_well(
    tool_corpus,
) -> None:
    """The three-leg case: the OCR hit collapses into the narration it repeats,
    and the frame leg's copy of that same keyframe must join it rather than
    appear beside it as a second sighting of one picture."""

    def make(conn: sqlite3.Connection) -> None:
        vid = add_video(conn, "narratedvid")
        add_cue(conn, vid, 0, 38.0, 44.0, "an architecture diagram with boxes and arrows")
        kf = add_ocr(conn, vid, 0, 40.0, "architecture diagram with boxes and arrows")
        add_frame_vector(conn, kf, vid, 40.0, "architecture diagram with boxes and arrows")

    parts = await tool_corpus(make)
    rows = rows_of(
        await search.run(parts.deps, q="architecture diagram with boxes and arrows", limit=5)
    )
    assert len(rows) == 1, rows
    assert rows[0]["source"] == "transcript+ocr+frame"
    assert rows[0]["frame_id"] == "narratedvid-00000"


# ----------------------------------------------------- §7.11: OCR min_chars


def test_min_chars_measures_the_frame_not_the_line(corpus: Corpus) -> None:
    """§7.11. `min_chars` was measured against a single OCR *line* while the leg
    matched whole frames, so `min_chars=500` nullified the OCR leg with no note
    — one leg filtered to nothing, which `all` means all forbids. tool-surface
    §4.1: on the OCR leg the segment is the frame.

    The field test ran against a stack at migration 0002, where the OCR index
    was still line-granular; migration 0003 moved the predicate onto
    `ocr_frames.text` with the index. So this is a guard, not a repair — and it
    is written line-first (five lines, none of them long enough on its own) so
    it fails the moment the predicate moves back to `ocr_lines`.
    """

    def make(conn: sqlite3.Connection) -> None:
        vid = add_video(conn, "denseslide0")
        # Five lines of ~40 chars: no line reaches 100, the frame is 200+.
        add_ocr(conn, vid, 0, 10.0, *[f"bullet {i} about evals " + "x" * 20 for i in range(5)])
        add_ocr(conn, vid, 1, 60.0, "evals")

    corpus.write(make)
    conn = corpus.read
    ids = pool(conn)

    dense = queries.search_ocr(
        conn, queries.SearchParams(q="evals", video_ids=ids, limit=10, min_chars=100)
    )
    assert [str(r["frame_id"]) for r in dense] == ["denseslide0-00000"]
    lines = str(dense[0]["text"]).split(queries.OCR_FRAME_SEPARATOR)
    assert max(len(line) for line in lines) < 100, "no single LINE would have passed"

    sparse = queries.search_ocr(
        conn, queries.SearchParams(q="evals", video_ids=ids, limit=10, max_chars=20)
    )
    assert [str(r["frame_id"]) for r in sparse] == ["denseslide0-00001"]


# --------------------------------- §7.10 / §9.1.5: the empty state, and §7.5


async def test_the_empty_state_names_the_legs_that_actually_ran(tool_corpus) -> None:
    """§7.10 and §9.1.5. "Every leg was queried and none of them matched." is
    false whenever the caller pinned `content_type` — and at `content_type=all`
    it contradicted a `note:` four lines above it in the same payload saying the
    semantic legs had been skipped."""
    parts = await tool_corpus(_six_videos)

    pinned = text_of(
        await search.run(parts.deps, q="zzzznothinghere", content_type="ocr", limit=3)
    )
    assert "Every leg was queried" not in pinned
    assert "The ocr leg was queried" in pinned
    # ...and why the others sat out, which here is the caller's own doing and
    # has no `note:` to point at.
    assert "you pinned content_type=ocr" in pinned
    assert "transcript and frame legs did not run" in pinned

    # No lexical footing: the semantic legs are gated off, and the payload says
    # so — so it must not also claim all three ran.
    gated = text_of(await search.run(parts.deps, q="zzzznothinghere", limit=3))
    assert "semantic (nearest-neighbour) legs were not queried" in gated
    assert "All three legs" not in gated
    assert "The transcript and ocr legs were queried" in gated
    assert "for the reason in the note above" in gated

    # And with footing, all three really do run, and the line says exactly that.
    everything = text_of(await search.run(parts.deps, q="reproducible zzzznothing", limit=3))
    assert "All three legs were queried and none of them matched." in everything


async def test_the_matched_phrase_survives_truncation(tool_corpus) -> None:
    """§7.5's second half. At `max_text_chars=400` the middle-truncation removed
    the matched phrase from a two-minute cluster, so the result showed neither
    the words that matched nor a timestamp near them."""
    phrase = "the dirty secret of forward deployed engineering"

    def make(conn: sqlite3.Connection) -> None:
        vid = add_video(conn, "longtalkvid")
        cues = [
            add_cue(
                conn, vid, i, i * 5.0, i * 5.0 + 4.0,
                f"and this is {phrase}" if i == 10
                else f"filler sentence number {i} " + "padding words " * 6,
            )
            for i in range(20)
        ]
        add_chunk(
            conn, vid, 0, cues[0], cues[-1], "the whole talk", 0.0, 99.0,
            vector_text=phrase,
        )

    parts = await tool_corpus(make)
    rows = rows_of(
        await search.run(
            parts.deps, q=phrase, content_type="transcript", limit=3, max_text_chars=200
        )
    )
    assert rows, rows
    assert "chars truncated" in rows[0]["text"], "the cluster is longer than the budget"
    assert phrase in rows[0]["text"]
    # ...and the citation is the matched cue inside that two-minute island.
    assert rows[0]["start"] == 0.0
    assert rows[0]["match_start"] == 50.0


# ===========================================================================
# The terra eval, 2026-08-10 (research/mcp-eval-terra-2026-08-10.md §4.1, §4.2).
#
# Six Codex/gpt-5.6-terra consumers on the live server. Two search-side
# failures, and both are about the same thing: a nearest-neighbour leg answers
# every query with its k nearest, and nothing in the payload — or in the
# ranking — told the difference between "this matched" and "this was returned".
# ===========================================================================


def _one_near_many_far(conn: sqlite3.Connection) -> None:
    """One chunk near the query vector, twenty scattered elsewhere.

    Every video also carries the query's word, so `has_lexical_footing` opens
    the semantic legs — the §4.1 case is precisely the one where the corpus
    *does* say the word somewhere and the KNN then hands back everything.
    """
    for i in range(21):
        vid = add_video(conn, f"vecfloor{i:03d}")
        cue = add_cue(conn, vid, 0, 0.0, 5.0, f"turbopuffer appears here in talk {i}")
        add_chunk(
            conn, vid, 0, cue, cue, f"talk {i} body",
            0.0, 5.0,
            # The fixture's vectors are `sin()` stand-ins: identical text is the
            # only thing that is genuinely NEAR, everything else sits out at the
            # ~1.0 background distance of two random directions — which is the
            # geometry this finding is about.
            vector_text=("turbopuffer" if i == 0 else f"unrelated topic {i}"),
        )


def test_the_vector_leg_keeps_only_the_band_around_its_own_best_hit(
    corpus: Corpus,
) -> None:
    """§4.1 HIGH. `vec_max_distance` admitted every non-anti-correlated chunk,
    so a KNN with k=800 returned a fifth of the corpus and unrelated talks took
    rank 1. The cut that binds is relative to THIS query's nearest hit, which is
    the only form that survives a change of embedder (VEC_MAX_MARGIN)."""
    corpus.write(_one_near_many_far)
    conn = corpus.read
    qvec = queries.pack_f32(vector_for("turbopuffer", TEXT_DIM))

    def run(margin: float):
        return queries.search_transcript(
            conn,
            queries.SearchParams(
                q="turbopuffer",
                video_ids=pool(conn),
                qvec=qvec,
                limit=100,
                cluster_gap=0.0,
                vec_max_distance=2.0,
                vec_max_margin=margin,
            ),
        )

    wide, banded = run(2.0), run(0.05)
    assert int(wide[0]["n_vec"]) == 21, "without a band, the KNN's whole k fuses"
    assert int(banded[0]["n_vec"]) == 1, "with one, only the chunk that is actually near"
    # The band is a candidate cut, not a re-ranking: it must not remove the
    # lexical leg's own rows.
    assert len(banded) == 21, "the FTS leg still answers for every talk"
    assert int(banded[0]["n_vec_knn"]) == 21, "and the payload can say what was dropped"


def test_the_frame_band_is_relative_too(corpus: Corpus) -> None:
    """Same rule on the frame leg (FRAME_MAX_MARGIN), same reason."""

    def build(conn: sqlite3.Connection) -> None:
        vid = add_video(conn, "framefloor0")
        near = add_ocr(conn, vid, 0, 1.0, "the wanted slide")
        add_frame_vector(conn, near, vid, 1.0, "the wanted slide")
        for i in range(1, 8):
            far = add_ocr(conn, vid, i, 10.0 * i, f"another slide {i}")
            add_frame_vector(conn, far, vid, 10.0 * i, f"nothing alike {i}")

    corpus.write(build)
    conn = corpus.read
    qimg = queries.pack_f32(vector_for("the wanted slide", FRAME_DIM))
    params = queries.SearchParams(q="slide", video_ids=pool(conn), limit=50, max_per_video=20)

    wide = queries.search_frames(conn, params, qimg, 20, max_distance=2.0, max_margin=2.0)
    banded = queries.search_frames(conn, params, qimg, 20, max_distance=2.0, max_margin=0.05)
    assert len(wide) == 8
    assert len(banded) == 1, "only the frame that is near the query survives"
    assert int(banded[0]["n_knn"]) == 8, "and the k it was chosen from is carried out"


def test_the_configured_margins_are_the_defaults_and_are_clamped(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Server-side, never prompt-only, and documented in `deploy/.env.example`.

    A margin is a search guarantee: an operator typo that would switch it off
    is clamped into the documented range rather than obeyed."""
    from vidtheque_mcp.config import Settings as _S

    shipped = _S(data_dir=Path("/data"), public_url="http://x", worker_url="http://y")
    assert shipped.vec_max_margin == queries.VEC_MAX_MARGIN == 0.20
    assert shipped.frame_max_margin == queries.FRAME_MAX_MARGIN == 0.15

    env = (Path(__file__).resolve().parents[2] / "deploy" / ".env.example").read_text()
    assert "VIDTHEQUE_VEC_MAX_MARGIN" in env
    assert "VIDTHEQUE_FRAME_MAX_MARGIN" in env

    monkeypatch.setenv("VIDTHEQUE_VEC_MAX_MARGIN", "20")
    monkeypatch.setenv("VIDTHEQUE_FRAME_MAX_MARGIN", "-1")
    monkeypatch.setenv("VIDTHEQUE_SECRET", "test-secret-not-for-production")
    monkeypatch.setenv("PUBLIC_URL", "http://localhost:8080")
    tuned = _S.from_env()
    assert tuned.vec_max_margin == 2.0, "clamped, not obeyed"
    assert tuned.frame_max_margin == 0.0


async def test_the_legs_line_splits_the_lexical_and_semantic_sub_legs(
    tool_corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§4.2 HIGH. `vidtheque://guide` teaches "`transcript 0` next to on-screen
    hits usually means the phrasing differs" — and the fused count can never
    read 0 while a KNN leg is returning its k. The split restores the rule.

    End to end with §4.1: the absolute ceiling is opened right up, so the only
    thing standing between the caller and "21 talks match turbopuffer" is the
    relevance band — and the line says how many of the k it kept."""
    parts = await tool_corpus(_one_near_many_far)
    # `Settings` is frozen; `Deps` is not, and the fixture's absolute ceiling
    # (0.72, the SigLIP-era number) would otherwise do the cutting for us.
    monkeypatch.setattr(
        parts.deps, "settings", replace(parts.deps.settings, vec_max_distance=2.0)
    )
    result = await search.run(
        parts.deps, q="turbopuffer", content_type="transcript", limit=5
    )
    body = text_of(result)
    assert "Legs: transcript " in body
    legs = [line for line in body.splitlines() if line.startswith("Legs:")][0]
    assert "(fts 21 cues · vec 1/21 chunks)" in legs, legs

    counts = result.structured_content["leg_counts"]
    assert counts["transcript_fts"] == 21
    assert counts["transcript_vec"] == 1, "one chunk is actually near the query"
    assert counts["transcript_vec_knn"] == 21, "…of the 21 the KNN handed back"


async def test_a_phrasing_miss_reads_as_fts_zero(tool_corpus) -> None:
    """The diagnostic itself: the words are not in the transcript, the topic is
    — and the caller can now tell those apart in one line."""

    def make(conn: sqlite3.Connection) -> None:
        vid = add_video(conn, "phrasingdif")
        cue = add_cue(conn, vid, 0, 0.0, 5.0, "we keep the keys and the values around")
        add_chunk(
            conn, vid, 0, cue, cue, "we keep the keys and the values around", 0.0, 5.0,
            # The topic, reachable by the semantic leg only. The fixture's
            # vectors are `sin()` stand-ins, so "near" means the same string.
            vector_text="keep retention",
        )

    parts = await tool_corpus(make)
    body = text_of(
        # `keep` has lexical footing, so the semantic legs run; `retention` is
        # not in the corpus, so the AND-ed FTS leg matches nothing.
        await search.run(
            parts.deps, q="keep retention", content_type="transcript", limit=5
        )
    )
    legs = [line for line in body.splitlines() if line.startswith("Legs:")][0]
    assert "(fts 0 cues · vec 1/1 chunks)" in legs, legs
    assert "transcript 1 segment " in legs, "…and the fused count still shows the hit"


async def test_a_phrase_that_lives_only_in_a_title_is_named(tool_corpus) -> None:
    """§9.8. The one place `search` cannot find a phrase is the title bar.

    Titles are not in `cues_fts`, so `fts 0` is truthful and the semantic leg
    ranks alone — live, a talk *named* "…without the on-call tax" came back at
    ranks 2-4 under an unrelated talk. The ranking is a calibrated change and
    is deferred; the silence is not, and the note carries the receipt the leg
    cannot rank.
    """

    def make(conn: sqlite3.Connection) -> None:
        named = add_video(
            conn, "titleonly01", title="Always-on agents without the on-call tax"
        )
        cue = add_cue(conn, named, 0, 0.0, 5.0, "we page the humans when it burns")
        add_chunk(conn, named, 0, cue, cue, "we page the humans when it burns", 0.0, 5.0)
        # The word lives in another video's DESCRIPTION-shaped text, never its
        # title: the column filter must not report that as a title match.
        other = add_video(conn, "othertalk01", title="Separating the task from the model")
        cue2 = add_cue(conn, other, 0, 0.0, 5.0, "self extract and self recheck")
        add_chunk(conn, other, 0, cue2, cue2, "self extract and self recheck", 0.0, 5.0)

    parts = await tool_corpus(make)
    result = await search.run(
        parts.deps, q="on-call tax", content_type="transcript", limit=5
    )
    body = text_of(result)
    note = next(n for n in result.structured_content["notes"] if "video title" in n)
    assert note in body, "the note is printed, not only structured"
    assert "(fts 0)" in note, note
    assert "1 video title does" in note, note
    assert "Always-on agents without the on-call tax" in note
    assert "titleonly01" in note
    assert 'video_title="…"' in note
    assert "othertalk01" not in note, "a non-title match must not be claimed as one"

    # A query with lexical footing in speech gets no such note — the diagnostic
    # only fires where there is nothing else to read.
    spoken = await search.run(
        parts.deps, q="humans burns", content_type="transcript", limit=5
    )
    assert not any("video title" in n for n in spoken.structured_content["notes"])


async def test_the_legs_line_names_its_units_and_never_hides_the_band(
    tool_corpus, monkeypatch: pytest.MonkeyPatch
) -> None:
    """§9.2 and §9.6, which are the same line read two ways.

    §9.2: three numbers in three units, unlabelled, and the guide's example
    added up by coincidence — so `transcript 130 (fts 369 · vec 123/800)` read
    as an arithmetic bug. §9.6: `a/b` was suppressed when the band kept
    everything, so `vec 800` (nothing was narrowed) printed more tidily than
    `vec 11/800` (the band bit hard) — the case that most needs attention
    looked like the cleanest.
    """
    parts = await tool_corpus(_one_near_many_far)
    monkeypatch.setattr(
        parts.deps, "settings", replace(parts.deps.settings, vec_max_distance=2.0)
    )
    result = await search.run(
        parts.deps, q="turbopuffer", content_type="transcript", limit=5
    )
    legs = [line for line in text_of(result).splitlines() if line.startswith("Legs:")][0]
    for unit in ("segments", "cues", "chunks"):
        assert unit in legs, legs
    counts = result.structured_content["leg_counts"]
    assert counts["transcript_vec"] != counts["transcript"], "the units really differ"

    # Band wide open: every one of the k nearest survives, and the line says so
    # in the same `kept/considered` shape rather than dropping the denominator.
    monkeypatch.setattr(
        parts.deps,
        "settings",
        replace(parts.deps.settings, vec_max_distance=2.0, vec_max_margin=2.0),
    )
    unbound = await search.run(
        parts.deps, q="turbopuffer", content_type="transcript", limit=5
    )
    line = [l for l in text_of(unbound).splitlines() if l.startswith("Legs:")][0]
    kept = unbound.structured_content["leg_counts"]
    assert kept["transcript_vec"] == kept["transcript_vec_knn"], "the band kept it all"
    assert f"vec {kept['transcript_vec']}/{kept['transcript_vec_knn']} chunks" in line, line


async def test_a_clamp_that_binds_says_so(tool_corpus) -> None:
    """§4.12 LOW. The caps are published ahead of the call, but the payload said
    nothing when one moved a number the caller sent: `limit=500` came back as
    `Results: 50/…` with no mention of the clamp, and a stress-testing consumer
    filed it three times. §5.2 deferred this half on 2026-08-09; two vendors'
    agents have now filed it independently, so it ships."""
    parts = await tool_corpus(_six_videos)
    loud = text_of(
        await search.run(parts.deps, q="good eval", content_type="transcript", limit=500)
    )
    assert "note: clamped server-side: limit=500 → 50" in loud
    assert "page with offset instead of raising limit" in loud

    quiet = text_of(
        await search.run(parts.deps, q="good eval", content_type="transcript", limit=5)
    )
    assert "clamped server-side" not in quiet, "a clamp that did not bind is not news"


def test_the_band_cannot_do_the_ceilings_job(corpus: Corpus) -> None:
    """Why both cuts exist, pinned — 2026-08-10 recalibration.

    A junk query's k nearest are FLAT: measured on the repaired embedding
    space, best 0.579 and 800th 0.771, a spread narrower than the band itself,
    so a margin around its own best hit keeps every one of them. Only the
    absolute ceiling can tell a query the corpus cannot answer from one it can.
    The fixture's `sin()` vectors reproduce exactly that shape — mutually
    unrelated texts all land at the ~1.0 background distance."""
    corpus.write(_one_near_many_far)
    conn = corpus.read
    # A query near nothing in this corpus: 21 chunks, all at the background
    # distance, none of them an answer.
    qvec = queries.pack_f32(vector_for("mutually unrelated to every chunk", TEXT_DIM))

    def run(ceiling: float, margin: float) -> int:
        rows = queries.search_transcript(
            conn,
            queries.SearchParams(
                q="turbopuffer",
                video_ids=pool(conn),
                qvec=qvec,
                limit=100,
                cluster_gap=0.0,
                vec_max_distance=ceiling,
                vec_max_margin=margin,
            ),
        )
        return int(rows[0]["n_vec"]) if rows else 0

    assert run(2.0, 0.20) == 21, "the band alone keeps the whole flat k"
    assert run(0.55, 0.20) == 0, "the ceiling is what makes the empty state reachable"
