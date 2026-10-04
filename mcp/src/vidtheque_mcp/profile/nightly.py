"""The nightly profile update — companion.md §2.4.

Once a day, on the job runner's poll tick, a model reads the live entries, the
signals since the last run and the verdicts those signals touched, and proposes
add/drop/reweight ops with a reason each. Each op goes through
`store.apply` as `actor=nightly`, the tool's own code path, so the store's
guards hold (owner entries are never dropped, 40 live entries at most). This
module adds the two guards that are about a night rather than a batch: at most
`MAX_OPS` applied, and no weight moves by more than `MAX_MOVE`.

`nightly_runs` holds one row per local day. The run claims it before the model
call and marks it `done` in the transaction that applies the ops, so a restart
never runs a day twice and a crash in between applied nothing.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Callable

import httpx2 as httpx

from ..config import ConfigError, _bool_env, _int_env
from ..llm import LLMSettings, LLMUnavailable, Model, build_model, is_configured
from . import feedback, store

logger = logging.getLogger(__name__)

MAX_OPS = 5
MAX_MOVE = 0.3
# Bounds on what the model reads, whatever piled up since the last run.
MAX_SIGNALS = 200
MAX_VERDICTS = 50
WINDOW_FLOOR_S = 7 * 86_400  # never read further back than a week
FIRST_WINDOW_S = 86_400
# A failed model call is retried that day, a few times, an hour apart; a run
# left `running` by a dead process counts as failed once it is this old.
MAX_ATTEMPTS = 3
RETRY_AFTER_S = 3_600
# How often the tick looks at the database once the hour has come.
CHECK_EVERY_S = 60

OPS_SCHEMA: dict[str, Any] = {
    "type": "object",
    "additionalProperties": False,
    "required": ["ops"],
    "properties": {
        "ops": {
            "type": "array",
            # Room past MAX_OPS so a long answer is cut by the guard, not refused whole.
            "maxItems": 20,
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["op", "reason"],
                "properties": {
                    "op": {"enum": ["add", "drop", "reweight"]},
                    "id": {"type": "integer"},
                    # Looser than the store's cap: a long topic is refused as one op
                    # by `store.apply`, not as an invalid answer that fails the night.
                    "text": {"type": "string", "minLength": 1, "maxLength": 200},
                    "weight": {"type": "number", "minimum": -1, "maximum": 1},
                    "reason": {"type": "string", "minLength": 1, "maxLength": 300},
                },
            },
        }
    },
}

SYSTEM = f"""You maintain one person's interest profile: short entries in plain
words, each weighted from -1 (less of this) to +1 (more of this). From what
they did since the last update, propose at most {MAX_OPS} changes, or none.
Answer with one JSON object, {{"ops": [...]}}, each op one of:
- {{"op": "add", "text": "...", "weight": w, "reason": "..."}}
- {{"op": "reweight", "id": n, "weight": w, "reason": "..."}}
- {{"op": "drop", "id": n, "reason": "..."}}
reason: one line naming the evidence, e.g. "4 searches on eval harnesses".
Rules the server enforces: a weight moves by at most {MAX_MOVE} a night and a new
entry starts within ±{MAX_MOVE}; entries marked owner are never
dropped, only reweighted; the profile holds at most {store.MAX_LIVE} entries.
Searches say what the person is working on; thumbs, mutes and asks are explicit
(a mute means "less like this"); "took back" undoes a thumb or mute an earlier
update read. An open is a mild interest and a dismiss a mild disinterest. Prefer reweighting
an entry to adding a near-duplicate. A project (marked after its source) is what the person is building now; it
lapses by itself, so leave it be. An entry's text is a short topic, 2-4 words
(at most {store.MAX_TEXT_CHARS} characters and {store.MAX_TEXT_WORDS} words); split a compound topic
into separate entries."""


@dataclass(frozen=True)
class NightlySettings:
    enabled: bool = True
    hour: int = 4

    @classmethod
    def from_env(cls) -> "NightlySettings":
        hour = _int_env("VIDTHEQUE_NIGHTLY_HOUR", 4)
        if not 0 <= hour <= 23:
            raise ConfigError(f"VIDTHEQUE_NIGHTLY_HOUR must be 0–23, got {hour}")
        return cls(enabled=_bool_env("VIDTHEQUE_NIGHTLY", True), hour=hour)


@dataclass
class Claim:
    run_id: int
    since_at: int
    until_at: int


@dataclass
class Outcome:
    state: str
    applied: list[int] = field(default_factory=list)
    refused: list[dict[str, Any]] = field(default_factory=list)


class Nightly:
    def __init__(
        self,
        db: Any,
        model: Model,
        label: str,
        *,
        hour: int = 4,
        owner_id: int = 1,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self.db = db
        self.model = model
        self.label = label
        self.hour = hour
        self.owner_id = owner_id
        # Local, aware time; injected so tests pick the day and the hour.
        self.clock = clock or (lambda: datetime.now().astimezone())
        self._task: asyncio.Task[Outcome | None] | None = None
        self._next_check = 0.0

    # ------------------------------------------------------------- the tick

    async def tick(self) -> None:
        """The runner's pre-claim hook: start today's run in the background when due.

        Never awaits the model: a run outlives many ticks, and the queue keeps moving.
        """
        if self._task is not None and not self._task.done():
            return
        now = self.clock()
        if now.hour < self.hour or now.timestamp() < self._next_check:
            return
        self._next_check = now.timestamp() + CHECK_EVERY_S
        self._task = asyncio.create_task(self._guarded(), name="vidtheque-nightly")

    async def aclose(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass

    async def _guarded(self) -> Outcome | None:
        try:
            return await self.run_once()
        except Exception:  # the tick's task must not die loudly every minute
            logger.exception("nightly profile update failed")
            return None

    # ------------------------------------------------------------- one run

    async def run_once(self) -> Outcome | None:
        """Today's run, if it is owed. None when the day is already settled."""
        now = self.clock()
        day, at = now.date().isoformat(), int(now.timestamp())
        claim = await self.db.write(lambda c: claim_day(c, day, at, self.owner_id))
        if claim is None:
            return None
        # Lapsed projects retire first, whatever the day's signals: no model involved.
        expired = await self.db.write(lambda c: store.expire(c, at, self.owner_id))
        if expired:
            logger.info("nightly profile update: %d project(s) expired", len(expired))
        prompt, n_signals, read = await self.db.read(
            lambda c: _prompt(c, claim.since_at, claim.until_at, self.owner_id)
        )
        if n_signals == 0:
            await self.db.write(lambda c: _finish(c, claim.run_id, "idle", at, n_signals=0))
            return Outcome("idle")
        try:
            answer = await self.model.complete(
                prompt, system=SYSTEM, schema=OPS_SCHEMA, purpose="nightly_update"
            )
        except LLMUnavailable as exc:
            await self.db.write(
                lambda c: _finish(c, claim.run_id, "failed", at, n_signals=n_signals, error=exc.reason)
            )
            logger.warning("nightly profile update: the model is unavailable (%s)", exc.reason)
            return Outcome("failed")

        def write(c: sqlite3.Connection) -> Outcome:
            outcome = apply_ops(c, answer["ops"], self.owner_id)
            feedback.mark_read(c, read, self.owner_id)
            _finish(
                c,
                claim.run_id,
                "done",
                at,
                n_signals=n_signals,
                n_applied=len(outcome.applied),
                refused=outcome.refused,
                model=self.label,
            )
            return outcome

        outcome = await self.db.write(write)
        logger.info(
            "nightly profile update: %d op(s) applied, %d refused",
            len(outcome.applied),
            len(outcome.refused),
        )
        return outcome


# --------------------------------------------------------------- the claim


def claim_day(conn: sqlite3.Connection, day: str, now: int, owner_id: int = 1) -> Claim | None:
    """Claim today's run, or None when today is done, idle, or not yet retryable."""
    row = conn.execute(
        "SELECT * FROM nightly_runs WHERE owner_id = ? AND day = ?", (owner_id, day)
    ).fetchone()
    since = _since(conn, now, owner_id)
    if row is None:
        run_id = conn.execute(
            "INSERT INTO nightly_runs (owner_id, day, state, started_at, since_at, until_at)"
            " VALUES (?, ?, 'running', ?, ?, ?)",
            (owner_id, day, now, since, now),
        ).lastrowid
        return Claim(int(run_id), since, now)
    retryable = row["state"] in ("failed", "running") and int(row["attempts"]) < MAX_ATTEMPTS
    if not retryable or now - int(row["started_at"]) < RETRY_AFTER_S:
        return None
    conn.execute(
        "UPDATE nightly_runs SET state = 'running', attempts = attempts + 1, started_at = ?,"
        " finished_at = NULL, since_at = ?, until_at = ?, error = NULL WHERE id = ?",
        (now, since, now, row["id"]),
    )
    return Claim(int(row["id"]), since, now)


def _since(conn: sqlite3.Connection, now: int, owner_id: int) -> int:
    """Where the last settled run stopped reading; a day back on the first run."""
    row = conn.execute(
        "SELECT MAX(until_at) FROM nightly_runs WHERE owner_id = ? AND state IN ('done','idle')",
        (owner_id,),
    ).fetchone()
    since = int(row[0]) if row[0] is not None else now - FIRST_WINDOW_S
    return max(since, now - WINDOW_FLOOR_S)


def _finish(
    conn: sqlite3.Connection,
    run_id: int,
    state: str,
    now: int,
    *,
    n_signals: int,
    n_applied: int = 0,
    refused: list[dict[str, Any]] | None = None,
    model: str | None = None,
    error: str | None = None,
) -> None:
    conn.execute(
        "UPDATE nightly_runs SET state = ?, finished_at = ?, n_signals = ?, n_applied = ?,"
        " refused = ?, model = ?, error = ? WHERE id = ?",
        (state, now, n_signals, n_applied, json.dumps(refused or []), model, error, run_id),
    )


# ---------------------------------------------------------------- the ops


def apply_ops(conn: sqlite3.Connection, ops: list[dict[str, Any]], owner_id: int = 1) -> Outcome:
    """Apply the model's ops one by one through `store.apply`, under the night's guards.

    One op per `store.apply` call, so each event keeps its own reason and a
    refused op costs only itself. Every store guard is checked before its
    write, so a refusal leaves nothing behind.
    """
    outcome = Outcome("done")
    touched: set[int] = set()

    def refuse(op: dict[str, Any], why: str) -> None:
        outcome.refused.append({**op, "refused": why})

    for op in ops:
        if len(outcome.applied) >= MAX_OPS:
            refuse(op, f"over {MAX_OPS} changes a night")
            continue
        kind, reason = op["op"], str(op["reason"])
        entry_id = op.get("id")
        if kind in ("drop", "reweight"):
            if entry_id is None or (kind == "reweight" and "weight" not in op):
                refuse(op, "incomplete op")
                continue
            if entry_id in touched:
                refuse(op, "an entry changes once a night")
                continue
        if kind == "add" and ("text" not in op or "weight" not in op):
            refuse(op, "incomplete op")
            continue

        if kind == "add":
            weight, capped = _clamp(float(op["weight"]), 0.0)
            batch = store.Ops(add=[(str(op["text"]), weight)], reason=_note(reason, capped))
        elif kind == "drop":
            batch = store.Ops(drop=[int(entry_id)], reason=reason)
        else:
            current = conn.execute(
                "SELECT weight FROM profile_entries WHERE id = ? AND owner_id = ?"
                " AND retired_at IS NULL",
                (int(entry_id), owner_id),
            ).fetchone()
            if current is None:
                refuse(op, "no live entry with that id")
                continue
            weight, capped = _clamp(float(op["weight"]), float(current[0]))
            if weight == float(current[0]):
                refuse(op, "no change")
                continue
            batch = store.Ops(reweight=[(int(entry_id), weight)], reason=_note(reason, capped))
        try:
            done = store.apply(conn, batch, actor="nightly", owner_id=owner_id)
        except store.ProfileRefused as refused:
            refuse(op, str(refused))
            continue
        if done.duplicates:
            refuse(op, "already an entry")
            continue
        outcome.applied.extend(done.event_ids)
        if entry_id is not None:
            touched.add(int(entry_id))
    return outcome


def _clamp(weight: float, current: float) -> tuple[float, bool]:
    low, high = max(-1.0, current - MAX_MOVE), min(1.0, current + MAX_MOVE)
    clamped = round(min(max(weight, low), high), 4)
    return clamped, clamped != weight


def _note(reason: str, capped: bool) -> str:
    return f"{reason} (capped at ±{MAX_MOVE} a night)" if capped else reason


# -------------------------------------------------------------- the prompt


def _prompt(
    conn: sqlite3.Connection, since: int, until: int, owner_id: int
) -> tuple[str, int, list[tuple[int, str]]]:
    """The night's prompt, how many things it reads, and the feedback rows it read.

    Thumbs and mutes come from `feedback`, netted to each video's state since
    the last night, not from their events in `signals`.
    """
    signals = conn.execute(
        "SELECT s.at, s.kind, s.offset_s, s.text, s.video_id, v.title, v.channel_name"
        " FROM signals s LEFT JOIN videos v ON v.id = s.video_id"
        " WHERE s.owner_id = ? AND s.at > ? AND s.at <= ?"
        " AND s.kind NOT IN ('thumb_up', 'thumb_down', 'mute')"
        " ORDER BY s.at DESC, s.id DESC LIMIT ?",
        (owner_id, since, until, MAX_SIGNALS),
    ).fetchall()
    moved = feedback.unread(conn, until, owner_id, MAX_SIGNALS)
    if not signals and not moved:
        return "", 0, []
    entries = store.entries(conn, owner_id)
    profile = (
        "\n".join(
            f"{e['id']}\t{float(e['weight']):+.2f}\t"
            f"{'owner' if e['source'] in store.OWNER_ACTORS else e['source']}"
            f"{' project' if e['kind'] == 'project' else ''}\t{e['text']}"
            for e in entries
        )
        or "(empty)"
    )
    lines = []
    for s in reversed(signals):
        what = s["text"] or ""
        if s["title"] is not None:
            what = f'"{s["title"]}" ({s["channel_name"] or "?"})' + (
                f" at {float(s['offset_s']):.0f}s" if s["offset_s"] is not None else ""
            ) + (f" — {s['text']}" if s["text"] else "")
        lines.append(f"{s['kind']}\t{what}")
    for f in moved:
        lines.append(f"{_moved(f['state'], f['seen'])}\t\"{f['title']}\" ({f['channel_name'] or '?'})")
    video_ids = list(
        dict.fromkeys(
            [int(s["video_id"]) for s in signals if s["video_id"] is not None]
            + [int(f["video_id"]) for f in moved]
        )
    )
    verdicts = conn.execute(
        "SELECT v.title, d.score, d.reason, d.explored FROM verdicts d"
        " JOIN videos v ON v.id = d.video_id"
        " WHERE d.video_id IN (SELECT value FROM json_each(?)) LIMIT ?",
        (json.dumps(video_ids), MAX_VERDICTS),
    ).fetchall()
    judged = "\n".join(
        f'"{d["title"]}": score {d["score"]}, {d["reason"]}'
        + (" (outside the profile)" if d["explored"] else "")
        for d in verdicts
    ) or "(none)"
    prompt = (
        f"Profile (id, weight, source, entry; {len(entries)} of {store.MAX_LIVE}):\n{profile}\n\n"
        f"What they did since the last update ({len(signals)} signals, oldest first;"
        f" then {len(moved)} thumbs or mutes as they stand now):\n"
        + "\n".join(lines)
        + f"\n\nVerdicts on the videos above (score 0 skip … 3 watch whole):\n{judged}"
    )
    return prompt, len(signals) + len(moved), [(int(f["video_id"]), str(f["state"])) for f in moved]


def _moved(state: str, seen: str) -> str:
    """A video's feedback since the last night, as one line's kind."""
    if state == "none":
        return f"took back {feedback.KIND_OF[seen]}"
    if seen == "none":
        return feedback.KIND_OF[state]
    return f"{feedback.KIND_OF[state]} (was {feedback.KIND_OF[seen]})"


# ------------------------------------------------------------- assembly


def build_nightly(db: Any) -> tuple[Nightly | None, httpx.AsyncClient | None]:
    """The update and the HTTP client it owns, or (None, None) when off.

    Off unless the companion model is configured, and off when
    `VIDTHEQUE_NIGHTLY=0` even then.
    """
    nightly = NightlySettings.from_env()
    if not nightly.enabled:
        return None, None
    settings = LLMSettings.from_env()
    if not is_configured(settings):
        return None, None
    http = httpx.AsyncClient() if settings.backend == "api" else None
    model = build_model(settings, http, db)  # type: ignore[arg-type]
    assert model is not None
    label = f"{settings.backend}:{settings.model or 'default'}"
    return Nightly(db, model, label, hour=nightly.hour), http
