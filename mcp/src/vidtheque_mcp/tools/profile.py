"""`profile`: read and edit the owner's interest profile (companion.md §2.2, tool-surface §4.12).

Called bare it returns the live entries and the revision. `add`, `drop` and
`reweight` in one call are one batch: every guard is checked before anything
is written, so a refused call changes nothing.
"""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from typing import Any, Literal

from mcp_types import CallToolResult
from pydantic import BaseModel, ConfigDict

from ..errors import ToolError
from ..profile import store
from .base import Deps, handle_errors, text_result


class NewEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")

    text: str
    weight: float
    kind: Literal["topic", "project"] = "topic"


class Reweight(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: int
    weight: float


@handle_errors
async def profile(
    deps: Deps,
    add: list[NewEntry] | None = None,
    drop: list[int] | None = None,
    reweight: list[Reweight] | None = None,
    reason: str | None = None,
) -> CallToolResult:
    ops = store.Ops(
        add=[(e.text, e.weight, e.kind) for e in add or ()],
        drop=list(drop or ()),
        reweight=[(r.id, r.weight) for r in reweight or ()],
        reason=reason,
    )
    applied = store.Applied()
    if ops.add or ops.drop or ops.reweight:
        try:
            applied = await deps.db.write(lambda c: store.apply(c, ops, actor="agent"))
        except store.ProfileRefused as refused:
            raise ToolError(refused.code, str(refused), refused.next_hint) from None
    rows, revision = await deps.db.read(_snapshot)
    return text_result(_render(rows, revision, applied), _structured(rows, revision, applied))


def _snapshot(conn: sqlite3.Connection) -> tuple[list[sqlite3.Row], int]:
    return store.entries(conn), store.revision(conn)


def _render(rows: list[sqlite3.Row], revision: int, applied: store.Applied) -> str:
    lines = [f"Profile: {len(rows)} of {store.MAX_LIVE} entries · revision {revision}"]
    if applied.event_ids:
        lines.append(f"Applied {len(applied.event_ids)} change(s).")
    for text in applied.duplicates:
        lines.append(f"note: {text!r} is already an entry; not added twice.")
    if applied.refreshed:
        lines.append(f"Refreshed {len(applied.refreshed)} project(s) for another 30 days.")
    if rows:
        lines.append("")
        lines.append("id\tweight\ttext\tsource\tkind")
        lines.extend(
            f"{r['id']}\t{float(r['weight']):+.2f}\t{r['text']}\t{r['source']}\t{_kind(r)}"
            for r in rows
        )
    else:
        lines.append("The profile is empty.")
    lines.append("")
    lines.append(
        "next: add=[{text, weight}] for a new interest (weight -1..1, negative = less "
        "of this), kind=project for what the user is building now; reweight=[{id, weight}] "
        "or drop=[id] to change one. Edit, never rewrite."
    )
    return "\n".join(lines)


def _kind(row: sqlite3.Row) -> str:
    if row["expires_at"] is None:
        return str(row["kind"])
    until = datetime.fromtimestamp(int(row["expires_at"]), timezone.utc).date().isoformat()
    return f"{row['kind']} until {until}"


def _structured(rows: list[sqlite3.Row], revision: int, applied: store.Applied) -> dict[str, Any]:
    return {
        "revision": revision,
        "entries": [
            {
                "id": int(r["id"]),
                "weight": float(r["weight"]),
                "text": r["text"],
                "source": r["source"],
                "kind": r["kind"],
                "expires_at": r["expires_at"],
            }
            for r in rows
        ],
        "applied_events": applied.event_ids,
        "duplicates": applied.duplicates,
        "refreshed": applied.refreshed,
    }
