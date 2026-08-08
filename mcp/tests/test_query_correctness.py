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
    conn: sqlite3.Connection, video_id: int, ordinal: int, t_s: float, text: str
) -> int:
    cur = conn.execute(
        "INSERT INTO keyframes (video_id, ord, t_s, shot_id, shot_start_s, shot_end_s, phash, "
        "sharpness, width, height, jpeg_path, jpeg_bytes, ocr_state) "
        "VALUES (?, ?, ?, 1, ?, ?, ?, 1.0, 16, 16, ?, 1, 'done')",
        (video_id, ordinal, t_s, t_s, t_s + 1, 1000 + ordinal, f"k/{video_id}-{ordinal}.jpg"),
    )
    kf = int(cur.lastrowid or 0)
    conn.execute(
        "INSERT INTO ocr_lines (keyframe_id, video_id, t_s, line_no, text, conf, x0, y0, x1, y1) "
        "VALUES (?, ?, ?, 0, ?, 0.9, 0, 0, 1, 1)",
        (kf, video_id, t_s, text),
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


def test_the_configured_floors_are_the_defaults(settings: Settings) -> None:
    """The knobs are wired, and `deploy/.env.example` documents both."""
    assert settings.vec_max_distance == queries.VEC_MAX_DISTANCE
    assert settings.frame_max_distance == queries.FRAME_MAX_DISTANCE
    env = (Path(__file__).resolve().parents[2] / "deploy" / ".env.example").read_text()
    assert "VIDTHEQUE_VEC_MAX_DISTANCE" in env
    assert "VIDTHEQUE_FRAME_MAX_DISTANCE" in env
