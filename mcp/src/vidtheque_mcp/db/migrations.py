"""The migrations runner. ``PRAGMA user_version`` is the authority.

It is a 32-bit integer in the database header, read and written transactionally
with the migration itself, and it needs no table to exist before it can be read
— which matters when the migration you are about to run is "create the first
table" (index-schema §1.10).

The ``schema_migrations`` table is the **audit trail**, not the source of truth:
version, name, checksum of the applied SQL, timestamp. If ``user_version`` and
the max applied row disagree, that is a hard boot error — someone edited the
file by hand.
"""

from __future__ import annotations

import hashlib
import re
import sqlite3
from dataclasses import dataclass
from pathlib import Path

MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_NAME = re.compile(r"^(\d{4})_([a-z0-9_]+)\.sql$")

_AUDIT_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
  version    INTEGER PRIMARY KEY,
  name       TEXT    NOT NULL,
  checksum   TEXT    NOT NULL,
  applied_at INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT
"""


class MigrationError(RuntimeError):
    """A boot-fatal schema problem."""


@dataclass(frozen=True)
class Migration:
    version: int
    name: str
    sql: str

    @property
    def checksum(self) -> str:
        return hashlib.sha256(self.sql.encode("utf-8")).hexdigest()


def discover(directory: Path | None = None) -> list[Migration]:
    """Numbered files, applied in order, no runtime branching."""
    directory = directory or MIGRATIONS_DIR
    found: list[Migration] = []
    for path in sorted(directory.glob("*.sql")):
        match = _NAME.match(path.name)
        if not match:
            raise MigrationError(
                f"Migration file {path.name!r} does not match NNNN_name.sql"
            )
        found.append(
            Migration(
                version=int(match.group(1)),
                name=match.group(2),
                sql=path.read_text(encoding="utf-8"),
            )
        )
    versions = [m.version for m in found]
    if len(set(versions)) != len(versions):
        raise MigrationError(f"Duplicate migration versions: {versions}")
    return found


def current_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def applied(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    table = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if table is None:
        return []
    return list(conn.execute("SELECT * FROM schema_migrations ORDER BY version"))


def migrate(conn: sqlite3.Connection, directory: Path | None = None) -> list[int]:
    """Apply pending migrations. Returns the versions applied in this call."""
    conn.execute(_AUDIT_DDL)

    version = current_version(conn)
    rows = applied(conn)
    max_applied = max((int(r["version"]) for r in rows), default=0)
    if max_applied != version:
        raise MigrationError(
            f"PRAGMA user_version is {version} but the audit trail's highest "
            f"applied migration is {max_applied}. The file was edited by hand; "
            "refusing to guess."
        )

    # A database from a *newer* build than this one. `user_version` matching
    # the audit trail only proves the file is self-consistent, not that this
    # binary understands it — so rolling a release back started cleanly against
    # a schema it had never seen and read and wrote it anyway. There is no
    # downgrade path, so refusing is the only safe answer.
    # (2026-08-10 audit, F-23.)
    known = discover(directory)
    latest_known = max((m.version for m in known), default=0)
    if version > latest_known:
        raise MigrationError(
            f"the database is at schema {version} and this build knows only "
            f"{latest_known}. It was written by a newer vidtheque; there is no "
            "downgrade path. Run the newer build, or restore a backup."
        )

    by_version = {int(r["version"]): r for r in rows}
    pending: list[Migration] = []
    for migration in known:
        if migration.version <= version:
            recorded = by_version.get(migration.version)
            if recorded is not None and recorded["checksum"] != migration.checksum:
                raise MigrationError(
                    f"Migration {migration.version:04d}_{migration.name} was "
                    "edited after it was applied (checksum mismatch). Add a new "
                    "migration instead of changing a shipped one."
                )
            continue
        pending.append(migration)

    done: list[int] = []
    for migration in pending:
        _apply_one(conn, migration)
        done.append(migration.version)
    return done


def _apply_one(conn: sqlite3.Connection, migration: Migration) -> None:
    # One transaction each. FK enforcement is per-connection, so turning it off
    # for the duration of a table rebuild (SQLite's documented 12-step ALTER) is
    # scoped to this connection; `foreign_key_check` runs before commit.
    #
    # `BEGIN IMMEDIATE` is inside the script rather than issued before it:
    # `executescript()` commits any *pending* transaction before running, so a
    # transaction opened out here would be silently closed and the migration
    # would stop being atomic.
    fk_was_on = bool(conn.execute("PRAGMA foreign_keys").fetchone()[0])
    if fk_was_on:
        conn.execute("PRAGMA foreign_keys = OFF")
    try:
        conn.executescript("BEGIN IMMEDIATE;\n" + migration.sql)
        try:
            violations = list(conn.execute("PRAGMA foreign_key_check"))
            if violations:
                raise MigrationError(
                    f"Migration {migration.version:04d}_{migration.name} left "
                    f"{len(violations)} foreign-key violations"
                )
            conn.execute(f"PRAGMA user_version = {migration.version}")
            conn.execute(
                "INSERT INTO schema_migrations (version, name, checksum) VALUES (?, ?, ?)",
                (migration.version, migration.name, migration.checksum),
            )
        except BaseException:
            conn.execute("ROLLBACK")
            raise
        conn.execute("COMMIT")
    finally:
        if fk_was_on:
            conn.execute("PRAGMA foreign_keys = ON")
