"""Job rows and the state machine from index-schema §1.9.

``jobs`` is the handle the model polls; ``job_items`` is the per-video work, so
one ``index-video`` call covering 200 playlist entries is one handle.

```
                     ┌──────────── cancel_requested=1 ───────────┐
                     ▼                                           │
  queued ──claim──> running ──all items terminal──> done         │
     │                 │                                         │
     │                 ├── item fails, attempts<max ──> requeue item
     │                 ├── all items failed ──────────> failed   │
     │                 └── heartbeat stale ───────────> requeue (crash recovery)
     └──────────────── cancel ────────────────────────> cancelled ┘
```

Wire vocabulary is the same vocabulary: ``job-status`` renders ``state``
verbatim. No translation table, so no drift.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
from typing import Any, Sequence

STAGES = ("fetch", "stt", "chunk", "text_embed", "keyframe", "ocr", "frame_embed")

# The five rows the wire shows, and which internal stages roll into each.
WIRE_STAGES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("download", ("fetch",)),
    ("transcribe", ("stt", "chunk")),
    ("keyframes", ("keyframe",)),
    ("ocr", ("ocr",)),
    ("embed", ("text_embed", "frame_embed")),
)

TERMINAL = {"done", "failed", "skipped", "cancelled"}


def new_job_id() -> str:
    return "job_" + secrets.token_hex(6)


class DuplicateInFlight(Exception):
    """The partial unique index refused a second job for a queued video."""

    def __init__(self, job_public_id: str | None) -> None:
        super().__init__("already indexing")
        self.job_public_id = job_public_id


def create_job(
    conn: sqlite3.Connection,
    kind: str,
    args: dict[str, Any],
    items: Sequence[tuple[str, int | None]],
    priority: int = 100,
) -> str:
    """Insert a job and its items. Runs inside the caller's transaction."""
    public_id = new_job_id()
    cursor = conn.execute(
        "INSERT INTO jobs (owner_id, public_id, kind, args_json, n_items, priority) "
        "VALUES (1, ?, ?, ?, ?, ?)",
        (public_id, kind, json.dumps(args), len(items), priority),
    )
    job_id = int(cursor.lastrowid or 0)
    for seq, (url, video_id) in enumerate(items):
        try:
            conn.execute(
                "INSERT INTO job_items (job_id, seq, source_url, video_id) VALUES (?, ?, ?, ?)",
                (job_id, seq, url, video_id),
            )
        except sqlite3.IntegrityError as exc:
            # The in-flight guard is the partial unique index, not application
            # logic: job_items_one_inflight makes a second index-video on an
            # already-queued video an IntegrityError.
            if "job_items.video_id" not in str(exc):
                raise
            existing = conn.execute(
                "SELECT j.public_id FROM job_items i JOIN jobs j ON j.id = i.job_id "
                "WHERE i.video_id = ? AND i.state IN ('queued','running') LIMIT 1",
                (video_id,),
            ).fetchone()
            raise DuplicateInFlight(existing["public_id"] if existing else None) from exc
    return public_id


def claim_next(conn: sqlite3.Connection) -> sqlite3.Row | None:
    """Claiming is one statement — UPDATE … RETURNING (SQLite 3.35+)."""
    return conn.execute(
        """
        UPDATE jobs
           SET state = 'running', started_at = unixepoch(), heartbeat_at = unixepoch()
         WHERE id = (SELECT id FROM jobs
                      WHERE state = 'queued' AND not_before <= unixepoch()
                      ORDER BY priority, id LIMIT 1)
        RETURNING id, public_id, kind, args_json
        """
    ).fetchone()


def claim_item(conn: sqlite3.Connection, job_id: int) -> sqlite3.Row | None:
    return conn.execute(
        """
        UPDATE job_items
           SET state = 'running', started_at = unixepoch(), attempts = attempts + 1
         WHERE id = (SELECT id FROM job_items
                      WHERE job_id = ? AND state = 'queued'
                      ORDER BY seq LIMIT 1)
        RETURNING id, job_id, seq, source_url, video_id, attempts, max_attempts
        """,
        (job_id,),
    ).fetchone()


def heartbeat(conn: sqlite3.Connection, job_id: int) -> None:
    conn.execute("UPDATE jobs SET heartbeat_at = unixepoch() WHERE id = ?", (job_id,))


def record_stage(conn: sqlite3.Connection, item_id: int, stage: str, pct: float) -> None:
    """Per-stage progress for the running item — the PipelineRunner's seam."""
    conn.execute(
        "UPDATE job_items SET stage = ?, stage_pct = ? WHERE id = ?",
        (stage, max(0.0, min(1.0, pct)), item_id),
    )


def finish_item(
    conn: sqlite3.Connection,
    item_id: int,
    state: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    conn.execute(
        "UPDATE job_items SET state = ?, error_code = ?, error_message = ?, "
        "finished_at = unixepoch() WHERE id = ?",
        (state, error_code, error_message, item_id),
    )


def requeue_item(conn: sqlite3.Connection, item_id: int) -> None:
    conn.execute(
        "UPDATE job_items SET state = 'queued', stage = NULL, stage_pct = 0.0 WHERE id = ?",
        (item_id,),
    )


def finish_job(
    conn: sqlite3.Connection,
    job_id: int,
    state: str,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    conn.execute(
        "UPDATE jobs SET state = ?, error_code = ?, error_message = ?, "
        "finished_at = unixepoch() WHERE id = ?",
        (state, error_code, error_message, job_id),
    )


def request_cancel(conn: sqlite3.Connection, public_id: str) -> None:
    """Cancellation is cooperative and its own column.

    Not a state: a running job that has been asked to stop is still running
    until it stops, and job-status should say so.
    """
    conn.execute("UPDATE jobs SET cancel_requested = 1 WHERE public_id = ?", (public_id,))


def log(
    conn: sqlite3.Connection,
    job_id: int,
    message: str,
    level: str = "info",
    item_id: int | None = None,
    stage: str | None = None,
    data: dict[str, Any] | None = None,
) -> None:
    conn.execute(
        "INSERT INTO job_events (job_id, item_id, level, stage, message, data_json) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (job_id, item_id, level, stage, message, json.dumps(data) if data else None),
    )


# ------------------------------------------------------------------ reading

_JOB_SQL = """
SELECT j.id, j.public_id, j.state, j.kind, j.n_items, j.n_done, j.n_failed,
       j.cancel_requested, j.error_code, j.error_message,
       j.created_at, j.started_at, j.finished_at,
       ROUND((j.n_done
              + COALESCE((SELECT SUM(stage_pct) FROM job_items i
                           WHERE i.job_id = j.id AND i.state = 'running'), 0))
             * 1.0 / MAX(j.n_items, 1), 3) AS progress
FROM jobs j
"""


def get_job(conn: sqlite3.Connection, public_id: str) -> sqlite3.Row | None:
    return conn.execute(_JOB_SQL + " WHERE j.public_id = ?", (public_id,)).fetchone()


def latest_job_for_video(conn: sqlite3.Connection, video_id: int) -> sqlite3.Row | None:
    return conn.execute(
        _JOB_SQL
        + " WHERE j.id IN (SELECT job_id FROM job_items WHERE video_id = ?)"
        " ORDER BY j.created_at DESC LIMIT 1",
        (video_id,),
    ).fetchone()


def list_jobs(conn: sqlite3.Connection, state: str, limit: int) -> list[sqlite3.Row]:
    clause = {
        "all": "",
        "active": " WHERE j.state IN ('queued','running')",
        "failed": " WHERE j.state = 'failed'",
        "done": " WHERE j.state = 'done'",
    }.get(state, " WHERE j.state IN ('queued','running')")
    return conn.execute(
        _JOB_SQL + clause + " ORDER BY j.created_at DESC LIMIT ?", (limit,)
    ).fetchall()


def job_items(conn: sqlite3.Connection, job_id: int, limit: int = 20) -> list[sqlite3.Row]:
    return conn.execute(
        """
        SELECT i.id, i.seq, i.source_url, i.state, i.stage, i.stage_pct,
               i.error_code, i.error_message, i.started_at, i.finished_at,
               v.public_id, v.title, v.channel_name, v.duration_s
        FROM job_items i LEFT JOIN videos v ON v.id = i.video_id
        WHERE i.job_id = ? ORDER BY i.seq LIMIT ?
        """,
        (job_id, limit),
    ).fetchall()


def item_stages(conn: sqlite3.Connection, video_id: int) -> dict[str, sqlite3.Row]:
    rows = conn.execute(
        "SELECT stage, state, error, started_at, finished_at FROM video_stages WHERE video_id = ?",
        (video_id,),
    ).fetchall()
    return {str(r["stage"]): r for r in rows}


def active_job_count(conn: sqlite3.Connection) -> int:
    return int(
        conn.execute("SELECT COUNT(*) FROM jobs WHERE state IN ('queued','running')").fetchone()[0]
    )
