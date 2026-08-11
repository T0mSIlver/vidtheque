"""SQLite persistence for the authorization server.

A separate file from the corpus DB, so a corpus rebuild, copy or restore never
touches credentials (research doc §7.2). Four tables and no ORM.

**CIMD clients are never stored.** Only DCR registrations land in
``oauth_clients``; a Client ID Metadata Document is fetched, validated and
synthesised per request, which is the whole point of CIMD — nothing to
garbage-collect and no per-connection client explosion.
"""

from __future__ import annotations

import contextlib
import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path

_DDL = """
PRAGMA journal_mode = WAL;

CREATE TABLE IF NOT EXISTS oauth_clients (
  client_id          TEXT PRIMARY KEY,
  client_secret_hash TEXT,               -- NULL for public clients
  metadata_json      TEXT NOT NULL,      -- OAuthClientInformationFull
  created_at         INTEGER NOT NULL,
  last_seen_at       INTEGER
);

CREATE TABLE IF NOT EXISTS auth_codes (      -- 5 min TTL, single use
  code           TEXT PRIMARY KEY,
  client_id      TEXT NOT NULL,
  redirect_uri   TEXT NOT NULL,
  redirect_explicit INTEGER NOT NULL DEFAULT 1,
  code_challenge TEXT NOT NULL,
  scopes         TEXT NOT NULL,
  resource       TEXT,
  subject        TEXT NOT NULL,
  expires_at     INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS refresh_tokens (  -- sha256 hash only, rotating
  token_hash TEXT PRIMARY KEY,
  client_id  TEXT NOT NULL,
  subject    TEXT NOT NULL,
  scopes     TEXT NOT NULL,
  resource   TEXT,
  issued_at  INTEGER NOT NULL,
  expires_at INTEGER,
  rotated_to TEXT,
  revoked_at INTEGER
);

CREATE TABLE IF NOT EXISTS login_sessions (
  sid        TEXT PRIMARY KEY,
  subject    TEXT NOT NULL,
  expires_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS pending_authorizations (
  state_key  TEXT PRIMARY KEY,
  payload    TEXT NOT NULL,
  expires_at INTEGER NOT NULL
);
"""


@dataclass
class AuthStore:
    path: Path

    def __post_init__(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(self.path), isolation_level=None, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._conn.executescript(_DDL)
        # Owner-only. This file holds live session ids — replaying one is being
        # the owner — and the mode was whatever the process umask happened to
        # be, which on a default 022 is world-readable. Set after connect so it
        # applies to the file SQLite actually created.
        # (2026-08-10 audit, auth hardening.)
        with contextlib.suppress(OSError):
            self.path.chmod(0o600)

    def close(self) -> None:
        self._conn.close()

    # ------------------------------------------------------------- clients

    def save_client(self, client_id: str, metadata: dict, secret_hash: str | None) -> None:
        self._conn.execute(
            "INSERT INTO oauth_clients (client_id, client_secret_hash, metadata_json, created_at) "
            "VALUES (?, ?, ?, ?) ON CONFLICT(client_id) DO UPDATE SET "
            "metadata_json = excluded.metadata_json, last_seen_at = excluded.created_at",
            (client_id, secret_hash, json.dumps(metadata), int(time.time())),
        )

    def load_client(self, client_id: str) -> dict | None:
        row = self._conn.execute(
            "SELECT metadata_json FROM oauth_clients WHERE client_id = ?", (client_id,)
        ).fetchone()
        return None if row is None else json.loads(row["metadata_json"])

    # --------------------------------------------------------------- codes

    def save_code(self, code: str, record: dict) -> None:
        self._conn.execute(
            "INSERT INTO auth_codes (code, client_id, redirect_uri, redirect_explicit, "
            "code_challenge, scopes, resource, subject, expires_at) "
            "VALUES (:code, :client_id, :redirect_uri, :redirect_explicit, :code_challenge, "
            ":scopes, :resource, :subject, :expires_at)",
            {"code": code, **record},
        )

    def take_code(self, code: str) -> sqlite3.Row | None:
        """Single use: read and delete in the same statement pair."""
        row = self._conn.execute("SELECT * FROM auth_codes WHERE code = ?", (code,)).fetchone()
        if row is None:
            return None
        self._conn.execute("DELETE FROM auth_codes WHERE code = ?", (code,))
        if int(row["expires_at"]) < int(time.time()):
            return None
        return row

    def peek_code(self, code: str) -> sqlite3.Row | None:
        row = self._conn.execute("SELECT * FROM auth_codes WHERE code = ?", (code,)).fetchone()
        if row is None or int(row["expires_at"]) < int(time.time()):
            return None
        return row

    # ------------------------------------------------------ refresh tokens

    def save_refresh(
        self,
        token_hash: str,
        client_id: str,
        subject: str,
        scopes: list[str],
        resource: str | None,
        expires_at: int | None,
    ) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO refresh_tokens "
            "(token_hash, client_id, subject, scopes, resource, issued_at, expires_at) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (token_hash, client_id, subject, " ".join(scopes), resource, int(time.time()), expires_at),
        )

    def load_refresh(self, token_hash: str) -> sqlite3.Row | None:
        row = self._conn.execute(
            "SELECT * FROM refresh_tokens WHERE token_hash = ?", (token_hash,)
        ).fetchone()
        if row is None or row["revoked_at"] is not None:
            return None
        if row["expires_at"] is not None and int(row["expires_at"]) < int(time.time()):
            return None
        return row

    def rotate_refresh(self, old_hash: str, new_hash: str) -> None:
        """OAuth 2.1 requires rotation for public clients, which is all of ours."""
        self._conn.execute(
            "UPDATE refresh_tokens SET rotated_to = ?, revoked_at = ? WHERE token_hash = ?",
            (new_hash, int(time.time()), old_hash),
        )

    def revoke_refresh(self, token_hash: str) -> None:
        self._conn.execute(
            "UPDATE refresh_tokens SET revoked_at = ? WHERE token_hash = ?",
            (int(time.time()), token_hash),
        )

    def revoke_client_refresh(self, client_id: str) -> None:
        self._conn.execute(
            "UPDATE refresh_tokens SET revoked_at = ? WHERE client_id = ? AND revoked_at IS NULL",
            (int(time.time()), client_id),
        )

    # ------------------------------------------------------ login sessions

    def save_session(self, sid: str, subject: str, expires_at: int) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO login_sessions (sid, subject, expires_at) VALUES (?, ?, ?)",
            (sid, subject, expires_at),
        )

    def delete_session(self, sid: str | None) -> None:
        """Sign out: the row goes, so the cookie is dead even if it is replayed.

        Clearing the cookie alone would leave a live `login_sessions` row that
        anything holding a copy of the value could still present.
        """
        if not sid:
            return
        self._conn.execute("DELETE FROM login_sessions WHERE sid = ?", (sid,))

    def load_session(self, sid: str | None) -> str | None:
        if not sid:
            return None
        row = self._conn.execute(
            "SELECT subject, expires_at FROM login_sessions WHERE sid = ?", (sid,)
        ).fetchone()
        if row is None or int(row["expires_at"]) < int(time.time()):
            return None
        return str(row["subject"])

    # ------------------------------------------- pending /authorize state

    def save_pending(self, key: str, payload: dict, ttl_s: int = 600) -> None:
        self._conn.execute(
            "INSERT OR REPLACE INTO pending_authorizations (state_key, payload, expires_at) "
            "VALUES (?, ?, ?)",
            (key, json.dumps(payload), int(time.time()) + ttl_s),
        )

    def take_pending(self, key: str) -> dict | None:
        row = self._conn.execute(
            "SELECT payload, expires_at FROM pending_authorizations WHERE state_key = ?", (key,)
        ).fetchone()
        if row is None:
            return None
        self._conn.execute("DELETE FROM pending_authorizations WHERE state_key = ?", (key,))
        if int(row["expires_at"]) < int(time.time()):
            return None
        return json.loads(row["payload"])

    # ------------------------------------------------------------ upkeep

    def purge_expired(self) -> None:
        now = int(time.time())
        self._conn.execute("DELETE FROM auth_codes WHERE expires_at < ?", (now,))
        self._conn.execute("DELETE FROM login_sessions WHERE expires_at < ?", (now,))
        self._conn.execute("DELETE FROM pending_authorizations WHERE expires_at < ?", (now,))
