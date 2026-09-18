"""Build an immutable, filtered corpus generation."""

from __future__ import annotations

import argparse
import errno
import hashlib
import json
import os
import re
import shutil
import sqlite3
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from urllib.parse import quote

import sqlite_vec

from .db.connection import open_read_connection, open_write_connection
from .db.migrations import current_version, discover, migrate
from .db.queries import QUERYABLE_INDEX_STATES

GENERATION_RE = re.compile(r"\d{4}-\d{2}-\d{2}-[a-z0-9-]+\Z")
OPERATIONAL_TABLES = (
    "job_items",
    "job_events",
    "jobs",
    "follows",
    "follow_seen",
    "follow_spend",
    # Client addresses and daily spend; the serving box keeps its own.
    "ask_budget",
    # Titles and descriptions the owner wrote; no public read uses them.
    "collections",
    "collection_videos",
)
# Every other table must be empty in a generation, so a table a later migration adds
# fails the build until someone decides which side of this line it is on.
CORPUS_TABLES = frozenset(
    {
        "videos",
        "video_stages",
        "chapters",
        "video_links",
        "cues",
        "chunks",
        "keyframes",
        "ocr_lines",
        "ocr_frames",
        "video_tags",
        "tags",
        "speakers",
        "owners",
        "config",
        "schema_migrations",
    }
)
VIRTUAL_PREFIXES = ("cues_fts", "ocr_frames_fts", "videos_fts", "vec_chunks", "vec_frames", "sqlite_")
FTS_TABLES = ("cues_fts", "ocr_frames_fts", "videos_fts")


class SnapshotError(RuntimeError):
    """A corpus generation failed a publish safety check."""


@dataclass(frozen=True)
class KeepRule:
    kind: str
    value: str


def _log(message: str) -> None:
    print(message, file=sys.stderr)


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _in_flight(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT j.public_id AS job, j.state AS job_state, ji.id AS item,
               ji.state AS item_state, ji.stage, ji.source_url,
               COALESCE(v.public_id, '') AS video
          FROM jobs j
          LEFT JOIN job_items ji ON ji.job_id = j.id AND ji.state = 'running'
          LEFT JOIN videos v ON v.id = ji.video_id
         WHERE j.state = 'running' OR ji.id IS NOT NULL
         ORDER BY j.id, ji.seq
        """
    ).fetchall()
    return [dict(row) for row in rows]


def _check_busy(conn: sqlite3.Connection, *, allow_busy: bool) -> None:
    busy = _in_flight(conn)
    if busy and not allow_busy:
        detail = json.dumps(busy, sort_keys=True, separators=(",", ":"))
        raise SnapshotError(f"source queue has claimed or in-flight work: {detail}")


def _rule_condition(rule: KeepRule) -> str:
    if rule.kind == "channel":
        return "v.channel_name = ?"
    if rule.kind == "tag":
        return (
            "EXISTS (SELECT 1 FROM video_tags vt JOIN tags t ON t.id = vt.tag_id "
            "WHERE vt.video_id = v.id AND t.full = ?)"
        )
    raise SnapshotError(f"unknown keep rule type: {rule.kind}")


def _keep_predicate(rules: Sequence[KeepRule]) -> tuple[str, list[str]]:
    clauses = [_rule_condition(rule) for rule in rules]
    return " OR ".join(f"({clause})" for clause in clauses), [rule.value for rule in rules]


def _rule_counts(conn: sqlite3.Connection, rules: Sequence[KeepRule]) -> list[dict[str, Any]]:
    marks = ",".join("?" for _ in QUERYABLE_INDEX_STATES)
    counts = []
    for rule in rules:
        count = conn.execute(
            f"SELECT COUNT(*) FROM videos v WHERE v.index_state IN ({marks}) "
            f"AND {_rule_condition(rule)}",
            (*QUERYABLE_INDEX_STATES, rule.value),
        ).fetchone()[0]
        counts.append({"type": rule.kind, "value": rule.value, "count": int(count)})
    return counts


def _filter_copy(conn: sqlite3.Connection, rules: Sequence[KeepRule]) -> tuple[int, int]:
    before = int(conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0])
    predicate, values = _keep_predicate(rules)
    state_marks = ",".join("?" for _ in QUERYABLE_INDEX_STATES)

    conn.execute("BEGIN IMMEDIATE")
    try:
        conn.execute("CREATE TEMP TABLE snapshot_keep (video_id INTEGER PRIMARY KEY)")
        conn.execute(
            f"INSERT INTO snapshot_keep SELECT v.id FROM videos v "
            f"WHERE v.index_state IN ({state_marks}) AND ({predicate})",
            (*QUERYABLE_INDEX_STATES, *values),
        )
        conn.execute("DELETE FROM jobs")
        conn.execute("DELETE FROM follow_spend")
        conn.execute("DELETE FROM ask_budget")
        conn.execute("DELETE FROM collections")
        conn.execute("DELETE FROM videos WHERE id NOT IN (SELECT video_id FROM snapshot_keep)")
        # What a dropped video leaves behind by name: its tags, its speakers.
        conn.execute("DELETE FROM tags WHERE id NOT IN (SELECT tag_id FROM video_tags)")
        conn.execute(
            "DELETE FROM speakers WHERE id NOT IN "
            "(SELECT speaker_id FROM cues WHERE speaker_id IS NOT NULL)"
        )
        # Pipeline error text can name the private box's paths and hosts.
        conn.execute("UPDATE video_stages SET error = NULL WHERE error IS NOT NULL")
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")

    kept = int(conn.execute("SELECT COUNT(*) FROM videos").fetchone()[0])
    return kept, before - kept


def _check_operational_tables(conn: sqlite3.Connection) -> None:
    counts = {
        table: int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])
        for table in OPERATIONAL_TABLES
    }
    remaining = {table: count for table, count in counts.items() if count}
    if remaining:
        raise SnapshotError(f"operational table check failed: {remaining}")
    unclassified = {}
    for (table,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'"):
        if table in CORPUS_TABLES or table in OPERATIONAL_TABLES:
            continue
        if table.startswith(VIRTUAL_PREFIXES):
            continue
        count = int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])
        if count:
            unclassified[table] = count
    if unclassified:
        raise SnapshotError(
            f"unclassified table check failed: {unclassified} - add each table to "
            "CORPUS_TABLES or OPERATIONAL_TABLES"
        )


def _verify_snapshot(conn: sqlite3.Connection) -> None:
    orphan_checks = {
        "cues": "SELECT COUNT(*) FROM cues WHERE video_id NOT IN (SELECT id FROM videos)",
        "chunks": "SELECT COUNT(*) FROM chunks WHERE video_id NOT IN (SELECT id FROM videos)",
        "keyframes": (
            "SELECT COUNT(*) FROM keyframes WHERE video_id NOT IN (SELECT id FROM videos)"
        ),
        "ocr_lines": (
            "SELECT COUNT(*) FROM ocr_lines WHERE video_id NOT IN (SELECT id FROM videos)"
        ),
        "vec_chunks": (
            "SELECT COUNT(*) FROM vec_chunks WHERE chunk_id NOT IN (SELECT id FROM chunks)"
        ),
        "vec_frames": (
            "SELECT COUNT(*) FROM vec_frames WHERE keyframe_id NOT IN (SELECT id FROM keyframes)"
        ),
    }
    orphans = {name: int(conn.execute(sql).fetchone()[0]) for name, sql in orphan_checks.items()}
    orphans = {name: count for name, count in orphans.items() if count}
    if orphans:
        raise SnapshotError(f"orphan check failed: {orphans}")

    for table in FTS_TABLES:
        try:
            conn.execute(f"INSERT INTO {table}({table}) VALUES('integrity-check')")
        except sqlite3.Error as exc:
            raise SnapshotError(f"{table} integrity check failed: {exc}") from exc

    foreign_keys = [tuple(row) for row in conn.execute("PRAGMA foreign_key_check")]
    if foreign_keys:
        raise SnapshotError(f"foreign_key_check failed: {foreign_keys}")

    integrity = [str(row[0]) for row in conn.execute("PRAGMA integrity_check")]
    if integrity != ["ok"]:
        raise SnapshotError(f"integrity_check failed: {integrity}")


def _keyframe_files(conn: sqlite3.Connection, data_dir: Path) -> dict[str, list[Path]]:
    grouped: dict[str, list[Path]] = {}
    rows = conn.execute(
        """
        SELECT v.public_id, k.jpeg_path
          FROM keyframes k JOIN videos v ON v.id = k.video_id
         ORDER BY v.public_id, k.ord
        """
    )
    for row in rows:
        public_id = str(row["public_id"])
        relative = Path(str(row["jpeg_path"]))
        expected = ("keyframes", public_id)
        if (
            Path(public_id).name != public_id
            or public_id in {"", ".", ".."}
            or relative.is_absolute()
            or len(relative.parts) < 3
            or relative.parts[:2] != expected
            or ".." in relative.parts
        ):
            raise SnapshotError(f"keyframe path check failed for video {public_id!r}: {relative}")
        source = data_dir / relative
        if not source.is_file():
            raise SnapshotError(f"keyframe file check failed: missing {relative}")
        grouped.setdefault(public_id, []).append(relative)
    return grouped


def _link_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if source.stat().st_dev == destination.parent.stat().st_dev:
        try:
            os.link(source, destination)
            return
        except OSError as exc:
            if exc.errno != errno.EXDEV:
                raise
    shutil.copy2(source, destination)


def _copy_keyframes(conn: sqlite3.Connection, data_dir: Path, generation_dir: Path) -> int:
    grouped = _keyframe_files(conn, data_dir)
    for public_id, paths in grouped.items():
        (generation_dir / "keyframes" / public_id).mkdir(parents=True)
        for relative in paths:
            _link_or_copy(data_dir / relative, generation_dir / relative)
    return len(grouped)


def _write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    temporary = path.with_name(f".{path.name}.tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        json.dump(manifest, stream, indent=2, sort_keys=True)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)


def _immutable_copy(path: Path) -> sqlite3.Connection:
    uri = f"file:{quote(str(path.resolve()))}?mode=ro&immutable=1"
    source = sqlite3.connect(uri, uri=True, isolation_level=None)
    destination = sqlite3.connect(":memory:", isolation_level=None)
    try:
        for conn in (source, destination):
            conn.enable_load_extension(True)
            sqlite_vec.load(conn)
            conn.enable_load_extension(False)
        source.backup(destination)
    except BaseException:
        destination.close()
        raise
    finally:
        source.close()
    destination.row_factory = sqlite3.Row
    destination.execute("PRAGMA foreign_keys = ON")
    return destination


def verify_generation(generation_dir: Path, *, served: bool = False) -> str:
    """Check a generation against its manifest.

    `served` is for a generation that has already been activated once: the
    server opens its database for writing at boot (WAL, migrations, the ask
    budget), so the file no longer matches the manifest's size and hash.
    """
    generation_dir = generation_dir.resolve()
    generation_id = generation_dir.name
    manifest_path = generation_dir / "MANIFEST.json"
    database_path = generation_dir / "vidtheque.db"

    if not generation_dir.is_dir():
        raise SnapshotError(f"generation directory check failed: {generation_dir}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SnapshotError(f"manifest check failed: {exc}") from exc
    if not isinstance(manifest, dict):
        raise SnapshotError("manifest check failed: root must be an object")
    if manifest.get("id") != generation_id:
        raise SnapshotError(
            f"manifest id check failed: expected {generation_id!r}, got {manifest.get('id')!r}"
        )
    if not database_path.is_file():
        raise SnapshotError("database file check failed: vidtheque.db is missing")

    actual_bytes = database_path.stat().st_size
    if not served and manifest.get("db_bytes") != actual_bytes:
        raise SnapshotError(
            f"db_bytes check failed: expected {manifest.get('db_bytes')!r}, got {actual_bytes}"
        )
    if not served and manifest.get("db_sha256") != _sha256(database_path):
        raise SnapshotError("db_sha256 check failed")

    keyframes_dir = generation_dir / "keyframes"
    actual_keyframe_dirs = (
        sum(1 for path in keyframes_dir.iterdir() if path.is_dir())
        if keyframes_dir.is_dir()
        else 0
    )
    if manifest.get("keyframe_dirs") != actual_keyframe_dirs:
        raise SnapshotError(
            "keyframe_dirs check failed: "
            f"expected {manifest.get('keyframe_dirs')!r}, got {actual_keyframe_dirs}"
        )

    conn = _immutable_copy(database_path)
    try:
        database_version = current_version(conn)
        latest_version = max((migration.version for migration in discover()), default=0)
        if not served and manifest.get("schema_version") != database_version:
            raise SnapshotError(
                "schema_version check failed: manifest "
                f"{manifest.get('schema_version')!r}, database {database_version}"
            )
        # Newer would crash-loop the server; older would be migrated in place at boot.
        if database_version != latest_version:
            raise SnapshotError(
                f"schema_version check failed: generation is at {database_version}, this "
                f"release serves {latest_version}; run the same release on both boxes"
            )
        if not served:
            _check_operational_tables(conn)
        database_keyframe_dirs = len(_keyframe_files(conn, generation_dir))
        if database_keyframe_dirs != actual_keyframe_dirs:
            raise SnapshotError(
                "keyframe_dirs database check failed: "
                f"database {database_keyframe_dirs}, files {actual_keyframe_dirs}"
            )
        _verify_snapshot(conn)
    finally:
        conn.close()
    return generation_id


def build_generation(
    data_dir: Path,
    out_dir: Path,
    generation: str,
    rules: Sequence[KeepRule],
    *,
    allow_busy: bool = False,
) -> dict[str, Any]:
    if not GENERATION_RE.fullmatch(generation):
        raise SnapshotError(
            "generation id must match YYYY-MM-DD-<slug>, with slug characters [a-z0-9-]"
        )
    if not rules:
        raise SnapshotError("at least one --keep-channel or --keep-tag rule is required")

    data_dir = data_dir.resolve()
    out_dir = out_dir.resolve()
    source_path = data_dir / "vidtheque.db"
    if not source_path.is_file():
        raise SnapshotError(f"source database does not exist: {source_path}")

    out_dir.mkdir(parents=True, exist_ok=True)
    generation_dir = out_dir / generation
    try:
        generation_dir.mkdir()
    except FileExistsError as exc:
        raise SnapshotError(f"generation already exists: {generation_dir}") from exc

    snapshot_path = generation_dir / "vidtheque.db"
    try:
        _log(f"checking source queue in {source_path}")
        source: sqlite3.Connection | None = None
        try:
            source = open_read_connection(source_path)
            _check_busy(source, allow_busy=allow_busy)
            source.execute("PRAGMA query_only = OFF")
            _log(f"copying database to generation {generation}")
            source.execute("VACUUM INTO ?", (str(snapshot_path),))
        finally:
            if source is not None:
                source.close()

        conn = open_write_connection(snapshot_path)
        try:
            migrate(conn)
            _check_busy(conn, allow_busy=allow_busy)
            rule_counts = _rule_counts(conn, rules)
            kept, dropped = _filter_copy(conn, rules)
            _check_operational_tables(conn)
            conn.execute("VACUUM")
            _verify_snapshot(conn)
            schema_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
            _log(f"copying keyframes for {kept} kept videos")
            keyframe_dirs = _copy_keyframes(conn, data_dir, generation_dir)
        finally:
            conn.close()

        db_bytes = snapshot_path.stat().st_size
        manifest: dict[str, Any] = {
            "id": generation,
            "created_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            "schema_version": schema_version,
            "keep_rules": {
                "channels": [rule.value for rule in rules if rule.kind == "channel"],
                "tags": [rule.value for rule in rules if rule.kind == "tag"],
            },
            "videos": {"total": kept, "per_rule": rule_counts},
            "dropped": dropped,
            "db_sha256": _sha256(snapshot_path),
            "db_bytes": db_bytes,
            "keyframe_dirs": keyframe_dirs,
            # publishing.md §3 adds alignment rows in a later change.
            "alignment": [],
        }
        _write_manifest(generation_dir / "MANIFEST.json", manifest)
        _log(f"generation {generation} is complete")
        return manifest
    except BaseException:
        shutil.rmtree(generation_dir, ignore_errors=True)
        raise


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--verify", type=Path, metavar="GENERATION_DIR")
    parser.add_argument(
        "--served", action="store_true", help="with --verify: skip the size and hash checks"
    )
    parser.add_argument("--data-dir", type=Path)
    parser.add_argument("--out-dir", type=Path)
    parser.add_argument("--generation")
    parser.add_argument("--keep-channel", action="append", default=[], metavar="NAME")
    parser.add_argument("--keep-tag", action="append", default=[], metavar="TAG")
    parser.add_argument("--allow-busy", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _parser()
    args = parser.parse_args(argv)
    try:
        if args.verify is not None:
            if any((args.data_dir, args.out_dir, args.generation, args.keep_channel, args.keep_tag)):
                parser.error("--verify cannot be combined with build arguments")
            generation_id = verify_generation(args.verify, served=args.served)
            print(f"ok {generation_id}")
            return 0
        if args.data_dir is None or args.out_dir is None or args.generation is None:
            parser.error("build mode requires --data-dir, --out-dir, and --generation")
        rules = [KeepRule("channel", value) for value in args.keep_channel]
        rules.extend(KeepRule("tag", value) for value in args.keep_tag)
        manifest = build_generation(
            args.data_dir,
            args.out_dir,
            args.generation,
            rules,
            allow_busy=args.allow_busy,
        )
    except (SnapshotError, sqlite3.Error, OSError) as exc:
        print(f"corpus snapshot failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(manifest, sort_keys=True, separators=(",", ":")))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
