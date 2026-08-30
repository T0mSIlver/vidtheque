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


ALL_VERSIONS = [m.version for m in migrations.discover()]


def test_migrations_apply_on_a_fresh_database(fresh: sqlite3.Connection) -> None:
    applied = migrations.migrate(fresh)
    assert applied == ALL_VERSIONS
    assert migrations.current_version(fresh) == ALL_VERSIONS[-1]
    rows = migrations.applied(fresh)
    assert [int(r["version"]) for r in rows] == ALL_VERSIONS


def test_migrations_are_idempotent(fresh: sqlite3.Connection) -> None:
    migrations.migrate(fresh)
    assert migrations.migrate(fresh) == []


def test_user_version_is_the_authority(fresh: sqlite3.Connection) -> None:
    migrations.migrate(fresh)
    # Someone edited the file by hand: user_version and the audit trail disagree.
    # One *past* the last shipped migration, computed rather than written down —
    # a literal here turns into a real version the day it is added (it did: 5).
    fresh.execute(f"PRAGMA user_version = {migrations.current_version(fresh) + 1}")
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
    """After 0004 both legs are one model in one space at its native width."""
    migrations.migrate(fresh)
    config = dict(fresh.execute("SELECT key, value FROM config"))
    assert config["text_embed.model"] == "Qwen/Qwen3-VL-Embedding-2B"
    assert config["frame_embed.model"] == "Qwen/Qwen3-VL-Embedding-2B"
    assert config["text_embed.dim"] == "2048"
    assert config["frame_embed.dim"] == "2048"
    assert config["diarization.enabled"] == "0"


def _env_defaults() -> dict[str, str]:
    text = (Path(__file__).resolve().parents[2] / "deploy/.env.example").read_text()
    return dict(
        line.split("=", 1)  # type: ignore[misc]
        for line in text.splitlines()
        if line and not line.startswith("#") and "=" in line
    )


def test_seeded_model_ids_are_the_ones_the_default_worker_serves(
    fresh: sqlite3.Connection,
) -> None:
    """The whole anti-drift table is a string comparison against what the worker
    reports. Ship defaults that disagree and both vector legs turn themselves
    off on a default install — research/e2e-smoke-2026-08-08.md §4.1."""
    migrations.migrate(fresh)
    config = dict(fresh.execute("SELECT key, value FROM config"))
    env = _env_defaults()
    assert config["text_embed.model"] == env["EMBED_MODEL"]
    assert config["frame_embed.model"] == env["IMAGE_EMBED_MODEL"]
    assert config["ocr.model"] == env["OCR_MODEL"]
    assert config["stt.model"] == env["STT_MODEL"]


def test_the_rename_leaves_a_deliberate_operator_value_alone(
    fresh: sqlite3.Connection, tmp_path: Path
) -> None:
    """0002 renames the 0001 defaults; it must not overwrite a chosen model."""
    only_first = tmp_path / "m1"
    only_first.mkdir()
    first = migrations.discover()[0]
    (only_first / f"{first.version:04d}_{first.name}.sql").write_text(first.sql)
    migrations.migrate(fresh, only_first)
    fresh.execute("UPDATE config SET value = 'BAAI/bge-m3' WHERE key = 'text_embed.model'")
    fresh.execute(
        "INSERT INTO videos (source_id, url, title) VALUES ('x', 'https://x', 'T')"
    )
    fresh.execute(
        "INSERT INTO video_stages (video_id, stage, state, model_key) "
        "VALUES (1, 'frame_embed', 'done', 'siglip2-so400m-patch16-naflex')"
    )
    fresh.commit()

    # Up to 0002 only: 0004 moves the shipped pair to the unified model, and
    # this test is about 0002's guard.
    _migrate_up_to(fresh, 2, tmp_path / "staged")

    config = dict(fresh.execute("SELECT key, value FROM config"))
    assert config["text_embed.model"] == "BAAI/bge-m3"
    # The rename carries the stage rows with it: the same weights produced them,
    # so the reindex planner must not read them as stale.
    assert config["frame_embed.model"] == "google/siglip2-so400m-patch16-naflex"
    assert (
        fresh.execute(
            "SELECT model_key FROM video_stages WHERE stage = 'frame_embed'"
        ).fetchone()[0]
        == config["frame_embed.model"]
    )


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
    # The OCR index is over `ocr_frames`, one document per keyframe (§2.5).
    fresh.execute(
        "INSERT INTO ocr_frames (keyframe_id, video_id, t_s, text) "
        "VALUES (?, ?, 0, 'nvidia-smi torch.compile')",
        (kf, vid),
    )
    hits = fresh.execute(
        "SELECT COUNT(*) FROM ocr_frames_fts WHERE ocr_frames_fts MATCH '\"nvidia-smi\"'"
    ).fetchone()[0]
    assert hits == 1


def _migrate_up_to(conn: sqlite3.Connection, version: int, staging: Path) -> None:
    """Apply the shipped migrations up to `version`, from a staged copy.

    The copies are byte-identical, so the checksums the audit trail records are
    the shipped ones and the rest of the run applies normally afterwards.
    """
    staging.mkdir(exist_ok=True)
    for migration in migrations.discover():
        if migration.version <= version:
            (staging / f"{migration.version:04d}_{migration.name}.sql").write_text(
                migration.sql, encoding="utf-8"
            )
    migrations.migrate(conn, staging)


def test_ocr_frame_index_backfills_from_the_lines_already_indexed(
    fresh: sqlite3.Connection, tmp_path: Path
) -> None:
    """0003 upgrades an existing 886 MB index in place: the frame documents are
    built from `ocr_lines`, so no keyframe is re-read and no worker is called."""
    _migrate_up_to(fresh, 2, tmp_path / "staged")
    fresh.execute("INSERT INTO videos (source_id, url, title) VALUES ('v', 'https://v', 'T')")
    vid = fresh.execute("SELECT id FROM videos").fetchone()[0]
    fresh.execute(
        "INSERT INTO keyframes (video_id, ord, t_s, shot_id, shot_start_s, shot_end_s, phash, "
        "sharpness, width, height, jpeg_path, jpeg_bytes) "
        "VALUES (?, 0, 30.0, 0, 30.0, 31.0, 1, 1, 1, 1, 'p', 1)",
        (vid,),
    )
    kf = fresh.execute("SELECT id FROM keyframes").fetchone()[0]
    # Deliberately out of insertion order: `line_no` is reading order, not rowid.
    for line_no, text in ((1, "for retrieval augmented generation"), (0, "Vector databases")):
        fresh.execute(
            "INSERT INTO ocr_lines (keyframe_id, video_id, t_s, line_no, text, x0, y0, x1, y1) "
            "VALUES (?, ?, 30.0, ?, ?, 0, 0, 1, 1)",
            (kf, vid, line_no, text),
        )

    # Staged at 2, so 0003 is the next one to run. Everything after it rides
    # along; this test is about what 0003 does, not about how many exist.
    assert migrations.migrate(fresh)[:2] == [3, 4]

    row = fresh.execute("SELECT video_id, t_s, text FROM ocr_frames").fetchone()
    assert (row[0], row[1]) == (vid, 30.0)
    assert row[2] == "Vector databases | for retrieval augmented generation"
    # The terms are on different lines; only a frame-granular index matches.
    matched = fresh.execute(
        "SELECT COUNT(*) FROM ocr_frames_fts WHERE ocr_frames_fts MATCH 'vector AND retrieval'"
    ).fetchone()[0]
    assert matched == 1
    assert fresh.execute("SELECT COUNT(*) FROM ocr_frames_fts_docsize").fetchone()[0] == 1
    fresh.execute("INSERT INTO ocr_frames_fts(ocr_frames_fts) VALUES('integrity-check')")
    # The line index and its triggers are gone — one OCR index, not two.
    left = fresh.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE name IN ('ocr_fts','ocr_ai','ocr_ad','ocr_au')"
    ).fetchone()[0]
    assert left == 0


def test_deleting_a_video_clears_the_frame_index_too(fresh: sqlite3.Connection) -> None:
    """The cascade reaches `ocr_frames` through `keyframes`, and its delete
    trigger is what keeps the FTS index from keeping postings for dead text."""
    migrations.migrate(fresh)
    fresh.execute("INSERT INTO videos (source_id, url, title) VALUES ('v', 'https://v', 'T')")
    vid = fresh.execute("SELECT id FROM videos").fetchone()[0]
    fresh.execute(
        "INSERT INTO keyframes (video_id, ord, t_s, shot_id, shot_start_s, shot_end_s, phash, "
        "sharpness, width, height, jpeg_path, jpeg_bytes) "
        "VALUES (?, 0, 1.0, 0, 1.0, 2.0, 1, 1, 1, 1, 'p', 1)",
        (vid,),
    )
    kf = fresh.execute("SELECT id FROM keyframes").fetchone()[0]
    fresh.execute(
        "INSERT INTO ocr_frames (keyframe_id, video_id, t_s, text) VALUES (?, ?, 1.0, 'a slide')",
        (kf, vid),
    )
    assert fresh.execute("SELECT COUNT(*) FROM ocr_frames_fts_docsize").fetchone()[0] == 1
    fresh.execute("DELETE FROM videos WHERE id = ?", (vid,))
    assert fresh.execute("SELECT COUNT(*) FROM ocr_frames").fetchone()[0] == 0
    assert fresh.execute("SELECT COUNT(*) FROM ocr_frames_fts_docsize").fetchone()[0] == 0
    fresh.execute("INSERT INTO ocr_frames_fts(ocr_frames_fts) VALUES('integrity-check')")


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
        (chunk, vid, pack_f32([0.0] * 2048)),
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


def test_an_old_sqlite_fails_with_a_named_error_not_a_syntax_error(
    fresh: sqlite3.Connection, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Below the floor, 0003's ordered aggregate is a parse error pointing at
    the migration — which reads like a typo, not a platform mismatch. The
    check names the platform (field report, 2026-08-12: bookworm's 3.40.1
    crash-looped every clean install)."""
    monkeypatch.setattr(migrations.sqlite3, "sqlite_version_info", (3, 40, 1))
    monkeypatch.setattr(migrations.sqlite3, "sqlite_version", "3.40.1")
    with pytest.raises(migrations.MigrationError, match=r"3\.40\.1 .* 3\.44\.0"):
        migrations.migrate(fresh)


def test_the_spend_table_carries_todays_budget_across_the_upgrade(
    fresh: sqlite3.Connection, tmp_path: Path
) -> None:
    """0007 moves the budget off the ledger, and must not grant a fresh day doing it.

    Without the backfill the first check after the deploy reads an empty table
    and hands out a full sixteen hours to a day that may already have spent
    them — the refund this migration exists to close, given away once by the
    fix. Rows older than the retention window stay behind.
    """
    _migrate_up_to(fresh, 6, tmp_path / "staged")
    fresh.execute(
        "INSERT INTO collections (kind, slug, title, source_url) "
        "VALUES ('channel', 'c', 'C', 'https://www.youtube.com/@c')"
    )
    cid = fresh.execute("SELECT id FROM collections").fetchone()[0]
    fresh.execute("INSERT INTO follows (collection_id) VALUES (?)", (cid,))
    for source_id, decision, duration, age in (
        ("vid00000001", "queued", 3600.0, 600),
        ("vid00000002", "held_budget", 7200.0, 600),
        ("vid00000003", "queued", None, 600),
        ("vid00000004", "queued", 1800.0, 40 * 86_400),
    ):
        fresh.execute(
            "INSERT INTO follow_seen (collection_id, source_id, url, duration_s, decision, "
            "decided_at) VALUES (?, ?, 'https://v', ?, ?, unixepoch() - ?)",
            (cid, source_id, duration, decision, age),
        )

    assert migrations.migrate(fresh)[0] == 7

    carried = dict(fresh.execute("SELECT source_id, duration_s FROM follow_spend").fetchall())
    # The hold is not a spend, and the row past retention does not come back.
    assert carried == {"vid00000001": 3600.0, "vid00000003": 0.0}
    spent = fresh.execute(
        "SELECT SUM(duration_s) FROM follow_spend WHERE spent_at > unixepoch() - 86400"
    ).fetchone()[0]
    assert spent == 3600.0


def test_unfollowing_orphans_the_spend_rather_than_deleting_it(
    fresh: sqlite3.Connection,
) -> None:
    """The cascade is what made the budget refundable; here it is `SET NULL`."""
    migrations.migrate(fresh)
    fresh.execute(
        "INSERT INTO collections (kind, slug, title, source_url) "
        "VALUES ('channel', 'c', 'C', 'https://www.youtube.com/@c')"
    )
    cid = fresh.execute("SELECT id FROM collections").fetchone()[0]
    fresh.execute("INSERT INTO follows (collection_id) VALUES (?)", (cid,))
    fresh.execute(
        "INSERT INTO follow_seen (collection_id, source_id, url, decision) "
        "VALUES (?, 'v', 'https://v', 'queued')",
        (cid,),
    )
    fresh.execute(
        "INSERT INTO follow_spend (collection_id, source_id, duration_s) VALUES (?, 'v', 3600.0)",
        (cid,),
    )

    fresh.execute("DELETE FROM collections WHERE id = ?", (cid,))

    assert fresh.execute("SELECT COUNT(*) FROM follow_seen").fetchone()[0] == 0
    row = fresh.execute("SELECT collection_id, duration_s FROM follow_spend").fetchone()
    assert (row[0], row[1]) == (None, 3600.0)
