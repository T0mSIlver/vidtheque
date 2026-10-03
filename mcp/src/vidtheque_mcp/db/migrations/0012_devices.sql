-- vidtheque migration 0012 — the phones a verdict may be pushed to.
--
-- companion.md §6. Additive only: one new table, nothing existing is touched.
-- `token` is the Firebase Cloud Messaging registration token the app sends to
-- `POST /dashboard/api/devices`; registering it again refreshes `last_seen`,
-- which is how a token the app stopped using ages out of the list.

CREATE TABLE devices (
  id         INTEGER PRIMARY KEY,
  owner_id   INTEGER NOT NULL DEFAULT 1 REFERENCES owners(id),
  token      TEXT    NOT NULL UNIQUE CHECK (length(token) BETWEEN 1 AND 4096),
  created_at INTEGER NOT NULL DEFAULT (unixepoch()),
  last_seen  INTEGER NOT NULL DEFAULT (unixepoch())
) STRICT;

CREATE INDEX devices_by_owner ON devices(owner_id, last_seen DESC);
