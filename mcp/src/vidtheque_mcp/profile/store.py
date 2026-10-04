"""The interest profile's statements. Sync, connection-first, like `follows/store.py`.

companion.md §2.1–2.2 and the store's half of §2.4's guards. Every change is
an entry write plus a `profile_events` row carrying the entry's state before
and after, so a revert is writing `before` back. Callers run these through
`Database.write`, whose transaction makes a refused batch write nothing. The MCP
tool, the app and the nightly update all apply operations through `apply`, so
the guards hold whoever calls.
"""

from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass, field
from typing import Any, Sequence

ACTORS = ("owner", "agent", "nightly", "app")
# The human's own writes. `app` is the owner on the phone; both are protected
# from being dropped by an agent or the nightly update (§2.4).
OWNER_ACTORS = frozenset({"owner", "app"})

MAX_LIVE = 40
# An entry is a short topic, so a verdict can name it in a one-line chip
# (companion.md §2.1); a compound topic is two entries.
MAX_TEXT_CHARS = 32
MAX_TEXT_WORDS = 5
MAX_REASON_CHARS = 300

KINDS = ("topic", "project")
# A project is what the owner is building now (#159): it lapses this long after
# it was last written, so one nobody mentions any more leaves on its own.
PROJECT_TTL_S = 30 * 86_400
# Projects come from Claude's memory in bulk; this keeps them from crowding out topics.
MAX_PROJECTS = 10


class ProfileRefused(Exception):
    """An operation the store will not apply. Nothing in its batch was written."""

    def __init__(self, code: str, message: str, next_hint: str) -> None:
        super().__init__(message)
        self.code = code
        self.next_hint = next_hint


@dataclass(frozen=True)
class Ops:
    """One batch: applied together or not at all."""

    # (text, weight) or (text, weight, kind); kind defaults to "topic".
    add: Sequence[tuple[str, float] | tuple[str, float, str]] = ()
    drop: Sequence[int] = ()
    reweight: Sequence[tuple[int, float]] = ()
    reason: str | None = None


@dataclass
class Applied:
    event_ids: list[int] = field(default_factory=list)
    # Adds whose text matched a live entry; skipped rather than duplicated.
    duplicates: list[str] = field(default_factory=list)
    # Adds of a live project's text: its expiry moved, nothing else did, so no event.
    refreshed: list[str] = field(default_factory=list)


# ----------------------------------------------------------------- reads


# Live: not retired, and not past its expiry even if the nightly update has
# not retired it yet. Takes one parameter, the current time.
LIVE = "retired_at IS NULL AND (expires_at IS NULL OR expires_at > ?)"


def entries(
    conn: sqlite3.Connection, owner_id: int = 1, now: int | None = None
) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT id, text, weight, source, kind, expires_at, created_at FROM profile_entries"
            f" WHERE owner_id = ? AND {LIVE} ORDER BY weight DESC, id",
            (owner_id, _now(now)),
        )
    )


def revision(conn: sqlite3.Connection, owner_id: int = 1) -> int:
    """The latest event id on this owner's profile; 0 before the first write."""
    row = conn.execute(
        "SELECT MAX(e.id) FROM profile_events e"
        " JOIN profile_entries p ON p.id = e.entry_id WHERE p.owner_id = ?",
        (owner_id,),
    ).fetchone()
    return int(row[0] or 0)


# ----------------------------------------------------------------- writes


def apply(
    conn: sqlite3.Connection, ops: Ops, actor: str, owner_id: int = 1, now: int | None = None
) -> Applied:
    """Apply one batch of add/drop/reweight in a single transaction."""
    _check_actor(actor)
    now = _now(now)
    reason = _reason(ops.reason)
    adds = [_new(item) for item in ops.add]
    for _, weight, _ in adds:
        _check_weight(weight)
    for _, weight in ops.reweight:
        _check_weight(weight)

    done = Applied()
    live = {int(r["id"]): r for r in entries(conn, owner_id, now)}
    touched = [*ops.drop, *(entry_id for entry_id, _ in ops.reweight)]
    unknown = [i for i in touched if i not in live]
    if unknown:
        raise ProfileRefused(
            "E_UNKNOWN_ENTRY",
            f"no live profile entry with id {', '.join(map(str, unknown))}.",
            "call profile with no arguments for the current ids.",
        )
    if len(set(touched)) != len(touched):
        raise ProfileRefused(
            "E_BAD_PARAM",
            "an entry id appears more than once across drop and reweight.",
            "name each entry once per call.",
        )
    for entry_id in ops.drop:
        _guard_owner_entry(live[entry_id], actor)

    texts = {str(r["text"]).casefold(): r for r in live.values()}
    for entry_id, weight in ops.reweight:
        done.event_ids.append(_set(conn, entry_id, actor, "reweight", reason, weight=weight))
    for entry_id in ops.drop:
        done.event_ids.append(_set(conn, entry_id, actor, "drop", reason, live=False))
    n_live = len(live) - len(ops.drop)
    n_projects = sum(
        1 for i, r in live.items() if r["kind"] == "project" and i not in ops.drop
    )
    for raw, weight, kind in adds:
        text = _text(raw)
        same = texts.get(text.casefold())
        if same is not None:
            if kind == "project" and same["kind"] == "project":
                # Writing a project again is what keeps it: its 30 days restart.
                conn.execute(
                    "UPDATE profile_entries SET expires_at = ? WHERE id = ?",
                    (now + PROJECT_TTL_S, same["id"]),
                )
                done.refreshed.append(text)
            else:
                done.duplicates.append(text)
            continue
        n_live += 1
        _guard_cap(n_live)
        expires_at = None
        if kind == "project":
            n_projects += 1
            _guard_projects(n_projects)
            expires_at = now + PROJECT_TTL_S
        entry_id = int(
            conn.execute(
                "INSERT INTO profile_entries (owner_id, text, weight, source, kind, expires_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (owner_id, text, weight, actor, kind, expires_at),
            ).lastrowid
        )
        texts[text.casefold()] = _entry(conn, entry_id)
        after = {"text": text, "weight": weight, "live": True}
        done.event_ids.append(_event(conn, actor, "add", entry_id, None, after, reason))
    return done


def expire(conn: sqlite3.Connection, now: int | None = None, owner_id: int = 1) -> list[int]:
    """Retire every project past its expiry, one `drop` event each, as the nightly update.

    Not under the owner guard: an entry that carries an expiry was written to lapse.
    """
    lapsed = conn.execute(
        "SELECT id FROM profile_entries WHERE owner_id = ? AND retired_at IS NULL"
        " AND expires_at <= ? ORDER BY id",
        (owner_id, _now(now)),
    ).fetchall()
    reason = f"project not written again in {PROJECT_TTL_S // 86_400} days"
    return [_set(conn, int(r["id"]), "nightly", "drop", reason, live=False) for r in lapsed]


def revert_event(
    conn: sqlite3.Connection, event_id: int, actor: str, owner_id: int = 1
) -> int:
    """Undo one event by writing its `before` state back. Returns the revert's event id."""
    _check_actor(actor)
    event = _owned_event(conn, event_id, owner_id)
    if event is None:
        raise ProfileRefused(
            "E_UNKNOWN_EVENT",
            f"no profile event {event_id}.",
            "read the profile history for event ids.",
        )
    return _undo(conn, event, actor, owner_id, f"revert of event {event_id}")


def revert_to(
    conn: sqlite3.Connection, target: int, actor: str, owner_id: int = 1
) -> list[int]:
    """Roll the profile back to revision ``target``: undo every later event, newest first."""
    _check_actor(actor)
    later = list(
        conn.execute(
            "SELECT e.* FROM profile_events e"
            " JOIN profile_entries p ON p.id = e.entry_id"
            " WHERE p.owner_id = ? AND e.id > ? ORDER BY e.id DESC",
            (owner_id, target),
        )
    )
    reason = f"revert to revision {target}"
    return [_undo(conn, event, actor, owner_id, reason) for event in later]


# --------------------------------------------------------------- internals


def _undo(
    conn: sqlite3.Connection, event: sqlite3.Row, actor: str, owner_id: int, reason: str
) -> int:
    """Write back the fields this event changed, and only those.

    A reweight undone after a later drop restores the weight and leaves the
    entry retired; undoing events newest-first therefore lands exactly on the
    older revision.
    """
    entry_id = int(event["entry_id"])
    entry = _entry(conn, entry_id)
    after = json.loads(event["after"])
    before = json.loads(event["before"]) if event["before"] else {**after, "live": False}
    weight = float(before["weight"]) if before["weight"] != after["weight"] else None
    live = before["live"] if before["live"] != after["live"] else None
    if live is False and entry["retired_at"] is None:
        _guard_owner_entry(entry, actor)
    if live is True and entry["retired_at"] is not None:
        live_now = entries(conn, owner_id)
        _guard_cap(len(live_now) + 1)
        if entry["kind"] == "project":
            _guard_projects(sum(1 for r in live_now if r["kind"] == "project") + 1)
    return _set(conn, entry_id, actor, "revert", reason, weight=weight, live=live)


def _set(
    conn: sqlite3.Connection,
    entry_id: int,
    actor: str,
    op: str,
    reason: str | None,
    *,
    weight: float | None = None,
    live: bool | None = None,
) -> int:
    row = _entry(conn, entry_id)
    before = _snapshot(row)
    after = dict(before)
    if weight is not None:
        after["weight"] = weight
    if live is not None:
        after["live"] = live
    conn.execute(
        "UPDATE profile_entries SET weight = ?,"
        " retired_at = CASE WHEN ? THEN NULL ELSE COALESCE(retired_at, unixepoch()) END"
        " WHERE id = ?",
        (after["weight"], after["live"], entry_id),
    )
    if live is True and row["retired_at"] is not None and row["kind"] == "project":
        # A project brought back gets a fresh 30 days, or it would lapse again overnight.
        conn.execute(
            "UPDATE profile_entries SET expires_at = ? WHERE id = ?",
            (_now(None) + PROJECT_TTL_S, entry_id),
        )
    return _event(conn, actor, op, entry_id, before, after, reason)


def _event(
    conn: sqlite3.Connection,
    actor: str,
    op: str,
    entry_id: int,
    before: dict[str, Any] | None,
    after: dict[str, Any],
    reason: str | None,
) -> int:
    return int(
        conn.execute(
            "INSERT INTO profile_events (actor, op, entry_id, before, after, reason)"
            " VALUES (?, ?, ?, ?, ?, ?)",
            (
                actor,
                op,
                entry_id,
                json.dumps(before) if before is not None else None,
                json.dumps(after),
                reason,
            ),
        ).lastrowid
    )


def _entry(conn: sqlite3.Connection, entry_id: int) -> sqlite3.Row:
    return conn.execute("SELECT * FROM profile_entries WHERE id = ?", (entry_id,)).fetchone()


def _owned_event(conn: sqlite3.Connection, event_id: int, owner_id: int) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT e.* FROM profile_events e JOIN profile_entries p ON p.id = e.entry_id"
        " WHERE e.id = ? AND p.owner_id = ?",
        (event_id, owner_id),
    ).fetchone()


def _snapshot(row: sqlite3.Row) -> dict[str, Any]:
    return {"text": row["text"], "weight": float(row["weight"]), "live": row["retired_at"] is None}


def _guard_owner_entry(row: sqlite3.Row, actor: str) -> None:
    if row["source"] in OWNER_ACTORS and actor not in OWNER_ACTORS:
        raise ProfileRefused(
            "E_PROFILE_GUARD",
            f"entry {row['id']} was written by the owner and only the owner can drop it.",
            "reweight it instead, or ask the owner to drop it.",
        )


def _guard_cap(n_live: int) -> None:
    if n_live > MAX_LIVE:
        raise ProfileRefused(
            "E_PROFILE_GUARD",
            f"the profile is capped at {MAX_LIVE} live entries.",
            "drop an entry in the same call to make room.",
        )


def _guard_projects(n_projects: int) -> None:
    if n_projects > MAX_PROJECTS:
        raise ProfileRefused(
            "E_PROFILE_GUARD",
            f"the profile holds at most {MAX_PROJECTS} live projects.",
            "drop a project in the same call, or keep the ones that matter now.",
        )


def _new(item: tuple[Any, ...]) -> tuple[str, float, str]:
    text, weight, kind = (*item, "topic")[:3]
    if kind not in KINDS:
        raise ProfileRefused(
            "E_BAD_PARAM", f"kind {kind!r} is not one of {', '.join(KINDS)}.", "use topic or project."
        )
    return text, weight, kind


def _now(now: int | None) -> int:
    return int(time.time()) if now is None else now


def _check_actor(actor: str) -> None:
    if actor not in ACTORS:
        raise ValueError(f"unknown profile actor {actor!r}")


def _check_weight(weight: float) -> None:
    if not -1.0 <= weight <= 1.0:
        raise ProfileRefused(
            "E_BAD_PARAM",
            f"weight {weight} is outside [-1, 1].",
            "use a weight from -1 (less of this) to 1 (more of this).",
        )


def _text(raw: str) -> str:
    text = " ".join(raw.split())
    if not text:
        raise ProfileRefused("E_BAD_PARAM", "an entry's text is empty.", "say the interest in a few words.")
    words = len(text.split())
    if len(text) > MAX_TEXT_CHARS or words > MAX_TEXT_WORDS:
        raise ProfileRefused(
            "E_BAD_PARAM",
            f"{text!r} is {len(text)} characters and {words} words; an entry is at most"
            f" {MAX_TEXT_CHARS} characters and {MAX_TEXT_WORDS} words.",
            "write a short topic, 2-4 words; split a compound topic into separate entries.",
        )
    return text


def _reason(raw: str | None) -> str | None:
    if raw is None:
        return None
    reason = " ".join(raw.split())
    return reason[:MAX_REASON_CHARS] or None
