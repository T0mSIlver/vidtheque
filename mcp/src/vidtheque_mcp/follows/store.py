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
       f.next_check_at, f.last_new_at,
       f.last_error_code, f.last_error_message,
       f.created_at, f.updated_at
  FROM follows f
  JOIN collections c ON c.id = f.collection_id
"""

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
            "last_error_code = NULL, last_error_message = NULL, "
            "updated_at = unixepoch() WHERE collection_id = ?",
            (collection_id,),
        )
        return
    conn.execute(
        "UPDATE follows SET state = ?, updated_at = unixepoch() WHERE collection_id = ?",
        (state, collection_id),
    )


def check_now(conn: sqlite3.Connection, collection_id: int) -> None:
    """Make an active follow due immediately. A paused one stays paused."""
    conn.execute(
        "UPDATE follows SET next_check_at = 0, updated_at = unixepoch() "
        "WHERE collection_id = ? AND state <> 'paused'",
        (collection_id,),
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
    """Active follows whose clock has come round, oldest first.

    Oldest-first is what makes the shared daily budget fair: whoever has waited
    longest spends it first, rather than whoever happens to sort first by id.
    """
    return list(
        conn.execute(
            _SELECT
            + " WHERE f.state = 'active' AND f.next_check_at <= unixepoch() "
            "ORDER BY f.next_check_at, c.id LIMIT ?",
            (limit,),
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


def budget_spent_s(conn: sqlite3.Connection, window_s: int = 86_400) -> float:
    """Seconds of video accepted by every follow together in the rolling window.

    Hours of *video*, not GPU-minutes: the check knows a candidate's duration
    before it knows what indexing it will cost, and hours-of-video is the number
    an operator reasons about. An accepted candidate with an unknown duration
    contributes nothing to the sum and is therefore free — which is safe only
    because `judge_duration` holds an unknown duration for review whenever a
    length rule exists, and a follow with no length rule has no opinion about
    long videos anyway.
    """
    row = conn.execute(
        "SELECT COALESCE(SUM(duration_s), 0) AS spent FROM follow_seen "
        "WHERE decision = 'queued' AND decided_at > unixepoch() - ?",
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
