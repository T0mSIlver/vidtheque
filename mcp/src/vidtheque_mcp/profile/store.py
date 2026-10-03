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
from dataclasses import dataclass, field
from typing import Any, Sequence

ACTORS = ("owner", "agent", "nightly", "app")
# The human's own writes. `app` is the owner on the phone; both are protected
# from being dropped by an agent or the nightly update (§2.4).
OWNER_ACTORS = frozenset({"owner", "app"})

MAX_LIVE = 40
MAX_TEXT_CHARS = 200
MAX_REASON_CHARS = 300


class ProfileRefused(Exception):
    """An operation the store will not apply. Nothing in its batch was written."""

    def __init__(self, code: str, message: str, next_hint: str) -> None:
        super().__init__(message)
        self.code = code
        self.next_hint = next_hint


@dataclass(frozen=True)
class Ops:
    """One batch: applied together or not at all."""

    add: Sequence[tuple[str, float]] = ()
    drop: Sequence[int] = ()
    reweight: Sequence[tuple[int, float]] = ()
    reason: str | None = None


@dataclass
class Applied:
    event_ids: list[int] = field(default_factory=list)
    # Adds whose text matched a live entry; skipped rather than duplicated.
    duplicates: list[str] = field(default_factory=list)


# ----------------------------------------------------------------- reads


def entries(conn: sqlite3.Connection, owner_id: int = 1) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            "SELECT id, text, weight, source, created_at FROM profile_entries"
            " WHERE owner_id = ? AND retired_at IS NULL ORDER BY weight DESC, id",
            (owner_id,),
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
    conn: sqlite3.Connection, ops: Ops, actor: str, owner_id: int = 1
) -> Applied:
    """Apply one batch of add/drop/reweight in a single transaction."""
    _check_actor(actor)
    reason = _reason(ops.reason)
    for _, weight in ops.add:
        _check_weight(weight)
    for _, weight in ops.reweight:
        _check_weight(weight)

    done = Applied()
    live = {int(r["id"]): r for r in entries(conn, owner_id)}
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

    texts = {str(r["text"]).casefold() for r in live.values()}
    for entry_id, weight in ops.reweight:
        done.event_ids.append(_set(conn, entry_id, actor, "reweight", reason, weight=weight))
    for entry_id in ops.drop:
        done.event_ids.append(_set(conn, entry_id, actor, "drop", reason, live=False))
    n_live = len(live) - len(ops.drop)
    for raw, weight in ops.add:
        text = _text(raw)
        if text.casefold() in texts:
            done.duplicates.append(text)
            continue
        texts.add(text.casefold())
        n_live += 1
        _guard_cap(n_live)
        entry_id = int(
            conn.execute(
                "INSERT INTO profile_entries (owner_id, text, weight, source)"
                " VALUES (?, ?, ?, ?)",
                (owner_id, text, weight, actor),
            ).lastrowid
        )
        after = {"text": text, "weight": weight, "live": True}
        done.event_ids.append(_event(conn, actor, "add", entry_id, None, after, reason))
    return done


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
        _guard_cap(len(entries(conn, owner_id)) + 1)
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
    if len(text) > MAX_TEXT_CHARS:
        raise ProfileRefused(
            "E_BAD_PARAM",
            f"an entry is {len(text)} characters; the limit is {MAX_TEXT_CHARS}.",
            "one interest per entry, in a short phrase.",
        )
    return text


def _reason(raw: str | None) -> str | None:
    if raw is None:
        return None
    reason = " ".join(raw.split())
    return reason[:MAX_REASON_CHARS] or None
