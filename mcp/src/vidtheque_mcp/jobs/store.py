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
from dataclasses import dataclass
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

# How long a claim may go without a heartbeat before another pass reclaims it.
# The runner heartbeats every ``HEARTBEAT_INTERVAL_S`` (30 s) *and* on every
# progress report, so a live job is never this quiet; a killed process is.
DEFAULT_STALE_CLAIM_S = 300


def new_job_id() -> str:
    return "job_" + secrets.token_hex(6)


class DuplicateInFlight(Exception):
    """The partial unique index refused a second job for a queued video."""

    def __init__(self, job_public_id: str | None) -> None:
        super().__init__("already indexing")
        self.job_public_id = job_public_id


@dataclass(frozen=True)
class NewItem:
    """One row of a job to be. Terminal at birth is a legitimate state.

    A wave of ten URLs where nine are already indexed used to queue all ten,
    because the no-op shortcut only fired when *every* URL was current. The nine
    are `skipped/E_ALREADY_INDEXED` before the runner ever sees them: still rows,
    still counted, still explained in `job-status` — just not work.
    """

    url: str
    video_id: int | None = None
    state: str = "queued"
    error_code: str | None = None
    error_message: str | None = None

    @property
    def terminal(self) -> bool:
        return self.state in TERMINAL


def _as_item(entry: "NewItem | tuple[str, int | None]") -> NewItem:
    return entry if isinstance(entry, NewItem) else NewItem(entry[0], entry[1])


def create_job(
    conn: sqlite3.Connection,
    kind: str,
    args: dict[str, Any],
    items: Sequence["NewItem | tuple[str, int | None]"],
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
    for seq, entry in enumerate(items):
        item = _as_item(entry)
        url, video_id = item.url, item.video_id
        try:
            conn.execute(
                "INSERT INTO job_items (job_id, seq, source_url, video_id, state, "
                "error_code, error_message, finished_at) VALUES (?, ?, ?, ?, ?, ?, ?, "
                + ("unixepoch()" if item.terminal else "NULL")
                + ")",
                (job_id, seq, url, video_id, item.state, item.error_code, item.error_message),
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


def defer_job(
    conn: sqlite3.Connection,
    job_id: int,
    delay_s: int,
    error_code: str | None = None,
    error_message: str | None = None,
) -> None:
    """Put a claimed job back on the queue, unavailable until `delay_s` from now.

    The backoff lever the queue always had and nothing pulled: `claim_next`
    already filters on `not_before`, but a retryable item was requeued with the
    job still `running`, so `_drive` claimed the *same* item on the very next
    iteration. A 429 answered inside a millisecond is a 429 answered again.

    The error code is written *now*, while the job is still going, because that
    is the only moment it is true — a sibling item succeeding later is not a
    reason for the payload to stop saying the box was rate-limited.
    """
    conn.execute(
        "UPDATE jobs SET state = 'queued', heartbeat_at = NULL, "
        "not_before = unixepoch() + ?, "
        "error_code = COALESCE(?, error_code), error_message = COALESCE(?, error_message) "
        "WHERE id = ?",
        (max(0, int(delay_s)), error_code, error_message, job_id),
    )


def cancel_item(conn: sqlite3.Connection, item_id: int, reason: str) -> None:
    """Terminate one item without touching the rest of its job.

    Used when `force_reindex` supersedes a claim nobody is holding: the item is
    over, it produced nothing, and the row says which job took the video.
    """
    conn.execute(
        "UPDATE job_items SET state = 'cancelled', error_code = 'E_SUPERSEDED', "
        "error_message = ?, finished_at = unixepoch() WHERE id = ?",
        (reason, item_id),
    )


# ------------------------------------------------------------- crash recovery


def stale_claims(
    conn: sqlite3.Connection, older_than_s: int, exclude: Sequence[int] = ()
) -> list[int]:
    """Ids of `running` jobs whose claim has gone quiet. Read-only.

    ``exclude`` is what this process is holding right now: an event loop that
    is blocked on a 30-minute transcription is alive, not crashed, and must
    never reclaim its own work.
    """
    placeholders = ",".join("?" for _ in exclude)
    clause = f" AND id NOT IN ({placeholders})" if exclude else ""
    rows = conn.execute(
        "SELECT id FROM jobs WHERE state = 'running' AND "
        "COALESCE(heartbeat_at, started_at, created_at) < unixepoch() - ?" + clause,
        (int(older_than_s), *exclude),
    ).fetchall()
    return [int(row["id"]) for row in rows]


def reclaim_stale(
    conn: sqlite3.Connection, older_than_s: int, exclude: Sequence[int] = ()
) -> list[str]:
    """Give a dead process's claims back to the queue. Returns the job ids.

    A killed runner leaves three lies behind: a `running` job nobody is
    driving, a `running` item nobody is executing, and — because the pipeline
    marks a stage before it starts — a `running` stage on a video whose
    `index_state` still says `indexing`. All three are reset here, to the state
    a *resume* reads correctly: the finished stages stay finished (that is
    `_should_run`'s job), only the interrupted one goes back to `pending`.

    ``attempts`` is not incremented: ``claim_item`` already counted this
    attempt when it handed the item out, so a job that reliably kills the
    process still retires after ``max_attempts`` instead of looping forever.
    """
    reclaimed: list[str] = []
    for job_id in stale_claims(conn, older_than_s, exclude):
        items = conn.execute(
            "SELECT id, video_id, attempts, max_attempts FROM job_items "
            "WHERE job_id = ? AND state = 'running'",
            (job_id,),
        ).fetchall()
        for item in items:
            if item["video_id"] is not None:
                _reset_video(conn, int(item["video_id"]))
            if int(item["attempts"]) >= int(item["max_attempts"]):
                finish_item(
                    conn,
                    int(item["id"]),
                    "failed",
                    "E_CRASHED",
                    f"the indexing process died on this item {item['attempts']} time(s); "
                    "it will not be retried automatically.",
                )
                continue
            requeue_item(conn, int(item["id"]))
        conn.execute(
            "UPDATE jobs SET state = 'queued', heartbeat_at = NULL WHERE id = ?", (job_id,)
        )
        log(
            conn,
            job_id,
            f"requeued: no heartbeat for over {older_than_s}s, the process that "
            f"claimed it is gone ({len(items)} item(s) reset)",
            "warn",
        )
        row = conn.execute("SELECT public_id FROM jobs WHERE id = ?", (job_id,)).fetchone()
        if row is not None:
            reclaimed.append(str(row["public_id"]))
    return reclaimed


def release_claims(conn: sqlite3.Connection, job_ids: Sequence[int]) -> list[str]:
    """Hand back what this process is holding, because it is stopping. Now.

    The same three lies `reclaim_stale` undoes, undone at the moment they become
    lies instead of five minutes later. A controlled shutdown cancels the task
    mid-item, so the row said `running` with a heartbeat seconds old: a quick
    restart correctly left it alone, and the claim sat wedged for the whole
    staleness window with nobody driving it.

    Unlike a crash there is no `E_CRASHED` retirement — being stopped is not the
    item's fault, and it goes back on the queue whatever its attempt count.
    """
    released: list[str] = []
    for job_id in job_ids:
        row = conn.execute(
            "SELECT public_id, state FROM jobs WHERE id = ?", (job_id,)
        ).fetchone()
        if row is None or str(row["state"]) != "running":
            continue  # it finished, or deferred itself, before the stop landed
        items = conn.execute(
            "SELECT id, video_id FROM job_items WHERE job_id = ? AND state = 'running'",
            (job_id,),
        ).fetchall()
        for item in items:
            if item["video_id"] is not None:
                _reset_video(conn, int(item["video_id"]), "interrupted: the runner was stopped")
            requeue_item(conn, int(item["id"]))
        conn.execute(
            "UPDATE jobs SET state = 'queued', heartbeat_at = NULL WHERE id = ?", (job_id,)
        )
        log(
            conn,
            job_id,
            f"requeued: the runner was stopped mid-item ({len(items)} item(s) reset). "
            "A restart claims it immediately.",
            "warn",
        )
        released.append(str(row["public_id"]))
    return released


def _reset_video(
    conn: sqlite3.Connection,
    video_id: int,
    reason: str = "interrupted: the indexing process was killed mid-stage",
) -> None:
    conn.execute(
        "UPDATE video_stages SET state = 'pending', error = ? "
        "WHERE video_id = ? AND state = 'running'",
        (reason, video_id),
    )
    # `stale` is the schema's word for "indexed, just not with the current
    # pipeline" — it stays searchable, which is right for a video whose
    # *reindex* died. A first index that died has nothing to search yet.
    conn.execute(
        "UPDATE videos SET index_state = "
        "CASE WHEN indexed_at IS NULL THEN 'pending' ELSE 'stale' END, "
        "updated_at = unixepoch() WHERE id = ? AND index_state = 'indexing'",
        (video_id,),
    )


def inflight_claim(conn: sqlite3.Connection, video_id: int) -> sqlite3.Row | None:
    """The queued/running job item that owns this video, if there is one.

    ``live`` is the difference between "somebody is indexing this right now"
    and "a claim nobody is holding": it is what tells `force_reindex` whether
    it may supersede. Call this *after* a stale sweep, and it is simply
    ``state = 'running'`` — a crashed claim has been requeued by then, and
    anything still running is either progressing or being driven by a process
    whose own sweep deliberately left it alone. Superseding on a stale
    heartbeat instead would let a slow stage have its item cancelled underneath
    it.
    """
    return conn.execute(
        """
        SELECT i.id AS item_id, i.state AS item_state, j.id AS job_id,
               j.public_id, j.state AS job_state, j.heartbeat_at,
               unixepoch() - COALESCE(j.heartbeat_at, j.started_at, j.created_at) AS quiet_s,
               (j.state = 'running') AS live
        FROM job_items i JOIN jobs j ON j.id = i.job_id
        WHERE i.video_id = ? AND i.state IN ('queued','running')
        LIMIT 1
        """,
        (video_id,),
    ).fetchone()


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

# One item's share of the job, as a fraction it can only ever climb: the stages
# it has finished plus how far it is into the one it is on. `stage_pct` alone
# was the bug — it restarts at 0 on every stage, so the overall figure printed
# 0.5 during `fetch` and 0.05 a second later during `stt`.
_STAGE_ORDINAL = "CASE i.stage " + " ".join(
    f"WHEN '{stage}' THEN {index}" for index, stage in enumerate(STAGES)
) + " ELSE 0 END"

# The floor: stages this job has actually finished for this video, which is
# durable work no retry undoes. `requeue_item` clears `stage`, so a late-stage
# item that failed retryably reported *nothing* until it was claimed again and
# started over — the job's percentage fell back to the terminal siblings and
# climbed the same ground twice.
#
# Keyed on `jobs.created_at`, not `started_at`: `claim_next` rewrites
# `started_at` on every claim, and stages a *previous* job left behind are not
# this job's progress. `_invalidate_stages` nulls `finished_at`, so a forced
# reindex starts from zero exactly as it should.
_HIGH_WATER = (
    "(SELECT COUNT(*) FROM video_stages s WHERE s.video_id = i.video_id "
    "AND s.state IN ('done','skipped') AND COALESCE(s.finished_at, 0) >= j.created_at)"
)
_ITEM_FRACTION = (
    f"MAX((({_STAGE_ORDINAL}) + i.stage_pct), {_HIGH_WATER}) / {float(len(STAGES))}"
)


def _count(state: str) -> str:
    return (
        f"(SELECT COUNT(*) FROM job_items i WHERE i.job_id = j.id AND i.state = '{state}')"
    )


# `n_done`/`n_failed` are read back off the items rather than off the trigger
# rollup, so the four terminal counts and `n_items` always add up in the
# payload — a job that reads `done` with nothing done has to say so in numbers.
_JOB_SQL = f"""
SELECT j.id, j.public_id, j.state, j.kind, j.n_items,
       j.cancel_requested, j.error_code, j.error_message,
       j.created_at, j.started_at, j.finished_at, j.heartbeat_at,
       {_count('done')} AS n_done,
       {_count('failed')} AS n_failed,
       {_count('skipped')} AS n_skipped,
       {_count('cancelled')} AS n_cancelled,
       ROUND(MIN(1.0,
             ((SELECT COUNT(*) FROM job_items i WHERE i.job_id = j.id
                AND i.state IN ('done','failed','skipped','cancelled'))
              + COALESCE((SELECT SUM({_ITEM_FRACTION}) FROM job_items i
                           WHERE i.job_id = j.id AND i.state IN ('running','queued')), 0.0)
             ) * 1.0 / MAX(j.n_items, 1)), 3) AS progress
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
               i.video_id, v.public_id, v.title, v.channel_name, v.duration_s
        FROM job_items i LEFT JOIN videos v ON v.id = i.video_id
        WHERE i.job_id = ? ORDER BY i.seq LIMIT ?
        """,
        (job_id, limit),
    ).fetchall()


def degraded_items(conn: sqlite3.Connection, job_id: int, limit: int = 20) -> list[sqlite3.Row]:
    """Items that finished `done` on a video with a stage that failed.

    The silent loss this exists to name: OCR, keyframes or either embedding leg
    can fail without taking the video down — `_finalize` only calls `fetch` and
    `stt` essential — so the item is `done`, the job is `done`, `n_failed` is 0,
    and a requested search channel is simply missing. One row per failed stage.
    """
    return conn.execute(
        """
        SELECT i.seq, i.source_url, i.video_id, v.public_id, s.stage, s.error
        FROM job_items i
        JOIN video_stages s ON s.video_id = i.video_id AND s.state = 'failed'
        LEFT JOIN videos v ON v.id = i.video_id
        WHERE i.job_id = ? AND i.state = 'done'
        ORDER BY i.seq, s.stage LIMIT ?
        """,
        (job_id, limit),
    ).fetchall()


def failed_stages(conn: sqlite3.Connection, video_id: int) -> list[str]:
    """Which stages a `ready` video is missing. The resume plan, in one query."""
    rows = conn.execute(
        "SELECT stage FROM video_stages WHERE video_id = ? AND state = 'failed' "
        "ORDER BY stage",
        (video_id,),
    ).fetchall()
    return [str(row["stage"]) for row in rows]


def item_counts(conn: sqlite3.Connection, job_id: int) -> dict[str, int]:
    """Items by state. The aggregation reads these, never the trigger rollup."""
    rows = conn.execute(
        "SELECT state, COUNT(*) AS n FROM job_items WHERE job_id = ? GROUP BY state",
        (job_id,),
    ).fetchall()
    return {str(row["state"]): int(row["n"]) for row in rows}


def item_error_counts(
    conn: sqlite3.Connection, job_id: int, states: Sequence[str] = tuple(sorted(TERMINAL))
) -> dict[str, int]:
    """Typed item error codes, counted. The job's own code is one summary of a
    job; this is all of them, and it is the one an unattended driver can act on.

    A job with nine successes and one `E_RATE_LIMIT` used to aggregate to plain
    `done` with a null code, which reads exactly like a clean run.
    """
    placeholders = ",".join("?" for _ in states)
    rows = conn.execute(
        "SELECT error_code, COUNT(*) AS n FROM job_items WHERE job_id = ? "
        f"AND error_code IS NOT NULL AND state IN ({placeholders}) GROUP BY error_code",
        (job_id, *states),
    ).fetchall()
    return {str(row["error_code"]): int(row["n"]) for row in rows}


def first_failure(
    conn: sqlite3.Connection, job_id: int, code: str | None = None
) -> sqlite3.Row | None:
    """The failed item that should speak for the job, in seq order.

    With `code`, the first item that failed *that* way — which is how
    `E_RATE_LIMIT` keeps priority over a later, less actionable failure.
    """
    clause = " AND error_code = ?" if code else ""
    params: tuple[Any, ...] = (job_id, code) if code else (job_id,)
    return conn.execute(
        "SELECT error_code, error_message FROM job_items WHERE job_id = ? "
        "AND state = 'failed'" + clause + " ORDER BY seq LIMIT 1",
        params,
    ).fetchone()


def nonproductive_reasons(
    conn: sqlite3.Connection, job_id: int, limit: int = 3
) -> list[str]:
    """Why the items that produced nothing produced nothing, in seq order."""
    rows = conn.execute(
        "SELECT source_url, error_message FROM job_items WHERE job_id = ? "
        "AND state IN ('skipped','cancelled') ORDER BY seq LIMIT ?",
        (job_id, limit),
    ).fetchall()
    return [
        f"{row['source_url']}: {row['error_message']}"
        if row["error_message"]
        else str(row["source_url"])
        for row in rows
    ]


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
