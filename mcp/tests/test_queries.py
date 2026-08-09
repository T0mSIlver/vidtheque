"""Seeded-fixture tests for the search legs and the bounded-work rules."""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from vidtheque_mcp.config import Settings
from vidtheque_mcp.db import queries
from vidtheque_mcp.db.connection import open_read_connection, open_write_connection

from .conftest import TEXT_DIM, seed, vector_for


@pytest.fixture
def conn(settings: Settings) -> sqlite3.Connection:
    seed(settings.db_path, settings.keyframes_dir)
    connection = open_read_connection(settings.db_path)
    yield connection
    connection.close()


@pytest.fixture
def novec(tmp_path: Path) -> sqlite3.Connection:
    """A corpus indexed without any vectors — the FTS-only case."""
    data = tmp_path / "novec"
    (data / "keyframes").mkdir(parents=True)
    seed(data / "vidtheque.db", data / "keyframes", with_vectors=False)
    connection = open_read_connection(data / "vidtheque.db")
    yield connection
    connection.close()


def ids(conn: sqlite3.Connection) -> list[int]:
    return queries.resolve_videos(conn, queries.CorpusFilter())


# ------------------------------------------------------------ filter resolution


def test_channel_filter_is_case_insensitive_substring(conn: sqlite3.Connection) -> None:
    found = queries.resolve_videos(conn, queries.CorpusFilter(channel="KARPathy"))
    assert len(found) == 1


def test_published_filters_bound_the_pool(conn: sqlite3.Connection) -> None:
    after = queries.resolve_videos(conn, queries.CorpusFilter(published_after=1_700_000_000))
    assert len(after) == 2


def test_tag_filter_uses_and_semantics(conn: sqlite3.Connection) -> None:
    both = queries.resolve_videos(
        conn, queries.CorpusFilter(tags=["topic:attention", "series:gpu-mode"])
    )
    assert len(both) == 1
    one = queries.resolve_videos(conn, queries.CorpusFilter(tags=["topic:attention"]))
    assert len(one) == 3


# ---------------------------------------------------------------- search legs


def test_fts_only_when_vectors_are_absent(novec: sqlite3.Connection) -> None:
    pool = ids(novec)
    params = queries.SearchParams(q="cache", video_ids=pool, qvec=None, limit=10)
    assert params.legs == {"do_fts": True, "do_vec": False, "do_browse": False}
    rows = queries.search_transcript(novec, params)
    assert rows, "the lexical leg must still answer with no vectors at all"


def test_hybrid_adds_the_vector_leg(conn: sqlite3.Connection) -> None:
    pool = ids(conn)
    qvec = queries.pack_f32(vector_for("we cache the keys and the values", TEXT_DIM))
    lexical = queries.search_transcript(
        conn, queries.SearchParams(q="tokenization", video_ids=pool, limit=10)
    )
    hybrid = queries.search_transcript(
        conn, queries.SearchParams(q="tokenization", video_ids=pool, qvec=qvec, limit=10)
    )
    # The semantic leg finds what FTS missed; it is not merely a reranker.
    assert len(hybrid) >= len(lexical)


def test_porter_stemming_matches_inflections(conn: sqlite3.Connection) -> None:
    pool = ids(conn)
    rows = queries.search_transcript(conn, queries.SearchParams(q="caching", video_ids=pool, limit=10))
    texts = " ".join(str(r["text"]) for r in rows)
    assert "cache" in texts


def test_fts_terms_are_quote_wrapped() -> None:
    """Bare `nvidia-smi` and `torch.compile` are FTS5 parse errors."""
    assert queries.sanitize_fts("nvidia-smi") == '"nvidia-smi"'
    assert queries.sanitize_fts("torch.compile") == '"torch.compile"'
    # Documented operators survive.
    assert queries.sanitize_fts("cache AND paged") == '"cache" AND "paged"'
    assert queries.sanitize_fts("attn*") == '"attn"*'
    assert queries.sanitize_fts('"exact phrase"') == '"exact phrase"'


def test_hostile_queries_do_not_raise(conn: sqlite3.Connection) -> None:
    pool = ids(conn)
    for hostile in ["nvidia-smi", "torch.compile", 'a"b', "((", "NOT", "-", "a NEAR b", "*"]:
        params = queries.SearchParams(q=hostile, video_ids=pool, limit=5)
        queries.search_transcript(conn, params)


def test_ocr_leg_matches_screen_identifiers(conn: sqlite3.Connection) -> None:
    pool = ids(conn)
    rows = queries.search_ocr(conn, queries.SearchParams(q="nvidia-smi", video_ids=pool, limit=5))
    assert [r["text"] for r in rows] == ["nvidia-smi 18304MiB"]


def test_the_ocr_leg_keeps_a_line_the_presenter_also_narrated(
    conn: sqlite3.Connection,
) -> None:
    """The leg does no dedup: that rule is one rule, and it is caller-side.

    SQL used to drop any OCR line a longer transcript cue matching the same
    query overlapped within +/-5s, with no similarity test — so on a screencast
    (the corpus this is for) the OCR channel went silent on every narrated
    word, and said nothing about it (smoke §4.4).
    """
    pool = ids(conn)
    rows = queries.search_ocr(
        conn, queries.SearchParams(q="fragmentation", video_ids=pool, limit=5)
    )
    # 12.0s on screen, under a longer cue at 13.5s that says something else.
    assert [r["text"] for r in rows] == ["paged kv cache | block table | 4% fragmentation"]


def test_frame_leg_returns_assembled_frame_ids(conn: sqlite3.Connection) -> None:
    pool = ids(conn)
    qimg = queries.pack_f32(vector_for("kv cache size = 2 * n_layers * n_heads", 1152))
    rows = queries.search_frames(
        conn, queries.SearchParams(q="kv cache", video_ids=pool, limit=5), qimg, 20
    )
    assert rows[0]["frame_id"] == "kCc8FmEb1nY-00000"
    assert rows[0]["video_id"] == "kCc8FmEb1nY"


# ----------------------------------------------------------------- clustering


def test_clustering_bounds_a_run_on_both_axes(conn: sqlite3.Connection) -> None:
    """Gap-only clustering collapsed a whole video into one result on the
    fixture; the 120 s grid is the hard ceiling."""
    pool = ids(conn)
    rows = queries.search_transcript(
        conn,
        queries.SearchParams(q="cache OR attention OR memory", video_ids=pool, limit=20, cluster_gap=8.0),
    )
    karpathy = [r for r in rows if r["start_s"] < 500]
    assert karpathy, "expected hits in the fixture's first video"
    for row in karpathy:
        assert float(row["end_s"]) - float(row["start_s"]) <= queries.CLUSTER_MAX_SECONDS


def test_contiguous_cues_collapse_into_one_segment(conn: sqlite3.Connection) -> None:
    pool = ids(conn)
    rows = queries.search_transcript(
        conn, queries.SearchParams(q="cache", video_ids=pool, limit=20, cluster_gap=8.0)
    )
    first = next(r for r in rows if r["video_id"] == 1)
    assert int(first["n_cues"]) >= 1
    assert json.loads(first["cue_ids"]) == sorted(json.loads(first["cue_ids"]))


def test_cluster_gap_zero_returns_raw_cues(conn: sqlite3.Connection) -> None:
    pool = ids(conn)
    clustered = queries.search_transcript(
        conn, queries.SearchParams(q="cache", video_ids=pool, limit=20, cluster_gap=8.0)
    )
    raw = queries.search_transcript(
        conn, queries.SearchParams(q="cache", video_ids=pool, limit=20, cluster_gap=0.0)
    )
    assert all(int(r["n_cues"]) == 1 for r in raw)
    assert len(raw) >= len(clustered)


# ------------------------------------------------------ diversity and paging


def test_per_video_diversity_cap_applies_before_the_page(conn: sqlite3.Connection) -> None:
    pool = ids(conn)
    rows = queries.search_transcript(
        conn,
        queries.SearchParams(
            q="cache OR attention OR memory OR tokenization",
            video_ids=pool,
            limit=50,
            cluster_gap=0.0,
            max_per_video=1,
        ),
    )
    per_video: dict[int, int] = {}
    for row in rows:
        per_video[int(row["video_id"])] = per_video.get(int(row["video_id"]), 0) + 1
    assert max(per_video.values()) == 1


def test_per_video_diversity_cap_applies_to_the_ocr_leg_too(
    conn: sqlite3.Connection,
) -> None:
    """The diversity and probe tests only ever exercised the transcript leg, so
    the other two legs' caps were unasserted (review's coverage note)."""
    pool = ids(conn)
    rows = queries.search_ocr(
        conn,
        queries.SearchParams(q="cache OR nvidia OR block", video_ids=pool, limit=50, max_per_video=1),
    )
    per_video: dict[int, int] = {}
    for row in rows:
        per_video[int(row["video_id"])] = per_video.get(int(row["video_id"]), 0) + 1
    assert per_video
    assert max(per_video.values()) == 1


def test_per_video_diversity_cap_applies_to_the_frame_leg_too(
    conn: sqlite3.Connection,
) -> None:
    pool = ids(conn)
    qimg = queries.pack_f32(vector_for("kv cache size = 2 * n_layers * n_heads", 1152))
    rows = queries.search_frames(
        conn,
        queries.SearchParams(q="kv cache", video_ids=pool, limit=50, max_per_video=1),
        qimg,
        50,
        max_distance=2.0,
    )
    per_video: dict[str, int] = {}
    for row in rows:
        per_video[str(row["video_id"])] = per_video.get(str(row["video_id"]), 0) + 1
    assert per_video
    assert max(per_video.values()) == 1


def test_ocr_probe_and_page_share_the_filter(conn: sqlite3.Connection) -> None:
    """The transcript leg had this assertion; the OCR leg did not."""
    pool = ids(conn)
    params = queries.SearchParams(q="cache OR nvidia OR block", video_ids=pool, limit=50)
    rows = queries.search_ocr(conn, params)
    total, hit_ceiling = queries.probe_ocr(conn, params, headroom=1000)
    assert hit_ceiling is False
    assert total == len(rows)


def test_has_more_comes_from_limit_plus_one(conn: sqlite3.Connection) -> None:
    pool = ids(conn)
    params = queries.SearchParams(
        q="cache OR attention OR memory OR tokenization OR percent OR mechanism",
        video_ids=pool,
        limit=1,
        cluster_gap=0.0,
        max_per_video=20,
    )
    rows = queries.search_transcript(conn, params)
    assert len(rows) == params.limit + 1  # the +1 IS has_more
    assert len(rows[: params.limit]) == 1


def test_count_probe_is_bounded_by_the_ceiling(conn: sqlite3.Connection) -> None:
    pool = ids(conn)
    params = queries.SearchParams(
        q="cache OR attention OR memory OR tokenization OR percent OR mechanism",
        video_ids=pool,
        limit=1,
        offset=0,
        cluster_gap=0.0,
        max_per_video=20,
    )
    total, hit_ceiling = queries.probe_transcript(conn, params, headroom=1)
    assert total <= params.offset + params.limit + 1
    assert hit_ceiling is (total == params.offset + params.limit + 1)

    generous, hit = queries.probe_transcript(conn, params, headroom=1000)
    assert hit is False
    assert generous >= total


def test_probe_and_page_share_the_filter(conn: sqlite3.Connection) -> None:
    """The probe cannot disagree with the page because it is the same CTE."""
    pool = ids(conn)
    params = queries.SearchParams(q="cache", video_ids=pool, limit=50, cluster_gap=0.0)
    rows = queries.search_transcript(conn, params)
    total, hit = queries.probe_transcript(conn, params, headroom=1000)
    assert hit is False
    assert total == len(rows)


def test_empty_video_pool_short_circuits(conn: sqlite3.Connection) -> None:
    rows = queries.search_transcript(
        conn, queries.SearchParams(q="cache", video_ids=[], limit=10)
    )
    assert rows == []


# ------------------------------------------------------------------ rollups


def test_corpus_rollup_counts(conn: sqlite3.Connection) -> None:
    row = queries.corpus_rollup(conn)
    assert int(row["videos_ready"]) == 3
    assert int(row["cues"]) == 10


def test_coverage_reflects_stage_state(conn: sqlite3.Connection) -> None:
    pool = ids(conn)
    rows = queries.list_videos(conn, pool, None, "any", "recency", 10, 0, 5000)
    coverage = {
        str(r["public_id"]): (r["has_transcript"], r["has_ocr"], r["has_frames"]) for r in rows
    }
    assert coverage["kCc8FmEb1nY"] == (1, 1, 1)
    assert coverage["eMlx5fFNoYc"] == (1, 0, 0)


def test_has_filter_finds_the_half_indexed(conn: sqlite3.Connection) -> None:
    pool = ids(conn)
    rows = queries.list_videos(conn, pool, None, "all", "recency", 10, 0, 5000)
    assert [str(r["public_id"]) for r in rows] == ["kCc8FmEb1nY"]


def test_video_ordering_options(conn: sqlite3.Connection) -> None:
    pool = ids(conn)
    by_recency = [
        str(r["public_id"]) for r in queries.list_videos(conn, pool, None, "any", "recency", 10, 0, 5000)
    ]
    assert by_recency[0] == "eMlx5fFNoYc"
    by_duration = [
        str(r["public_id"]) for r in queries.list_videos(conn, pool, None, "any", "duration", 10, 0, 5000)
    ]
    assert by_duration[0] == "kCc8FmEb1nY"


def test_key_texts_and_ocr_highlights_are_capped(conn: sqlite3.Connection) -> None:
    assert len(queries.key_texts(conn, 1, 3, None, None)) <= 3
    assert len(queries.ocr_highlights(conn, 1, 1, None, None)) == 1


def test_segment_context_queries(conn: sqlite3.Connection) -> None:
    cues = queries.context_transcript(conn, 1, 6.0, 45)
    assert len(cues) == 5  # the 420 s cue is outside the window
    assert queries.context_chapter(conn, 1, 6.0)["title"] == "intro"
    assert len(queries.context_ocr(conn, 1, 6.0, 45)) == 1


# ------------------------------------------------------ the dashboard's reads


def test_index_state_defaults_to_the_queryable_ones(settings: Settings) -> None:
    """A `pending` video has no data to answer with, so queries do not see it.

    The dashboard is the surface that must see it (dashboard.md §5.2), which is
    why that clause is now a filter with a default rather than a constant in
    the SQL.
    """
    seed(settings.db_path, settings.keyframes_dir)
    write = open_write_connection(settings.db_path)
    try:
        write.execute(
            "INSERT INTO videos (owner_id, source_id, url, title, index_state) "
            "VALUES (1, 'pend1ngv1d', 'https://youtu.be/pend1ngv1d', 'not yet', 'pending')"
        )
        write.commit()
    finally:
        write.close()
    conn = open_read_connection(settings.db_path)
    try:
        default = queries.resolve_videos(conn, queries.CorpusFilter())
        every = queries.resolve_videos(
            conn, queries.CorpusFilter(index_states=queries.INDEX_STATES)
        )
        only = queries.resolve_videos(conn, queries.CorpusFilter(index_states=("pending",)))
        assert len(every) == len(default) + 1
        assert len(only) == 1
    finally:
        conn.close()


def test_video_stages_come_back_in_pipeline_order(conn: sqlite3.Connection) -> None:
    """Not alphabetical: the PK is `(video_id, stage)` and `chunk` sorts first."""
    stages = [str(r["stage"]) for r in queries.video_stages(conn, 1)]
    assert stages == [s for s in queries.STAGE_ORDER if s in set(stages)]
    assert stages[0] == "stt" and stages[-1] == "frame_embed"
    assert queries.video_stages(conn, 3)  # the video with no OCR still has rows


def test_the_shot_timeline_is_one_grouped_query(conn: sqlite3.Connection) -> None:
    shots = queries.shot_timeline(conn, 1)
    assert [int(s["shot_id"]) for s in shots] == [0, 1]
    first = shots[0]
    assert float(first["start_s"]) <= float(first["end_s"])
    assert int(first["frames"]) == 1 and int(first["kept"]) == 1
    assert int(first["ocr_done"]) == 1
    assert len(queries.shot_timeline(conn, 1, limit=1)) == 1


def test_keyframe_and_cue_pages_probe_one_row_past_the_limit(
    conn: sqlite3.Connection,
) -> None:
    """`has_more` over a total, everywhere — the caller reads `len() > limit`."""
    assert len(queries.keyframe_page(conn, 1, 0, 1)) == 2
    assert len(queries.cue_page(conn, 1, 0, 2)) == 3
    assert len(queries.cue_page(conn, 1, 0, 50)) == 6  # nothing left to probe
    page = queries.cue_page(conn, 1, 0, 2)
    assert [float(c["start_s"]) for c in page[:2]] == [0.0, 3.0]
    assert str(page[0]["origin"]) == "whisperx"
    assert int(page[0]["has_words"]) == 0


def test_chunk_spans_are_scoped_to_the_cue_page(conn: sqlite3.Connection) -> None:
    page = queries.cue_page(conn, 1, 0, 50)
    spans = queries.chunk_spans(conn, 1, int(page[0]["id"]), int(page[-1]["id"]))
    assert len(spans) == 1
    assert int(spans[0]["first_cue_id"]) == int(page[0]["id"])
    # A cue-id range before the first chunk's cues overlaps nothing.
    assert queries.chunk_spans(conn, 1, -2, -1) == []


def test_ocr_boxes_come_back_grouped_and_normalized(conn: sqlite3.Connection) -> None:
    frames = queries.keyframe_page(conn, 2, 0, 10)
    ids_ = [int(f["id"]) for f in frames]
    boxes = queries.ocr_for_frames(conn, ids_)
    assert len(boxes) == 1
    lines = next(iter(boxes.values()))
    assert [str(line["text"]) for line in lines] == [
        "paged kv cache",
        "block table",
        "4% fragmentation",
    ]
    for line in lines:
        assert 0.0 <= float(line["x0"]) <= 1.0 and 0.0 <= float(line["y1"]) <= 1.0
    assert queries.ocr_for_frames(conn, []) == {}
    capped = queries.ocr_for_frames(conn, ids_, limit=2)
    assert sum(len(v) for v in capped.values()) == 2


def test_per_video_counts_are_one_row_of_counts(conn: sqlite3.Connection) -> None:
    row = queries.per_video_counts(conn, 2)
    assert int(row["cues"]) == 3
    assert int(row["chunks"]) == 1
    assert int(row["keyframes"]) == 1 == int(row["keyframes_kept"])
    assert int(row["ocr_frames"]) == 1 and int(row["ocr_lines"]) == 3
    assert int(row["cues_with_words"]) == 0
    assert int(row["jpeg_bytes"]) > 0
    assert int(queries.per_video_counts(conn, 3)["keyframes"]) == 0


def test_keyframe_bytes_come_from_the_column_not_the_filesystem(
    conn: sqlite3.Connection,
) -> None:
    total = queries.keyframe_bytes_total(conn)
    per_video = sum(int(queries.per_video_counts(conn, v)["jpeg_bytes"]) for v in (1, 2, 3))
    assert total == per_video > 0


def test_keyframe_detail_is_addressed_by_wire_id(conn: sqlite3.Connection) -> None:
    frame = queries.keyframe_detail(conn, "zduSFxRajkE", 0)
    assert frame is not None and int(frame["ord"]) == 0
    assert str(frame["public_id"]) == "zduSFxRajkE"
    assert queries.keyframe_detail(conn, "zduSFxRajkE", 999) is None


def test_cue_origins_show_where_a_transcript_came_from(conn: sqlite3.Connection) -> None:
    assert queries.cue_origins(conn, 1) == {"whisperx": 6}
    assert queries.cue_origins(conn, 99) == {}
