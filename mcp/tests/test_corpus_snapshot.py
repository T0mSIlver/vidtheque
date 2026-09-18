from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from pathlib import Path

import pytest
from vidtheque_mcp import corpus_snapshot
from vidtheque_mcp.corpus_snapshot import KeepRule, SnapshotError, build_generation
from vidtheque_mcp.db.connection import open_read_connection, open_write_connection
from vidtheque_mcp.db.migrations import current_version, migrate
from vidtheque_mcp.db.queries import pack_f32


@dataclass(frozen=True)
class Seeded:
    data_dir: Path
    video_ids: dict[str, int]
    row_ids: dict[str, dict[str, int]]


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _insert_video(
    conn: sqlite3.Connection,
    data_dir: Path,
    source_id: str,
    channel: str,
    state: str,
    tag_id: int,
    collection_id: int,
) -> tuple[int, dict[str, int]]:
    video = conn.execute(
        "INSERT INTO videos (source_id, url, title, description, channel_name, index_state) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (
            source_id,
            f"https://example.test/watch/{source_id}",
            f"Title {source_id}",
            f"Description {source_id}",
            channel,
            state,
        ),
    )
    video_id = int(video.lastrowid or 0)
    conn.execute(
        "INSERT INTO video_stages (video_id, stage, state) VALUES (?, 'stt', 'done')",
        (video_id,),
    )
    conn.execute(
        "INSERT INTO chapters (video_id, seq, start_s, end_s, title) "
        "VALUES (?, 0, 0, 10, 'chapter')",
        (video_id,),
    )
    conn.execute(
        "INSERT INTO video_links (video_id, t_s, url, title, seq) "
        "VALUES (?, 1, 'https://example.test/link', 'link', 0)",
        (video_id,),
    )
    cue = conn.execute(
        "INSERT INTO cues (video_id, seq, start_s, end_s, text) VALUES (?, 0, 0, 2, ?)",
        (video_id, f"receipt needle {source_id}"),
    )
    cue_id = int(cue.lastrowid or 0)
    chunk = conn.execute(
        "INSERT INTO chunks (video_id, seq, start_s, end_s, first_cue_id, last_cue_id, "
        "text, n_chars) VALUES (?, 0, 0, 2, ?, ?, ?, ?)",
        (video_id, cue_id, cue_id, f"chunk {source_id}", len(f"chunk {source_id}")),
    )
    chunk_id = int(chunk.lastrowid or 0)
    conn.execute(
        "INSERT INTO vec_chunks (chunk_id, video_id, start_s, embedding) VALUES (?, ?, ?, ?)",
        (chunk_id, video_id, 0.0, pack_f32([0.0] * 2048)),
    )

    relative = Path("keyframes") / source_id / "00000-000000000.jpg"
    keyframe_path = data_dir / relative
    keyframe_path.parent.mkdir(parents=True, exist_ok=True)
    keyframe_path.write_bytes(f"jpeg {source_id}".encode())
    keyframe = conn.execute(
        "INSERT INTO keyframes (video_id, ord, t_s, shot_id, shot_start_s, shot_end_s, "
        "phash, sharpness, width, height, jpeg_path, jpeg_bytes, ocr_state) "
        "VALUES (?, 0, 0, 0, 0, 1, ?, 1, 16, 16, ?, ?, 'done')",
        (video_id, video_id, str(relative), keyframe_path.stat().st_size),
    )
    keyframe_id = int(keyframe.lastrowid or 0)
    conn.execute(
        "INSERT INTO vec_frames (keyframe_id, video_id, t_s, embedding) VALUES (?, ?, ?, ?)",
        (keyframe_id, video_id, 0.0, pack_f32([0.0] * 2048)),
    )
    ocr_line = conn.execute(
        "INSERT INTO ocr_lines (keyframe_id, video_id, t_s, line_no, text, conf, "
        "x0, y0, x1, y1) VALUES (?, ?, 0, 0, ?, 1, 0, 0, 1, 1)",
        (keyframe_id, video_id, f"screen {source_id}"),
    )
    ocr_line_id = int(ocr_line.lastrowid or 0)
    conn.execute(
        "INSERT INTO ocr_frames (keyframe_id, video_id, t_s, text) VALUES (?, ?, 0, ?)",
        (keyframe_id, video_id, f"screen {source_id}"),
    )
    conn.execute("INSERT INTO video_tags (video_id, tag_id) VALUES (?, ?)", (video_id, tag_id))
    conn.execute(
        "INSERT INTO collection_videos (collection_id, video_id) VALUES (?, ?)",
        (collection_id, video_id),
    )
    return video_id, {
        "cue": cue_id,
        "chunk": chunk_id,
        "keyframe": keyframe_id,
        "ocr_line": ocr_line_id,
    }


def _seed(data_dir: Path) -> Seeded:
    data_dir.mkdir(parents=True)
    db_path = data_dir / "vidtheque.db"
    conn = open_write_connection(db_path)
    video_ids: dict[str, int] = {}
    row_ids: dict[str, dict[str, int]] = {}
    try:
        migrate(conn)
        keep_tag = int(
            conn.execute("INSERT INTO tags (ns, name) VALUES ('topic', 'keep')").lastrowid or 0
        )
        other_tag = int(
            conn.execute("INSERT INTO tags (ns, name) VALUES ('topic', 'other')").lastrowid or 0
        )
        manual = int(
            conn.execute(
                "INSERT INTO collections (slug, title) VALUES ('manual', 'Manual')"
            ).lastrowid
            or 0
        )
        specs = (
            ("channel0001", "Keep Channel", "ready", other_tag),
            ("tagonly0001", "Other Channel", "stale", keep_tag),
            ("bothkeep001", "Keep Channel", "ready", keep_tag),
            ("dropped0001", "Other Channel", "ready", other_tag),
            ("pending0001", "Keep Channel", "pending", keep_tag),
        )
        for source_id, channel, state, tag_id in specs:
            video_id, rows = _insert_video(
                conn, data_dir, source_id, channel, state, tag_id, manual
            )
            video_ids[source_id] = video_id
            row_ids[source_id] = rows

        follow_collection = int(
            conn.execute(
                "INSERT INTO collections (slug, title, kind, source_url) "
                "VALUES ('follow', 'Follow', 'channel', 'https://example.test/channel')"
            ).lastrowid
            or 0
        )
        conn.execute("INSERT INTO follows (collection_id) VALUES (?)", (follow_collection,))
        job = int(
            conn.execute(
                "INSERT INTO jobs (public_id, kind, state, collection_id) "
                "VALUES ('job_snapshot1', 'index', 'queued', ?)",
                (follow_collection,),
            ).lastrowid
            or 0
        )
        for seq, (source_id, video_id) in enumerate(video_ids.items()):
            item = int(
                conn.execute(
                    "INSERT INTO job_items (job_id, seq, source_url, video_id, state) "
                    "VALUES (?, ?, ?, ?, 'queued')",
                    (job, seq, f"https://example.test/{source_id}", video_id),
                ).lastrowid
                or 0
            )
            conn.execute(
                "INSERT INTO job_events (job_id, item_id, message) VALUES (?, ?, 'queued')",
                (job, item),
            )
        conn.execute(
            "INSERT INTO follow_seen (collection_id, source_id, url, decision, video_id, job_id) "
            "VALUES (?, 'seen-video', 'https://example.test/seen', 'queued', ?, ?)",
            (follow_collection, video_ids["channel0001"], job),
        )
        conn.execute(
            "INSERT INTO follow_spend (collection_id, source_id, duration_s) "
            "VALUES (?, 'seen-video', 10)",
            (follow_collection,),
        )
    finally:
        conn.close()

    (data_dir / "audio").mkdir()
    (data_dir / "audio" / "channel0001.opus").write_bytes(b"audio")
    (data_dir / "derived").mkdir()
    (data_dir / "derived" / "cache.jpg").write_bytes(b"derived")
    return Seeded(data_dir, video_ids, row_ids)


@pytest.fixture
def seeded(tmp_path: Path) -> Seeded:
    return _seed(tmp_path / "data")


def _rules() -> list[KeepRule]:
    return [KeepRule("channel", "Keep Channel"), KeepRule("tag", "topic:keep")]


def test_builds_filtered_generation_without_touching_source(
    seeded: Seeded, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    source_db = seeded.data_dir / "vidtheque.db"
    source_hash = _sha256(source_db)

    exit_code = corpus_snapshot.main(
        [
            "--data-dir",
            str(seeded.data_dir),
            "--out-dir",
            str(tmp_path / "generations"),
            "--generation",
            "2026-09-18-test",
            "--keep-channel",
            "Keep Channel",
            "--keep-tag",
            "topic:keep",
        ]
    )
    captured = capsys.readouterr()
    assert exit_code == 0, captured.err
    manifest = json.loads(captured.out)

    generation = tmp_path / "generations" / "2026-09-18-test"
    snapshot_db = generation / "vidtheque.db"
    assert _sha256(source_db) == source_hash
    assert not (generation / "audio").exists()
    assert not (generation / "derived").exists()

    kept = {"channel0001", "tagonly0001", "bothkeep001"}
    dropped = {"dropped0001", "pending0001"}
    conn = open_read_connection(snapshot_db)
    try:
        assert {str(row[0]) for row in conn.execute("SELECT public_id FROM videos")} == kept
        for source_id in kept:
            video_id = seeded.video_ids[source_id]
            for table in (
                "video_stages",
                "chapters",
                "video_links",
                "cues",
                "chunks",
                "keyframes",
                "ocr_lines",
                "ocr_frames",
                "video_tags",
                "collection_videos",
                "vec_chunks",
                "vec_frames",
            ):
                assert (
                    conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE video_id = ?", (video_id,)
                    ).fetchone()[0]
                    == 1
                )
            rows = seeded.row_ids[source_id]
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM cues_fts_docsize WHERE rowid = ?", (rows["cue"],)
                ).fetchone()[0]
                == 1
            )
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM ocr_frames_fts_docsize WHERE rowid = ?",
                    (rows["keyframe"],),
                ).fetchone()[0]
                == 1
            )
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM videos_fts_docsize WHERE rowid = ?", (video_id,)
                ).fetchone()[0]
                == 1
            )
        for source_id in dropped:
            video_id = seeded.video_ids[source_id]
            for table in (
                "video_stages",
                "chapters",
                "video_links",
                "cues",
                "chunks",
                "keyframes",
                "ocr_lines",
                "ocr_frames",
                "video_tags",
                "collection_videos",
                "vec_chunks",
                "vec_frames",
            ):
                assert (
                    conn.execute(
                        f"SELECT COUNT(*) FROM {table} WHERE video_id = ?", (video_id,)
                    ).fetchone()[0]
                    == 0
                )
            rows = seeded.row_ids[source_id]
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM cues_fts_docsize WHERE rowid = ?", (rows["cue"],)
                ).fetchone()[0]
                == 0
            )
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM ocr_frames_fts_docsize WHERE rowid = ?",
                    (rows["keyframe"],),
                ).fetchone()[0]
                == 0
            )
            assert (
                conn.execute(
                    "SELECT COUNT(*) FROM videos_fts_docsize WHERE rowid = ?", (video_id,)
                ).fetchone()[0]
                == 0
            )

        fts_hits = conn.execute(
            "SELECT c.video_id FROM cues_fts f JOIN cues c ON c.id = f.rowid "
            "WHERE cues_fts MATCH 'channel0001'"
        ).fetchall()
        assert [int(row[0]) for row in fts_hits] == [seeded.video_ids["channel0001"]]
        assert all(
            conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0
            for table in corpus_snapshot.OPERATIONAL_TABLES
        )
        assert (
            conn.execute(
                "SELECT COUNT(*) FROM collections WHERE source_url IS NOT NULL"
            ).fetchone()[0]
            == 0
        )
        schema_version = current_version(conn)
    finally:
        conn.close()

    source = open_read_connection(source_db)
    try:
        assert all(
            source.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] > 0
            for table in corpus_snapshot.OPERATIONAL_TABLES
        )
    finally:
        source.close()

    for source_id in kept:
        source_file = seeded.data_dir / "keyframes" / source_id / "00000-000000000.jpg"
        generated_file = generation / "keyframes" / source_id / source_file.name
        assert generated_file.read_bytes() == source_file.read_bytes()
        assert generated_file.stat().st_ino == source_file.stat().st_ino
    for source_id in dropped:
        assert not (generation / "keyframes" / source_id).exists()

    disk_manifest = json.loads((generation / "MANIFEST.json").read_text())
    assert manifest == disk_manifest
    assert manifest["schema_version"] == schema_version
    assert manifest["keep_rules"] == {
        "channels": ["Keep Channel"],
        "tags": ["topic:keep"],
    }
    assert manifest["videos"] == {
        "total": 3,
        "per_rule": [
            {"type": "channel", "value": "Keep Channel", "count": 2},
            {"type": "tag", "value": "topic:keep", "count": 2},
        ],
    }
    assert manifest["dropped"] == 2
    assert manifest["keyframe_dirs"] == 3
    assert manifest["alignment"] == []
    assert manifest["db_sha256"] == _sha256(snapshot_db)
    assert manifest["db_bytes"] == snapshot_db.stat().st_size


def test_refuses_missing_rules_bad_id_and_existing_generation(
    seeded: Seeded, tmp_path: Path
) -> None:
    generations = tmp_path / "generations"
    with pytest.raises(SnapshotError, match="at least one"):
        build_generation(seeded.data_dir, generations, "2026-09-18-none", [])
    with pytest.raises(SnapshotError, match="generation id"):
        build_generation(seeded.data_dir, generations, "latest", _rules())

    existing = generations / "2026-09-18-existing"
    existing.mkdir(parents=True)
    marker = existing / "keep"
    marker.write_text("untouched")
    with pytest.raises(SnapshotError, match="already exists"):
        build_generation(seeded.data_dir, generations, existing.name, _rules())
    assert marker.read_text() == "untouched"


def test_busy_queue_refusal_and_override(seeded: Seeded, tmp_path: Path) -> None:
    conn = open_write_connection(seeded.data_dir / "vidtheque.db")
    try:
        conn.execute("UPDATE jobs SET state = 'running', heartbeat_at = unixepoch()")
        conn.execute(
            "UPDATE job_items SET state = 'running', stage = 'stt' "
            "WHERE id = (SELECT MIN(id) FROM job_items)"
        )
    finally:
        conn.close()

    generations = tmp_path / "generations"
    refused = generations / "2026-09-18-busy"
    with pytest.raises(SnapshotError, match="item_state.*running.*job_snapshot1"):
        build_generation(seeded.data_dir, generations, refused.name, _rules())
    assert not refused.exists()

    manifest = build_generation(
        seeded.data_dir,
        generations,
        "2026-09-18-allowed",
        _rules(),
        allow_busy=True,
    )
    assert manifest["videos"]["total"] == 3


def test_verification_failure_removes_partial_generation(
    seeded: Seeded, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    generation = tmp_path / "generations" / "2026-09-18-broken"

    def fail_verification(_conn: sqlite3.Connection) -> None:
        raise SnapshotError("integrity_check failed: forced")

    monkeypatch.setattr(corpus_snapshot, "_verify_snapshot", fail_verification)
    with pytest.raises(SnapshotError, match="integrity_check failed"):
        build_generation(seeded.data_dir, generation.parent, generation.name, _rules())
    assert not generation.exists()
