"""Migrations apply on a fresh DB, and the invariants they encode hold."""

from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from vidtheque_mcp.db import migrations
from vidtheque_mcp.db.connection import open_write_connection, phash_to_signed


@pytest.fixture
def fresh(tmp_path: Path) -> sqlite3.Connection:
    conn = open_write_connection(tmp_path / "v.db")
    yield conn
    conn.close()


def test_migrations_apply_on_a_fresh_database(fresh: sqlite3.Connection) -> None:
    applied = migrations.migrate(fresh)
    assert applied == [1]
    assert migrations.current_version(fresh) == 1
    rows = migrations.applied(fresh)
    assert [int(r["version"]) for r in rows] == [1]


def test_migrations_are_idempotent(fresh: sqlite3.Connection) -> None:
    migrations.migrate(fresh)
    assert migrations.migrate(fresh) == []


def test_user_version_is_the_authority(fresh: sqlite3.Connection) -> None:
    migrations.migrate(fresh)
    # Someone edited the file by hand: user_version and the audit trail disagree.
    fresh.execute("PRAGMA user_version = 5")
    with pytest.raises(migrations.MigrationError, match="edited by hand"):
        migrations.migrate(fresh)


def test_edited_migration_is_detected(fresh: sqlite3.Connection, tmp_path: Path) -> None:
    directory = tmp_path / "m"
    directory.mkdir()
    (directory / "0001_initial.sql").write_text("CREATE TABLE a (x INTEGER) STRICT;")
    migrations.migrate(fresh, directory)
    (directory / "0001_initial.sql").write_text("CREATE TABLE a (x TEXT) STRICT;")
    with pytest.raises(migrations.MigrationError, match="checksum mismatch"):
        migrations.migrate(fresh, directory)


def test_pragmas_and_extension(fresh: sqlite3.Connection) -> None:
    migrations.migrate(fresh)
    assert fresh.execute("PRAGMA journal_mode").fetchone()[0].lower() == "wal"
    assert fresh.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert fresh.execute("SELECT vec_version()").fetchone()[0].startswith("v0.1")


def test_owner_columns_default_to_one(fresh: sqlite3.Connection) -> None:
    migrations.migrate(fresh)
    fresh.execute(
        "INSERT INTO videos (source_id, url, title) VALUES ('x', 'https://x', 'T')"
    )
    assert fresh.execute("SELECT owner_id FROM videos").fetchone()[0] == 1
    assert fresh.execute("SELECT COUNT(*) FROM owners").fetchone()[0] == 1
    with pytest.raises(sqlite3.IntegrityError):
        fresh.execute("INSERT INTO owners (id) VALUES (2)")


def test_config_is_seeded_with_the_decided_models(fresh: sqlite3.Connection) -> None:
    migrations.migrate(fresh)
    config = dict(fresh.execute("SELECT key, value FROM config"))
    assert config["text_embed.model"] == "qwen3-embedding-0.6b"
    assert config["text_embed.dim"] == "1024"
    assert config["frame_embed.dim"] == "1152"
    assert config["diarization.enabled"] == "0"


def test_vec_tables_have_no_partition_key(fresh: sqlite3.Connection) -> None:
    """The measured trap: PARTITION KEY preallocates a chunk per partition and
    applies `k` per partition."""
    migrations.migrate(fresh)
    for table in ("vec_chunks", "vec_frames"):
        sql = fresh.execute("SELECT sql FROM sqlite_master WHERE name = ?", (table,)).fetchone()[0]
        assert "PARTITION KEY" not in sql.upper()
        assert "chunk_size=256" in sql


def test_fts_triggers_keep_the_index_in_step(fresh: sqlite3.Connection) -> None:
    migrations.migrate(fresh)
    fresh.execute("INSERT INTO videos (source_id, url, title) VALUES ('v', 'https://v', 'T')")
    vid = fresh.execute("SELECT id FROM videos").fetchone()[0]
    for seq, text in enumerate(["hello caching world", ""]):
        fresh.execute(
            "INSERT INTO cues (video_id, seq, start_s, end_s, text) VALUES (?, ?, ?, ?, ?)",
            (vid, seq, seq, seq + 1, text),
        )
    # The empty document was guarded out. `COUNT(*) FROM cues_fts` counts the
    # *content* table (external content), so the index's own docsize shadow
    # table is what says how many documents were actually indexed.
    assert fresh.execute("SELECT COUNT(*) FROM cues").fetchone()[0] == 2
    assert fresh.execute("SELECT COUNT(*) FROM cues_fts_docsize").fetchone()[0] == 1
    # porter stems: "caching" matches "cache".
    assert fresh.execute("SELECT COUNT(*) FROM cues_fts WHERE cues_fts MATCH 'cache'").fetchone()[0] == 1
    fresh.execute("INSERT INTO cues_fts(cues_fts) VALUES('integrity-check')")


def test_ocr_tokenizer_keeps_identifiers_whole(fresh: sqlite3.Connection) -> None:
    migrations.migrate(fresh)
    fresh.execute("INSERT INTO videos (source_id, url, title) VALUES ('v', 'https://v', 'T')")
    vid = fresh.execute("SELECT id FROM videos").fetchone()[0]
    fresh.execute(
        "INSERT INTO keyframes (video_id, ord, t_s, shot_id, shot_start_s, shot_end_s, phash, "
        "sharpness, width, height, jpeg_path, jpeg_bytes) "
        "VALUES (?, 0, 0, 0, 0, 1, 1, 1, 1, 1, 'p', 1)",
        (vid,),
    )
    kf = fresh.execute("SELECT id FROM keyframes").fetchone()[0]
    fresh.execute(
        "INSERT INTO ocr_lines (keyframe_id, video_id, t_s, line_no, text, x0, y0, x1, y1) "
        "VALUES (?, ?, 0, 0, 'nvidia-smi torch.compile', 0, 0, 1, 1)",
        (kf, vid),
    )
    hits = fresh.execute("SELECT COUNT(*) FROM ocr_fts WHERE ocr_fts MATCH '\"nvidia-smi\"'").fetchone()[0]
    assert hits == 1


def test_cascade_delete_clears_fts_and_vectors(fresh: sqlite3.Connection) -> None:
    """vec0 tables are not reachable by foreign keys — the explicit delete
    triggers are what closes that."""
    from vidtheque_mcp.db.queries import pack_f32

    migrations.migrate(fresh)
    fresh.execute("INSERT INTO videos (source_id, url, title) VALUES ('v', 'https://v', 'T')")
    vid = fresh.execute("SELECT id FROM videos").fetchone()[0]
    fresh.execute(
        "INSERT INTO cues (video_id, seq, start_s, end_s, text) VALUES (?, 0, 0, 1, 'hello')",
        (vid,),
    )
    cue = fresh.execute("SELECT id FROM cues").fetchone()[0]
    fresh.execute(
        "INSERT INTO chunks (video_id, seq, start_s, end_s, first_cue_id, last_cue_id, text, "
        "n_chars) VALUES (?, 0, 0, 1, ?, ?, 'hello', 5)",
        (vid, cue, cue),
    )
    chunk = fresh.execute("SELECT id FROM chunks").fetchone()[0]
    fresh.execute(
        "INSERT INTO vec_chunks (chunk_id, video_id, start_s, embedding) VALUES (?, ?, 0.0, ?)",
        (chunk, vid, pack_f32([0.0] * 1024)),
    )
    fresh.execute("DELETE FROM videos WHERE id = ?", (vid,))
    assert fresh.execute("SELECT COUNT(*) FROM cues_fts_docsize").fetchone()[0] == 0
    assert fresh.execute("SELECT COUNT(*) FROM vec_chunks").fetchone()[0] == 0
    fresh.execute("INSERT INTO cues_fts(cues_fts) VALUES('integrity-check')")


def test_phash_is_stored_signed(fresh: sqlite3.Connection) -> None:
    migrations.migrate(fresh)
    signed = phash_to_signed(0xFFFF_FFFF_FFFF_FFFF)
    assert signed == -1
    fresh.execute("INSERT INTO videos (source_id, url, title) VALUES ('v', 'https://v', 'T')")
    vid = fresh.execute("SELECT id FROM videos").fetchone()[0]
    fresh.execute(
        "INSERT INTO keyframes (video_id, ord, t_s, shot_id, shot_start_s, shot_end_s, phash, "
        "sharpness, width, height, jpeg_path, jpeg_bytes) VALUES (?, 0, 0, 0, 0, 1, ?, 1, 1, 1, 'p', 1)",
        (vid, signed),
    )
    assert fresh.execute("SELECT phash_hamming(phash, 0) FROM keyframes").fetchone()[0] == 64


def test_in_flight_guard_is_the_partial_unique_index(fresh: sqlite3.Connection) -> None:
    from vidtheque_mcp.jobs.store import DuplicateInFlight, create_job

    migrations.migrate(fresh)
    fresh.execute("INSERT INTO videos (source_id, url, title) VALUES ('v', 'https://v', 'T')")
    vid = fresh.execute("SELECT id FROM videos").fetchone()[0]
    first = create_job(fresh, "index", {}, [("https://v", vid)])
    with pytest.raises(DuplicateInFlight) as caught:
        create_job(fresh, "index", {}, [("https://v", vid)])
    assert caught.value.job_public_id == first
