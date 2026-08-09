"""The embedder swap: migration 0004, staleness, honesty, and the query path.

Tom's decision of 2026-08-09 replaced SigLIP 2 + Qwen3-Embedding-0.6B with one
`Qwen/Qwen3-VL-Embedding-2B` at 2048 dims. That invalidates every vector in the
file, and the interesting part is not the swap — it is the window between the
migration and the re-embed finishing, when the corpus has transcripts and
keyframes it cannot answer semantically. A KNN over a half-filled index does
not fail; it quietly returns less. So the window is *reported*.

CPU-only, worker mocked, no model and no GPU.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from vidtheque_mcp.config import Settings
from vidtheque_mcp.db import migrations
from vidtheque_mcp.db.database import Database
from vidtheque_mcp.db.queries import embed_backlog, pack_f32
from vidtheque_mcp.tools import library, search

from .conftest import FRAME_DIM, TEXT_DIM, UNIFIED_MODEL, seed
from .pipeline_fakes import VIDEO_URL, FakeWorker
from .test_pipeline_e2e import Harness, body, harness

UNIFIED_DIM = 2048


@pytest.fixture
def fresh(tmp_path: Path) -> sqlite3.Connection:
    from vidtheque_mcp.db.connection import open_write_connection

    conn = open_write_connection(tmp_path / "v.db")
    try:
        yield conn
    finally:
        conn.close()


def _stage_up_to(conn: sqlite3.Connection, version: int, staging: Path) -> None:
    staging.mkdir(exist_ok=True)
    for migration in migrations.discover():
        if migration.version <= version:
            (staging / f"{migration.version:04d}_{migration.name}.sql").write_text(
                migration.sql, encoding="utf-8"
            )
    migrations.migrate(conn, staging)


def _declared_dim(conn: sqlite3.Connection, table: str) -> int:
    import re

    sql = conn.execute(
        "SELECT sql FROM sqlite_master WHERE name = ?", (table,)
    ).fetchone()[0]
    return int(re.search(r"FLOAT\[(\d+)\]", sql).group(1))


# --------------------------------------------------------------------------
# 0004: the schema and config side
# --------------------------------------------------------------------------


def test_both_vec_tables_are_rebuilt_at_2048(fresh: sqlite3.Connection) -> None:
    """Native dims, not the memo's MRL@1024 — so *both* tables move, not just
    the frame one. Truncation is the fallback lever, held in reserve."""
    migrations.migrate(fresh)
    assert _declared_dim(fresh, "vec_chunks") == UNIFIED_DIM
    assert _declared_dim(fresh, "vec_frames") == UNIFIED_DIM
    config = dict(fresh.execute("SELECT key, value FROM config"))
    assert config["text_embed.dim"] == "2048"
    assert config["frame_embed.dim"] == "2048"


def test_both_legs_name_one_checkpoint(fresh: sqlite3.Connection) -> None:
    migrations.migrate(fresh)
    config = dict(fresh.execute("SELECT key, value FROM config"))
    assert config["text_embed.model"] == UNIFIED_MODEL
    assert config["frame_embed.model"] == UNIFIED_MODEL


def test_the_instruction_record_is_two_different_strings(
    fresh: sqlite3.Connection,
) -> None:
    """One model, one space, two retrieval tasks. The record was a lie before
    (`query: ` against a model applying `Instruct: …`) and nothing read it
    loudly enough to notice; it is now what the worker echoes on /status."""
    migrations.migrate(fresh)
    config = dict(fresh.execute("SELECT key, value FROM config"))
    assert "transcript passage" in config["text_embed.query_prefix"]
    assert "video frame" in config["frame_embed.query_prefix"]
    assert config["text_embed.query_prefix"] != config["frame_embed.query_prefix"]
    assert config["text_embed.query_prefix"] != "query: "


def test_the_pipeline_version_records_that_the_contents_changed(
    fresh: sqlite3.Connection,
) -> None:
    """`user_version` is the shape of the file; `pipeline.version` is the
    semantics of its contents (§1.10). A swap changes no column and invalidates
    every vector — exactly what the second counter is for."""
    migrations.migrate(fresh)
    config = dict(fresh.execute("SELECT key, value FROM config"))
    assert config["pipeline.version"] == "2"


def test_the_dims_never_move_without_the_model(
    fresh: sqlite3.Connection, tmp_path: Path
) -> None:
    """A corpus deliberately on `bge-m3` (also 1024-d) must not get a 2048 dim
    written under a 1024-d model. The guards are coupled: the dim follows the
    model *having become* the unified one, not its own old value."""
    _stage_up_to(fresh, 3, tmp_path / "staged")
    fresh.execute("UPDATE config SET value = 'BAAI/bge-m3' WHERE key = 'text_embed.model'")
    fresh.commit()

    migrations.migrate(fresh)

    config = dict(fresh.execute("SELECT key, value FROM config"))
    assert config["text_embed.model"] == "BAAI/bge-m3"
    assert config["text_embed.dim"] == "1024", "left alone with its model"
    # The frame leg was on the shipped default, so it moved.
    assert config["frame_embed.model"] == UNIFIED_MODEL
    assert config["frame_embed.dim"] == "2048"


def test_only_the_two_embed_stages_go_stale(
    fresh: sqlite3.Connection, tmp_path: Path
) -> None:
    """`fetch`, `stt`, `chunk`, `keyframe` and `ocr` keep their `done` rows —
    which is what makes the re-embed cost a worker call and not a night of
    bandwidth."""
    _stage_up_to(fresh, 3, tmp_path / "staged")
    fresh.execute("INSERT INTO videos (source_id, url, title) VALUES ('v', 'https://v', 'T')")
    for stage in ("fetch", "stt", "chunk", "text_embed", "keyframe", "ocr", "frame_embed"):
        fresh.execute(
            "INSERT INTO video_stages (video_id, stage, state, model_key) "
            "VALUES (1, ?, 'done', 'whatever')",
            (stage,),
        )
    fresh.commit()

    migrations.migrate(fresh)

    states = dict(fresh.execute("SELECT stage, state FROM video_stages"))
    assert states["text_embed"] == "pending"
    assert states["frame_embed"] == "pending"
    assert all(
        states[s] == "done" for s in ("fetch", "stt", "chunk", "keyframe", "ocr")
    )
    keys = dict(fresh.execute("SELECT stage, model_key FROM video_stages"))
    assert keys["text_embed"] is None and keys["frame_embed"] is None
    assert keys["keyframe"] == "whatever"


def test_a_deliberate_skip_stays_a_skip(
    fresh: sqlite3.Connection, tmp_path: Path
) -> None:
    """`skipped` records a choice — a video indexed `channels=transcript` never
    wanted frame vectors — and `coverage` must not start reporting it as
    missing data."""
    _stage_up_to(fresh, 3, tmp_path / "staged")
    fresh.execute("INSERT INTO videos (source_id, url, title) VALUES ('v', 'https://v', 'T')")
    fresh.execute(
        "INSERT INTO video_stages (video_id, stage, state, model_key) "
        "VALUES (1, 'frame_embed', 'skipped', NULL)"
    )
    fresh.commit()
    migrations.migrate(fresh)
    assert (
        fresh.execute(
            "SELECT state FROM video_stages WHERE stage = 'frame_embed'"
        ).fetchone()[0]
        == "skipped"
    )


def test_the_cascade_triggers_survive_the_table_rebuild(
    fresh: sqlite3.Connection,
) -> None:
    """`chunks_ad`/`keyframes_ad` are ON `chunks`/`keyframes`, so a DROP of the
    vec tables leaves them in place. If it did not, deleting a video would
    silently strand its vectors and search would hand back frame ids that no
    longer resolve — invisible until someone clicked one."""
    migrations.migrate(fresh)
    fresh.execute("INSERT INTO videos (source_id, url, title) VALUES ('v', 'https://v', 'T')")
    vid = fresh.execute("SELECT id FROM videos").fetchone()[0]
    fresh.execute(
        "INSERT INTO cues (video_id, seq, start_s, end_s, text) VALUES (?, 0, 0, 1, 'hi')",
        (vid,),
    )
    cue = fresh.execute("SELECT id FROM cues").fetchone()[0]
    fresh.execute(
        "INSERT INTO chunks (video_id, seq, start_s, end_s, first_cue_id, last_cue_id, "
        "text, n_chars) VALUES (?, 0, 0, 1, ?, ?, 'hi', 2)",
        (vid, cue, cue),
    )
    chunk = fresh.execute("SELECT id FROM chunks").fetchone()[0]
    fresh.execute(
        "INSERT INTO keyframes (video_id, ord, t_s, shot_id, shot_start_s, shot_end_s, "
        "phash, sharpness, width, height, jpeg_path, jpeg_bytes) "
        "VALUES (?, 0, 0, 0, 0, 1, 1, 1, 1, 1, 'p', 1)",
        (vid,),
    )
    kf = fresh.execute("SELECT id FROM keyframes").fetchone()[0]
    fresh.execute(
        "INSERT INTO vec_chunks (chunk_id, video_id, start_s, embedding) VALUES (?, ?, 0.0, ?)",
        (chunk, vid, pack_f32([0.0] * UNIFIED_DIM)),
    )
    fresh.execute(
        "INSERT INTO vec_frames (keyframe_id, video_id, t_s, embedding) VALUES (?, ?, 0.0, ?)",
        (kf, vid, pack_f32([0.0] * UNIFIED_DIM)),
    )

    fresh.execute("DELETE FROM videos WHERE id = ?", (vid,))

    assert fresh.execute("SELECT COUNT(*) FROM vec_chunks").fetchone()[0] == 0
    assert fresh.execute("SELECT COUNT(*) FROM vec_frames").fetchone()[0] == 0


# --------------------------------------------------------------------------
# the honesty of the degraded window
# --------------------------------------------------------------------------


def _backlog(path: Path) -> dict[str, int]:
    from vidtheque_mcp.db.connection import open_read_connection

    conn = open_read_connection(path)
    try:
        return embed_backlog(conn)
    finally:
        conn.close()


def test_a_corpus_mid_reembed_reports_a_backlog(tmp_path: Path) -> None:
    data = tmp_path / "data"
    (data / "keyframes").mkdir(parents=True)
    seed(data / "vidtheque.db", data / "keyframes")
    assert _backlog(data / "vidtheque.db") == {"text": 0, "frame": 0}

    # What migration 0004 does to every already-embedded video.
    conn = sqlite3.connect(data / "vidtheque.db")
    conn.execute(
        "UPDATE video_stages SET state = 'pending', model_key = NULL "
        "WHERE stage IN ('text_embed', 'frame_embed')"
    )
    conn.commit()
    conn.close()

    backlog = _backlog(data / "vidtheque.db")
    assert backlog["text"] > 0 and backlog["frame"] > 0


def test_a_deliberately_skipped_leg_is_not_a_backlog(tmp_path: Path) -> None:
    """The distinction the whole `skipped` state exists for: never asked for is
    not the same as not done yet, and only the second is a degraded window."""
    data = tmp_path / "data"
    (data / "keyframes").mkdir(parents=True)
    seed(data / "vidtheque.db", data / "keyframes")
    conn = sqlite3.connect(data / "vidtheque.db")
    conn.execute("UPDATE video_stages SET state = 'skipped' WHERE stage = 'frame_embed'")
    conn.commit()
    conn.close()
    assert _backlog(data / "vidtheque.db")["frame"] == 0


def test_a_stage_that_never_ran_is_partial_coverage_not_a_backlog(
    tmp_path: Path,
) -> None:
    """The fixture's second video has keyframes and no `frame_embed` row at
    all. That is partial coverage — `data_status: partial`'s job — and calling
    it a backlog would re-label every pre-existing corpus as mid-re-embed."""
    data = tmp_path / "data"
    (data / "keyframes").mkdir(parents=True)
    seed(data / "vidtheque.db", data / "keyframes")
    assert _backlog(data / "vidtheque.db") == {"text": 0, "frame": 0}


async def test_corpus_summary_says_degraded_and_why(assembled) -> None:
    await assembled.db.write(
        lambda c: c.execute(
            "UPDATE video_stages SET state = 'pending', model_key = NULL "
            "WHERE stage IN ('text_embed', 'frame_embed')"
        )
    )
    result = await library.corpus_summary(assembled.deps)
    text = body(result)
    assert "data_status: degraded" in text
    assert "re-embedded" in text
    assert "keyword search is unaffected" in text
    assert result.structured_content["data_status"] == "degraded"
    assert result.structured_content["embed_backlog"]["frame"] > 0


async def test_search_notes_the_backlog_rather_than_narrowing_silently(
    assembled,
) -> None:
    """`all` means all — including being honest about what `all` could not
    reach. The legs still RUN: disabling them would be the §5.4 trap, because
    both embed stages skip themselves while the vector legs are off and the
    backfill would never finish."""
    await assembled.db.write(
        lambda c: c.execute(
            "UPDATE video_stages SET state = 'pending', model_key = NULL "
            "WHERE stage IN ('text_embed', 'frame_embed')"
        )
    )
    result = await search.run(assembled.deps, q="kv cache", content_type="all")
    notes = result.structured_content["notes"]
    assert any("re-embedded" in n for n in notes), notes
    assert assembled.db.vectors.enabled is True, "disabling would latch the backfill off"


async def test_a_current_corpus_prints_no_backlog_note(assembled) -> None:
    result = await search.run(assembled.deps, q="kv cache", content_type="all")
    assert not any("re-embedded" in n for n in result.structured_content["notes"])


# --------------------------------------------------------------------------
# drift now covers both spaces
# --------------------------------------------------------------------------


async def test_a_frame_space_model_mismatch_disables_the_legs_when_unified(
    tmp_path: Path,
) -> None:
    """With one model serving both, a frame-space mismatch IS a text-space
    mismatch. Checking only the text space was right when the two spaces came
    from two checkpoints, and is a hole now (memo §5.4)."""
    data = tmp_path / "data"
    (data / "keyframes").mkdir(parents=True)
    seed(data / "vidtheque.db", data / "keyframes")
    db = Database(path=data / "vidtheque.db")
    await db.open()
    try:
        assert db.unified_embedding is True
        db.note_worker_drift("some/other-model", FRAME_DIM, space="frame")
        assert db.vectors.enabled is False
        assert "some/other-model" in (db.vectors.reason or "")
    finally:
        await db.close()


async def test_a_frame_space_mismatch_stays_local_when_the_legs_differ(
    tmp_path: Path,
) -> None:
    """The restraint the old asymmetry was protecting, kept. Two checkpoints
    means a SigLIP mismatch says nothing about the transcript index, so it must
    not take the transcript leg down with it — the frame leg's own dimension
    check in `Deps.embed_query` handles that one."""
    data = tmp_path / "data"
    (data / "keyframes").mkdir(parents=True)
    seed(data / "vidtheque.db", data / "keyframes")
    conn = sqlite3.connect(data / "vidtheque.db")
    conn.execute(
        "UPDATE config SET value = 'google/siglip2-so400m-patch16-naflex' "
        "WHERE key = 'frame_embed.model'"
    )
    conn.commit()
    conn.close()

    db = Database(path=data / "vidtheque.db")
    await db.open()
    try:
        assert db.unified_embedding is False
        db.note_worker_drift("some/other-model", FRAME_DIM, space="frame")
        assert db.vectors.enabled is True
    finally:
        await db.close()


async def test_the_two_legs_ask_for_two_different_endpoints(assembled) -> None:
    """One model, one space, and still two calls: the model is
    instruction-aware and the legs want different instructions. Collapsing them
    would be one embedding answering two questions."""
    fake = assembled.deps.embeddings
    fake.calls.clear()
    await search.run(assembled.deps, q="kv cache", content_type="all")
    assert len(fake.calls) == 2, fake.calls


# --------------------------------------------------------------------------
# the write path, end to end at 2048
# --------------------------------------------------------------------------


async def test_2048_dim_vectors_reach_both_vec_tables(
    settings: Settings, clip: Path
) -> None:
    parts = await harness(settings, clip)
    try:
        await parts.index(url=VIDEO_URL)
        assert await parts.run() is True
        for table in ("vec_chunks", "vec_frames"):
            rows = await parts.rows(f"SELECT length(embedding) AS n FROM {table}")
            assert rows, table
            # f32, so four bytes a dimension.
            assert int(rows[0]["n"]) // 4 == UNIFIED_DIM, table
        assert parts.db.text_dim == parts.db.frame_dim == UNIFIED_DIM
    finally:
        await parts.db.close()
        parts.parts.auth.close()


async def test_a_worker_still_serving_the_old_width_writes_nothing(
    settings: Settings, clip: Path
) -> None:
    """The ordering trap, as a test: point the worker at the old model before
    migrating and every vector is refused rather than mixed into a 2048-d
    index."""
    parts = await harness(settings, clip, worker=FakeWorker(text_dim=1024))
    try:
        await parts.index(url=VIDEO_URL, channels="transcript")
        assert await parts.run() is True
        stages = await parts.stages()
        assert stages["text_embed"]["state"] == "skipped"
        assert "1024-d" in stages["text_embed"]["error"]
        assert await parts.rows("SELECT * FROM vec_chunks") == []
    finally:
        await parts.db.close()
        parts.parts.auth.close()


# --------------------------------------------------------------------------
# staleness: a model change re-embeds, and re-embeds ONLY
# --------------------------------------------------------------------------


async def _reembed(parts: Harness) -> None:
    """Move `config` onto a new checkpoint the way an operator would, then run
    the pipeline again over the same video."""
    await parts.db.write(
        lambda c: c.execute(
            "UPDATE config SET value = 'Qwen/Qwen3-VL-Embedding-8B' "
            "WHERE key IN ('text_embed.model', 'frame_embed.model')"
        )
    )
    parts.db.config["text_embed.model"] = "Qwen/Qwen3-VL-Embedding-8B"
    parts.db.config["frame_embed.model"] = "Qwen/Qwen3-VL-Embedding-8B"
    parts.worker.text_model = "Qwen/Qwen3-VL-Embedding-8B"
    parts.worker.frame_model = "Qwen/Qwen3-VL-Embedding-8B"
    # The other half of what migration 0004 does: the two stages go back to
    # `pending`, which is both the honest state to read and what makes
    # `index-video` treat this as a resume rather than short-circuit it as
    # "already indexed".
    await parts.db.write(
        lambda c: c.execute(
            "UPDATE video_stages SET state = 'pending', model_key = NULL "
            "WHERE stage IN ('text_embed', 'frame_embed') AND state = 'done'"
        )
    )
    await parts.index(url=VIDEO_URL)
    assert await parts.run() is True


async def test_a_model_key_mismatch_on_a_done_embed_row_re_embeds(
    settings: Settings, clip: Path
) -> None:
    """The plain `_should_run` rule, and the exact opposite of the `keyframe`
    stage's `+fused` provenance forgiveness: for an embedding stage a different
    checkpoint really is a different space, so a key mismatch on a `done` row
    MUST re-run. Comparing contracts here would leave stale-width vectors in
    place, marked done."""
    parts = await harness(settings, clip)
    try:
        await parts.index(url=VIDEO_URL)
        assert await parts.run() is True
        before = await parts.stages()
        assert before["text_embed"]["state"] == "done"
        assert before["text_embed"]["model_key"] == UNIFIED_MODEL

        await _reembed(parts)

        after = await parts.stages()
        assert after["text_embed"]["model_key"] == "Qwen/Qwen3-VL-Embedding-8B"
        assert after["frame_embed"]["model_key"] == "Qwen/Qwen3-VL-Embedding-8B"
        # Untouched stages kept their keys, so they never re-ran.
        assert after["keyframe"]["model_key"] == before["keyframe"]["model_key"]
        assert after["stt"]["model_key"] == before["stt"]["model_key"]
    finally:
        await parts.db.close()
        parts.parts.auth.close()


async def test_a_re_embed_fetches_no_media(settings: Settings, clip: Path) -> None:
    """The number that made this migration cheap. `want_media` is gated on the
    *keyframe* stage being stale and `need_audio` on the *stt* stage; a model
    swap touches neither, so the inputs are the JPEGs already on disk and the
    chunk text already in SQLite. Without this, every already-indexed video
    would re-download an mp4 that `keep_source=audio` already deleted."""
    parts = await harness(settings, clip)
    try:
        await parts.index(url=VIDEO_URL)
        assert await parts.run() is True
        parts.source.downloads.clear()
        parts.worker.calls.clear()

        await _reembed(parts)

        assert parts.source.downloads == [], "no download, of either kind"
        assert "transcribe" not in parts.worker.calls
        assert not any(c.startswith("ocr:") for c in parts.worker.calls)
        assert any(c.startswith("embed:document") for c in parts.worker.calls)
        assert any(c.startswith("embed_images:") for c in parts.worker.calls)
    finally:
        await parts.db.close()
        parts.parts.auth.close()


# --------------------------------------------------------------------------
# the staleness contract, stated directly
# --------------------------------------------------------------------------


def _pipeline():
    from vidtheque_mcp.pipeline.runner import IndexingPipeline
    from vidtheque_mcp.pipeline.settings import PipelineSettings

    return IndexingPipeline(
        db=None,  # type: ignore[arg-type]
        layout=None,  # type: ignore[arg-type]
        settings=PipelineSettings(),
        source=None,  # type: ignore[arg-type]
    )


def _run(stages: dict[str, dict]):
    from vidtheque_mcp.pipeline.runner import ItemRun

    run = ItemRun(ctx=None, args={})  # type: ignore[arg-type]
    run.stages = stages
    return run


@pytest.mark.parametrize("stage", ["text_embed", "frame_embed"])
def test_a_done_embed_row_goes_stale_on_a_model_change(stage: str) -> None:
    """The re-embed trigger, and the whole reason a swap costs a worker call
    rather than a migration of 40,000 rows."""
    pipeline = _pipeline()
    run = _run({stage: {"state": "done", "model_key": "Qwen/Qwen3-Embedding-0.6B"}})
    assert pipeline._should_run(run, stage, UNIFIED_MODEL) is True
    current = _run({stage: {"state": "done", "model_key": UNIFIED_MODEL}})
    assert pipeline._should_run(current, stage, UNIFIED_MODEL) is False


@pytest.mark.parametrize("stage", ["text_embed", "frame_embed"])
def test_provenance_forgiveness_never_reaches_an_embed_stage(stage: str) -> None:
    """The exact opposite of the `keyframe` stage's `+fused` rule, and the one
    place extending that grammar would be a silent disaster: everything before
    the `+` matching does NOT mean the same vectors. A checkpoint id can carry
    a `+` (or a revision suffix), and if `_stage_contract` applied here a
    1024-d model and a 2048-d one could compare equal and leave stale-width
    vectors in place, marked `done`."""
    pipeline = _pipeline()
    run = _run({stage: {"state": "done", "model_key": "org/model+rev1"}})
    assert pipeline._should_run(run, stage, "org/model+rev2") is True
