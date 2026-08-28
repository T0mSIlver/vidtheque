"""Every statement the follow feature runs. Sync, connection-first, like `jobs/store.py`.

A follow is a `collections` row (`kind='channel'|'playlist'`) plus a `follows`
row holding the rules and the clock. Nothing here opens a connection or decides
a policy: the callers are `tools/follows.py`, `dashboard/`, and the check
itself, and all three go through `Database.read`/`Database.write` so there is
still one writer.

The budget lives here rather than in the check because it is a property of the
*corpus*, not of one follow: `VIDTHEQUE_FOLLOW_DAILY_HOURS` bounds how much
video every follow together may accept in a rolling day, and a per-follow
accounting would let five follows spend five budgets.
"""

from __future__ import annotations

import re
import sqlite3
from typing import Any, Iterable, Sequence

from .rules import Rules, joined

# A `failing` follow is retried this often, and only this many times before it
# stops being due and waits for a human (following.md §11.4, migration 0008).
# One a day for a week: long enough to outlast a private afternoon or a broken
# yt-dlp build, short enough that a deleted channel is not polled for a year.
FAILING_RETRY_INTERVAL_S = 86_400
FAILING_MAX_TRIES = 7

# How long a `follow_spend` row is kept. The budget window is a day; this is
# the same 30 days `job_events` and `ask_budget` use, so the retention story on
# this box is one sentence rather than three.
SPEND_KEEP_DAYS = 30

# The columns a follow row is made of, in one place so the read paths and the
# update path cannot drift about what is settable.
RULE_COLUMNS = (
    "tabs",
    "min_duration_s",
    "max_duration_s",
    "title_include",
    "title_exclude",
    "channels",
    "tags",
    "backfill",
    "max_per_check",
    "mode",
    "check_interval_s",
)

# What a follow row and its collection look like joined — the shape every read
# in this module returns, so a template never has to know which half a column
# came from.
_SELECT = """
SELECT c.id            AS collection_id,
       c.slug          AS slug,
       c.title         AS title,
       c.description   AS description,
       c.kind          AS kind,
       c.source_url    AS source_url,
       c.last_sync_at  AS last_sync_at,
       c.created_at    AS collection_created_at,
       f.state, f.tabs, f.min_duration_s, f.max_duration_s,
       f.title_include, f.title_exclude, f.channels, f.tags,
       f.backfill, f.max_per_check, f.mode, f.check_interval_s,
       f.next_check_at, f.last_new_at, f.fail_count,
       f.last_error_code, f.last_error_message,
       f.created_at, f.updated_at
  FROM follows f
  JOIN collections c ON c.id = f.collection_id
"""

# Which follows the scheduler will enqueue, in one place: `due()` reads it and
# `check_now()` arms exactly the same set, so a payload can never promise a
# check on a row nothing will pick up. Takes `FAILING_MAX_TRIES` as a parameter
# rather than inlining it, so the constant has one definition. Unaliased:
# `collections` has neither column, so it reads the same inside the join `due()`
# builds and inside the bare `UPDATE follows` `check_now()` runs.
_SCHEDULABLE = "(state = 'active' OR (state = 'failing' AND fail_count < ?))"

_SLUG_STRIP = re.compile(r"[^a-z0-9]+")


def slugify(name: str, fallback: str) -> str:
    """A collection slug from a channel name. Never empty, never colliding blindly.

    `collections.slug` is `NOT NULL UNIQUE` and predates this feature, so it is
    a real constraint rather than decoration: `unique_slug` walks it.
    """
    base = _SLUG_STRIP.sub("-", name.strip().lower()).strip("-")
    return base[:48] or _SLUG_STRIP.sub("-", fallback.lower()).strip("-")[:48] or "follow"


def unique_slug(conn: sqlite3.Connection, base: str) -> str:
    slug, n = base, 1
    while conn.execute("SELECT 1 FROM collections WHERE slug = ?", (slug,)).fetchone():
        n += 1
        slug = f"{base[:44]}-{n}"
    return slug


# ------------------------------------------------------------------- writing


def create(
    conn: sqlite3.Connection,
    *,
    title: str,
    source_url: str,
    kind: str,
    rules: Rules,
    description: str | None = None,
) -> int:
    """Insert the collection and its follow row. Returns the collection id.

    `sync_cron` is deliberately left NULL — see migration 0006: the interval
    lives in `check_interval_s`, because a cron expression is a config language
    and this surface is not a config editor.
    """
    slug = unique_slug(conn, slugify(title, source_url))
    cursor = conn.execute(
        "INSERT INTO collections (owner_id, slug, title, description, kind, source_url) "
        "VALUES (1, ?, ?, ?, ?, ?)",
        (slug, title, description, kind, source_url),
    )
    collection_id = int(cursor.lastrowid or 0)
    conn.execute(
        "INSERT INTO follows (collection_id, tabs, min_duration_s, max_duration_s, "
        "title_include, title_exclude, channels, tags, backfill, max_per_check, "
        "mode, check_interval_s, next_check_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, unixepoch())",
        (
            collection_id,
            ",".join(rules.tabs),
            rules.min_duration_s,
            rules.max_duration_s,
            joined(rules.title_include),
            joined(rules.title_exclude),
            rules.channels,
            joined(rules.tags),
            rules.backfill,
            rules.max_per_check,
            rules.mode,
            rules.check_interval_s,
        ),
    )
    return collection_id


def update_rules(conn: sqlite3.Connection, collection_id: int, fields: dict[str, Any]) -> None:
    """Set the named rule columns. Unknown names are refused, not ignored."""
    unknown = [name for name in fields if name not in RULE_COLUMNS]
    if unknown:  # pragma: no cover - callers validate first; this is the guard
        raise ValueError(f"not rule columns: {', '.join(sorted(unknown))}")
    if not fields:
        return
    assignments = ", ".join(f"{name} = ?" for name in fields)
    conn.execute(
        f"UPDATE follows SET {assignments}, updated_at = unixepoch() WHERE collection_id = ?",
        (*fields.values(), collection_id),
    )


def set_state(conn: sqlite3.Connection, collection_id: int, state: str) -> None:
    """Pause, resume, or mark failing.

    Resuming re-arms the clock to *now* rather than to when it would have been
    due: a follow paused for a week does not owe a week of checks, and the
    catch-up burst that would produce is exactly what the daily budget exists
    to prevent, so it must not be created in the first place.
    """
    if state == "active":
        conn.execute(
            "UPDATE follows SET state = 'active', next_check_at = unixepoch(), "
            "fail_count = 0, last_error_code = NULL, last_error_message = NULL, "
            "updated_at = unixepoch() WHERE collection_id = ?",
            (collection_id,),
        )
        return
    conn.execute(
        "UPDATE follows SET state = ?, updated_at = unixepoch() WHERE collection_id = ?",
        (state, collection_id),
    )


def check_now(conn: sqlite3.Connection, collection_id: int) -> None:
    """Make a schedulable follow due immediately. Anything else is left alone.

    The predicate is `due()`'s, spelled the same way on purpose. It used to be
    `state <> 'paused'`, which armed a `failing` follow the scheduler filters
    out — `next_check_at = 0` on a row nothing reads — and the caller then
    printed "due now" about a check that could never be queued. A false receipt
    on the feature whose entire design is receipts that are true.

    Since 0008 a `failing` follow that has not used up its retries *is*
    schedulable, so this arms it: "check now" after a channel comes back is
    exactly the gesture, and it no longer has to be spelled pause-then-resume.
    A follow that gave up is left alone, and recovery from that is
    `set_state(..., 'active')`, which is what `resume` calls.
    """
    conn.execute(
        "UPDATE follows SET next_check_at = 0, updated_at = unixepoch() "
        "WHERE collection_id = ? AND " + _SCHEDULABLE,
        (collection_id, FAILING_MAX_TRIES),
    )


def schedule_next(conn: sqlite3.Connection, collection_id: int, interval_s: int) -> None:
    conn.execute(
        "UPDATE follows SET next_check_at = unixepoch() + ?, updated_at = unixepoch() "
        "WHERE collection_id = ?",
        (int(interval_s), collection_id),
    )
    conn.execute(
        "UPDATE collections SET last_sync_at = unixepoch() WHERE id = ?", (collection_id,)
    )


def record_error(
    conn: sqlite3.Connection, collection_id: int, code: str | None, message: str | None
) -> None:
    """The last thing that went wrong, and whether it is still going wrong.

    A source that pushes back is an ordinary operating condition here, so one
    `E_RATE_LIMIT` does not make a follow `failing` — the queue's own backoff
    owns that. `failing` is set by the caller when the *channel* is the problem.
    """
    conn.execute(
        "UPDATE follows SET last_error_code = ?, last_error_message = ?, "
        "updated_at = unixepoch() WHERE collection_id = ?",
        (code, message, collection_id),
    )


def note_failure(
    conn: sqlite3.Connection,
    collection_id: int,
    code: str,
    message: str,
    *,
    interval_s: int,
) -> int:
    """The channel itself is the problem: mark it, count it, slow it down.

    Returns the consecutive-failure count after this one, which is what the
    surfaces print. The retry clock is a day rather than the follow's own
    interval because a check against something that is not there buys nothing —
    but never *sooner* than that interval, or a broken follow would poll a
    source more often than a working one.
    """
    conn.execute(
        "UPDATE follows SET state = 'failing', fail_count = fail_count + 1, "
        "last_error_code = ?, last_error_message = ?, "
        "next_check_at = unixepoch() + ?, updated_at = unixepoch() "
        "WHERE collection_id = ?",
        (code, message, max(int(interval_s), FAILING_RETRY_INTERVAL_S), collection_id),
    )
    row = conn.execute(
        "SELECT fail_count FROM follows WHERE collection_id = ?", (collection_id,)
    ).fetchone()
    return int(row["fail_count"]) if row is not None else 0


def note_success(conn: sqlite3.Connection, collection_id: int) -> None:
    """A check that completed: forget the last error and the failure count.

    Not `set_state(..., 'active')`, which also re-arms the clock to *now*. That
    is right for a human pressing Resume and wrong here, because `schedule_next`
    has already put the next check an interval away. A `paused` follow keeps its
    state: a human may have paused it while the check was in flight.
    """
    conn.execute(
        "UPDATE follows SET "
        "state = CASE WHEN state = 'failing' THEN 'active' ELSE state END, "
        "fail_count = 0, last_error_code = NULL, last_error_message = NULL, "
        "updated_at = unixepoch() WHERE collection_id = ?",
        (collection_id,),
    )


def retries_left(row: sqlite3.Row) -> bool:
    """Is this `failing` follow one the check will still come back to?

    The distinction `failing` alone cannot carry, read off the number rather
    than off a fourth state word (migration 0008).
    """
    return int(row["fail_count"] or 0) < FAILING_MAX_TRIES


def note_arrivals(conn: sqlite3.Connection, collection_id: int) -> None:
    conn.execute(
        "UPDATE follows SET last_new_at = unixepoch() WHERE collection_id = ?",
        (collection_id,),
    )


def delete(conn: sqlite3.Connection, collection_id: int) -> None:
    """Unfollow. The videos it brought in stay — they are corpus, not membership.

    `collections` cascades to `follows`, `follow_seen` and `collection_videos`,
    and `jobs.collection_id` is `ON DELETE SET NULL`, so an unfollow never takes
    a job's history with it.

    `follow_spend` is `ON DELETE SET NULL` for a stronger reason than the jobs
    table: the cascade used to reach the rows the daily budget sums, so deleting
    a follow handed back hours the box had already spent (migration 0007). The
    spend outlives its follow deliberately.
    """
    conn.execute("DELETE FROM collections WHERE id = ?", (collection_id,))


# ------------------------------------------------------------------- reading


def get(conn: sqlite3.Connection, collection_id: int) -> sqlite3.Row | None:
    return conn.execute(_SELECT + " WHERE c.id = ?", (collection_id,)).fetchone()


def by_source_url(conn: sqlite3.Connection, source_url: str) -> sqlite3.Row | None:
    return conn.execute(_SELECT + " WHERE c.source_url = ?", (source_url,)).fetchone()


def by_slug(conn: sqlite3.Connection, slug: str) -> sqlite3.Row | None:
    return conn.execute(_SELECT + " WHERE c.slug = ?", (slug,)).fetchone()


def find(conn: sqlite3.Connection, needle: str) -> sqlite3.Row | None:
    """Resolve what a human or a model typed: a slug, a source URL, or a title.

    Case-insensitive substring on the title is the last resort and is
    deliberately unanchored — `channel`/`video_title` filters elsewhere in this
    codebase behave the same way, and at this table's size it is a scan of a
    few dozen rows.
    """
    for lookup in (by_slug, by_source_url):
        row = lookup(conn, needle)
        if row is not None:
            return row
    return conn.execute(
        _SELECT + " WHERE lower(c.title) LIKE '%' || lower(?) || '%' ORDER BY c.id LIMIT 1",
        (needle,),
    ).fetchone()


def list_follows(
    conn: sqlite3.Connection, *, limit: int = 50, offset: int = 0
) -> list[sqlite3.Row]:
    """Every follow, most recently active first. `limit` is the caller's clamp."""
    return list(
        conn.execute(
            _SELECT
            + " ORDER BY (f.state = 'failing') DESC, "
            "COALESCE(c.last_sync_at, 0) DESC, c.id LIMIT ? OFFSET ?",
            (limit + 1, offset),
        )
    )


def due(conn: sqlite3.Connection, limit: int = 5) -> list[sqlite3.Row]:
    """Follows whose clock has come round, oldest first.

    Oldest-first is what makes the shared daily budget fair: whoever has waited
    longest spends it first, rather than whoever happens to sort first by id.

    Active, and — since 0008 — `failing` with retries left. A channel that was
    private for an afternoon and one that was renamed for good raise the same
    exception, so the check comes back for a week before it believes the second
    reading (following.md §11.4).
    """
    return list(
        conn.execute(
            _SELECT
            + " WHERE " + _SCHEDULABLE + " AND f.next_check_at <= unixepoch() "
            "ORDER BY f.next_check_at, c.id LIMIT ?",
            (FAILING_MAX_TRIES, limit),
        )
    )


def counts(conn: sqlite3.Connection, collection_id: int) -> dict[str, int]:
    """`follow_seen` decisions for one follow, as a dict of decision -> rows."""
    rows = conn.execute(
        "SELECT decision, COUNT(*) AS n FROM follow_seen WHERE collection_id = ? "
        "GROUP BY decision",
        (collection_id,),
    )
    return {str(row["decision"]): int(row["n"]) for row in rows}


def brought_in_counts(conn: sqlite3.Connection) -> dict[int, int]:
    """How many videos each follow has queued, for every follow, in one query.

    The alternative is `counts()` per row, which is what a listing did until the
    docstring claiming "one round trip" stopped being true. Bounded work either
    way, but a per-row round trip in a payload builder is the shape that becomes
    a fan-out the moment somebody raises the cap.
    """
    rows = conn.execute(
        "SELECT collection_id, COUNT(*) AS n FROM follow_seen "
        "WHERE decision = 'queued' GROUP BY collection_id"
    )
    return {int(row["collection_id"]): int(row["n"]) for row in rows}


def totals(conn: sqlite3.Connection) -> dict[str, int]:
    """The Following page's header band. One query, no per-follow round trip."""
    row = conn.execute(
        """
        SELECT (SELECT COUNT(*) FROM follows)                               AS follows,
               (SELECT COUNT(*) FROM follows WHERE state = 'active')        AS active,
               (SELECT COUNT(*) FROM follows WHERE state = 'paused')        AS paused,
               (SELECT COUNT(*) FROM follows WHERE state = 'failing')       AS failing,
               (SELECT COUNT(*) FROM follows WHERE state = 'active'
                  AND next_check_at <= unixepoch() + 3600)                  AS due_soon,
               (SELECT COUNT(*) FROM follow_seen WHERE decision = 'queued') AS brought_in,
               (SELECT COUNT(*) FROM follow_seen
                 WHERE decision IN ('held_budget','held_review'))           AS held
        """
    ).fetchone()
    return {key: int(row[key] or 0) for key in row.keys()}


def seen_page(
    conn: sqlite3.Connection,
    collection_id: int,
    *,
    decisions: Sequence[str] | None = None,
    limit: int = 50,
    offset: int = 0,
) -> list[sqlite3.Row]:
    """The ledger, newest decision first. Returns `limit + 1` rows for `has_more`.

    `has_more` over an exact total, like every other list on these surfaces.
    """
    where = ["collection_id = ?"]
    params: list[Any] = [collection_id]
    if decisions:
        where.append("decision IN (" + ",".join("?" * len(decisions)) + ")")
        params.extend(decisions)
    params.extend((limit + 1, offset))
    return list(
        conn.execute(
            "SELECT * FROM follow_seen WHERE "
            + " AND ".join(where)
            + " ORDER BY decided_at DESC, id DESC LIMIT ? OFFSET ?",
            params,
        )
    )


def seen_ids(conn: sqlite3.Connection, collection_id: int) -> dict[str, sqlite3.Row]:
    """Every candidate already decided for this follow, keyed by source id.

    This is what stops a check reconsidering the same upload every six hours
    for a year — the `UNIQUE (collection_id, source_id)` index read forwards.
    """
    rows = conn.execute(
        "SELECT source_id, decision, duration_s, judged_from FROM follow_seen "
        "WHERE collection_id = ?",
        (collection_id,),
    )
    return {str(row["source_id"]): row for row in rows}


def held(conn: sqlite3.Connection, limit: int = 50) -> list[sqlite3.Row]:
    """Everything waiting on a human, across every follow, oldest upload first."""
    return list(
        conn.execute(
            "SELECT s.*, c.title AS follow_title, c.slug AS follow_slug "
            "FROM follow_seen s JOIN collections c ON c.id = s.collection_id "
            "WHERE s.decision = 'held_review' "
            "ORDER BY COALESCE(s.published_at, s.first_seen_at), s.id LIMIT ?",
            (limit + 1,),
        )
    )


def held_for_budget(conn: sqlite3.Connection, limit: int = 25) -> list[sqlite3.Row]:
    """Candidates the budget turned away, oldest upload first.

    They are re-decided on the next check rather than dropped, which is the
    whole difference between a budget and a filter.
    """
    return list(
        conn.execute(
            "SELECT * FROM follow_seen WHERE decision = 'held_budget' "
            "ORDER BY COALESCE(published_at, first_seen_at), id LIMIT ?",
            (limit,),
        )
    )


def record_seen(
    conn: sqlite3.Connection,
    collection_id: int,
    *,
    source_id: str,
    url: str,
    title: str | None,
    duration_s: float | None,
    published_at: int | None,
    tab: str,
    decision: str,
    reason: str | None,
    judged_from: str = "listing",
    video_id: int | None = None,
    job_id: int | None = None,
) -> None:
    """Upsert one ledger row.

    `first_seen_at` survives a re-decision and `decided_at` does not: a
    candidate held for budget on Monday and queued on Tuesday is one row that
    says both things, which is what makes "how long did the budget hold this
    up" answerable at all.
    """
    conn.execute(
        """
        INSERT INTO follow_seen (collection_id, source_id, url, title, duration_s,
                                 published_at, tab, decision, reason, judged_from,
                                 video_id, job_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT (collection_id, source_id) DO UPDATE SET
            url          = excluded.url,
            title        = COALESCE(excluded.title, follow_seen.title),
            duration_s   = COALESCE(excluded.duration_s, follow_seen.duration_s),
            published_at = COALESCE(excluded.published_at, follow_seen.published_at),
            tab          = excluded.tab,
            decision     = excluded.decision,
            reason       = excluded.reason,
            judged_from  = excluded.judged_from,
            video_id     = COALESCE(excluded.video_id, follow_seen.video_id),
            job_id       = COALESCE(excluded.job_id, follow_seen.job_id),
            decided_at   = unixepoch()
        """,
        (
            collection_id,
            source_id,
            url,
            title,
            duration_s,
            published_at,
            tab,
            decision,
            reason,
            judged_from,
            video_id,
            job_id,
        ),
    )


def attach_videos(conn: sqlite3.Connection, collection_id: int, video_ids: Iterable[int]) -> None:
    """Membership, once a candidate has actually become a video row."""
    for video_id in video_ids:
        conn.execute(
            "INSERT OR IGNORE INTO collection_videos (collection_id, video_id) VALUES (?, ?)",
            (collection_id, video_id),
        )


def link_landed(conn: sqlite3.Connection) -> int:
    """Join ledger rows to the videos they became, and record the membership.

    A candidate is queued before the video row exists, so `follow_seen.video_id`
    cannot be filled at decision time for anything new. This closes the loop
    afterwards by the one key both sides share — the source id — and it is
    idempotent, so the check can simply run it every time.
    """
    conn.execute(
        """
        UPDATE follow_seen SET video_id = (
                 SELECT v.id FROM videos v
                  WHERE v.source = 'youtube' AND v.source_id = follow_seen.source_id)
         WHERE video_id IS NULL
           AND EXISTS (SELECT 1 FROM videos v
                        WHERE v.source = 'youtube' AND v.source_id = follow_seen.source_id)
        """
    )
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO collection_videos (collection_id, video_id)
        SELECT collection_id, video_id FROM follow_seen
         WHERE video_id IS NOT NULL AND decision = 'queued'
        """
    )
    return int(cursor.rowcount or 0)


# -------------------------------------------------------------------- budget


def record_spend(
    conn: sqlite3.Connection, collection_id: int, source_id: str, duration_s: float
) -> None:
    """Write one accepted candidate's hours where an unfollow cannot take them back.

    Called in the same transaction as the `queued` ledger row, because a budget
    that can lose its write is a budget that occasionally grants a day twice.
    """
    conn.execute(
        "INSERT INTO follow_spend (collection_id, source_id, duration_s) VALUES (?, ?, ?)",
        (collection_id, source_id, float(duration_s)),
    )


def prune_spend(conn: sqlite3.Connection) -> int:
    """Drop spend rows past the retention window. Returns rows deleted.

    Same 30 days as `job_events` and `ask_budget`, so the box has one retention
    story rather than three. The budget itself only ever reads the last 24
    hours; the rest is the record of what following cost last week, which
    outlives the ledger rows it was derived from.
    """
    cursor = conn.execute(
        "DELETE FROM follow_spend WHERE spent_at < unixepoch() - ?",
        (SPEND_KEEP_DAYS * 86_400,),
    )
    return cursor.rowcount if cursor.rowcount and cursor.rowcount > 0 else 0


def spend_of_s(
    conn: sqlite3.Connection, collection_id: int, window_s: int = 86_400
) -> float:
    """One follow's share of the window, for the sentence an unfollow owes.

    Read before the delete, because the delete is what nulls the owner.
    """
    row = conn.execute(
        "SELECT COALESCE(SUM(duration_s), 0) AS spent FROM follow_spend "
        "WHERE collection_id = ? AND spent_at > unixepoch() - ?",
        (collection_id, int(window_s)),
    ).fetchone()
    return float(row["spent"] or 0.0)


def budget_spent_s(conn: sqlite3.Connection, window_s: int = 86_400) -> float:
    """Seconds of video accepted by every follow together in the rolling window.

    Hours of *video*, not GPU-minutes: the check knows a candidate's duration
    before it knows what indexing it will cost, and hours-of-video is the number
    an operator reasons about. An accepted candidate with an unknown duration
    contributes nothing to the sum and is therefore free — which is safe only
    because `judge_duration` holds an unknown duration for review whenever a
    length rule exists, and a follow with no length rule has no opinion about
    long videos anyway.

    The sum is over `follow_spend`, not over the ledger. It used to be the
    ledger, and `collections` cascades to the ledger, so unfollowing refunded
    hours the box had already spent on download and GPU (migration 0007,
    following.md §5).
    """
    row = conn.execute(
        "SELECT COALESCE(SUM(duration_s), 0) AS spent FROM follow_spend "
        "WHERE spent_at > unixepoch() - ?",
        (int(window_s),),
    ).fetchone()
    return float(row["spent"] or 0.0)


# --------------------------------------------------------------- check jobs


def recent_checks(
    conn: sqlite3.Connection, collection_id: int, limit: int = 10
) -> list[sqlite3.Row]:
    """This follow's own `follow_check` jobs, newest first."""
    return list(
        conn.execute(
            "SELECT id, public_id, state, error_code, error_message, created_at, "
            "started_at, finished_at FROM jobs "
            "WHERE collection_id = ? AND kind = 'follow_check' "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (collection_id, limit),
        )
    )


def index_jobs(
    conn: sqlite3.Connection, collection_id: int, limit: int = 10
) -> list[sqlite3.Row]:
    """The index jobs this follow's checks enqueued, newest first."""
    return list(
        conn.execute(
            "SELECT id, public_id, state, n_items, n_done, n_failed, created_at "
            "FROM jobs WHERE collection_id = ? AND kind <> 'follow_check' "
            "ORDER BY created_at DESC, id DESC LIMIT ?",
            (collection_id, limit),
        )
    )


def check_in_flight(conn: sqlite3.Connection, collection_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT public_id FROM jobs WHERE collection_id = ? AND kind = 'follow_check' "
        "AND state IN ('queued','running') LIMIT 1",
        (collection_id,),
    ).fetchone()
